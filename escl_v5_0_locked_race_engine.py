#!/usr/bin/env python3
"""ESCL v2.7 — Dynamic Lap-by-Lap Race Engine

Built from the validated v0.7 event-driven architecture, adapted for:
- live SQLite drivers/teams
- dynamic track definitions
- Race Weekend strategy
- per-lap tire/fuel state
- passing contests
- pit windows
- cautions/restarts
- damage/mechanical failures
- detailed Racecast
"""

from dataclasses import dataclass
import random
import hashlib, math

POINTS=[50,44,40,37,35,33,31,29,27,25,23,21,19,17,15,13,12,11,10,9,8,7,6,5]

@dataclass(frozen=True)
class Team:
    id:int
    name:str
    engine:int
    aero:int
    handling:int
    reliability:int
    pit:int

@dataclass(frozen=True)
class Driver:
    id:str
    name:str
    number:int
    team_id:int
    spd:int
    rcr:int
    qlf:int
    con:int
    tir:int
    drf:int
    ctl:int
    agg:int

@dataclass(frozen=True)
class Track:
    id:str
    name:str
    kind:str
    laps:int
    base_lap:float
    tire_wear:float
    tire_penalty:float
    fuel_window:float
    passing_range:float
    base_pass_prob:float
    caution_mult:float
    restart_gap:float
    spd_w:float
    rcr_w:float
    ctl_w:float
    con_w:float
    tir_w:float
    drf_w:float
    agg_w:float
    car_w:float

TRACK_LIBRARY={
    "CAR":Track("CAR","Carolina Motor Speedway","INTERMEDIATE",100,30.15,.91,1.48,40,.30,.075,1.00,.38,.48,.10,.13,.05,.07,.02,.05,.10),
    "GULF":Track("GULF","Gulf Coast Speedway","SUPERSPEEDWAY",80,47.80,.42,.55,34,.34,.088,1.25,.24,.24,.16,.05,.04,.03,.30,.08,.10),
    "PINE":Track("PINE","Pine Ridge Raceway","SHORT_TRACK",160,20.80,.78,1.05,52,.20,.078,1.45,.22,.18,.24,.22,.08,.06,.02,.10,.10),
    "MID":Track("MID","Midland Raceway","FLAT_OVAL",120,27.60,.72,1.10,44,.25,.072,1.15,.34,.22,.18,.28,.12,.08,.02,.00,.10),
    "RIDGE":Track("RIDGE","Ridgeview Speedway","HIGH_BANK",110,26.35,.88,1.38,42,.28,.072,1.15,.30,.34,.12,.20,.05,.14,.05,.00,.10),
    "COAST":Track("COAST","Coastal Grand Prix","ROAD_COURSE",55,91.50,.62,.88,22,.20,.065,.90,.45,.18,.25,.30,.14,.05,.00,.00,.08),
}

def car_rating(t):
    return .36*t.engine+.32*t.aero+.32*t.handling

def driver_track_skill(d,track):
    return (
        d.spd*track.spd_w+d.rcr*track.rcr_w+d.ctl*track.ctl_w+
        d.con*track.con_w+d.tir*track.tir_w+d.drf*track.drf_w+
        d.agg*track.agg_w
    )

def qualify_score(d,t,track,rng,setup="BALANCED"):
    skill=driver_track_skill(d,track)
    q=.54*d.qlf+.20*d.spd+.16*skill+.10*car_rating(t)
    if setup=="SPEED":
        q+=1.2
    elif setup=="HANDLING":
        q+=.25 if track.kind in ("SHORT_TRACK","FLAT_OVAL","ROAD_COURSE") else -.15
    return q+rng.gauss(0,1.35)

def race_skill(d,t,track,setup="BALANCED"):
    driver=driver_track_skill(d,track)
    score=driver+car_rating(t)*track.car_w
    if setup=="SPEED":
        score+=.75 if track.kind in ("INTERMEDIATE","HIGH_BANK","SUPERSPEEDWAY") else -.3
    elif setup=="HANDLING":
        score+=1.4 if track.kind in ("SHORT_TRACK","FLAT_OVAL","ROAD_COURSE") else .15
    return score

def strategy_for(driver_id,human_id,human_strategy):
    if driver_id==human_id:
        return human_strategy
    return {"setup_bias":"BALANCED","driving_style":"BALANCED","pit_plan":"STANDARD"}


def build_sector_telemetry(telemetry, structured_events, total_laps, track=None):
    """Build deterministic sector timing/race-state from authoritative lap states."""
    if not telemetry:
        return []

    events_by_key={}
    pit_events={}
    restart_laps=set()
    for idx,e in enumerate(structured_events):
        lap=int(e.get("lap",1)); sec=int(e.get("sector",1))
        events_by_key.setdefault((lap,sec),[]).append((idx,e))
        if e.get("type")=="PIT_STOP" and e.get("driver_id") is not None:
            pit_events.setdefault(str(e["driver_id"]),[]).append((lap,sec,e))
        elif e.get("type")=="RESTART":
            restart_laps.add(lap)

    kind=getattr(track,"kind","INTERMEDIATE") if track is not None else "INTERMEDIATE"
    battle_threshold={
        "SUPERSPEEDWAY":.18,"INTERMEDIATE":.24,"SHORT_TRACK":.16,
        "FLAT_OVAL":.22,"HIGH_BANK":.22,"ROAD_COURSE":.32
    }.get(kind,.24)

    frames=[]
    active_battles={}
    battle_seq=0
    elapsed={str(c["driver_id"]):0.0 for c in telemetry[0].get("cars",[])}
    last_elapsed={}
    pit_service_clock={}

    def mix(a,b,t):
        try: return float(a)+(float(b)-float(a))*t
        except Exception: return a if t<.5 else b

    for idx,a in enumerate(telemetry[:-1]):
        b=telemetry[min(idx+1,len(telemetry)-1)]
        lap=int(a.get("lap",idx+1))
        amap={str(c["driver_id"]):c for c in a.get("cars",[])}
        bmap={str(c["driver_id"]):c for c in b.get("cars",[])}

        # Approximate lap time from change in leader-relative state plus track base.
        # Race result authority is unchanged; these are timing checkpoints for broadcast/race state.
        base_lap=float(getattr(track,"base_lap",30.0) if track is not None else 30.0)

        for sector in (1,2,3):
            t=(sector-1)/3.0
            cars=[]
            for did,ca in amap.items():
                cb=bmap.get(did,ca)
                gap_a=ca.get("gap"); gap_b=cb.get("gap")
                if gap_a is None: gap=gap_b
                elif gap_b is None: gap=gap_a
                else: gap=mix(gap_a,gap_b,t)

                pos=ca.get("position") if sector<3 else cb.get("position",ca.get("position"))
                pit_phase=None
                service_time=None
                for plap,psec,pe in pit_events.get(did,[]):
                    if lap==plap:
                        if sector==max(1,psec-1): pit_phase="ENTRY"
                        if sector==psec:
                            pit_phase="SERVICE"
                            # Stable service estimate for display; team pit result is already in race time.
                            service_time=round(12.8 + ((int(did) if did.isdigit() else sum(map(ord,did)))%9-4)*.07,2)
                            pit_service_clock[did]=service_time
                        if sector==min(3,psec+1): pit_phase="EXIT"

                # Sector time is one third of base lap, adjusted by interval movement.
                ga=float(gap_a or 0.0); gb=float(gap_b if gap_b is not None else ga)
                interval_drift=(gb-ga)/3.0
                sector_time=max(base_lap*.24,base_lap/3.0+interval_drift)
                if pit_phase=="ENTRY": sector_time+=3.2
                elif pit_phase=="SERVICE": sector_time+=float(service_time or pit_service_clock.get(did,12.8))
                elif pit_phase=="EXIT": sector_time+=2.4

                elapsed[did]=elapsed.get(did,0.0)+sector_time
                cars.append({
                    **ca,
                    "position":int(pos),
                    "gap":None if gap is None else round(float(gap),3),
                    "tire":round(mix(ca.get("tire",0),cb.get("tire",ca.get("tire",0)),t),1),
                    "fuel":round(mix(ca.get("fuel",0),cb.get("fuel",ca.get("fuel",0)),t),1),
                    "damage":round(mix(ca.get("damage",0),cb.get("damage",ca.get("damage",0)),t),1),
                    "lane":round(mix(ca.get("lane",0),cb.get("lane",ca.get("lane",0)),t),2),
                    "pit_phase":pit_phase,
                    "pit_service_time":service_time,
                    "sector_time":round(sector_time,3),
                    "elapsed_time":round(elapsed[did],3),
                    "interval_ahead":None,
                    "interval_leader":None if gap is None else round(float(gap),3),
                    "battle_id":None,
                    "battle_with":None,
                    "battle_sectors":0,
                    "pass_attempt":False,
                    "restart_row":None,
                    "restart_lane":None,
                })
            cars.sort(key=lambda c:(c.get("out",False),c["position"]))

            # Exact interval-ahead field.
            for i,c in enumerate(cars):
                if c.get("out") or c.get("gap") is None:
                    continue
                if i==0:
                    c["interval_ahead"]=0.0
                else:
                    ahead=cars[i-1]
                    if ahead.get("gap") is not None:
                        c["interval_ahead"]=round(max(0.0,float(c["gap"])-float(ahead["gap"])),3)

            # Persistent battles use track-specific thresholds and duration counters.
            seen_pairs=set()
            for i in range(1,len(cars)):
                ahead,behind=cars[i-1],cars[i]
                interval=behind.get("interval_ahead")
                if ahead.get("out") or behind.get("out") or interval is None:
                    continue
                if interval<=battle_threshold:
                    pair=tuple(sorted((str(ahead["driver_id"]),str(behind["driver_id"]))))
                    seen_pairs.add(pair)
                    if pair not in active_battles:
                        battle_seq+=1
                        active_battles[pair]={"id":f"B{battle_seq:04d}","sectors":0}
                    active_battles[pair]["sectors"]+=1
                    info=active_battles[pair]
                    ahead["battle_id"]=behind["battle_id"]=info["id"]
                    ahead["battle_with"]=behind["driver_id"]
                    behind["battle_with"]=ahead["driver_id"]
                    ahead["battle_sectors"]=behind["battle_sectors"]=info["sectors"]
                    # Close battles become an explicit attempt state before a completed PASS event.
                    if interval<=battle_threshold*.55:
                        behind["pass_attempt"]=True
            for pair in list(active_battles):
                if pair not in seen_pairs:
                    active_battles.pop(pair,None)

            if lap in restart_laps and sector==1:
                for i,c in enumerate(cars):
                    c["restart_row"]=(i//2)+1
                    c["restart_lane"]="INSIDE" if i%2==0 else "OUTSIDE"

            evpairs=events_by_key.get((lap,sector),[])
            frames.append({
                "lap":lap,
                "sector":sector,
                "progress":round((lap-1)+(sector-1)/3.0,4),
                "flag":a.get("flag","GREEN"),
                "restart_formation":bool(lap in restart_laps and sector==1),
                "battle_threshold":battle_threshold,
                "event_types":[e.get("type") for _,e in evpairs],
                "event_ids":[ei for ei,_ in evpairs],
                "cars":cars,
            })
            last_elapsed={str(c["driver_id"]):c["elapsed_time"] for c in cars}

    last=telemetry[-1]
    final_cars=[]
    for c in last.get("cars",[]):
        did=str(c["driver_id"])
        final_cars.append({
            **c,
            "pit_phase":None,"pit_service_time":None,
            "sector_time":None,"elapsed_time":last_elapsed.get(did),
            "interval_ahead":None,"interval_leader":c.get("gap"),
            "battle_id":None,"battle_with":None,"battle_sectors":0,"pass_attempt":False,
            "restart_row":None,"restart_lane":None
        })
    final_cars.sort(key=lambda c:c["position"])
    for i,c in enumerate(final_cars):
        if c.get("gap") is not None:
            c["interval_ahead"]=0.0 if i==0 else (
                None if final_cars[i-1].get("gap") is None
                else round(max(0.0,float(c["gap"])-float(final_cars[i-1]["gap"])),3)
            )
    frames.append({
        "lap":int(last.get("lap",total_laps)),
        "sector":3,
        "progress":float(total_laps),
        "flag":"CHECKERED",
        "restart_formation":False,
        "battle_threshold":battle_threshold,
        "event_types":["CHECKERED"],
        "event_ids":[],
        "cars":final_cars,
    })
    return frames


def annotate_authoritative_sectors(frames, structured_events, track):
    """Annotate live sector checkpoints with intervals, battle persistence and event mapping."""
    events_by_key={}
    for idx,e in enumerate(structured_events):
        events_by_key.setdefault((int(e.get("lap",1)),int(e.get("sector",1))),[]).append((idx,e))
    threshold={"SUPERSPEEDWAY":.18,"INTERMEDIATE":.24,"SHORT_TRACK":.16,"FLAT_OVAL":.22,"HIGH_BANK":.22,"ROAD_COURSE":.32}.get(track.kind,.24)
    active={}; seq=0
    for f in frames:
        cars=f.get("cars",[])
        cars.sort(key=lambda c:(c.get("out",False),c["position"]))
        for i,c in enumerate(cars):
            c["interval_leader"]=c.get("gap")
            c["interval_ahead"]=0.0 if i==0 and c.get("gap") is not None else None
            if i>0 and c.get("gap") is not None and cars[i-1].get("gap") is not None:
                c["interval_ahead"]=round(max(0,float(c["gap"])-float(cars[i-1]["gap"])),3)
            c["battle_id"]=None;c["battle_with"]=None;c["battle_sectors"]=0;c["pass_attempt"]=False
            c["pit_phase"]=c.get("pit_phase");c["pit_service_time"]=c.get("pit_service_time")
            c["restart_row"]=None;c["restart_lane"]=None
        seen=set()
        for i in range(1,len(cars)):
            a,b=cars[i-1],cars[i]; interval=b.get("interval_ahead")
            featured=(int(a.get("position",99))<=12 or int(b.get("position",99))<=12)
            if interval is not None and not a.get("out") and not b.get("out") and featured and interval<=threshold*.55:
                pair=tuple(sorted((str(a["driver_id"]),str(b["driver_id"]))));seen.add(pair)
                if pair not in active:
                    seq+=1;active[pair]={"id":f"B{seq:04d}","n":0}
                active[pair]["n"]+=1; info=active[pair]
                a["battle_id"]=b["battle_id"]=info["id"];a["battle_with"]=b["driver_id"];b["battle_with"]=a["driver_id"]
                a["battle_sectors"]=b["battle_sectors"]=info["n"]
                if interval<=threshold*.25:b["pass_attempt"]=True
        for pair in list(active):
            if pair not in seen: active.pop(pair,None)
        evs=events_by_key.get((int(f["lap"]),int(f["sector"])),[])
        f["event_types"]=[e.get("type") for _,e in evs];f["event_ids"]=[i for i,_ in evs];f["battle_threshold"]=threshold
    return frames



def strategy_brain(s,track,lap,total_laps,position,field_size,caution=False):
    strat=s["strategy"]; left=max(0,total_laps-lap); tire=float(s["tire"]); fuel=float(s["fuel"])
    diff={"ROAD_COURSE":1.30,"SHORT_TRACK":1.12,"FLAT_OVAL":1.05,"INTERMEDIATE":1.0,"HIGH_BANK":.92,"SUPERSPEEDWAY":.78}.get(track.kind,1.0)
    score=0.0; reasons=[]
    if fuel<max(2.0,min(track.fuel_window*.30,left+1)):score+=4.5;reasons.append("fuel window")
    if tire<35:score+=3.0;reasons.append("worn tires")
    elif tire<50:score+=1.5;reasons.append("tire age")
    if strat["pit_plan"]=="SHORT":score+=.9
    elif strat["pit_plan"]=="LONG":score-=.8
    if position<=5:score-=1.35*diff;reasons.append("protect track position")
    elif position>field_size*.65:score+=.65;reasons.append("need track position")
    if caution:
        if left<=5:score-=2.2 if position<=8 else .3
        elif left<=15 and tire<65:score+=1.0
        if track.kind=="ROAD_COURSE":score-=.45
    elif left<=8:score-=1.2
    return {"pit":score>=1.55,"score":round(score,2),"reasons":reasons,"laps_left":left,"position":position}

def service_choice(s,track,lap,position):
    left=max(0,track.laps-lap)
    if s["tire"]>78 and s["fuel"]<max(3,left+1):return "FUEL_ONLY"
    if left<=18 and s["tire"]>48 and track.kind!="ROAD_COURSE":return "TWO_TIRES"
    return "FOUR_TIRES"

def service_time_for(choice,team,rng):
    base={"FUEL_ONLY":6.4,"TWO_TIRES":8.9,"FOUR_TIRES":13.5}[choice]
    crew=(team.pit-50)*(.018 if choice!="FOUR_TIRES" else .025)
    sig={"FUEL_ONLY":.25,"TWO_TIRES":.32,"FOUR_TIRES":.40}[choice]
    return max(base-1.1,base-crew+rng.gauss(0,sig))

def team_context(s,order):
    pos=next((i+1 for i,x in enumerate(order) if x["d"].id==s["d"].id),len(order))
    mates=[(i+1,x) for i,x in enumerate(order) if x["d"].team_id==s["d"].team_id and x["d"].id!=s["d"].id and not x["out"]]
    near=min(mates,key=lambda q:abs(q[0]-pos)) if mates else None
    return pos,(near[1] if near else None)

def restart_lane_choice(s,track):
    d=s["d"]
    if track.kind=="ROAD_COURSE": return "SINGLE_FILE"
    inside=d.rcr*.45+d.ctl*.35+d.con*.20
    outside=d.spd*.40+d.agg*.35+d.rcr*.25
    if track.kind in ("HIGH_BANK","SUPERSPEEDWAY"): outside+=3
    if track.kind in ("SHORT_TRACK","FLAT_OVAL"): inside+=2
    return "OUTSIDE" if outside>inside else "INSIDE"


def strategy_callout(name, reasons, decision="PIT", service_choice=None):
    reasons=reasons or []
    if "cover nearby pit call" in reasons:
        return f"{name} responds to a nearby stop and comes to pit road to cover the undercut."
    if "undercut opportunity" in reasons:
        return f"{name} pits early, attempting the undercut."
    if "overcut / clean air" in reasons and decision=="STAY_OUT":
        return f"{name} stays out to protect clean air and attempt the overcut."
    if "avoid teammate double-stack" in reasons and decision=="STAY_OUT":
        return f"{name} stays out to avoid stacking behind a teammate."
    if "late-race gamble" in reasons:
        return f"{name} makes a late-race strategy gamble."
    if "protect late track position" in reasons and decision=="STAY_OUT":
        return f"{name} stays out to protect late-race track position."
    if "fuel window" in reasons:
        return f"{name} pits as the fuel window closes."
    if "worn tires" in reasons or "tire age" in reasons:
        return f"{name} comes to pit road for fresh tires."
    if decision=="STAY_OUT":
        return f"{name} stays on track."
    if service_choice=="TWO_TIRES":
        return f"{name} takes two tires in a track-position gamble."
    if service_choice=="FUEL_ONLY":
        return f"{name} takes fuel only for a quick stop."
    return f"{name} heads to pit road."

def stable_stream(seed,label):
    n=int.from_bytes(hashlib.sha256(f"{seed}:{label}".encode()).digest()[:8],"big")
    return random.Random(n)

def broadcast_importance(e,human_id=None):
    t=e.get("type");m=e.get("metadata",{})
    if t in ("INCIDENT","CAUTION","LEAD_CHANGE","RESTART","CHECKERED","MECHANICAL"):return 100
    if e.get("driver_id")==human_id:return 90
    r=m.get("strategy_reasons") or []
    if any(x in r for x in ("undercut opportunity","cover nearby pit call","overcut / clean air","late-race gamble","avoid teammate double-stack")):return 80
    if m.get("service_choice") in ("TWO_TIRES","FUEL_ONLY"):return 75
    if t=="PASS" and (e.get("position") or 99)<=5:return 70
    return 25

def build_broadcast_feed(events,human_id=None):
    out=[]
    for e in events:
        if e.get("type")=="CAUTION_PIT_CALL" and e.get("metadata",{}).get("decision")=="STAY_OUT" and e.get("driver_id")!=human_id:continue
        if broadcast_importance(e,human_id)>=70:out.append(e)
    return out[:120]

def build_strategy_recap(events,finish):
    fp={str((x.get("driver_id") or x.get("id")) if isinstance(x,dict) else x["d"].id):
            (x.get("position",i+1) if isinstance(x,dict) else i+1) for i,x in enumerate(finish)};c=[]
    for e in events:
        m=e.get("metadata",{});r=m.get("strategy_reasons") or [];ch=m.get("service_choice")
        if r or ch:
            p=fp.get(str(e.get("driver_id")),99);w=max(0,25-p) if p<=10 else 0
            if any(x in r for x in ("undercut opportunity","cover nearby pit call","overcut / clean air","late-race gamble")):w+=12
            if ch in ("TWO_TIRES","FUEL_ONLY"):w+=8
            c.append((w,e))
    c.sort(key=lambda x:x[0],reverse=True);out=[];seen=set()
    for _,e in c:
        msg=e.get("metadata",{}).get("strategy_explanation") or e.get("message");key=(e.get("driver_id"),msg)
        if msg and key not in seen:
            seen.add(key);out.append({"lap":e.get("lap"),"sector":e.get("sector"),"driver_id":e.get("driver_id"),"message":msg})
        if len(out)>=5:break
    return out

def simulate_race(drivers,teams,track_id,seed,human_id=None,human_strategy=None,racecast=True,qualifying_grid=None,capture_telemetry=False):
    track=TRACK_LIBRARY[track_id]
    rng=random.Random(seed)
    presentation_rng=stable_stream(seed,"presentation")
    rng_stream_seeds={k:int.from_bytes(hashlib.sha256(f"{seed}:{k}".encode()).digest()[:8],"big") for k in ("pace","passing","incident","mechanical","pit","presentation")}
    team_map={t.id:t for t in teams}
    human_strategy=human_strategy or {"setup_bias":"BALANCED","driving_style":"BALANCED","pit_plan":"STANDARD"}

    # -------------------------
    # QUALIFYING
    # -------------------------
    grid=[]
    if qualifying_grid:
        driver_by_id={d.id:d for d in drivers}
        for item in sorted(qualifying_grid,key=lambda x:x["position"]):
            did=str(item["driver_id"])
            d=driver_by_id.get(did)
            if d is not None:
                grid.append((float(item["lap_time"]),d))
        if len(grid)!=len(drivers):
            raise ValueError("QUALIFYING_GRID_INCOMPLETE")
    else:
        for d in drivers:
            strat=strategy_for(d.id,human_id,human_strategy)
            q=qualify_score(d,team_map[d.team_id],track,rng,strat["setup_bias"])
            qlap=track.base_lap-(q-80)*.010+rng.gauss(0,.018 if track.kind!="ROAD_COURSE" else .035)
            grid.append((qlap,d))
        grid.sort(key=lambda x:x[0])

    # -------------------------
    # INITIAL STATE
    # -------------------------
    state={}
    for pos,(qlap,d) in enumerate(grid,1):
        t=team_map[d.team_id]
        strat=strategy_for(d.id,human_id,human_strategy)

        setup=max(-2.3,min(2.3,rng.gauss(0,1.05)+(t.handling-80)*.08))
        if strat["setup_bias"]=="SPEED":
            setup+=.30
        elif strat["setup_bias"]=="HANDLING":
            setup+=.55

        state[d.id]={
            "d":d,
            "time":(pos-1)*(.19 if track.kind!="SUPERSPEEDWAY" else .08),
            "tire":100.0,
            "fuel":track.fuel_window,
            "damage":0.0,
            "out":False,
            "laps":0,
            "led":0,
            "pits":0,
            "start":pos,
            "qlap":qlap,
            "setup":setup,
            "momentum":0.0,
            "last_pit_lap":0,
            "pit_phase":None,
            "pit_service_time":None,
            "pit_call_lap":None,
            "pit_call_sector":None,
            "strategy":strat,
            "incident":False,
            "mechanical":False,
        }

    events=[]
    structured_events=[]
    telemetry=[]
    telemetry_sector_frames=[]

    def sector_for(lap, *ids):
        total=int(lap)
        for value in ids:
            try: total+=int(value)
            except Exception: total+=sum(ord(ch) for ch in str(value))
        return (total % 3)+1

    def add_event(event_type, lap, message, driver_id=None, other_driver_id=None, position=None, severity="INFO", sector=None, metadata=None):
        structured_events.append({
            "lap":int(lap),
            "sector":int(sector or sector_for(lap, driver_id or 0, other_driver_id or 0)),
            "type":event_type,
            "driver_id":driver_id,
            "other_driver_id":other_driver_id,
            "position":position,
            "severity":severity,
            "message":message,
            "metadata":metadata or {}
        })
    if racecast:
        msg=f"Lap 1 — GREEN FLAG at {track.name}. {grid[0][1].name} leads the field from pole."
        events.append(msg)
        add_event("GREEN_FLAG",1,msg,driver_id=grid[0][1].id,position=1,sector=1)

    caution=0
    caution_count=0
    explicit_passes=0
    prev_leader=grid[0][1].id
    human_last_pos=state[human_id]["start"] if human_id in state else None

    for lap in range(1,track.laps+1):
        for _s in state.values():
            if _s.get("pit_phase")=="EXIT":
                _s["pit_phase"]=None
                _s["pit_service_time"]=None
        active=[s for s in state.values() if not s["out"]]

        # -------------------------
        # -------------------------
        # BROADCAST TELEMETRY SNAPSHOT
        # -------------------------
        if capture_telemetry:
            live_order=sorted(
                state.values(),
                key=lambda x:(x["out"], x["time"])
            )
            leader_time=next((x["time"] for x in live_order if not x["out"]),0.0)
            previous_positions={}
            if telemetry:
                previous_positions={str(c["driver_id"]):c["position"] for c in telemetry[-1]["cars"]}
            telemetry.append({
                "lap":lap,
                "flag":"CAUTION" if caution else "GREEN",
                "cars":[{
                    "driver_id":x["d"].id,
                    "name":x["d"].name,
                    "number":x["d"].number,
                    "team_id":x["d"].team_id,
                    "position":i+1,
                    "gap":round(max(0.0,x["time"]-leader_time),3) if not x["out"] else None,
                    "tire":round(x["tire"],1),
                    "fuel":round(x["fuel"],1),
                    "damage":round(x["damage"],1),
                    "pits":x["pits"],
                    "out":bool(x["out"]),
                    "pit":bool(x.get("pit",False)),
                    "laps_led":x["led"],
                    "position_delta":(previous_positions.get(str(x["d"].id),i+1)-(i+1)),
                    "lane":(
                        1 if track.kind=="ROAD_COURSE"
                        else 0 if caution
                        else ((i + lap + x["d"].number) % 3)-1
                    )
                } for i,x in enumerate(live_order)]
            })

        # CAUTION PROCEDURE — v4.6
        # -------------------------
        if caution:
            frozen=sorted(active,key=lambda s:s["time"])
            leader_time=frozen[0]["time"] if frozen else 0.0

            # Three explicit yellow sectors: field catches pace car, pit road opens,
            # then restart formation. No green-flag passing occurs.
            for sector in (1,2,3):
                if sector==1:
                    add_event("PACE_CAR",lap,"The field gathers behind the pace car.",
                              driver_id=frozen[0]["d"].id if frozen else None,position=1,sector=1,
                              severity="INFO",metadata={"frozen_order":True,"caution_remaining":caution})
                if sector==2:
                    add_event("PIT_ROAD_OPEN",lap,"Pit road is open under caution.",sector=2,severity="INFO",
                              metadata={"caution_remaining":caution})
                    # Pit/stay-out is strategy-aware. Track position is preserved among
                    # stay-outs; pitters cycle behind them according to pit service.
                    pitters=[]
                    for s in frozen:
                        strat=s["strategy"]
                        pos=frozen.index(s)+1
                        brain=strategy_brain(s,track,lap,track.laps,pos,len(frozen),True)
                        left=brain["laps_left"]
                        if left<=12 and pos>12: brain["score"]+=1.35;brain["reasons"].append("late-race gamble")
                        if pos<=4 and left<=15 and s["tire"]>55: brain["score"]-=1.0;brain["reasons"].append("protect late track position")
                        if any(p["d"].team_id==s["d"].team_id for p in pitters) and s["fuel"]>2 and s["tire"]>30:
                            brain["score"]-=1.15;brain["reasons"].append("avoid teammate double-stack")
                        brain["pit"]=brain["score"]>=1.55
                        if brain["pit"]:
                            pitters.append(s)
                            explanation=strategy_callout(s["d"].name,brain["reasons"],"PIT")
                            add_event("CAUTION_PIT_CALL",lap,explanation,
                                      driver_id=s["d"].id,sector=2,severity="INFO",
                                      metadata={"decision":"PIT","tire":round(s["tire"],1),"fuel":round(s["fuel"],1),
                                                "strategy_score":brain["score"],"strategy_reasons":brain["reasons"],
                                                "strategy_explanation":explanation,"position":pos,"laps_left":brain["laps_left"]})
                            if racecast and (s["d"].id==human_id or pos<=5): events.append(f"Lap {lap} S2 — {explanation}")
                        else:
                            explanation=strategy_callout(s["d"].name,brain["reasons"],"STAY_OUT")
                            add_event("CAUTION_PIT_CALL",lap,explanation,
                                      driver_id=s["d"].id,sector=2,severity="INFO",
                                      metadata={"decision":"STAY_OUT","tire":round(s["tire"],1),"fuel":round(s["fuel"],1),
                                                "strategy_score":brain["score"],"strategy_reasons":brain["reasons"],
                                                "strategy_explanation":explanation,"position":pos,"laps_left":brain["laps_left"]})
                            if racecast and (s["d"].id==human_id or pos<=3): events.append(f"Lap {lap} S2 — {explanation}")

                    for s in pitters:
                        t=team_map[s["d"].team_id]
                        pos=frozen.index(s)+1;choice=service_choice(s,track,lap,pos)
                        stop=service_time_for(choice,t,rng)
                        s["time"]+=stop;s["fuel"]=track.fuel_window
                        if choice=="FOUR_TIRES":s["tire"]=100.0
                        elif choice=="TWO_TIRES":s["tire"]=max(s["tire"],78.0)
                        s["pits"]+=1;s["last_pit_lap"]=lap
                        add_event("PIT_STOP",lap,f"{s['d'].name} completes a caution pit stop.",
                                  driver_id=s["d"].id,sector=2,severity="INFO",
                                  metadata={"under_caution":True,"pit_stop":s["pits"],"service_time":round(stop,2),
                                            "sector_authoritative":True,"service_choice":choice})

                # Yellow pace and conservation are sector-authoritative.
                for s in active:
                    s["time"] += (track.base_lap*1.65)/3.0
                    s["fuel"] -= .35/3.0
                    s["tire"]=max(0,s["tire"]-.12/3.0)

                if capture_telemetry:
                    yorder=sorted([s for s in state.values() if not s["out"]],key=lambda s:s["time"])
                    ybase=yorder[0]["time"] if yorder else 0.0
                    telemetry_sector_frames.append({
                        "lap":lap,"sector":sector,"progress":round((lap-1)+(sector-1)/3.0,4),
                        "flag":"CAUTION","restart_formation":bool(sector==3 and caution==1),
                        "cars":[{
                            "driver_id":x["d"].id,"name":x["d"].name,"number":x["d"].number,"team_id":x["d"].team_id,
                            "position":j+1,"gap":round(max(0.0,x["time"]-ybase),3),"tire":round(x["tire"],1),
                            "fuel":round(x["fuel"],1),"damage":round(x["damage"],1),"pits":x["pits"],"out":False,
                            "pit":False,"pit_phase":None,"pit_service_time":None,"laps_led":x["led"],"lane":0,
                            "restart_row":((j//2)+1) if sector==3 and caution==1 else None,
                            "restart_lane":restart_lane_choice(x,track) if sector==3 and caution==1 else None
                        } for j,x in enumerate(yorder)]
                    })

            for s in active:
                s["laps"] += 1

            caution-=1
            if caution==0:
                order=sorted([s for s in state.values() if not s["out"]],key=lambda s:s["time"])
                base=order[0]["time"] if order else 0.0
                for k,s in enumerate(order):
                    s["time"]=base+k*track.restart_gap
                leader_pref=restart_lane_choice(order[0],track) if order else None
                msg=f"Lap {lap} — GREEN FLAG restart. The field is double-file."
                if order and leader_pref not in (None,"SINGLE_FILE"):
                    msg+=f" {order[0]['d'].name} prefers the {leader_pref.lower()} lane."
                if racecast: events.append(msg)
                add_event("RESTART",lap,msg,driver_id=order[0]["d"].id if order else None,
                          position=1,sector=3,severity="INFO",
                          metadata={"double_file":True,"rows":(len(order)+1)//2,
                                    "leader_lane_preference":restart_lane_choice(order[0],track) if order else None,
                                    "second_lane_preference":restart_lane_choice(order[1],track) if len(order)>1 else None})
            continue

        # -------------------------
        # GREEN FLAG PIT DECISIONS NOW RESOLVE INSIDE SECTORS
        # -------------------------
        # -------------------------
        # AUTHORITATIVE SECTOR PACE / PASSING
        # -------------------------
        # v4.2: green-flag race time now advances in three sectors. Passes resolve
        # between sector checkpoints, so sector state participates in the race itself.
        for sector in (1,2,3):
            order=sorted([s for s in state.values() if not s["out"]],key=lambda s:s["time"])
            rank={s["d"].id:i for i,s in enumerate(order)}

            # PIT CALL: strategy evaluates the car throughout the lap, but green-flag
            # entry is committed in Sector 3. This creates a real call -> entry ->
            # service -> exit sequence in authoritative race time.
            if sector==2:
                for s in order:
                    if s.get("pit_phase") is not None: continue
                    strat=s["strategy"]
                    tire_threshold=18 if track.kind!="SHORT_TRACK" else 22
                    fuel_threshold=2.5
                    if strat["pit_plan"]=="SHORT":
                        tire_threshold+=10; fuel_threshold+=4
                    elif strat["pit_plan"]=="LONG":
                        tire_threshold-=7; fuel_threshold-=1.0
                    pos=rank.get(s["d"].id,0)+1
                    brain=strategy_brain(s,track,lap,track.laps,pos,len(order),False)
                    _,mate=team_context(s,order)
                    nearby=[x for x in order[max(0,pos-4):min(len(order),pos+3)] if x["d"].id!=s["d"].id and x.get("pit_phase")=="CALLED"]
                    if nearby and s["tire"]<68: brain["score"]+=.75;brain["reasons"].append("cover nearby pit call")
                    if pos>5 and s["tire"]<55 and track.kind!="SUPERSPEEDWAY": brain["score"]+=.45;brain["reasons"].append("undercut opportunity")
                    if pos<=5 and track.kind in ("ROAD_COURSE","SHORT_TRACK","FLAT_OVAL") and s["tire"]>42: brain["score"]-=.55;brain["reasons"].append("overcut / clean air")
                    if mate is not None and mate.get("pit_phase")=="CALLED" and s["fuel"]>1.5: brain["score"]-=1.35;brain["reasons"].append("avoid teammate double-stack")
                    brain["pit"]=brain["score"]>=1.55
                    if lap<track.laps-3 and ((s["fuel"]<fuel_threshold or s["tire"]<tire_threshold) or brain["pit"]):
                        s["pit_phase"]="CALLED"
                        s["pit_call_lap"]=lap;s["pit_call_sector"]=sector
                        explanation=strategy_callout(s["d"].name,brain["reasons"],"PIT")
                        add_event("PIT_CALL",lap,explanation,
                                  driver_id=s["d"].id,sector=2,severity="INFO",
                                  metadata={"pit_plan":strat["pit_plan"],"tire":round(s["tire"],1),"fuel":round(s["fuel"],1),
                                            "strategy_score":brain["score"],"strategy_reasons":brain["reasons"],
                                            "strategy_explanation":explanation,"position":pos,"laps_left":brain["laps_left"]})
                        if racecast and (s["d"].id==human_id or pos<=5): events.append(f"Lap {lap} S2 — {explanation}")

            if sector==3:
                for s in order:
                    if s.get("pit_phase")!="CALLED": continue
                    d=s["d"]; t=team_map[d.team_id]; strat=s["strategy"]
                    lane_loss={"INTERMEDIATE":20.4,"SUPERSPEEDWAY":27.0,"SHORT_TRACK":13.2,
                               "FLAT_OVAL":18.2,"HIGH_BANK":19.4,"ROAD_COURSE":23.5}[track.kind]
                    entry_loss=lane_loss*.43
                    s["time"]+=entry_loss
                    s["pit_phase"]="ENTRY"
                    add_event("PIT_ENTRY",lap,f"{d.name} enters pit road.",driver_id=d.id,sector=3,severity="INFO",
                              metadata={"entry_loss":round(entry_loss,2),"pit_stop":s["pits"]+1})

                    current_pos=rank.get(d.id,len(order)-1)+1
                    choice=service_choice(s,track,lap,current_pos)
                    service=service_time_for(choice,t,rng)
                    if strat["pit_plan"]=="SHORT": service-=.05
                    elif strat["pit_plan"]=="LONG": service+=.10
                    s["time"]+=service;s["pit_service_time"]=service;s["pit_phase"]="SERVICE"
                    service_msg=strategy_callout(d.name,[],"PIT",choice)
                    add_event("PIT_SERVICE",lap,service_msg,
                              driver_id=d.id,sector=3,severity="INFO",
                              metadata={"service_time":round(service,2),"pit_stop":s["pits"]+1,
                                        "service_choice":choice,"strategy_explanation":service_msg})
                    if racecast and (d.id==human_id or current_pos<=5): events.append(f"Lap {lap} S3 — {service_msg}")
                    exit_loss=lane_loss*.57;s["time"]+=exit_loss;s["fuel"]=track.fuel_window
                    if choice=="FOUR_TIRES": s["tire"]=100.0
                    elif choice=="TWO_TIRES": s["tire"]=max(s["tire"],78.0)
                    s["pits"]+=1;s["last_pit_lap"]=lap
                    s["pit_phase"]="EXIT"
                    add_event("PIT_STOP",lap,f"{d.name} completes a green-flag pit stop.",
                              driver_id=d.id,sector=3,severity="INFO",
                              metadata={"under_caution":False,"pit_stop":s["pits"],
                                        "service_time":round(service,2),"entry_loss":round(entry_loss,2),
                                         "exit_loss":round(exit_loss,2),"sector_authoritative":True,"service_choice":choice})
                    if racecast and (d.id==human_id or presentation_rng.random()<.05):
                        events.append(f"Lap {lap} S3 — {d.name} completes a green-flag pit stop.")

            for s in order:
                d=s["d"]; t=team_map[d.team_id]; strat=s["strategy"]
                skill=race_skill(d,t,track,strat["setup_bias"])
                base=track.base_lap-(skill-80)*(.0105 if track.kind!="ROAD_COURSE" else .025)

                tire_age=100-s["tire"]
                wear_mod=1-(d.tir-50)/440
                if strat["driving_style"]=="AGGRESSIVE": wear_mod*=1.06
                elif strat["driving_style"]=="CONSERVATIVE": wear_mod*=.95
                if strat["pit_plan"]=="LONG": wear_mod*=.96

                fall=(tire_age/100)**1.72*track.tire_penalty*(1-(d.tir-50)/360)
                sigma=(.19 if track.kind!="ROAD_COURSE" else .34)-(d.con-75)*.0017
                if strat["driving_style"]=="AGGRESSIVE": sigma*=1.12
                elif strat["driving_style"]=="CONSERVATIVE": sigma*=.88
                sigma=max(.085,sigma)

                traffic=0.0
                i=rank[d.id]
                if i>0:
                    ahead=order[i-1]
                    gap=max(0,s["time"]-ahead["time"])
                    if gap<track.passing_range*1.7:
                        traffic=max(0,.065-(d.rcr-75)*.002)

                setup_effect=-s["setup"]*(.010 if track.kind!="ROAD_COURSE" else .022)
                damage=s["damage"]*(.019 if track.kind!="ROAD_COURSE" else .035)
                style_pace=-.012 if strat["driving_style"]=="AGGRESSIVE" else (.018 if strat["driving_style"]=="CONSERVATIVE" else 0)

                # Divide pace and variance across sectors. Independent sector noise sums
                # to approximately the old lap-level variance.
                sector_time=(base+fall+traffic+setup_effect+damage+style_pace-s["momentum"]*.005)/3.0
                sector_time+=rng.gauss(0,sigma/(3**.5))
                s["time"]+=sector_time

            # Passing resolves after every sector instead of once after the full lap.
            order=sorted([s for s in state.values() if not s["out"]],key=lambda s:s["time"])
            for i in range(1,len(order)):
                ch,df=order[i],order[i-1]
                gap=ch["time"]-df["time"]
                if 0<=gap<track.passing_range:
                    a,b=ch["d"],df["d"]
                    astrat=ch["strategy"]; bstrat=df["strategy"]
                    attack=(
                        (a.rcr-b.rcr)*.010+(a.ctl-b.ctl)*.006+(a.spd-b.spd)*.006+
                        (a.agg-50)*.0022+(a.drf-b.drf)*(.008 if track.kind=="SUPERSPEEDWAY" else .001)
                    )
                    if astrat["driving_style"]=="AGGRESSIVE": attack+=.025
                    elif astrat["driving_style"]=="CONSERVATIVE": attack-=.025
                    defend=(b.rcr-80)*.004+(b.ctl-80)*.003+.045
                    if bstrat["driving_style"]=="CONSERVATIVE": defend+=.010

                    # Scale old lap opportunity across three sector checks.
                    lap_prob=max(.018,min(.24,track.base_pass_prob+attack-defend))
                    prob=1-(1-lap_prob)**(1/3)
                    if rng.random()<prob:
                        ch["time"],df["time"]=df["time"]-.012,ch["time"]+.035
                        explicit_passes+=1
                        pass_position=max(1,i)
                        add_event("PASS",lap,f"{a.name} completes a pass on {b.name}.",
                                  driver_id=a.id,other_driver_id=b.id,position=pass_position,
                                  sector=sector,metadata={"gap_before":round(gap,3),"authoritative_sector":True})
                        if racecast and (a.id==human_id or b.id==human_id or rng.random()<.025):
                            events.append(f"Lap {lap} S{sector} — {a.name} completes a pass on {b.name}.")

            # AUTHORITATIVE SECTOR INCIDENT / CAUTION CHECK
            # Risk is divided across three sectors so the overall race frequency stays
            # near the calibrated lap-level baseline while timing becomes exact.
            if caution==0 and lap<track.laps-2:
                candidates=[]
                live_order=sorted([x for x in state.values() if not x["out"]],key=lambda x:x["time"])
                live_rank={x["d"].id:i for i,x in enumerate(live_order)}
                for s in live_order:
                    d=s["d"]; strat=s["strategy"]
                    i=live_rank[d.id]
                    traffic_risk=1.0
                    other=None
                    if i>0:
                        ahead=live_order[i-1]
                        gap=max(0.0,s["time"]-ahead["time"])
                        if gap<track.passing_range*.65:
                            traffic_risk=1.35
                            other=ahead
                    lap_risk=(
                        .00050*track.caution_mult+
                        max(0,d.agg-70)*.000025*track.caution_mult+
                        max(0,80-d.ctl)*.000024*track.caution_mult+
                        max(0,80-d.con)*.000020*track.caution_mult
                    )
                    if strat["driving_style"]=="AGGRESSIVE": lap_risk*=1.35
                    elif strat["driving_style"]=="CONSERVATIVE": lap_risk*=.72
                    if s["tire"]<10: lap_risk*=1.45
                    lap_risk*=traffic_risk
                    sector_risk=1-(1-min(.20,lap_risk))**(1/3)
                    if rng.random()<sector_risk:
                        candidates.append((s,other,gap if other is not None else None))

                if candidates:
                    s,other,incident_gap=rng.choice(candidates); d=s["d"]
                    caution_count+=1
                    caution=3 if track.kind!="ROAD_COURSE" else 2
                    s["incident"]=True
                    terminal=.15+(.03 if s["strategy"]["driving_style"]=="AGGRESSIVE" else 0)
                    if rng.random()<terminal:
                        s["out"]=True;s["damage"]=100
                        msg=f"Lap {lap} S{sector} — CAUTION. {d.name} is out after an incident."
                        add_event("INCIDENT",lap,msg,driver_id=d.id,
                                  other_driver_id=other["d"].id if other else None,
                                  severity="MAJOR",sector=sector,
                                  metadata={"terminal":True,"damage":100,"authoritative_sector":True,
                                            "traffic_contact":bool(other),"gap_before":round(incident_gap,3) if incident_gap is not None else None})
                        add_event("CAUTION",lap,msg,driver_id=d.id,severity="MAJOR",sector=sector,
                                  metadata={"authoritative_sector":True,"freeze_order":True})
                    else:
                        damage=rng.uniform(7,27);s["damage"]+=damage;s["time"]+=rng.uniform(2,7)
                        msg=f"Lap {lap} S{sector} — CAUTION. {d.name} has trouble and takes damage."
                        add_event("INCIDENT",lap,msg,driver_id=d.id,
                                  other_driver_id=other["d"].id if other else None,
                                  severity="MEDIUM",sector=sector,
                                  metadata={"terminal":False,"damage":round(damage,1),"authoritative_sector":True,
                                            "traffic_contact":bool(other),"gap_before":round(incident_gap,3) if incident_gap is not None else None})
                        add_event("CAUTION",lap,msg,driver_id=d.id,severity="MEDIUM",sector=sector,
                                  metadata={"authoritative_sector":True,"freeze_order":True})
                    if racecast: events.append(msg)

            # Sector checkpoint comes from live authoritative state.
            if capture_telemetry:
                live=sorted(state.values(),key=lambda s:(s["out"],s["time"]))
                leader_time=next((x["time"] for x in live if not x["out"]),0.0)
                telemetry_sector_frames.append({
                    "lap":lap,"sector":sector,
                    "progress":round((lap-1)+(sector-1)/3.0,4),
                    "flag":"GREEN",
                    "cars":[{
                        "driver_id":x["d"].id,"name":x["d"].name,"number":x["d"].number,"team_id":x["d"].team_id,
                        "position":j+1,"gap":round(max(0.0,x["time"]-leader_time),3) if not x["out"] else None,
                        "tire":round(x["tire"],1),"fuel":round(x["fuel"],1),"damage":round(x["damage"],1),
                        "pits":x["pits"],"out":bool(x["out"]),"pit":bool(x.get("pit_phase")),"pit_phase":x.get("pit_phase"),
                        "pit_service_time":round(x["pit_service_time"],2) if x.get("pit_service_time") is not None else None,
                        "laps_led":x["led"],
                        "lane":1 if track.kind=="ROAD_COURSE" else ((j+lap+sector+x["d"].number)%3)-1
                    } for j,x in enumerate(live)]
                })
        # Tire/fuel/momentum are consumed after the three authoritative sectors.
        for s in [x for x in state.values() if not x["out"]]:
            d=s["d"]; strat=s["strategy"]
            wear_mod=1-(d.tir-50)/440
            if strat["driving_style"]=="AGGRESSIVE": wear_mod*=1.06
            elif strat["driving_style"]=="CONSERVATIVE": wear_mod*=.95
            if strat["pit_plan"]=="LONG": wear_mod*=.96
            s["fuel"]-=1.0
            wear=track.tire_wear*wear_mod+rng.uniform(-.025,.025)
            s["tire"]=max(0,s["tire"]-wear)
            s["laps"]+=1
            s["momentum"]=max(-2,min(2,s["momentum"]*.82+rng.gauss(0,.20)))

        # -------------------------
        # INCIDENTS / CAUTIONS RESOLVE INSIDE SECTORS (v4.5)
        # -------------------------
        # -------------------------
        # MECHANICAL RELIABILITY
        # -------------------------
        for s in [x for x in state.values() if not x["out"]]:
            rel=team_map[s["d"].team_id].reliability
            perlap=max(.000012,.00016-(rel-75)*.000011)
            if track.kind=="ROAD_COURSE":
                perlap*=1.4
            if rng.random()<perlap:
                s["out"]=True
                s["mechanical"]=True
                msg=f"Lap {lap} — {s['d'].name} retires with a mechanical problem."
                add_event("MECHANICAL",lap,msg,driver_id=s["d"].id,severity="MAJOR",metadata={"terminal":True})
                if racecast:
                    events.append(msg)

        # -------------------------
        # LEADER / HUMAN POSITION
        # -------------------------
        order=sorted(state.values(),key=lambda s:(s["out"],-s["laps"],s["time"]))
        if order and not order[0]["out"]:
            order[0]["led"]+=1
            lid=order[0]["d"].id
            if lid!=prev_leader and lap>2 and not caution:
                old_leader=prev_leader
                msg=f"Lap {lap} — {order[0]['d'].name} takes the lead."
                add_event("LEAD_CHANGE",lap,msg,driver_id=lid,other_driver_id=old_leader,position=1,sector=sector_for(lap,lid,old_leader))
                if racecast:
                    events.append(msg)
            prev_leader=lid

        if human_id in state and not state[human_id]["out"] and racecast:
            active_order=[s for s in order if not s["out"]]
            human_pos=next((i+1 for i,s in enumerate(active_order) if s["d"].id==human_id),human_last_pos)
            if human_last_pos is not None and human_pos is not None:
                change=human_last_pos-human_pos
                if abs(change)>=4:
                    direction="climbs" if change>0 else "falls"
                    events.append(f"Lap {lap} — {state[human_id]['d'].name} {direction} to P{human_pos}.")
                human_last_pos=human_pos

    finish=sorted(state.values(),key=lambda s:(s["out"],-s["laps"],s["time"]))
    if capture_telemetry:
        leader_time=finish[0]["time"] if finish else 0.0
        telemetry.append({
            "lap":track.laps,
            "flag":"CHECKERED",
            "cars":[{
                "driver_id":x["d"].id,
                "name":x["d"].name,
                "number":x["d"].number,
                "team_id":x["d"].team_id,
                "position":i+1,
                "gap":round(max(0.0,x["time"]-leader_time),3) if not x["out"] else None,
                "tire":round(x["tire"],1),
                "fuel":round(x["fuel"],1),
                "damage":round(x["damage"],1),
                "pits":x["pits"],
                "out":bool(x["out"]),
                "pit":bool(x.get("pit",False)),
                "laps_led":x["led"],
                "position_delta":0,
                "lane":0
            } for i,x in enumerate(finish)]
        })
    if racecast:
        msg=f"Lap {track.laps} — CHECKERED FLAG. {finish[0]['d'].name} wins at {track.name}."
        events.append(msg)
        add_event("CHECKERED",track.laps,msg,driver_id=finish[0]["d"].id,position=1,sector=3,severity="HIGHLIGHT")

    sector_telemetry=[]
    if capture_telemetry:
        sector_telemetry=annotate_authoritative_sectors(telemetry_sector_frames,structured_events,track)
        final_live=telemetry[-1]["cars"] if telemetry else []
        sector_telemetry.append({
            "lap":track.laps,"sector":3,"progress":float(track.laps),"flag":"CHECKERED",
            "event_types":["CHECKERED"],"event_ids":[],"battle_threshold":sector_telemetry[-1].get("battle_threshold",.24) if sector_telemetry else .24,
            "cars":[{**c,"interval_leader":c.get("gap"),"interval_ahead":None,"battle_id":None,"battle_with":None,
                     "battle_sectors":0,"pass_attempt":False,"pit_phase":None,"pit_service_time":None,
                     "restart_row":None,"restart_lane":None} for c in final_live]
        })

    broadcast_feed=build_broadcast_feed(structured_events,human_id)
    strategy_recap=build_strategy_recap(structured_events,finish)
    return {
        "track":track,
        "grid":grid,
        "finish":finish,
        "events":events,
        "structured_events":structured_events,
        "broadcast_feed":broadcast_feed,
        "strategy_recap":strategy_recap,
        "rng_stream_seeds":rng_stream_seeds,
        "telemetry":telemetry,
        "sector_telemetry":sector_telemetry,
        "meta":{"cautions":caution_count,"passes":explicit_passes}
    }
