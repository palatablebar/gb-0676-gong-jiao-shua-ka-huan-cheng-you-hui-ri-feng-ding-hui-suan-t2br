import pytest
from decimal import Decimal
from app.pipeline import calculate,execute,load_sample
from app.modules.m4_journeys.rules import evaluate as pair
from app.modules.m5_discounts.rules import evaluate as discount
from app import store
def tap(id,minute,route="S1",reported="0",fare_type=None,card="1001"):
    return {"event_id":id,"card":card,"route":route,"fare_type":fare_type or ("segmented" if route=="S1" else "flat"),"result":"success","reported":reported,"at":f"2026-11-19T{8+minute//60:02d}:{minute%60:02d}:00+08:00"}
def journey(id,minute,completed=None,route="01",base="2.50",card="1001"):
    row={"event_id":id,"card":card,"business_day":"2026-11-19","at":f"2026-11-19T{minute//60:02d}:{minute%60:02d}:00+08:00","route":route,"base_fare":base}
    if completed is not None:row["completed_at"]=f"2026-11-19T{completed//60:02d}:{completed%60:02d}:00+08:00"
    return row
@pytest.fixture
def seg(): return calculate(load_sample("segmented.csv"))
def test_pair_merges_two_taps_into_one_journey():
    "TRAP-27 分段两刷合并一程，只计一次费"
    r=pair([tap("A1",0,"S1","2.50"),tap("A2",20,"S1","2.50")],{})
    assert len(r["rows"])==1 and r["metrics"]["paired"]==1
    j=r["rows"][0]
    assert j["pairing"]=="已配对" and j["boarding_event"]=="A1" and j["alighting_event"]=="A2"
    assert j["completed_at"]==j["alighted_at"] and j["reported"]=="5.00"
def test_orphan_keeps_original_record_old_result():
    "TRAP-30 配对失败保留原记录和旧版单次计费结果"
    r=pair([tap("A1",0,"S1","2.50")],{})
    j=r["rows"][0]
    assert j["pairing"]=="未配对" and "保留原记录" in j["pair_reason"]
    assert j["event_id"]=="A1" and j["reported"]=="2.50" and j["completed_at"]==j["at"]
def test_interrupted_boarding_becomes_orphan():
    r=pair([tap("A1",0),tap("A2",10,"01","2.50"),tap("A3",20)],{})
    assert [j["pairing"] for j in r["rows"]]==["未配对","一票制","未配对"]
def test_other_segmented_route_does_not_close_boarding():
    r=pair([tap("A1",0,"S1"),tap("A2",10,"S2",fare_type="segmented")],{})
    assert r["metrics"]["unpaired"]==2 and r["metrics"]["paired"]==0
def test_transfer_anchor_is_alighting_completion():
    "TRAP-28 裁决：换乘间隔以上一付费行程完成时刻（下车）起算，长途刚下车可接续"
    rows=[journey("J1",480,completed=560,route="S1"),journey("J2",570,route="02")]
    r=discount(rows,{})["rows"][1]
    assert r["transfer_discount"]=="1.00" and r["transfer_minutes"]=="10.00"
    assert r["transfer_anchor"]=="J1" and r["anchor_completed_at"].endswith("09:20:00+08:00")
def test_boarding_anchor_would_exclude_is_granted():
    "反证：若以上车时刻起算同一接续会被误排（90分钟>60）"
    rows=[journey("J1",480,completed=560,route="S1"),journey("J2",570,route="02")]
    assert discount(rows,{})["rows"][1]["expected"]=="1.50"
def test_exact_window_from_alighting_excluded():
    "TRAP-23 保持：恰好60分钟（自下车起算）排除"
    rows=[journey("J1",480,completed=560,route="S1"),journey("J2",620,route="02")]
    assert discount(rows,{})["rows"][1]["transfer_discount"]=="0.00"
def test_sample_pairing_counts(seg):
    m=seg["stages"][3]["metrics"]
    assert (m["journeys"],m["paired"],m["unpaired"])==(16,4,3)
def test_sample_double_charge_exposed(seg):
    rows={r["event_id"]:r for r in seg["stages"][5]["rows"]}
    assert rows["S005"]["expected"]=="2.50" and rows["S005"]["reported"]=="5.00"
    assert rows["S005"]["difference"]=="-2.50" and rows["S005"]["status"]=="差异"
def test_sample_supplementary_duplicate_not_double_charged(seg):
    "TRAP-29 同一补刷重复出现不重复占额"
    assert seg["stages"][0]["metrics"]["duplicate"]==1
    day={d["card"]:d for d in seg["stages"][4]["card_days"]}["1004"]
    assert day["journeys"]==1 and day["expected_total"]=="2.50"
def test_sample_adjudication_long_journey(seg):
    rows={r["event_id"]:r for r in seg["stages"][4]["rows"]}
    assert rows["S012"]["transfer_discount"]=="1.00" and rows["S012"]["transfer_minutes"]=="10.00"
    assert rows["S012"]["transfer_anchor"]=="S010"
def test_sample_zero_paid_not_renew_anchor(seg):
    rows={r["event_id"]:r for r in seg["stages"][4]["rows"]}
    assert rows["S017"]["expected"]=="0.00" and rows["S018"]["transfer_discount"]=="0.00"
def test_card_day_unique_and_conserved(seg):
    days=seg["stages"][4]["card_days"]
    keys=[(d["card"],d["business_day"]) for d in days]
    assert len(keys)==len(set(keys))
    for d in days:
        assert Decimal(d["base_total"])==Decimal(d["expected_total"])+Decimal(d["transfer_total"])+Decimal(d["cap_total"])
    assert sum(Decimal(d["expected_total"]) for d in days)==Decimal(seg["stages"][4]["metrics"]["expected_total"])
def test_unverifiable_amount_keeps_original(seg):
    "金额无法核实：原记录隔离保留，配对失败的上车刷按旧版结果保留"
    q={r.get("event_id"):r for r in seg["stages"][5]["cumulative_quarantine"]}
    assert q["S020"]["reason"]=="EMPTY_VALUE" and q["S020"]["_raw"]
    rows={r["event_id"]:r for r in seg["stages"][4]["rows"]}
    assert rows["S019"]["pairing"]=="未配对" and rows["S019"]["expected"]=="2.50"
def test_balanced_lineage_with_pairs(seg):
    m=seg["stages"][5]["metrics"]
    assert m["balanced"] is True and m["accepted_inputs"]+m["quarantine_inputs"]+m["excluded"]==m["input"]
    assert seg["stages"][6]["metrics"]["ready"] is True
def test_rerun_same_input_reuses_batch():
    "TRAP-29 同源重跑只复用批次，不重复占额"
    raw=load_sample("segmented.csv");a=execute(raw);b=execute(raw)
    assert a["run_id"]==b["run_id"] and len(store.history())==1
    day={d["card"]:d for d in b["stages"][4]["card_days"]}["1004"]
    assert day["expected_total"]=="2.50"
def test_e2e_segmented_flow(client):
    p=client.post("/api/runs",json={"sample":"segmented.csv"})
    assert p.status_code==200 and len(p.get_json()["stages"])==7
    assert p.get_json()["stages"][5]["metrics"]["balanced"] is True
def test_pages_trace_pairing_and_basis(client):
    client.post("/api/runs",json={"sample":"segmented.csv"})
    journeys=client.get("/journeys").data.decode()
    assert "配对说明" in journeys and "变化原因" in journeys and "已配对" in journeys
    discounts=client.get("/discounts").data.decode()
    assert "卡日汇总" in discounts and "换乘间隔裁决" in discounts and "锚点完成时刻" in discounts
def test_archive_keeps_pair_sources(client):
    import csv,io
    run=client.post("/api/runs",json={"sample":"segmented.csv"}).get_json()
    rows=list(csv.reader(io.StringIO(client.get("/api/runs/"+run["run_id"]+"/archive").data.decode("utf-8-sig"))))
    paired=[x for x in rows[1:] if x[3]=="accepted" and "已配对" in x[6]]
    assert paired and all(x[4].count(",")>=1 for x in paired)
