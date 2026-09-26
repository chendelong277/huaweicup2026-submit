from __future__ import annotations

import argparse, csv, importlib.util, json, sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("q2_solver",HERE/"q2_solver.py")
q2=importlib.util.module_from_spec(spec); assert spec.loader
sys.modules["q2_solver"]=q2; spec.loader.exec_module(q2)

def read_csv(p):
    with p.open(encoding="utf-8-sig") as f:return list(csv.DictReader(f))

def overlap(intervals):
    xs=sorted(intervals)
    return any(xs[i][0] < xs[i-1][1]-1e-8 for i in range(1,len(xs)))

def main(data_root:Path, results:Path):
    nodes,vehicles,box_map,_,dem,uavs,batteries,_=q2.load_entities(data_root)
    plan=read_csv(results/"transport_plan.csv"); delivery=read_csv(results/"delivery_timeline.csv")
    resources=read_csv(results/"transport_resource_timeline.csv"); trips=read_csv(results/"trip_summary.csv")
    checks=[]
    def add(name,ok,detail=""): checks.append({"check":name,"passed":bool(ok),"detail":detail})
    counts=Counter(r["box_id"] for r in delivery)
    add("every_box_exactly_once",set(counts)==set(box_map) and all(v==1 for v in counts.values()),f"delivered={len(counts)}, expected={len(box_map)}")
    for t in trips:
        tid=t["trip_id"]; legs=sorted([r for r in plan if r["trip_id"]==tid],key=lambda x:int(x["sequence_no"]))
        add(f"{tid}:route_closed",bool(legs) and legs[0]["from_node"]=="O01" and legs[-1]["to_node"]=="O01")
        add(f"{tid}:route_continuity",all(legs[i-1]["to_node"]==legs[i]["from_node"] for i in range(1,len(legs))))
        vt=t["vehicle_type"]; v=vehicles[vt]; energy=sum(float(x["energy_kwh"]) for x in legs)
        add(f"{tid}:mass",float(t["payload_kg"])<=v.max_payload_kg+1e-8)
        add(f"{tid}:volume",float(t["volume_m3"])<=v.max_volume_m3+1e-8)
        add(f"{tid}:energy",energy<=(1-q2.RESERVE)*v.usable_energy_kwh+1e-8,f"energy={energy:.6f}")
        add(f"{tid}:soc",float(t["soc_end"])>=q2.RESERVE-1e-8 and abs(float(t["soc_end"])-(1-energy/v.usable_energy_kwh))<1e-7)
    hard=[]
    for r in delivery:
        b=box_map[r["box_id"]]; target=b.deadline_s if b.is_first_batch else (b.expected_time_s if b.material_type=="医疗物资" else None)
        if target is not None and float(r["delivery_time_s"])>target+1e-8: hard.append(r["box_id"])
    add("hard_deadlines",not hard,";".join(hard))
    by_res=defaultdict(list)
    for r in resources: by_res[(r["resource_type"],r["resource_id"])].append((float(r["start_time_s"]),float(r["end_time_s"]),r))
    for key,ints in by_res.items(): add(f"resource_nonoverlap:{key[0]}:{key[1]}",not overlap([(a,b) for a,b,_ in ints]))
    for (typ,rid),ints in by_res.items():
        if typ!="battery":continue
        vt=rid.split("-")[0]; full=batteries[vt]["full_charge_s"]
        for a,b,r in ints:
            if r["activity"]=="charging":
                expected=q2.charge_time(float(r["soc_before"]),full)
                add(f"charge_duration:{rid}:{r['trip_id']}",abs((b-a)-expected)<1e-6,f"actual={b-a:.6f}, expected={expected:.6f}")
    summary={"overall_pass":all(x["passed"] for x in checks),"checks":len(checks),"failed":[x for x in checks if not x["passed"]]}
    q2.write_csv(results/"constraint_audit.csv",checks,["check","passed","detail"])
    (results/"validation_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--data-root",type=Path,required=True);ap.add_argument("--results-dir",type=Path,required=True);a=ap.parse_args();main(a.data_root,a.results_dir)
