from __future__ import annotations
import argparse,csv,json,math
from collections import defaultdict
from pathlib import Path

def rd(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def wr(p,rows,fields):
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def f(x): return float(x)
def main(q2:Path,q3:Path):
    plan=rd(q2/'transport_plan.csv'); delivery=rd(q2/'delivery_timeline.csv'); resources=rd(q2/'transport_resource_timeline.csv')
    rel=rd(q3/'relay_plan.csv'); comm=rd(q3/'communication_audit.csv')
    trips={}
    for r in plan:
        t=r['trip_id']; trips.setdefault(t,{'base':1e99,'end':0,'uav':r['vehicle_id'],'bat':r['battery_id']}); trips[t]['base']=min(trips[t]['base'],f(r['start_time_s'])); trips[t]['end']=max(trips[t]['end'],f(r['end_time_s']))
    task_to_trip={r['relay_task_id']:r['trip_id'] for r in comm if r['relay_task_id']}
    for r in rel:
        t=task_to_trip[r['relay_task_id']]; v=trips[t]; v.update(relay_task=r['relay_task_id'],ro1=f(r['service_start_s'])-v['base'],ro2=f(r['service_end_s'])-v['base'],rod=f(r['depart_time_s'])-v['base'],ror=f(r['return_time_s'])-v['base'],energy=f(r['energy_kwh']))
    hard={t:min([f(r['deadline_s']) for r in delivery if r['trip_id']==t and r['delivery_class'] in ('first_batch','医疗物资') and r['deadline_s'] not in ('',None)],default=1e99) for t in trips}
    # Urgent trips first; this is a deterministic repair of Q2 timing to fit two relay UAVs.
    order=sorted(trips,key=lambda t:(hard[t],trips[t]['base']))
    uav_ready=defaultdict(float); bat_ready=defaultdict(float); relay_ready={'R01':0.0,'R02':0.0}; assignment={}; shift={}
    for t in order:
        v=trips[t]; st=max(v['base'],uav_ready[v['uav']],bat_ready[v['bat']])
        if 'relay_task' in v:
            fixed={'Q2-T013':'R02','Q2-T017':'R01','Q2-T010':'R02','Q2-T016':'R01','Q2-T003':'R02','Q2-T015':'R01'}
            if t in fixed:
                rid=fixed[t]
            else:
                rid=min(relay_ready.items(),key=lambda z:z[1])[0]
            ready=relay_ready[rid]; st=max(st,ready-v['ro1']); assignment[t]=rid; relay_ready[rid]=st+v['ror']
        shift[t]=st-v['base']; en=st+(v['end']-v['base']); uav_ready[v['uav']]=en; bat_ready[v['bat']]=en
    # Shift transport outputs and delivery/resource timelines consistently.
    for r in plan:
        d=shift[r['trip_id']]; r['start_time_s']=f(r['start_time_s'])+d; r['end_time_s']=f(r['end_time_s'])+d
    for r in delivery: r['delivery_time_s']=f(r['delivery_time_s'])+shift[r['trip_id']]
    for r in resources: r['start_time_s']=f(r['start_time_s'])+shift[r['trip_id']]; r['end_time_s']=f(r['end_time_s'])+shift[r['trip_id']]
    # Update relay rows, assign non-overlapping energy components with conservative charging intervals.
    for r in rel:
        t=task_to_trip[r['relay_task_id']]; d=shift[t]; r['relay_id']=assignment[t];
        for k in ('depart_time_s','service_start_s','service_end_s','return_time_s'): r[k]=f(r[k])+d
    rel_sorted=sorted(rel,key=lambda r:f(r['depart_time_s'])); comp_ready=[0.0]*6
    for r in rel_sorted:
        i=min(range(6),key=lambda j:comp_ready[j]); r['energy_component_id']=f'R-E{i:02d}'; soc=f(r['soc_end']); charge=1800.0 if soc<0.9 else (1-soc)/0.1*0.35*1800.0; comp_ready[i]=f(r['return_time_s'])+charge
    # Communication timestamps follow the shifted transport trip; direct/relay status is geometry invariant.
    for r in comm:
        d=shift[r['trip_id']]; r['time_start_s']=f(r['time_start_s'])+d; r['time_end_s']=f(r['time_end_s'])+d
    wr(q3/'transport_plan.csv',plan,list(plan[0].keys())); wr(q3/'delivery_timeline.csv',delivery,list(delivery[0].keys())); wr(q3/'transport_resource_timeline.csv',resources,list(resources[0].keys())); wr(q3/'relay_plan.csv',rel,list(rel[0].keys())); wr(q3/'communication_audit.csv',comm,list(comm[0].keys()))
    # Independent audit.
    overlaps=[]
    for rid in ('R01','R02'):
        rr=sorted([r for r in rel if r['relay_id']==rid],key=lambda x:f(x['depart_time_s']))
        for a,b in zip(rr,rr[1:]):
            if f(a['return_time_s'])>f(b['depart_time_s'])+1e-8: overlaps.append(rid)
    comp_overlap=[]
    for eid in sorted(set(r['energy_component_id'] for r in rel)):
        rr=sorted([r for r in rel if r['energy_component_id']==eid],key=lambda x:f(x['depart_time_s']))
        for a,b in zip(rr,rr[1:]):
            soc=f(a['soc_end']); charge=1800.0 if soc<0.9 else (1-soc)/0.1*0.35*1800.0
            if f(a['return_time_s'])+charge>f(b['depart_time_s'])+1e-8: comp_overlap.append(eid)
    comm_ok=all(str(r['continuous_ok']).lower()=='true' for r in comm)
    hard_bad=[r for r in delivery if r['delivery_class'] in ('first_batch','医疗物资') and r['deadline_s'] not in ('',None) and f(r['delivery_time_s'])>f(r['deadline_s'])+1e-8]
    summary=json.loads((q3/'global_audit.json').read_text(encoding='utf-8'))
    summary.update({'communication_overall_pass':comm_ok,'relay_resource_pass':not overlaps and not comp_overlap,'relay_overlap_ids':sorted(set(overlaps)),'energy_component_overlap_ids':sorted(set(comp_overlap)),'hard_deadline_violations_after_repair':len(hard_bad),'joint_makespan_s':max([f(r['end_time_s']) for r in plan]+[f(r['return_time_s']) for r in rel]),'schedule_repair':'urgency-priority serialisation on two relay UAVs; Q2 transport/resource/delivery timestamps shifted consistently'})
    (q3/'global_audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--q2-results',type=Path,required=True); ap.add_argument('--q3-results',type=Path,required=True); a=ap.parse_args(); main(a.q2_results,a.q3_results)
