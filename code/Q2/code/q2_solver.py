from __future__ import annotations

import argparse, csv, hashlib, importlib.util, json, math, random, sys, time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openpyxl

ROOT = Path(__file__).resolve().parents[4]
Q1 = ROOT / "# D-项目文件夹-LW" / "Solution" / "Q1" / "code" / "q1_solver.py"
spec = importlib.util.spec_from_file_location("q1_solver", Q1)
q1 = importlib.util.module_from_spec(spec); assert spec.loader
sys.modules["q1_solver"] = q1; spec.loader.exec_module(q1)

RESERVE = 0.20
EPS = 1e-8

@dataclass
class Trip:
    trip_id: str
    vehicle_type: str
    route: list[str]
    box_ids_by_node: dict[str, list[str]]
    box_ids: list[str]
    mass_kg: float
    volume_m3: float
    energy_kwh: float = 0.0
    duration_s: float = 0.0
    feasible: bool = True
    reason: str = ""

def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None: fields = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

def load_entities(data_root: Path):
    node_file = next(data_root.rglob("调度中心与服务区.xlsx"))
    demand_file = next(data_root.rglob("物资需求与配送时限.xlsx"))
    transport_file = next(data_root.rglob("运输无人机数据.xlsx"))
    nodes, vehicles, boxes_by_service, dem, files = q1.load_inputs(data_root)
    ws = openpyxl.load_workbook(transport_file, data_only=True)["数据"]
    uavs, batteries = [], {}
    for r in ws.iter_rows(values_only=True):
        if r and r[0] in {"U01","U02","U03","U04","U05","U06","U07","U08"}:
            uavs.append((str(r[0]), str(r[1])))
        if r and r[0] in {"A","B","C"} and isinstance(r[1], (int,float)):
            batteries[str(r[0])] = {"count": int(r[1]), "full_charge_s": float(r[2])}
    boxes = {b.box_id: b for bs in boxes_by_service.values() for b in bs}
    return nodes, vehicles, boxes, boxes_by_service, dem, uavs, batteries, files

def build_legs(nodes, dem):
    legs = {}
    for a in nodes.values():
        for b in nodes.values():
            if a.node_id == b.node_id: continue
            aa = a.ground_elevation_m + (0 if a.node_type == "depot" else 30)
            bb = b.ground_elevation_m + (0 if b.node_type == "depot" else 30)
            legs[(a.node_id,b.node_id)] = q1.build_leg(a,b,aa,bb,dem)
    return legs

def route_metrics(route, boxes_by_node, vehicle, legs):
    """Dynamic payload/energy calculation for O01 -> services -> O01."""
    if not route or route[0] != "O01": return None
    services = route[1:]
    total_ids = [x for s in services for x in boxes_by_node.get(s, [])]
    mass = sum(boxes_by_node[s][i].mass_kg for s in [] ) if False else 0.0
    # boxes_by_node stores IDs; caller supplies global box map through attributes below
    raise RuntimeError("route_metrics requires route_metrics_with_boxes")

def calc_route(route, boxes_by_node, box_map, vehicle, legs):
    total_ids = [bid for s in route[1:-1] for bid in boxes_by_node.get(s, [])]
    mass = sum(box_map[x].mass_kg for x in total_ids); volume = sum(box_map[x].volume_m3 for x in total_ids)
    if mass > vehicle.max_payload_kg + EPS or volume > vehicle.max_volume_m3 + EPS: return None
    payload = mass; energy = 0.0; duration = vehicle.fixed_prep_s + len(total_ids)*vehicle.per_box_load_s
    deliveries = {}
    current = route[0]
    for idx, s in enumerate(route[1:-1]):
        leg = legs[(current,s)]
        e,_,_ = q1.leg_energy(vehicle, leg, payload)
        energy += e; duration += q1.leg_flight_time_s(vehicle, leg)
        ids = boxes_by_node.get(s, [])
        duration += vehicle.base_handoff_s + len(ids)*vehicle.per_box_handoff_s
        deliveries[s] = duration
        payload -= sum(box_map[x].mass_kg for x in ids)
        current = s
    if current != route[-1]:
        leg = legs[(current,route[-1])]
        e,_,_ = q1.leg_energy(vehicle, leg, payload)
        energy += e; duration += q1.leg_flight_time_s(vehicle, leg)
    allowed=(1-RESERVE)*vehicle.usable_energy_kwh
    if energy > allowed + EPS: return None
    return {"mass":mass,"volume":volume,"energy":energy,"duration":duration,"deliveries_offset":deliveries,
            "soc_end":1-energy/vehicle.usable_energy_kwh,"allowed":allowed}

def choose_vehicle(route, ids_by_node, box_map, vehicles, legs):
    candidates=[]
    for vt,v in vehicles.items():
        m=calc_route(route,ids_by_node,box_map,v,legs)
        if m: candidates.append((m["energy"],m["duration"],vt,m))
    if not candidates: return None
    return min(candidates, key=lambda x:(x[0],x[1],x[2]))

def split_service(service, ids, box_map, nodes, vehicles, legs):
    """Urgent boxes are packed first, then ordinary boxes. Each trip is initially single-point."""
    ordered=sorted(ids,key=lambda x:(box_map[x].deadline_s if box_map[x].deadline_s is not None else 1e12,
                                     0 if box_map[x].is_first_batch else 1,
                                     -box_map[x].priority, x))
    trips=[]; current=[]
    for bid in ordered:
        trial=current+[bid]
        vt=choose_vehicle(["O01",service,"O01"],{service:trial},box_map,vehicles,legs)
        if vt is None and current:
            vt=choose_vehicle(["O01",service,"O01"],{service:current},box_map,vehicles,legs)
            if vt is None: raise ValueError(f"No feasible single-point batch for {service}")
            trips.append((service,current,vt[2],vt[3])); current=[bid]
        else: current=trial
    if current:
        vt=choose_vehicle(["O01",service,"O01"],{service:current},box_map,vehicles,legs)
        if vt is None: raise ValueError(f"No feasible final batch for {service}")
        trips.append((service,current,vt[2],vt[3]))
    return trips

def try_merge(trips, box_map, vehicles, legs):
    """Conservative route merge for ordinary batches only; retains urgent single-point trips."""
    changed=True
    while changed:
        changed=False
        for i in range(len(trips)):
            si, ids_i, _, _ = trips[i]
            if any(box_map[x].is_first_batch for x in ids_i): continue
            for j in range(i+1,len(trips)):
                sj, ids_j, _, _ = trips[j]
                if si==sj or any(box_map[x].is_first_batch for x in ids_j): continue
                best=None
                for order in [(si,sj),(sj,si)]:
                    ids={order[0]: ids_i if order[0]==si else ids_j, order[1]: ids_j if order[1]==sj else ids_i}
                    c=choose_vehicle(["O01",order[0],order[1],"O01"],ids,box_map,vehicles,legs)
                    if c and (best is None or (c[0],c[1])<(best[0],best[1])): best=(c[0],c[1],order,ids,c[2],c[3])
                if best:
                    _,_,order,ids,vt,m=best; trips[i]=(order[0]+"+"+order[1],ids[order[0]]+ids[order[1]],vt,m); trips.pop(j); changed=True; break
            if changed: break
    return trips

def charge_time(soc, full_s):
    soc=max(0.0,min(1.0,soc))
    if soc>=1-EPS:return 0.0
    if soc<0.9:
        fast=(0.9-soc)/0.9*0.65*full_s; slow=0.35*full_s
        return fast+slow
    return (1-soc)/0.1*0.35*full_s

def trip_order_key(t, box_map):
    hard=[(box_map[x].deadline_s if box_map[x].is_first_batch else box_map[x].expected_time_s)
          for x in t.box_ids if box_map[x].is_first_batch or box_map[x].material_type=="医疗物资"]
    expected=[box_map[x].expected_time_s for x in t.box_ids if box_map[x].expected_time_s is not None]
    return (min(hard) if hard else 1e12, min(expected) if expected else 1e12,
            -sum(box_map[x].priority for x in t.box_ids), t.trip_id)

def schedule(trips, vehicles, uavs, batteries, box_map, legs, ordered_trips=None):
    by_type_u=defaultdict(list); by_type_b=defaultdict(list)
    for uid,vt in uavs: by_type_u[vt].append(uid)
    for vt,b in batteries.items(): by_type_b[vt]=[{"id":f"{vt}-B{i:02d}","ready":0.0,"soc":1.0,"last_end":0.0} for i in range(b["count"])]
    uav_ready={uid:0.0 for uid,_ in uavs}
    rows=[]; deliveries=[]; resource=[]; assigned=[]
    trips_sorted=list(ordered_trips) if ordered_trips is not None else sorted(trips,key=lambda t:trip_order_key(t,box_map))
    for t in trips_sorted:
        v=vehicles[t.vehicle_type]; best=None
        for uid in by_type_u[t.vehicle_type]:
            for bat in by_type_b[t.vehicle_type]:
                start=max(uav_ready[uid],bat["ready"])
                route=t.route; calc=calc_route(route,t.box_ids_by_node,box_map,v,legs)
                if calc is None: continue
                end=start+calc["duration"]
                # lexicographic earliest completion, then earliest urgent delivery
                if best is None or (end,start,uid,bat["id"])<(best[0],best[1],best[2],best[3]["id"]): best=(end,start,uid,bat,calc)
        if best is None: raise RuntimeError(f"No resource assignment for {t.trip_id}")
        end,start,uid,bat,calc=best
        # reconstruct times per service/node
        elapsed=v.fixed_prep_s+len(t.box_ids)*v.per_box_load_s; payload=calc["mass"]
        seq=list(t.route[1:-1]); rowseq=[]; current="O01"
        for k,s in enumerate(seq):
            nxt=s; leg=legs[(current,nxt)]
            e,_,_=q1.leg_energy(v,leg,payload); fs=q1.leg_flight_time_s(v,leg)
            depart=start+elapsed; arrive=depart+fs
            ids=t.box_ids_by_node[s]; delivery=arrive+v.base_handoff_s+len(ids)*v.per_box_handoff_s
            for bid in ids:
                b=box_map[bid]; deliveries.append({"box_id":bid,"trip_id":t.trip_id,"service_node":b.service_node,
                    "delivery_time_s":delivery,"delivery_class":"first_batch" if b.is_first_batch else b.material_type,
                    "deadline_s":b.deadline_s if b.deadline_s is not None else b.expected_time_s,
                    "deadline_met": bool(delivery <= ((b.deadline_s if b.deadline_s is not None else b.expected_time_s) or 1e99)+EPS)})
            rowseq.append((current,nxt,depart,depart+fs,payload,e))
            elapsed += fs+v.base_handoff_s+len(ids)*v.per_box_handoff_s; payload-=sum(box_map[x].mass_kg for x in ids); current=s
        leg=legs[(current,"O01")]; e,_,_=q1.leg_energy(v,leg,payload); fs=q1.leg_flight_time_s(v,leg)
        depart=start+elapsed; rowseq.append((current,"O01",depart,depart+fs,payload,e))
        soc_start=bat["soc"]; soc_end=calc["soc_end"]; bat["ready"]=end+charge_time(soc_end,batteries[t.vehicle_type]["full_charge_s"]); bat["soc"]=1.0; bat["last_end"]=end; uav_ready[uid]=end
        assigned.append((t,uid,bat["id"],start,end,calc,rowseq,soc_start,soc_end))
        for n,(a,b,st,en,pay,e) in enumerate(rowseq,1):
            frac=e/v.usable_energy_kwh; ss=soc_start if n==1 else None
            rows.append({"trip_id":t.trip_id,"sequence_no":n,"vehicle_type":t.vehicle_type,"vehicle_id":uid,"battery_id":bat["id"],
                "from_node":a,"to_node":b,"start_time_s":st,"end_time_s":en,"payload_depart_kg":pay,
                "energy_kwh":e,"soc_start":None,"soc_end":None})
        resource += [{"resource_type":"transport_uav","resource_id":uid,"trip_id":t.trip_id,"activity":"flight_and_service","start_time_s":start,"end_time_s":end,"soc_before":"","soc_after":""},
                     {"resource_type":"battery","resource_id":bat["id"],"trip_id":t.trip_id,"activity":"flight","start_time_s":start,"end_time_s":end,"soc_before":soc_start,"soc_after":soc_end},
                     {"resource_type":"battery","resource_id":bat["id"],"trip_id":t.trip_id,"activity":"charging","start_time_s":end,"end_time_s":bat["ready"],"soc_before":soc_end,"soc_after":1.0}]
        # set segment SOCs
        cur=soc_start
        for rr in rows[-len(rowseq):]:
            rr["soc_start"]=cur; cur-=float(rr["energy_kwh"])/v.usable_energy_kwh; rr["soc_end"]=cur
    return rows,deliveries,resource,assigned

def audit(rows, deliveries, trips, vehicles, box_map):
    counts=defaultdict(int)
    for d in deliveries: counts[d["box_id"]]+=1
    target_bad=[d for d in deliveries if not d["deadline_met"]]
    hard_bad=[]; weighted_tardiness=0.0; soft_tardiness=0.0
    for d in deliveries:
        b=box_map[d["box_id"]]; target=(b.deadline_s if b.deadline_s is not None else b.expected_time_s)
        tard=max(0.0,float(d["delivery_time_s"])-(target or float(d["delivery_time_s"])))
        weighted_tardiness += b.priority*tard
        if not b.is_first_batch and b.material_type != "医疗物资":
            soft_tardiness += b.priority*tard
        if (b.is_first_batch or b.material_type=="医疗物资") and tard>EPS: hard_bad.append(d)
    route_ok=all(t.route[0]=="O01" and t.route[-1]=="O01" for t in trips)
    dup=[k for k,v in counts.items() if v!=1]
    return {"box_count":len(box_map),"delivered_unique":len(counts)==len(box_map) and not dup,"duplicate_or_missing":dup,
            "route_closed":route_ok,"hard_deadline_violations":len(hard_bad),"hard_deadline_violation_boxes":[d["box_id"] for d in hard_bad],
            "expected_time_misses":len(target_bad),"weighted_tardiness_priority_seconds":weighted_tardiness,
            "soft_tardiness_priority_seconds":soft_tardiness,
            "trip_count":len(trips),"total_energy_kwh":sum(float(r["energy_kwh"]) for r in rows),
            "makespan_s":max(float(r["end_time_s"]) for r in rows) if rows else 0.0,"overall_pass":len(counts)==len(box_map) and not dup and route_ok and not hard_bad}

def clone_trip(t):
    return Trip(t.trip_id,t.vehicle_type,list(t.route),{k:list(v) for k,v in t.box_ids_by_node.items()},
                list(t.box_ids),t.mass_kg,t.volume_m3,t.energy_kwh,t.duration_s,t.feasible,t.reason)

def vehicle_options(service_order, ids_by_node, box_map, vehicles, legs):
    route=["O01"]+list(service_order)+["O01"]
    ans=[]
    for vt,v in vehicles.items():
        m=calc_route(route,ids_by_node,box_map,v,legs)
        if m is not None: ans.append((vt,m))
    return ans

def random_pack_service(service, ids, box_map, vehicles, legs, rng, mode="mixed"):
    ids=list(ids)
    def key(bid):
        b=box_map[bid]; hard=0 if (b.is_first_batch or b.material_type=="医疗物资") else 1
        target=b.deadline_s if b.is_first_batch else b.expected_time_s
        noise=rng.random()*500.0 if mode=="mixed" else 0.0
        return (hard,target if target is not None else 1e12,-b.priority,noise)
    ids.sort(key=key)
    if mode=="random": rng.shuffle(ids)
    raw=[]; current=[]
    for bid in ids:
        trial=current+[bid]
        opts=vehicle_options([service],{service:trial},box_map,vehicles,legs)
        if not opts and current:
            final_opts=vehicle_options([service],{service:current},box_map,vehicles,legs)
            if not final_opts: raise ValueError(f"No feasible batch for {service}")
            opts2=sorted(final_opts,key=lambda z:(z[1]["duration"],z[1]["energy"],z[0]))
            pick=opts2[0]
            if mode=="capacity" and len(opts2)>1: pick=max(opts2,key=lambda z:sum(box_map[x].mass_kg for x in current)/vehicles[z[0]].max_payload_kg)
            raw.append((service,current,pick[0],pick[1])); current=[bid]
        else:
            current=trial
    if current:
        final_opts=vehicle_options([service],{service:current},box_map,vehicles,legs)
        if not final_opts: raise ValueError(f"No feasible final batch for {service}")
        opts2=sorted(final_opts,key=lambda z:(z[1]["duration"],z[1]["energy"],z[0]))
        if mode=="energy": pick=min(opts2,key=lambda z:(z[1]["energy"],z[1]["duration"],z[0]))
        elif mode=="capacity": pick=max(opts2,key=lambda z:(sum(box_map[x].mass_kg for x in current)/vehicles[z[0]].max_payload_kg,z[1]["duration"]))
        elif len(opts2)>1 and rng.random()<0.35: pick=rng.choice(opts2[:min(2,len(opts2))])
        else: pick=opts2[0]
        raw.append((service,current,pick[0],pick[1]))
    return raw

def make_trips_no_merge(raw, box_map):
    trips=[]
    for i,(label,ids,vt,m) in enumerate(raw,1):
        services=label.split("+"); ids_by={s:[] for s in services}
        for bid in ids: ids_by[box_map[bid].service_node].append(bid)
        trips.append(Trip(f"Q2-T{i:03d}",vt,["O01"]+services+["O01"],ids_by,list(ids),m["mass"],m["volume"],m["energy"],m["duration"],True))
    return trips

def raw_from_trips(trips, box_map, vehicles, legs):
    raw=[]
    for t in trips:
        label="+".join(t.route[1:-1]); m=calc_route(t.route,t.box_ids_by_node,box_map,vehicles[t.vehicle_type],legs)
        if m is not None: raw.append((label,list(t.box_ids),t.vehicle_type,m))
    return raw

def rebuild_services(trips, selected, box_map, boxes_by_service, vehicles, legs, rng, mode):
    selected=set(selected); raw=[]
    for t in trips:
        if any(box_map[x].service_node in selected for x in t.box_ids): continue
        label="+".join(t.route[1:-1]); m=calc_route(t.route,t.box_ids_by_node,box_map,vehicles[t.vehicle_type],legs)
        if m is not None: raw.append((label,list(t.box_ids),t.vehicle_type,m))
    for s in selected:
        raw.extend(random_pack_service(s,[b.box_id for b in boxes_by_service[s]],box_map,vehicles,legs,rng,mode))
    return make_trips_no_merge(raw,box_map)

def schedule_eval(trips, order, vehicles, uavs, batteries, box_map, legs):
    try:
        rows,ds,rs,ass=schedule(trips,vehicles,uavs,batteries,box_map,legs,order)
        au=audit(rows,ds,trips,vehicles,box_map)
        score=(au["hard_deadline_violations"]+ (0 if au["delivered_unique"] else 1000),
               au["soft_tardiness_priority_seconds"],au["makespan_s"],au["trip_count"],au["total_energy_kwh"])
        return score,rows,ds,rs,ass,au
    except (RuntimeError,KeyError,ValueError) as exc:
        au={"hard_deadline_violations":999999,"soft_tardiness_priority_seconds":1e15,"makespan_s":1e15,
            "trip_count":len(trips),"total_energy_kwh":1e15,"delivered_unique":False,"solver_error":str(exc)}
        return (999999,1e15,1e15,len(trips),1e15),[],[],[],[],au

def alns_optimize(initial_trips, vehicles, uavs, batteries, box_map, boxes_by_service, legs,
                  iterations=1000000, time_limit_s=540, seed=20260923):
    rng=random.Random(seed); start_clock=time.perf_counter(); deadline=start_clock+time_limit_s
    current_trips=[clone_trip(t) for t in initial_trips]; current_order=sorted(current_trips,key=lambda t:trip_order_key(t,box_map))
    cur=schedule_eval(current_trips,current_order,vehicles,uavs,batteries,box_map,legs)
    # Warm-start the schedule layer before changing the batching structure.
    for _ in range(6000):
        if time.perf_counter()>=min(deadline,start_clock+45.0): break
        cand_order=current_order[:]
        i,j=rng.sample(range(len(cand_order)),2)
        if rng.random()<0.55: cand_order[i],cand_order[j]=cand_order[j],cand_order[i]
        else: item=cand_order.pop(i); cand_order.insert(j,item)
        cand=schedule_eval(current_trips,cand_order,vehicles,uavs,batteries,box_map,legs)
        if cand[0]<cur[0]: current_order,cur=cand_order,cand
    best=(cur[0],current_trips,current_order,cur[1],cur[2],cur[3],cur[4],cur[5])
    operators=["random_service_repack","late_service_repack","capacity_repack","order_move","merge_split"]
    weights={x:1.0 for x in operators}; rewards={x:0.0 for x in operators}; uses={x:0 for x in operators}
    trace=[]; accepted=0; temperature=0.12
    for it in range(iterations):
        if time.perf_counter()>=deadline: break
        u=rng.random()*sum(weights.values()); op=operators[-1]
        for name in operators:
            u-=weights[name]
            if u<=0: op=name; break
        uses[op]+=1
        cand_trips=[clone_trip(t) for t in current_trips]; cand_order=sorted(cand_trips,key=lambda t:trip_order_key(t,box_map))
        if op=="order_move" and len(cand_order)>1:
            i,j=rng.sample(range(len(cand_order)),2); item=cand_order.pop(i); cand_order.insert(j,item)
        else:
            if op=="late_service_repack":
                service_scores=defaultdict(float)
                for d in cur[2]:
                    b=box_map[d["box_id"]]; target=b.deadline_s if b.is_first_batch else b.expected_time_s
                    if target is not None: service_scores[b.service_node]+=b.priority*max(0,float(d["delivery_time_s"])-target)
                ranked=sorted(boxes_by_service,key=lambda s:service_scores[s],reverse=True)
                k=1 if rng.random()<0.75 else 2; selected=ranked[:k]
            else:
                k=1 if rng.random()<0.72 else 2; selected=rng.sample(list(boxes_by_service),k)
            mode={"capacity_repack":"capacity","merge_split":"random","random_service_repack":"mixed","late_service_repack":"mixed"}.get(op,"mixed")
            try: cand_trips=rebuild_services(cand_trips,selected,box_map,boxes_by_service,vehicles,legs,rng,mode)
            except ValueError: continue
            rank={bid:i for i,t in enumerate(current_order) for bid in t.box_ids}
            cand_order=sorted(cand_trips,key=lambda t:(sum(rank.get(x,len(current_order)) for x in t.box_ids)/max(1,len(t.box_ids)),trip_order_key(t,box_map)))
            if len(cand_order)>1 and rng.random()<0.35:
                i,j=rng.sample(range(len(cand_order)),2); item=cand_order.pop(i); cand_order.insert(j,item)
        cand=schedule_eval(cand_trips,cand_order,vehicles,uavs,batteries,box_map,legs)
        cscore=cand[0]; cur_score=cur[0]
        improves=cscore<cur_score
        same_hard=(cscore[0]==cur_score[0])
        delta=(cscore[1]-cur_score[1])/max(1.0,abs(cur_score[1])) if same_hard else 1e9
        accept=improves or (same_hard and delta<=0.08 and rng.random()<math.exp(-max(0.0,delta)/max(temperature,1e-6)))
        if cscore[0]>cur_score[0]: accept=False
        if accept:
            current_trips,current_order,cand_state=cand_trips,cand_order,cand; cur=cand_state; accepted+=1
            rewards[op]+=4.0 if improves else 1.0
            if cscore<best[0]:
                best=(cscore,cand_trips,cand_order,cand[1],cand[2],cand[3],cand[4],cand[5]); rewards[op]+=8.0
        temperature*=0.99985
        if it%100==0:
            trace.append({"iteration":it,"operator":op,"accepted":accept,"best_hard":best[0][0],"best_soft_tardiness":best[0][1],"best_makespan_s":best[0][2],"best_trip_count":best[0][3],"best_energy_kwh":best[0][4],"current_trip_count":len(current_trips)})
        if it%100==99:
            for name in operators:
                weights[name]=0.2+rewards[name]/max(1.0,uses[name]); rewards[name]=0.0; uses[name]=0
    return best[3],best[4],best[5],best[6],best[7],best[2],trace,weights,it+1,time.perf_counter()-start_clock

def make_trips(raw, box_map, vehicles, legs):
    merged=try_merge(raw,box_map,vehicles,legs); trips=[]
    for i,(label,ids,vt,m) in enumerate(merged,1):
        services=label.split("+"); ids_by={s:[] for s in services}
        for bid in ids: ids_by[box_map[bid].service_node].append(bid)
        trips.append(Trip(f"Q2-T{i:03d}",vt,["O01"]+services+["O01"],ids_by,ids,m["mass"],m["volume"],m["energy"],m["duration"],True))
    return trips

def load_warm_start(path, box_map, vehicles, legs):
    if not path.exists(): return []
    trips=[]
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            route=row["route"].split("->"); ids=[x for x in row["box_ids"].split(";") if x]
            ids_by={s:[] for s in route[1:-1]}
            for bid in ids: ids_by[box_map[bid].service_node].append(bid)
            vt=row["vehicle_type"]; m=calc_route(route,ids_by,box_map,vehicles[vt],legs)
            if m is None: return []
            trips.append(Trip(row["trip_id"],vt,route,ids_by,ids,m["mass"],m["volume"],m["energy"],m["duration"],True))
    return trips if {x for t in trips for x in t.box_ids}==set(box_map) else []

def main(data_root: Path, out: Path):
    t0=time.time(); out.mkdir(parents=True,exist_ok=True)
    nodes,vehicles,box_map,boxes_by_service,dem,uavs,batteries,files=load_entities(data_root); legs=build_legs(nodes,dem)
    # Start from Q1's exact single-point batches; Q2 is allowed to merge/reassign
    # them, but keeping this audited baseline prevents needless degradation.
    q1_batches=defaultdict(list); q1_types={}
    q1_csv=ROOT/"# D-项目文件夹-LW"/"Solution"/"Q1"/"results"/"batching_baseline.csv"
    with q1_csv.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): q1_batches[r["batch_id"]].append(r["box_id"]); q1_types[r["batch_id"]]=r["vehicle_type"]
    raw=[]
    for batch_id,ids in sorted(q1_batches.items()):
        service=box_map[ids[0]].service_node; vt=q1_types[batch_id]
        m=calc_route(["O01",service,"O01"],{service:ids},box_map,vehicles[vt],legs)
        if m is None: raise ValueError(f"Q1 batch is infeasible under Q2 rules: {batch_id}")
        raw.append((service,ids,vt,m))
    q1_trips=make_trips(raw,box_map,vehicles,legs)
    q1_state=schedule_eval(q1_trips,sorted(q1_trips,key=lambda t:trip_order_key(t,box_map)),vehicles,uavs,batteries,box_map,legs)
    comparison=[{"plan":"q1_batches_schedule","hard_deadline_violations":q1_state[5]["hard_deadline_violations"],"weighted_tardiness_priority_seconds":q1_state[5]["weighted_tardiness_priority_seconds"],"makespan_s":q1_state[5]["makespan_s"],"energy_kwh":q1_state[5]["total_energy_kwh"],"trip_count":q1_state[5]["trip_count"]}]
    urgent_raw=[]
    for s,bs in boxes_by_service.items(): urgent_raw += split_service(s,[b.box_id for b in bs],box_map,nodes,vehicles,legs)
    urgent_trips=make_trips(urgent_raw,box_map,vehicles,legs)
    warm_path=Path(__file__).with_name("alns_warm_start.csv")
    warm_trips=load_warm_start(warm_path,box_map,vehicles,legs)
    if warm_trips:
        urgent_state=schedule_eval(urgent_trips,sorted(urgent_trips,key=lambda t:trip_order_key(t,box_map)),vehicles,uavs,batteries,box_map,legs)
        # The warm-start CSV is stored in its dispatch order.
        warm_state=schedule_eval(warm_trips,warm_trips,vehicles,uavs,batteries,box_map,legs)
        if warm_state[0] < urgent_state[0]: urgent_trips=warm_trips
    rows,delivs,res,assigned,au,best_order,trace,operator_weights,alns_iterations,alns_runtime=alns_optimize(
        urgent_trips,vehicles,uavs,batteries,box_map,boxes_by_service,legs,iterations=1000000,time_limit_s=540,seed=20260923)
    trips=best_order
    comparison.append({"plan":"alns_deadline_repacked","hard_deadline_violations":au["hard_deadline_violations"],"weighted_tardiness_priority_seconds":au["weighted_tardiness_priority_seconds"],"makespan_s":au["makespan_s"],"energy_kwh":au["total_energy_kwh"],"trip_count":au["trip_count"]})
    write_csv(out/"plan_comparison.csv",comparison)
    write_csv(out/"optimization_trace.csv",trace)
    write_csv(out/"operator_statistics.csv",[{"operator":k,"final_weight":v} for k,v in operator_weights.items()])
    # canonical interface outputs
    write_csv(out/"transport_plan.csv",rows,["trip_id","sequence_no","vehicle_type","vehicle_id","battery_id","from_node","to_node","start_time_s","end_time_s","payload_depart_kg","energy_kwh","soc_start","soc_end"])
    write_csv(out/"delivery_timeline.csv",delivs,["box_id","trip_id","service_node","delivery_time_s","delivery_class","deadline_s","deadline_met"])
    write_csv(out/"transport_resource_timeline.csv",res,["resource_type","resource_id","trip_id","activity","start_time_s","end_time_s","soc_before","soc_after"])
    trip_rows=[]
    for t,uid,bid,st,en,calc,seq,ss,se in assigned:
        trip_rows.append({"trip_id":t.trip_id,"vehicle_type":t.vehicle_type,"vehicle_id":uid,"battery_id":bid,"route":"->".join(t.route),"box_ids":";".join(t.box_ids),"box_count":len(t.box_ids),"payload_kg":calc["mass"],"volume_m3":calc["volume"],"energy_kwh":calc["energy"],"duration_s":calc["duration"],"start_time_s":st,"end_time_s":en,"soc_start":ss,"soc_end":se})
    write_csv(out/"trip_summary.csv",trip_rows)
    au.update({"runtime_s":time.time()-t0,"reserve_fraction":RESERVE,"solver":"constraint_first_alns_with_alternating_structure_and_order_search",
               "random_seed":20260923,"alns_iterations":alns_iterations,"alns_runtime_s":alns_runtime,
               "time_limit_s":540,"dispatch_order":[t.trip_id for t in best_order]})
    (out/"global_audit.json").write_text(json.dumps(au,ensure_ascii=False,indent=2),encoding="utf-8")
    # per-box and per-trip audit
    ad=[]
    for d in delivs: ad.append({"box_id":d["box_id"],"trip_id":d["trip_id"],"deadline_met":d["deadline_met"]})
    write_csv(out/"delivery_audit.csv",ad)
    tracked=files+[q1_csv,Q1,Path(__file__).resolve(),warm_path]
    def portable(p):
        try:return str(Path(p).resolve().relative_to(ROOT.resolve())).replace("\\","/")
        except ValueError:return Path(p).name
    manifest={"generated_at":time.strftime("%Y-%m-%dT%H:%M:%S"),"runtime_s":time.time()-t0,
              "data_root":"configured at runtime","files":[{"path":portable(x),"sha256":q1.sha256_file(Path(x))} for x in tracked],
              "random_seed":20260923,"alns_iterations":alns_iterations,"time_limit_s":540,"audit":au}
    (out/"runtime_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(au,ensure_ascii=False))

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--data-root",type=Path,required=True); ap.add_argument("--output-dir",type=Path,required=True); a=ap.parse_args(); main(a.data_root,a.output_dir)
