from app.reasons import reject
from app.values import instant,number,money
from datetime import timezone,timedelta
from itertools import groupby
ZONE=timezone(timedelta(hours=8))
def business_day(at): return at.astimezone(ZONE).date().isoformat()
def base_journey(row,pairing,reason):
    at=instant(row["at"])
    return {**row,"business_day":business_day(at),"journey":row["card"]+"-"+row["event_id"],"boarding_event":row["event_id"],"alighting_event":"—","alighted_at":"—","completed_at":row["at"],"pairing":pairing,"pair_reason":reason}
def flat_journey(row): return base_journey(row,"一票制","一票制单次刷卡，无需配对")
def orphan_journey(row): return base_journey(row,"未配对","分段计价缺少配对刷卡，保留原记录按旧版单次计费")
def pair_journey(boarding,alighting):
    journey=base_journey(boarding,"已配对","分段计价上下车配对，两次刷卡合并为一次行程，消除重复扣费")
    journey["reported"]=money(number(boarding["reported"])+number(alighting["reported"]))
    journey["alighting_event"]=alighting["event_id"]
    journey["alighted_at"]=alighting["at"]
    journey["completed_at"]=alighting["at"]
    journey["_sources"]=sorted(set(boarding.get("_sources",[boarding.get("_line",0)])+alighting.get("_sources",[alighting.get("_line",0)])))
    return journey
def pair_card(taps):
    "同一卡按时间序：分段线路连续两刷配对为一程；被其他刷卡打断或未闭合的登机刷按孤儿保留。"
    journeys=[];paired=orphaned=0;open_boarding=None
    for row in taps:
        if row.get("fare_type")!="segmented":
            if open_boarding is not None:journeys.append(orphan_journey(open_boarding));orphaned+=1;open_boarding=None
            journeys.append(flat_journey(row));continue
        if open_boarding is None:open_boarding=row;continue
        if open_boarding["route"]==row["route"]:journeys.append(pair_journey(open_boarding,row));paired+=1;open_boarding=None
        else:journeys.append(orphan_journey(open_boarding));orphaned+=1;open_boarding=row
    if open_boarding is not None:journeys.append(orphan_journey(open_boarding));orphaned+=1
    return journeys,paired,orphaned
def evaluate(rows,context):
    bad=[];seen=set();valid=[]
    for row in sorted(rows,key=lambda r:(r.get("card",""),r["at"],r.get("event_id",""))):
        if row.get("result")!="success":bad.append(reject(row,"FAILED_TAP","失败刷卡不构成行程"));continue
        key=(row["card"],row["at"])
        if key in seen:bad.append(reject(row,"SIMULTANEOUS_TAP","同卡同刻多行程"));continue
        seen.add(key);valid.append(row)
    good=[];paired=orphaned=0
    for card,group in groupby(valid,key=lambda r:r["card"]):
        journeys,p,o=pair_card(list(group));good.extend(journeys);paired+=p;orphaned+=o
    return {"rows":good,"quarantine":bad,"metrics":{"journeys":len(good),"cards":len(set(r["card"] for r in good)),"paired":paired,"unpaired":orphaned}}
