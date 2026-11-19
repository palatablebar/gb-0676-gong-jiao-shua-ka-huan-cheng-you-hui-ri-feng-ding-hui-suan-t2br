from decimal import Decimal
from app.values import number,instant,money
from app.errors import DomainError
def evaluate(rows,context):
    params=context.get("parameters",{});cap=number(params.get("daily_cap","6.00"));off=number(params.get("transfer_discount","1.00"));window=number(params.get("transfer_minutes","60"))
    if cap<0 or off<0 or window<0:raise DomainError("BAD_DISCOUNT","优惠参数不能为负")
    spent={};anchor={};good=[];summary={}
    for row in sorted(rows,key=lambda r:(r["card"],instant(r["at"]),r["event_id"])):
        key=(row["card"],row["business_day"]);base=number(row["base_fare"]);previous=anchor.get(key);eligible=False;interval=None
        if previous:
            # 裁决：换乘间隔以上一次实际付费行程的完成时刻为起算事实
            # （分段计价线路=下车刷卡时刻，一票制线路=上车刷卡时刻），窗口[0,window)分钟。
            interval=Decimal(str((instant(row["at"])-instant(previous["completed"])).total_seconds()))/Decimal(60)
            eligible=previous["route"]!=row["route"] and Decimal(0)<=interval<window
        transfer=min(base,off) if eligible else Decimal(0)
        after_transfer=base-transfer;remaining=max(Decimal(0),cap-spent.get(key,Decimal(0)));charge=min(after_transfer,remaining)
        cap_discount=after_transfer-charge;spent[key]=spent.get(key,Decimal(0))+charge
        good.append({**row,"transfer_anchor":previous["event_id"] if eligible else "—","anchor_completed_at":previous["completed"] if eligible else "—","transfer_minutes":str(interval.quantize(Decimal("0.01"))) if eligible else "—","transfer_discount":money(transfer),"cap_discount":money(cap_discount),"expected":money(charge),"day_paid":money(spent[key])})
        if charge>0:anchor[key]={"event_id":row["event_id"],"route":row["route"],"completed":row.get("completed_at") or row["at"]}
        day=summary.setdefault(key,{"card":key[0],"business_day":key[1],"journeys":0,"base_total":Decimal(0),"transfer_total":Decimal(0),"cap_total":Decimal(0),"expected_total":Decimal(0)})
        day["journeys"]+=1;day["base_total"]+=base;day["transfer_total"]+=transfer;day["cap_total"]+=cap_discount;day["expected_total"]+=charge
    card_days=[{**day,"base_total":money(day["base_total"]),"transfer_total":money(day["transfer_total"]),"cap_total":money(day["cap_total"]),"expected_total":money(day["expected_total"])} for _,day in sorted(summary.items())]
    return {"rows":good,"quarantine":[],"card_days":card_days,"metrics":{"expected_total":money(sum((number(r["expected"]) for r in good),Decimal(0))),"daily_caps":len(spent),"transfer_hits":sum(r["transfer_anchor"]!="—" for r in good)}}
