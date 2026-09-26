"""Relay-aware timing neighbourhood for ALNS-Q3.

Uses WHLi's strict-DEM blind-window surrogate as an inner evaluation, so a
failed two-airframe gate feeds back into transport dispatch order and start
floors before the expensive final MILP.  The surrogate is only a ranking aid;
the WHLi strict MILP/dense/closure chain remains the feasibility authority.
"""
from __future__ import annotations
import argparse, json, random, sys, time
from pathlib import Path

REPO=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(REPO/"members"/"WHLi"/"code"))
import q2_memetic3 as m3

def run(data_root:Path, transport_file:Path, out:Path, budget_s:float=8.0, seed:int=20260924):
    t0=time.perf_counter(); rng=random.Random(seed); out.parent.mkdir(parents=True,exist_ok=True)
    original=json.loads(transport_file.read_text(encoding="utf-8"))
    gen=[dict(trip=int(t["trip"]),route=list(t["route"]),boxes=list(t["boxes"]),
              model=t["model"],drone=t["drone"],battery=t["battery"],
              orig_start=float(t["start"]),orig_ret=float(t["return_time"])) for t in original["trips"]]
    m3.SUR=m3.RelaySurrogate(str(data_root),step=20.0,budget="strict")
    original_floors={t["trip"]:t["orig_start"] for t in gen}
    obj,detail=m3.evaluate_gen(gen,original_floors)
    initial=obj; best=(obj,gen,detail); n=0
    # Retiming first: this targets the timing-span pressure highlighted by
    # WHLi.  Preserve zero hard violations and zero weighted tardiness.
    key=lambda v:(v[0],v[2],v[1],v[3],v[4])
    floors=dict(original_floors)
    while time.perf_counter()-t0<min(3.0,budget_s/3) and best[0][1]>0:
        trial_best=None
        for tid in floors:
            for delta in (150.0,300.0,600.0,1200.0):
                fl=dict(floors);fl[tid]+=delta
                ro,rd=m3.evaluate_gen(gen,fl)
                if ro[0]==0 and ro[2]<=initial[2]+1e-8 and (trial_best is None or key(ro)<key(trial_best[0])):
                    trial_best=(ro,fl,rd)
            if time.perf_counter()-t0>=min(3.0,budget_s/3):break
        if trial_best is None or key(trial_best[0])>=key(best[0]):break
        ro,floors,rd=trial_best;best=(ro,gen,rd)
    # E007 order/relocation neighbourhood, scored with relay pressure before
    # makespan.  Only one changed dispatch order per move; accepted feasible
    # moves become the next ALNS state.
    current=[dict(t) for t in gen]; cur_obj=obj
    while time.perf_counter()-t0<budget_s and len(current)>1:
        n+=1; trial=[dict(t) for t in current]
        i,j=rng.sample(range(len(trial)),2)
        ranks=sorted(range(len(trial)),key=lambda k:trial[k]["orig_start"])
        if rng.random()<.5: ranks[i],ranks[j]=ranks[j],ranks[i]
        else: ranks.insert(j,ranks.pop(i))
        for rank,k in enumerate(ranks):trial[k]["orig_start"]=rank*1e-3
        o,d=m3.evaluate_gen(trial)
        if key(o)<key(cur_obj):current,cur_obj=trial,o
        if o[0]==0 and key(o)<key(best[0]):best=(o,trial,d)
    bo,bg,bd=best
    converted=m3.build_sol_from_gen(bg,bo,bd)
    if converted is not None:
        out.write_text(json.dumps(converted,ensure_ascii=False,indent=2),encoding="utf-8")
    record=dict(initial_objective=initial,final_objective=bo,
                objective_order=["hard_violations","weighted_tardiness","relay_surrogate_penalty","makespan","energy"],
                iterations=n,wall_runtime_s=time.perf_counter()-t0,
                candidate_written=converted is not None,
                surrogate_is_not_feasibility_certificate=True)
    (out.parent/"relay_feedback_audit.json").write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
    return record

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--data-root",type=Path,required=True);p.add_argument("--transport",type=Path,required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--budget",type=float,default=8);p.add_argument("--seed",type=int,default=20260924)
    a=p.parse_args();print(json.dumps(run(a.data_root,a.transport,a.output,a.budget,a.seed),ensure_ascii=False,indent=2))
