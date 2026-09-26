"""WHLi strict relay gate for ALNS-Q3 transport candidates.

Uses the tested WHLi chain: strict direct probe -> coarse candidate catalog ->
strict candidate screening -> two-relay set-cover/occupancy MILP -> assembly ->
dense and refined continuity checks.  This is a feasibility gate, not a
replacement for the ALNS transport search.
"""
from __future__ import annotations
import argparse, csv, json, os, subprocess, sys, time, math
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
WH = REPO / "members" / "WHLi"
PY = sys.executable

def call(args, env=None):
    subprocess.run([PY, *map(str, args)], cwd=REPO, env=env, check=True)

def q2_json(q2_dir: Path, out: Path):
    with (q2_dir / "trip_summary.csv").open(encoding="utf-8-sig", newline="") as f:
        rows=list(csv.DictReader(f))
    # Per-box delivery times: the WHLi assembly audit only has a per-trip
    # `delivery` slot, so we fill it with the trip's LATEST actual box delivery
    # (conservative: every box deadline check then uses the worst time in the
    # trip).  Per-box precision remains in our own delivery_timeline audit.
    delivery={}
    dt=q2_dir/"delivery_timeline.csv"
    if dt.exists():
        with dt.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                delivery.setdefault(r["trip_id"],{})[r["box_id"]]=float(r["delivery_time_s"])
    trips=[]
    for i,r in enumerate(rows,1):
        route=r["route"].split("->")[1:-1]
        boxes=[x for x in r["box_ids"].split(";") if x]
        dmap=delivery.get(r["trip_id"],{})
        fallback=float(r["end_time_s"])
        deliv=max([dmap.get(b,fallback) for b in boxes] or [fallback])
        trips.append(dict(trip=i, site=route[0], boxes=boxes,
                          drone=r["vehicle_id"], battery=r["battery_id"], model=r["vehicle_type"],
                          start=float(r["start_time_s"]), return_time=float(r["end_time_s"]),
                          route=route, soc=float(r["soc_end"])*100.0,
                          energy=float(r["energy_kwh"]), delivery=deliv))
    obj=dict(source="ALNS-Q2 trip_summary.csv", trips=trips)
    out.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8")
    return obj

def add_refined_relay_seed(catalog: dict, incumbent_file: Path) -> str | None:
    """Offer a known hover coordinate to the *strict* screener, not its old coverage.

    The historical plan supplies geometry only.  The candidate's route coverage
    is recomputed against the current transport and DEM before the MILP can use it.
    The coarse flight-energy field is merely a screening prefilter; the MILP
    recomputes the actual flight energy and time from the geometry.
    """
    if not incumbent_file.exists():
        return None
    plan=json.loads(incumbent_file.read_text(encoding="utf-8"))
    seeds=[s for s in plan.get("relay_sortie_plan",[]) if "+de-opt" in s.get("candidate","")]
    if not seeds:
        return None
    source=next((c for c in catalog["candidates"] if c["id"]=="grid16-3/300"),None)
    if source is None:
        return None
    seed=seeds[0]
    cid="whli-v5-refined-RS01/300"
    injected=dict(source)
    injected["id"]=cid
    injected["point"]=list(seed["position"])
    injected["agl"]=float(seed["agl"])
    # Force evaluation on every current blind trip; these are NOT certified
    # coverage claims and are discarded by q3_multipoint_strict_candidates.py.
    blind_ids=list(catalog["blind_trips"])
    injected["full_coverage_trips"]=blind_ids
    injected["cover"]={t:{"min_access_margin_db":0.0} for t in blind_ids}
    old_energy=seed.get("optimization",{}).get("old_energy")
    if old_energy is not None:
        injected["flight_energy"]=max(0.0,float(source["flight_energy"])+float(seed["energy"])-float(old_energy))
    catalog["candidates"].append(injected)
    catalog["candidate_count"]=len(catalog["candidates"])
    return cid

def run(data_root: Path, q2_dir: Path, out: Path, dense=False, strict_step_s=20.0,
        transport_file:Path|None=None, deadline_perf:float|None=None,
        dense_step_s:float=1.0):
    t0=time.perf_counter(); out.mkdir(parents=True,exist_ok=True)
    if transport_file is None:
        q2_json(q2_dir,out/"q2_transport_whli.json")
    else:
        source=json.loads(transport_file.read_text(encoding="utf-8"))
        (out/"q2_transport_whli.json").write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding="utf-8")
    env=os.environ.copy()
    env["PYTHONPATH"]=os.pathsep.join([str(REPO/"shared"/"common_code"),str(WH/"code"/"vendor"),str(WH/"code"),env.get("PYTHONPATH","")])
    direct=out/"direct_strict2s.json"; coarse=out/"candidate_coarse.json"
    strict=out/"candidate_strict.json"; milp=out/"relay_milp.json"
    assembled=out/"assembled.json"
    call([WH/"code"/"q3_multipoint_direct_probe.py",data_root,out/"q2_transport_whli.json",direct,2,"strict",2],env)
    # The coarse WHLi grid catalog is geometry-only.  Reuse its positions, but
    # recompute strict coverage for this ALNS-Q2 transport schedule.
    reference=WH/"experiments"/"q3_multipoint_candidates_v2_grid.json"
    refined_seed_id=None
    if reference.exists():
        # Historical catalog trip IDs refer to a different Q2 plan.  Re-key
        # coverage by exact route signature; never match by numeric ID alone.
        base=json.loads(reference.read_text(encoding="utf-8"))
        new_direct=json.loads(direct.read_text(encoding="utf-8"))
        route_old={str(t):tuple(v["route"]) for t,v in base["blind_trips"].items()}
        route_new={str(t["trip"]):tuple(t["route"]) for t in new_direct["trips"] if t["blind_intervals"]}
        for c in base["candidates"]:
            old_cover=c.get("cover",{})
            c["cover"]={t:old_cover[o] for t,route in route_new.items()
                        for o,r in route_old.items() if r==route and o in old_cover}
            old_full=set(map(str,c.get("full_coverage_trips",[])))
            c["full_coverage_trips"]=[t for t,route in route_new.items()
                                      if any(r==route and o in old_full for o,r in route_old.items())]
        refined_seed_id=add_refined_relay_seed(base,WH/"experiments"/"q3_v5_assembled_opt.json")
        reference=out/"candidate_route_remapped.json"
        reference.write_text(json.dumps(base,ensure_ascii=False),encoding="utf-8")
    else:
        call([WH/"code"/"q3_multipoint_relay_candidates.py",data_root,out/"q2_transport_whli.json",direct,coarse,"grid"],env)
        reference=coarse
    warm_ids={"grid16-3/300","grid5-5/300","grid10-8/300","chendelong-RS01/300"}
    if refined_seed_id:
        warm_ids.discard("grid16-3/300")
        warm_ids.add(refined_seed_id)
    # Check the tiny geometry seed set first.  It is fully re-evaluated by the
    # strict DEM screener; if it cannot yield a two-relay schedule, only then
    # pay for screening the complete 400+ position catalog.
    seed_catalog=json.loads(reference.read_text(encoding="utf-8"))
    seed_catalog["candidates"]=[c for c in seed_catalog["candidates"] if c["id"] in warm_ids]
    seed_catalog["candidate_count"]=len(seed_catalog["candidates"])
    seed_input=out/"candidate_seed_input.json"
    seed_input.write_text(json.dumps(seed_catalog,ensure_ascii=False),encoding="utf-8")
    call([WH/"code"/"q3_multipoint_strict_candidates.py",data_root,out/"q2_transport_whli.json",seed_input,direct,strict,strict_step_s,-5],env)
    catalog=json.loads(strict.read_text(encoding="utf-8"))
    short=[c for c in catalog["candidates"] if c["id"] in warm_ids]
    blind=set(catalog["blind_trips"])
    warm_pool=out/"candidate_warm_shortlist.json"
    warm_result=None
    if blind and set().union(*(set(c["full_coverage_trips"]) for c in short))>=blind:
        warm_pool.write_text(json.dumps(dict(catalog,candidates=short,candidate_count=len(short)),ensure_ascii=False),encoding="utf-8")
        warm_milp=out/"relay_milp_warm.json"
        call([WH/"code"/"vendor"/"q3_relay_sortie_probe.py",data_root,direct,warm_pool,warm_milp,3,2],env)
        warm_result=json.loads(warm_milp.read_text(encoding="utf-8"))
    strict_scope="seed_shortlist" if warm_result and warm_result.get("selected") else "full_catalog"
    if warm_result and warm_result.get("selected"):
        milp.write_text(json.dumps(warm_result,ensure_ascii=False,indent=2),encoding="utf-8")
    else:
        call([WH/"code"/"q3_multipoint_strict_candidates.py",data_root,out/"q2_transport_whli.json",reference,direct,strict,strict_step_s,-5],env)
        call([WH/"code"/"vendor"/"q3_relay_sortie_probe.py",data_root,direct,strict,milp,15,2],env)
    m=json.loads(milp.read_text(encoding="utf-8"))
    label=("relay_milp_feasible" if m.get("selected") else
           {0:"relay_milp_no_solution",1:"relay_milp_time_limit_no_incumbent",2:"relay_milp_infeasible"}.get(m["solver_status"],"relay_milp_error"))
    result=dict(status=label,
                solver_status=m["solver_status"],relay_milp=m,wall_runtime_s=time.perf_counter()-t0)
    result["strict_candidate_step_s"]=strict_step_s
    result["strict_candidate_scope"]=strict_scope
    result["refined_seed_id"]=refined_seed_id
    result["refined_seed_source"]="WHLi historical hover geometry; coverage independently rechecked" if refined_seed_id else None
    result["warm_shortlist_tested"]=warm_result is not None
    result["warm_shortlist_selected"]=bool(warm_result and warm_result.get("selected"))
    if m.get("selected"):
        call([WH/"code"/"vendor"/"q3_assemble_and_check.py",data_root,out/"q2_transport_whli.json",direct,milp,assembled],env)
        result["assembled"]=json.loads(assembled.read_text(encoding="utf-8"))
        if dense and (deadline_perf is None or deadline_perf-time.perf_counter()>75.0):
            dense_tag=str(dense_step_s).replace(".", "p")
            dense_file=out/f"dense{dense_tag}s_strict.json"
            call([WH/"code"/"q3_multipoint_dense_check.py",data_root,out/"q2_transport_whli.json",assembled,dense_file,dense_step_s,"strict"],env)
            result["dense_check"]=json.loads(dense_file.read_text(encoding="utf-8"))
            result["dense_check_step_s"]=dense_step_s
            if result["dense_check"]["outage_samples"] == 0:
                closure=out/"continuity_closure.json"
                closure_env=dict(env)
                closure_env["Q3CLO_DATA"]=str(data_root.resolve())
                closure_env["Q3CLO_Q2"]=str((out/"q2_transport_whli.json").resolve())
                call([WH/"code"/"q3_continuity_closure.py",assembled,closure],closure_env)
                result["continuity_closure"]=json.loads(closure.read_text(encoding="utf-8"))
        if result.get("continuity_closure",{}).get("continuous_feasible"):
            result["status"]="fully_audited_feasible"
        elif result.get("continuity_closure"):
            result["status"]="refined_outage"
        elif result.get("dense_check") and result["dense_check"].get("outage_samples",0)>0:
            result["status"]="dense_outage"
        else:
            result["status"]="pending_continuity_audit"
    result["wall_runtime_s"]=time.perf_counter()-t0
    (out/"gate_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--data-root",type=Path,required=True);p.add_argument("--q2-dir",type=Path,required=True);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--dense",action="store_true");p.add_argument("--transport-file",type=Path);p.add_argument("--dense-step",type=float,default=1.0,help="dense continuity sampling step in seconds (default 1.0; 0.5 = original WHLi gate)")
    a=p.parse_args();print(json.dumps(run(a.data_root,a.q2_dir,a.output_dir,a.dense,transport_file=a.transport_file,dense_step_s=a.dense_step),ensure_ascii=False,indent=2))
