from __future__ import annotations

import argparse, csv, json, math, time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import openpyxl
from scipy.io import loadmat
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

MEMBER_ROOT = Path(__file__).resolve().parents[2]
Q1_PATH = MEMBER_ROOT / "Q1" / "code" / "q1_solver.py"
import importlib.util, sys
spec = importlib.util.spec_from_file_location("q1_solver", Q1_PATH)
q1 = importlib.util.module_from_spec(spec); assert spec.loader
sys.modules["q1_solver"] = q1; spec.loader.exec_module(q1)

EPS = 1e-9
G = 9.80665

@dataclass
class Endpoint:
    kind: str
    lon: float
    lat: float
    alt_m: float

@dataclass
class RelayCandidate:
    candidate_id: str
    lon: float
    lat: float
    ground_m: float
    hover_height_m: float
    alt_m: float
    cover: list[int]
    min_margin_db: float
    energy_kwh: float
    flight_time_s: float

def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f: return list(csv.DictReader(f))

def local_xy(lon, lat, lon0, lat0):
    return ((lon-lon0)*111320*math.cos(math.radians((lat+lat0)/2)), (lat-lat0)*111195)

def load_relay(data_root: Path):
    p = next(data_root.rglob("中继无人机数据.xlsx")); ws = openpyxl.load_workbook(p, data_only=True)["数据"]
    vals = list(ws.values)
    row = next(r for r in vals if r and r[0] == "R" and isinstance(r[2], (int,float)))
    par = {"mass_kg":float(row[4]),"speed_mps":float(row[5]),"power_kw":float(row[6]),"energy_kwh":float(row[7]),"reserve":float(row[8])/100,
           "prep_s":float(row[9]),"link_s":float(row[10]),"turn_s":float(row[11]),"climb_mps":float(row[12]),"descent_mps":float(row[13]),
           "climb_eff":float(row[14]),"hover_kw":float(row[16]),"comm_kw":float(row[17]),"max_hover_m":float(row[18]),"count":2,"component_count":6,"charge_s":1800.0}
    ids=[str(r[0]) for r in vals if r and r[0] in {"R01","R02"}]
    par["ids"] = ids
    return par

def load_link_params(data_root: Path):
    p=next(data_root.rglob("通信链路参数.xlsx")); ws=openpyxl.load_workbook(p,data_only=True)["数据"]
    d={}
    for r in list(ws.values)[2:]:
        if r and r[1] and r[4] is not None: d[str(r[1])] = float(r[4])
    rows=list(ws.values)
    def val(cat,name):
        for r in rows[2:]:
            if r and str(r[0])==cat and str(r[1])==name: return float(r[4])
        raise KeyError((cat,name))
    return {"f_mhz":val("传播参数","载波频率（MHz）"),"Lsys":val("传播参数","系统损耗（dB）"),"Lobs":val("传播参数","地形遮挡附加损耗（dB）"),"Psens":val("接收参数","接收灵敏度（dBm）"),"M":val("接收参数","衰落裕量（dB）"),
            "transport_pt":val("运输无人机","发射功率（dBm）"),"transport_g":val("运输无人机","天线增益（dBi）"),
            "relay_access_pt":val("中继接入端","发射功率（dBm）"),"relay_access_g":val("中继接入端","天线增益（dBi）"),
            "relay_back_pt":val("中继回传端","发射功率（dBm）"),"relay_back_g":val("中继回传端","天线增益（dBi）"),
            "gateway_pt":val("固定网关 G01","发射功率（dBm）"),"gateway_g":val("固定网关 G01","天线增益（dBi）"),"gateway_h":val("固定网关 G01","天线离地高度（m）")}

def terrain_los(a: Endpoint, b: Endpoint, dem, samples: int | None = None):
    lon=np.asarray(dem["longitude"])[0]; lat=np.asarray(dem["latitude"])[:,0]; raster=np.asarray(dem["dem"]); nodata=float(np.asarray(dem["nodata"]).ravel()[0])
    n=max(5, min(20, samples or int(math.ceil(math.hypot((b.lon-a.lon)*111320*math.cos(math.radians((a.lat+b.lat)/2)),(b.lat-a.lat)*111195)/30))))
    max_clear=-1e99
    for i in range(1,n):
        u=i/n; lo=a.lon+(b.lon-a.lon)*u; la=a.lat+(b.lat-a.lat)*u; z=a.alt_m+(b.alt_m-a.alt_m)*u
        ix=int(np.clip(round((lo-float(lon[0]))/float(lon[1]-lon[0])),0,len(lon)-1)); iy=int(np.clip(round((float(lat[0])-la)/float(lat[0]-lat[1])),0,len(lat)-1))
        ground=float(raster[iy,ix]);
        if not np.isfinite(ground) or ground==nodata: return False, 1e9
        max_clear=max(max_clear, ground-z)
        if ground >= z-1e-6: return False, max_clear
    return True, max_clear

def fspl_db(a: Endpoint,b: Endpoint, lp):
    dx,dy=local_xy(b.lon,b.lat,a.lon,a.lat); dz=b.alt_m-a.alt_m; dkm=max(math.sqrt(dx*dx+dy*dy+dz*dz)/1000,1e-6)
    return 32.44+20*math.log10(lp["f_mhz"])+20*math.log10(dkm)

def link(a: Endpoint,b: Endpoint, kind: str, dem, lp):
    if kind == "tg":
        limits=[lp["transport_pt"]+lp["transport_g"]+lp["gateway_g"]-lp["Lsys"]-lp["Psens"]-lp["M"], lp["gateway_pt"]+lp["gateway_g"]+lp["transport_g"]-lp["Lsys"]-lp["Psens"]-lp["M"]]
    elif kind == "tr":
        limits=[lp["transport_pt"]+lp["transport_g"]+lp["relay_access_g"]-lp["Lsys"]-lp["Psens"]-lp["M"], lp["relay_access_pt"]+lp["relay_access_g"]+lp["transport_g"]-lp["Lsys"]-lp["Psens"]-lp["M"]]
    else:
        limits=[lp["relay_back_pt"]+lp["relay_back_g"]+lp["gateway_g"]-lp["Lsys"]-lp["Psens"]-lp["M"], lp["gateway_pt"]+lp["gateway_g"]+lp["relay_back_g"]-lp["Lsys"]-lp["Psens"]-lp["M"]]
    los,clear=terrain_los(a,b,dem)
    loss=fspl_db(a,b,lp)+(lp["Lobs"] if not los else 0)
    margin=min(limits)-loss
    return margin >= -1e-8, margin, los

def node_endpoint(nodes, node_id, alt_offset=0):
    n=nodes[node_id]; return Endpoint("gateway" if node_id=="O01" else "transport",n.lon,n.lat,n.ground_elevation_m+alt_offset)

def op_alt(nodes,node_id): return nodes[node_id].ground_elevation_m + (0 if node_id=="O01" else 30)

def load_nodes(data_root):
    nodes, vehicles, boxes, dem, files = q1.load_inputs(data_root)
    return nodes,dem,vehicles

def segment_endpoint(nodes, seg_from, seg_to, u, stage, leg):
    a=nodes[seg_from]; b=nodes[seg_to]
    if stage=="from": return Endpoint("transport",a.lon,a.lat,op_alt(nodes,seg_from))
    if stage=="to": return Endpoint("transport",b.lon,b.lat,op_alt(nodes,seg_to))
    if u <= leg.climb_m/(leg.climb_m + leg.horizontal_distance_m + leg.descent_m + EPS):
        z=op_alt(nodes,seg_from) + (leg.cruise_altitude_m-op_alt(nodes,seg_from))*u/max(leg.climb_m/(leg.climb_m+leg.horizontal_distance_m+leg.descent_m+EPS),EPS)
        return Endpoint("transport",a.lon,a.lat,z)
    cfrac=leg.climb_m/(leg.climb_m+leg.horizontal_distance_m+leg.descent_m+EPS); dfrac=leg.descent_m/(leg.climb_m+leg.horizontal_distance_m+leg.descent_m+EPS)
    if u >= 1-dfrac:
        v=(u-(1-dfrac))/max(dfrac,EPS); return Endpoint("transport",b.lon,b.lat,leg.cruise_altitude_m+(op_alt(nodes,seg_to)-leg.cruise_altitude_m)*v)
    v=(u-cfrac)/max(1-cfrac-dfrac,EPS); return Endpoint("transport",a.lon+(b.lon-a.lon)*v,a.lat+(b.lat-a.lat)*v,leg.cruise_altitude_m)

def build_trajectory(rows, nodes, legs):
    # continuous time slices: every flight segment plus service/turn gaps represented at the endpoint.
    out=[]
    for r in rows:
        st=float(r["start_time_s"]); en=float(r["end_time_s"]); a=r["from_node"]; b=r["to_node"]
        leg=legs[(a,b)]
        out.append({"start":st,"end":en,"from":a,"to":b,"leg":leg,"kind":"flight"})
    return sorted(out,key=lambda x:(x["start"],x["end"]))

def all_time_slices(traj,nodes,legs):
    slices=[]
    for i,s in enumerate(traj):
        # five samples per flight, plus endpoint gaps between consecutive legs.
        for j in range(5):
            u=j/4; t=s["start"]+(s["end"]-s["start"])*u
            e=segment_endpoint(nodes,s["from"],s["to"],u,"flight",s["leg"])
            slices.append((t,e,s))
        if i+1<len(traj) and traj[i+1]["start"]>s["end"]+EPS:
            e=node_endpoint(nodes,s["to"],0 if s["to"]=="O01" else 30)
            slices.append(((s["end"]+traj[i+1]["start"])/2,e,{"start":s["end"],"end":traj[i+1]["start"],"kind":"service"}))
    return sorted(slices,key=lambda x:x[0])

def relay_energy(candidate: RelayCandidate, nodes, relay, dem):
    # candidate flight uses O01 ground operation height, relay is at candidate altitude.
    o=node_endpoint(nodes,"O01",0); p=Endpoint("relay",candidate.lon,candidate.lat,candidate.alt_m)
    d=math.hypot(*local_xy(o.lon,o.lat,p.lon,p.lat)); climb=max(0,candidate.alt_m-o.alt_m); descent=climb
    t=2*(d/relay["speed_mps"])+climb/relay["climb_mps"]+descent/relay["descent_mps"]
    e=2*(relay["power_kw"]*(d/relay["speed_mps"])/3600.0)+relay["mass_kg"]*G*(climb+descent)/(3_600_000*relay["climb_eff"])
    return e,t

def candidate_search(trip_id, slices, nodes, dem, lp, relay, legs):
    # Candidate cloud: trajectory points, midpoint offsets, and local offsets around the route.
    pts=[]
    for _,e,_ in slices:
        pts.append((e.lon,e.lat))
    lon0=sum(p[0] for p in pts)/len(pts); lat0=sum(p[1] for p in pts)/len(pts)
    cxy=[(0,0),(-1000,0),(1000,0),(0,-1000),(0,1000)]
    heights=(100,200,300)
    candidates=[]; seen=set()
    for lon,lat in pts[::max(1,len(pts)//3)]+[(lon0,lat0)]:
        for dx,dy in cxy:
            lo=lon+dx/(111320*math.cos(math.radians(lat))); la=lat+dy/111195
            for h in heights:
                key=(round(lo,6),round(la,6),h)
                if key in seen: continue
                seen.add(key)
                # nearest DEM ground
                dl=np.asarray(dem["longitude"])[0]; da=np.asarray(dem["latitude"])[:,0]; rr=np.asarray(dem["dem"])
                ix=int(np.clip(round((lo-float(dl[0]))/float(dl[1]-dl[0])),0,len(dl)-1)); iy=int(np.clip(round((float(da[0])-la)/float(da[0]-da[1])),0,len(da)-1)); ground=float(rr[iy,ix]); alt=ground+h
                p=Endpoint("relay",lo,la,alt); cover=[]; margins=[]
                gw=Endpoint("gateway",nodes["O01"].lon,nodes["O01"].lat,nodes["O01"].ground_elevation_m+lp["gateway_h"])
                for k,(_,te,_) in enumerate(slices):
                    ok1,m1,_=link(te,p,"tr",dem,lp); ok2,m2,_=link(p,gw,"rg",dem,lp)
                    if ok1 and ok2: cover.append(k); margins.append(min(m1,m2))
                if cover:
                    c=RelayCandidate(f"{trip_id}-C{len(candidates)+1:03d}",lo,la,ground,h,alt,cover,min(margins),0,0)
                    c.energy_kwh,c.flight_time_s=relay_energy(c,nodes,relay,dem); candidates.append(c)
    # maximize coverage then margin, favor low energy
    candidates.sort(key=lambda c:(-len(c.cover),-c.min_margin_db,c.energy_kwh))
    return candidates

def _relay_sortie_options(blind_data, candidate_pool, nodes, dem, lp, relay,
                          relay_count=2, max_candidates_per_trip=35,
                          milp_time_limit=6.0, pool_cap=80):
    """WHLi-style global relay sortie enumeration and occupancy MILP.

    Candidate positions are first pooled across all blind trips.  A sortie may
    cover several trips only when one position covers every sampled blind
    slice of those trips.  The MILP then enforces set coverage and the
    two-airframe interval occupancy constraints simultaneously.
    """
    blind_ids = sorted((t for t in blind_data if blind_data[t]["blind"]),
                       key=lambda t: min(x[0] for x in blind_data[t]["blind"]))
    if not blind_ids:
        return [], {}, {"status": "direct_only", "candidate_count": 0, "option_count": 0}
    gw = Endpoint("gateway", nodes["O01"].lon, nodes["O01"].lat,
                  nodes["O01"].ground_elevation_m + lp["gateway_h"])
    usable = (1.0 - relay["reserve"]) * relay["energy_kwh"]
    # Keep the strongest candidates from each route.  This mirrors WHLi's
    # coarse/full-cover + near-candidate pool while keeping a 100 s run small.
    pool = {}
    for c in candidate_pool:
        key = (round(c.lon, 6), round(c.lat, 6), round(c.hover_height_m, 3))
        old = pool.get(key)
        if old is None or (len(c.cover), c.min_margin_db, -c.energy_kwh) > (len(old.cover), old.min_margin_db, -old.energy_kwh):
            pool[key] = c
    candidates = sorted(pool.values(), key=lambda c: (-len(c.cover), -c.min_margin_db, c.energy_kwh))[:pool_cap]
    # Backhaul is position-only; cache it once instead of repeating it for
    # every candidate/trip/slice combination.
    backhaul_ok = {}
    for ci,c in enumerate(candidates):
        p=Endpoint("relay",c.lon,c.lat,c.alt_m)
        backhaul_ok[ci]=link(p,gw,"rg",dem,lp)[0]
    cover_by_cand = {}
    for ci, c in enumerate(candidates):
        if not backhaul_ok[ci]:
            cover_by_cand[ci]=[]; continue
        p = Endpoint("relay", c.lon, c.lat, c.alt_m)
        covered = []
        for tid in blind_ids:
            ok_all = True
            for _, ep, _ in blind_data[tid]["blind"]:
                ok1, _, _ = link(ep, p, "tr", dem, lp)
                if not ok1:
                    ok_all = False; break
            if ok_all: covered.append(tid)
        cover_by_cand[ci] = covered
    index = {t: i for i, t in enumerate(blind_ids)}
    options = []
    for ci, c in enumerate(candidates):
        compat = [t for t in cover_by_cand[ci] if t in index]
        compat.sort(key=lambda t: min(x[0] for x in blind_data[t]["blind"]))
        for i, first in enumerate(compat):
            group = []
            for t in compat[i:]:
                group.append(t)
                a = min(min(x[0] for x in blind_data[z]["blind"]) for z in group)
                b = max(max(x[0] for x in blind_data[z]["blind"]) for z in group)
                energy = c.energy_kwh + (relay["hover_kw"] + relay["comm_kw"]) * max(0.0, b-a) / 3600.0
                if energy > usable + 1e-8: break
                lead = relay["prep_s"] + c.flight_time_s / 2.0 + relay["link_s"]
                launch = a - lead
                return_time = b + c.flight_time_s / 2.0
                ready = return_time + relay["turn_s"]
                if launch < -1e-8: continue
                options.append({"candidate": c, "covers": tuple(group), "service_start": a,
                                "service_end": b, "launch": launch, "return_time": return_time,
                                "ready": ready, "energy": energy})
    if not options:
        return [], {}, {"status": "no_options", "candidate_count": len(candidates), "option_count": 0}
    # Deduplicate equivalent candidate/mask options, retaining the shortest
    # occupied interval and then the lowest energy.
    uniq = {}
    for o in options:
        key = (round(o["candidate"].lon, 6), round(o["candidate"].lat, 6), tuple(o["covers"]))
        old = uniq.get(key)
        if old is None or (o["ready"]-o["launch"], o["energy"]) < (old["ready"]-old["launch"], old["energy"]): uniq[key] = o
    options = list(uniq.values())
    events = sorted({o["launch"] for o in options} | {o["ready"] for o in options})
    slots = [(a+b)/2.0 for a,b in zip(events, events[1:])]
    active = [[j for j,o in enumerate(options) if o["launch"] <= t < o["ready"]] for t in slots]
    active = [a for a in active if len(a) > relay_count]
    seen, rows = set(), []
    for a in active:
        k = tuple(a)
        if k not in seen: seen.add(k); rows.append(a)
    A = lil_matrix((len(blind_ids)+len(rows), len(options)), dtype=float)
    for j,o in enumerate(options):
        for t in o["covers"]: A[index[t],j] = 1.0
    for r,a in enumerate(rows, len(blind_ids)): A[r,a] = 1.0
    lower = np.r_[np.ones(len(blind_ids)), np.zeros(len(rows))]
    upper = np.r_[np.full(len(blind_ids), np.inf), np.full(len(rows), relay_count)]
    cost = np.array([1000.0 + 10.0*o["energy"] + 1e-3*o["ready"] for o in options])
    sol = milp(cost, integrality=np.ones(len(options)), bounds=Bounds(np.zeros(len(options)), np.ones(len(options))),
               constraints=LinearConstraint(A.tocsr(), lower, upper),
               options={"time_limit": float(milp_time_limit), "mip_rel_gap": 0.02})
    if sol.x is None:
        return [], {}, {"status": int(sol.status), "message": str(sol.message), "candidate_count": len(candidates), "option_count": len(options), "resource_rows": len(rows)}
    chosen = [options[i] for i,v in enumerate(sol.x) if v > .5]
    chosen.sort(key=lambda o: o["launch"])
    ready = [0.0]*relay_count; assignment = {}
    for o in chosen:
        eligible = [j for j in range(relay_count) if ready[j] <= o["launch"] + 1e-7]
        if not eligible:
            return [], {}, {"status": "assignment_failed", "candidate_count": len(candidates), "option_count": len(options), "resource_rows": len(rows)}
        j = min(eligible, key=lambda x: ready[x]); assignment[id(o)] = f"R{j+1:02d}"; ready[j] = o["ready"]
    trip_task = {}
    for k,o in enumerate(chosen, 1):
        task_id = f"Q3-R{k:03d}"
        for t in o["covers"]: trip_task.setdefault(t, task_id)
        o["task_id"] = task_id; o["relay_id"] = assignment[id(o)]
    diag = {"status": int(sol.status), "message": str(sol.message), "candidate_count": len(candidates),
            "option_count": len(options), "resource_rows": len(rows), "selected_sorties": len(chosen),
            "relay_count": relay_count, "covered_trips": sorted(trip_task)}
    return chosen, trip_task, diag

def main(data_root: Path, out: Path, q2_dir: Path | None = None):
    t0=time.time(); out.mkdir(parents=True,exist_ok=True)
    nodes,dem,vehicles=load_nodes(data_root); relay=load_relay(data_root); lp=load_link_params(data_root)
    q2=q2_dir or (MEMBER_ROOT / "Q2" / "results")
    plan=read_csv(q2/"transport_plan.csv")
    # Construct DEM-consistent legs for all nodes.
    legs={}
    for a in nodes.values():
        for b in nodes.values():
            if a.node_id!=b.node_id: legs[(a.node_id,b.node_id)] = q1.build_leg(a,b,op_alt(nodes,a.node_id),op_alt(nodes,b.node_id),dem)
    bytrip=defaultdict(list)
    for r in plan: bytrip[r["trip_id"]].append(r)
    audit_rows=[]; relay_rows=[]; trip_summ=[]; blind_data={}; candidate_pool=[]
    gw=Endpoint("gateway",nodes["O01"].lon,nodes["O01"].lat,nodes["O01"].ground_elevation_m+lp["gateway_h"])
    for trip_id, rows in bytrip.items():
        rows=sorted(rows,key=lambda r:int(r["sequence_no"])); slices=all_time_slices(build_trajectory(rows,nodes,legs),nodes,legs)
        direct=[link(te,gw,"tg",dem,lp) for _,te,_ in slices]
        blind=[slices[k] for k,x in enumerate(direct) if not x[0]]
        blind_data[trip_id]={"slices":slices,"direct":direct,"blind":blind}
        if blind:
            candidate_pool.extend(candidate_search(trip_id,slices,nodes,dem,lp,relay,legs)[:35])
    chosen,trip_task,milp_diag=_relay_sortie_options(blind_data,candidate_pool,nodes,dem,lp,relay)
    comp_ready=[0.0]*relay["component_count"]
    by_task={}
    for o in chosen:
        c=o["candidate"]; eligible=[i for i,r in enumerate(comp_ready) if r<=o["launch"]+EPS]
        if not eligible:
            milp_diag["energy_component_assignment_failed"]=True
            continue
        i=min(eligible,key=lambda j:comp_ready[j]); soc=1-o["energy"]/relay["energy_kwh"]
        charge=relay["charge_s"] if soc<.9 else (1-soc)/.1*.35*relay["charge_s"]
        comp_ready[i]=o["return_time"]+charge
        rr={"relay_task_id":o["task_id"],"relay_type":"R","relay_id":o["relay_id"],
            "energy_component_id":f"R-E{i:02d}","x":c.lon,"y":c.lat,
            "hover_height_m":c.hover_height_m,"depart_time_s":o["launch"],
            "service_start_s":o["service_start"],"service_end_s":o["service_end"],
            "return_time_s":o["return_time"],"energy_kwh":o["energy"],"soc_end":soc}
        relay_rows.append(rr); by_task[o["task_id"]]=(rr,c)
    for trip_id,data in blind_data.items():
        slices,direct,blind=data["slices"],data["direct"],data["blind"]
        for k,(tm,te,meta) in enumerate(slices):
            ok,m,los=direct[k]; mode="direct" if ok else "relay"; rid=""; rlos=False; glos=False; minm=m
            if not ok:
                rid=trip_task.get(trip_id,""); hit=by_task.get(rid)
                if hit:
                    rr,c=hit; p=Endpoint("relay",c.lon,c.lat,c.alt_m)
                    ok1,m1,l1=link(te,p,"tr",dem,lp); ok2,m2,l2=link(p,gw,"rg",dem,lp)
                    rlos=l1; glos=l2; minm=min(m1,m2)
                    ok=ok1 and ok2 and float(rr["service_start_s"])<=tm<=float(rr["service_end_s"])
                else: ok=False
            audit_rows.append({"trip_id":trip_id,"time_start_s":tm,"time_end_s":tm,"mode":mode,"direct_link_ok":bool(direct[k][0]),"relay_task_id":rid,"uav_relay_los_ok":bool(rlos),"relay_gateway_los_ok":bool(glos),"min_link_margin_db":float(minm),"continuous_ok":bool(ok)})
        trip_summ.append({"trip_id":trip_id,"direct_slices":sum(x[0] for x in direct),"total_slices":len(slices),"blind_slices":len(blind),"relay_tasks":int(trip_id in trip_task),"continuous_ok":not blind or trip_id in trip_task})
    # Resource audit: conservative interval overlap and energy checks for relay tasks.
    relay_rows.sort(key=lambda x:float(x["depart_time_s"]))
    overlap=[]
    for rid in relay["ids"]:
        rr=[x for x in relay_rows if x["relay_id"]==rid]
        for a,b in zip(rr,rr[1:]):
            if float(a["return_time_s"])+relay["turn_s"]>float(b["depart_time_s"])+EPS: overlap.append(rid)
    comm_ok=all(bool(r["continuous_ok"]) for r in audit_rows)
    relay_ok=all(float(r["soc_end"])>=relay["reserve"]-EPS for r in relay_rows) and not overlap and not milp_diag.get("energy_component_assignment_failed") and len(chosen)==len(relay_rows)
    write_csv(out/"relay_plan.csv",relay_rows,["relay_task_id","relay_type","relay_id","energy_component_id","x","y","hover_height_m","depart_time_s","service_start_s","service_end_s","return_time_s","energy_kwh","soc_end"])
    write_csv(out/"communication_audit.csv",audit_rows,["trip_id","time_start_s","time_end_s","mode","direct_link_ok","relay_task_id","uav_relay_los_ok","relay_gateway_los_ok","min_link_margin_db","continuous_ok"])
    write_csv(out/"trip_communication_summary.csv",trip_summ,["trip_id","direct_slices","total_slices","blind_slices","relay_tasks","continuous_ok"])
    summary={"q2_trip_count":len(bytrip),"q3_relay_task_count":len(relay_rows),"communication_slices":len(audit_rows),"communication_gap_slices":sum(not bool(r["continuous_ok"]) for r in audit_rows),"communication_overall_pass":comm_ok,"relay_resource_pass":relay_ok,"relay_energy_kwh":sum(float(r["energy_kwh"]) for r in relay_rows),"transport_energy_kwh":sum(float(r["energy_kwh"]) for r in plan),"joint_energy_kwh":sum(float(r["energy_kwh"]) for r in relay_rows)+sum(float(r["energy_kwh"]) for r in plan),"joint_makespan_s":max([float(r["end_time_s"]) for r in plan]+[float(r["return_time_s"]) for r in relay_rows] or [0]),"relay_overlap_ids":sorted(set(overlap)),"relay_milp":milp_diag,"runtime_s":time.time()-t0,"link_model":"FSPL + DEM LOS + bidirectional budget","sample_points_per_flight":5}
    (out/"global_audit.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--data-root",type=Path,required=True); ap.add_argument("--output-dir",type=Path,required=True); ap.add_argument("--q2-dir",type=Path); a=ap.parse_args(); main(a.data_root,a.output_dir,a.q2_dir)
