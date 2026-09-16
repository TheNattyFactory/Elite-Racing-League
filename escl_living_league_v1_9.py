#!/usr/bin/env python3
"""Elite Stock Car League — ESCL v1.9 Living League Prototype
Persistent CPU drivers, three-car rosters, offseason roster movement, retirements,
rookies, teammate comparisons, contracts, and Silly Season news.
Standard library only.
"""

from dataclasses import dataclass, asdict, field
from pathlib import Path
from collections import defaultdict
import argparse, json, random

# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class Team:
    id:int
    name:str
    engine:int
    aero:int
    handling:int
    reliability:int
    pit:int
    performance_delta:float=0.0
    prestige:float=50.0
    trend:str="STABLE"

@dataclass
class Driver:
    id:int
    name:str
    number:int
    age:int
    team_id:int
    role:str
    contract_years:int
    salary_xp:float
    attrs:dict
    human:bool=False
    xp:float=0.0
    career_starts:int=0
    career_wins:int=0
    career_top5:int=0
    career_top10:int=0
    career_poles:int=0
    championships:int=0
    playoff_appearances:int=0
    seasons:int=0
    retired:bool=False
    season_history:list=field(default_factory=list)

    # v1.8 hidden career-arc fields.
    potential:int=80
    peak_start:int=26
    peak_end:int=31
    dev_type:str="NORMAL"
    form:float=0.0

@dataclass
class Track:
    id:str
    name:str
    kind:str
    laps:int
    base_lap:float
    tire_wear:float
    tire_penalty:float
    fuel_window:float
    caution_mult:float
    spd_w:float
    rcr_w:float
    ctl_w:float
    con_w:float
    tir_w:float
    drf_w:float
    agg_w:float

# ============================================================
# CONTENT
# ============================================================

TEAMS=[
    Team(1,"Blue Ridge Motorsports",81,81,80,82,80,0,66),
    Team(2,"Peachtree Racing",79,80,82,80,83,0,62),
    Team(3,"Ironhorse Motorsports",83,79,78,81,79,0,64),
    Team(4,"Liberty Speedworks",80,82,79,79,81,0,63),
    Team(5,"Great Lakes Racing",78,79,81,84,82,0,59),
    Team(6,"Lone Star Competition",82,78,80,80,78,0,61),
    Team(7,"Appalachian Racing",77,78,79,83,80,0,52),
    Team(8,"Pacific Coast Motorsports",80,81,81,78,81,0,60),
]
TM={t.id:t for t in TEAMS}

TRACKS=[
Track("CAR","Carolina Motor Speedway","INTERMEDIATE",100,30.15,.91,1.48,40,1.00,.42,.22,.14,.07,.07,.03,.05),
Track("GULF","Gulf Coast Speedway","SUPERSPEEDWAY",80,47.80,.42,.55,34,1.35,.20,.18,.06,.06,.04,.34,.12),
Track("PINE","Pine Ridge Raceway","SHORT_TRACK",160,20.80,.78,1.05,52,1.75,.16,.26,.22,.09,.07,.02,.18),
Track("MID","Midland Raceway","FLAT_OVAL",120,27.60,.72,1.10,44,1.15,.20,.20,.28,.14,.10,.02,.06),
Track("RIDGE","Ridgeview Speedway","HIGH_BANK",110,26.35,.88,1.38,42,1.20,.32,.14,.20,.07,.16,.06,.05),
Track("COAST","Coastal Grand Prix","ROAD_COURSE",55,91.50,.62,.88,22,.90,.18,.25,.30,.14,.06,.00,.07),
]
TB={t.id:t for t in TRACKS}

REGULAR=["CAR","GULF","PINE","MID","RIDGE","COAST","CAR","PINE","RIDGE","MID",
         "GULF","COAST","CAR","MID","PINE","RIDGE","GULF","CAR","COAST","MID"]
PLAYOFFS=["PINE","GULF","MID","RIDGE","COAST","CAR"]
POINTS=[50,44,40,37,35,33,31,29,27,25,23,21,19,17,15,13,12,11,10,9,8,7,6,5]
ROLES=["LEAD","SECOND","THIRD"]

BASE_NAMES=[
"Marcus Bell","Evan Reynolds","Dylan Cross","Cole Mercer","Nate Holloway","Jace Walker",
"Ryan Maddox","Caleb Stone","Luke Bennett","Mason Reed","Tyler Vaughn","Grant Keller",
"Austin Price","Logan Hayes","Cody Barrett","Blake Turner","Noah Pierce","Wyatt Brooks",
"Gavin Cole","Trevor Lane","Hunter Dean","Chase Monroe","Derek West"
]


CANONICAL_CPUS=[
    # name, number, team_id, SPD,RCR,QLF,CON,TIR,DRF,CTL,AGG
    ("Marcus Bell",7,1,88,82,78,74,70,76,80,72),
    ("Evan Reynolds",18,1,80,88,76,86,82,72,83,62),

    ("Dylan Cross",11,2,78,84,80,82,90,69,84,60),
    ("Cole Mercer",24,2,85,80,82,70,72,76,78,88),
    ("Nate Holloway",33,2,76,82,74,91,86,68,80,55),

    ("Jace Walker",4,3,84,86,79,75,74,82,85,78),
    ("Ryan Maddox",17,3,79,76,90,81,75,72,77,65),
    ("Caleb Stone",29,3,81,83,75,84,79,77,86,66),

    ("Luke Bennett",6,4,86,77,85,72,68,74,80,82),
    ("Mason Reed",41,4,77,89,73,88,84,71,82,58),
    ("Tyler Vaughn",88,4,83,81,80,76,77,75,79,75),

    ("Grant Keller",15,5,80,84,77,85,88,70,83,61),
    ("Austin Price",22,5,87,79,88,73,71,76,81,77),
    ("Logan Hayes",9,5,78,82,74,90,85,73,80,57),

    ("Cody Barrett",44,6,84,85,81,74,73,80,86,84),
    ("Blake Turner",51,6,79,78,86,82,76,69,78,64),
    ("Noah Pierce",3,6,82,87,76,86,81,75,84,63),

    # Wyatt Brooks is omitted to make room for the human rookie at Appalachian.
    ("Gavin Cole",12,7,85,82,83,71,70,84,80,86),
    ("Trevor Lane",67,7,81,85,78,80,78,79,85,72),

    ("Hunter Dean",31,8,83,76,89,74,72,71,79,79),
    ("Chase Monroe",73,8,77,83,75,87,86,74,81,59),
    ("Derek West",95,8,86,81,84,73,75,78,83,81),
]

ROOKIE_FIRST=["Aiden","Brody","Camden","Eli","Grayson","Hudson","Isaac","Jonah","Landon","Micah",
              "Nolan","Owen","Parker","Rhett","Silas","Tate","Wesley","Zane"]
ROOKIE_LAST=["Adams","Bishop","Carter","Dalton","Ellis","Foster","Griffin","Hart","Irwin","Justice",
             "King","Lawson","Miller","Nash","Owens","Pruitt","Quinn","Rhodes","Sawyer","Tucker"]

# ============================================================
# HELPERS
# ============================================================

def team_equipment(team):
    return round(.36*team.engine+.32*team.aero+.32*team.handling+team.performance_delta,2)

def attr(rng,mean,sd=4):
    return max(58,min(95,int(round(rng.gauss(mean,sd)))))

def make_attrs(rng,mean):
    return {
        "SPD":attr(rng,mean),"RCR":attr(rng,mean),"QLF":attr(rng,mean,5),"CON":attr(rng,mean),
        "TIR":attr(rng,mean),"DRF":attr(rng,mean,5),"CTL":attr(rng,mean),"AGG":attr(rng,mean,6)
    }

def development_profile(rng, age, base_mean):
    """Generate hidden career arc.
    Potential caps long-term growth; peak window controls when decline begins.
    """
    roll=rng.random()
    if roll<.08:
        dev="GENERATIONAL"
        potential=rng.randint(max(88,base_mean+6),96)
        peak_start=rng.randint(24,27)
        peak_end=rng.randint(31,34)
    elif roll<.25:
        dev="HIGH"
        potential=rng.randint(max(84,base_mean+4),92)
        peak_start=rng.randint(25,28)
        peak_end=rng.randint(30,33)
    elif roll<.82:
        dev="NORMAL"
        potential=rng.randint(max(80,base_mean+2),88)
        peak_start=rng.randint(26,29)
        peak_end=rng.randint(30,32)
    else:
        dev="EARLY_PEAK"
        potential=rng.randint(max(78,base_mean),86)
        peak_start=rng.randint(23,26)
        peak_end=rng.randint(28,30)

    # Existing older veterans should not get absurdly late peaks.
    if age>=30:
        peak_start=min(peak_start,age-1)
        peak_end=max(age, min(peak_end,age+2))

    return potential,peak_start,peak_end,dev

def rookie_profile(rng):
    """Rookie class contains a mix of ordinary prospects and rare stars."""
    roll=rng.random()
    if roll<.05:
        return rng.randint(76,79), rng.randint(91,96), "GENERATIONAL"
    if roll<.22:
        return rng.randint(74,78), rng.randint(86,92), "HIGH"
    if roll<.78:
        return rng.randint(71,76), rng.randint(81,88), "NORMAL"
    return rng.randint(69,74), rng.randint(78,84), "EARLY_PEAK"

def overall(d):
    a=d.attrs
    return round((a["SPD"]*.18+a["RCR"]*.17+a["QLF"]*.09+a["CON"]*.12+a["TIR"]*.11+
                  a["DRF"]*.08+a["CTL"]*.16+a["AGG"]*.09),1)

def race_skill(d,track):
    a=d.attrs
    driver=(a["SPD"]*track.spd_w+a["RCR"]*track.rcr_w+a["CTL"]*track.ctl_w+
            a["CON"]*track.con_w+a["TIR"]*track.tir_w+a["DRF"]*track.drf_w+
            a["AGG"]*track.agg_w)
    return .76*driver + .24*team_equipment(TM[d.team_id])

def q_skill(d,track):
    a=d.attrs
    return .50*a["QLF"]+.20*a["SPD"]+.15*a["CTL"]+.15*team_equipment(TM[d.team_id])

def role_salary(role,team_id):
    base=.25
    eq=team_equipment(TM[team_id])
    if eq>=80: base+=.03
    if eq>=82: base+=.02
    base += {"LEAD":.08,"SECOND":.04,"THIRD":0}[role]
    return round(min(.45,base),2)

def xp_for_finish(finish,pole,led,salary):
    x=salary+.25
    if finish<=10:x+=.25
    if finish<=5:x+=.25
    if finish==1:x+=.50
    if pole:x+=.10
    if led:x+=.10
    return round(x,2)

# ============================================================
# INITIAL LEAGUE
# ============================================================

def build_initial_league(seed=1600):
    rng=random.Random(seed)
    drivers=[]
    did=1

    for row in CANONICAL_CPUS:
        name,number,team_id,spd,rcr,qlf,con,tir,drf,ctl,agg=row
        attrs={"SPD":spd,"RCR":rcr,"QLF":qlf,"CON":con,"TIR":tir,"DRF":drf,"CTL":ctl,"AGG":agg}
        base_mean=sum(attrs.values())/8

        # Canonical veterans keep their identity but receive a hidden career arc.
        age=rng.randint(22,32)
        pot,pk1,pk2,dev=development_profile(rng,age,int(round(base_mean)))

        # Prevent established Genesis drivers from receiving absurd ceilings far
        # beyond the ratings that define their identity.
        current=max(attrs.values())
        pot=max(int(round(base_mean))+2,min(93,pot))
        pot=max(pot,min(91,current))

        slot=len([d for d in drivers if d.team_id==team_id])
        role=ROLES[min(slot,2)]

        drivers.append(Driver(
            did,name,number,age,team_id,role,
            rng.choice([1,2]),role_salary(role,team_id),attrs,
            potential=pot,peak_start=pk1,peak_end=pk2,dev_type=dev
        ))
        did+=1

    human=Driver(
        999,"Genesis Driver",28,18,7,"THIRD",1,.25,
        {"SPD":78,"RCR":77,"QLF":75,"CON":73,"TIR":76,"DRF":73,"CTL":77,"AGG":73},
        human=True,xp=14.0,
        potential=90,peak_start=26,peak_end=32,dev_type="HIGH"
    )
    drivers.append(human)

    # Defensive guarantee: exactly three cars per team.
    for tid in range(1,9):
        roster=[d for d in drivers if d.team_id==tid]
        while len(roster)<3:
            name=f"{rng.choice(ROOKIE_FIRST)} {rng.choice(ROOKIE_LAST)}"
            role=ROLES[len(roster)]
            age=18+rng.randint(0,2)
            start_mean,potential,dev=rookie_profile(rng)
            if dev=="GENERATIONAL":
                pk1,pk2=rng.randint(23,26),rng.randint(31,34)
            elif dev=="HIGH":
                pk1,pk2=rng.randint(24,27),rng.randint(30,33)
            elif dev=="EARLY_PEAK":
                pk1,pk2=rng.randint(22,25),rng.randint(27,30)
            else:
                pk1,pk2=rng.randint(25,28),rng.randint(29,32)

            rookie=Driver(
                did,name,rng.randint(2,99),age,tid,role,1,role_salary(role,tid),
                make_attrs(rng,start_mean),
                potential=potential,peak_start=pk1,peak_end=pk2,dev_type=dev
            )
            drivers.append(rookie)
            roster.append(rookie)
            did+=1

    normalize_roles(drivers)
    return drivers,human


# ============================================================
# RACING
# ============================================================

def simulate_race(drivers,track,seed,playoff=False):
    rng=random.Random(seed)
    qual=[]
    for d in drivers:
        q=q_skill(d,track)+rng.gauss(0,4.0)
        qual.append((q,d))
    qual.sort(key=lambda x:x[0],reverse=True)
    pole_id=qual[0][1].id

    scores=[]
    for q,d in qual:
        base=race_skill(d,track)
        # Race-weekend setup + controlled race variance.
        weekend=rng.gauss(0,2.6)
        consistency=(d.attrs["CON"]-75)*.06
        tire=(d.attrs["TIR"]-75)*.035
        risk=max(0,d.attrs["AGG"]-82)*.05
        score=base+weekend+consistency+tire-risk+d.form*.35

        if playoff:
            # Championship pressure/form: controlled and temporary.
            score += rng.gauss(0,0.85) + (d.attrs["CON"]-78)*.018

        # Rare incident/mechanical penalty.
        incident_risk=.018*track.caution_mult + max(0,d.attrs["AGG"]-78)*.0011 - max(0,d.attrs["CTL"]-78)*.0007
        if rng.random()<max(.006,incident_risk):
            score-=rng.uniform(8,20)

        mech=max(.003,.014-(TM[d.team_id].reliability-78)*.0012)
        if rng.random()<mech:
            score-=rng.uniform(12,25)

        scores.append((score,d))

    scores.sort(key=lambda x:x[0],reverse=True)
    finish=[d for _,d in scores]
    leader_candidates=finish[:6]
    led=set()
    for d in leader_candidates:
        chance=.55 if d==finish[0] else .25
        if rng.random()<chance:
            led.add(d.id)
    return pole_id,finish,led

def run_season(drivers,season_no,seed):
    rng=random.Random(seed)
    standings={d.id:{"points":0,"wins":0,"top5":0,"top10":0,"poles":0} for d in drivers}
    race_log=[]

    for rnd,tid in enumerate(REGULAR,1):
        track=TB[tid]
        pole,finish,led=simulate_race(drivers,track,rng.randrange(1,10**9),False)
        for pos,d in enumerate(finish,1):
            st=standings[d.id]
            bonus=(2 if pos==1 else 0)+(1 if d.id in led else 0)+(1 if d.id==pole else 0)
            st["points"]+=POINTS[pos-1]+bonus
            st["wins"]+=(pos==1); st["top5"]+=(pos<=5); st["top10"]+=(pos<=10); st["poles"]+=(d.id==pole)
            d.career_starts+=1; d.career_wins+=(pos==1); d.career_top5+=(pos<=5); d.career_top10+=(pos<=10); d.career_poles+=(d.id==pole)
            if d.human:
                d.xp+=xp_for_finish(pos,d.id==pole,d.id in led,d.salary_xp)
        race_log.append({"round":rnd,"phase":"REGULAR","track":track.name,"winner":finish[0].name})

    regular=sorted(drivers,key=lambda d:(-standings[d.id]["points"],-standings[d.id]["wins"],-standings[d.id]["top5"]))
    top8={d.id for d in regular[:8]}
    playoff_points={d.id:2000+standings[d.id]["wins"]*3 for d in regular[:8]}

    for idx,tid in enumerate(PLAYOFFS,1):
        track=TB[tid]
        pole,finish,led=simulate_race(drivers,track,rng.randrange(1,10**9),True)
        for pos,d in enumerate(finish,1):
            st=standings[d.id]
            bonus=(2 if pos==1 else 0)+(1 if d.id in led else 0)+(1 if d.id==pole else 0)
            st["points"]+=POINTS[pos-1]+bonus
            st["wins"]+=(pos==1); st["top5"]+=(pos<=5); st["top10"]+=(pos<=10); st["poles"]+=(d.id==pole)
            if d.id in playoff_points:
                playoff_points[d.id]+=POINTS[pos-1]+bonus
            d.career_starts+=1; d.career_wins+=(pos==1); d.career_top5+=(pos<=5); d.career_top10+=(pos<=10); d.career_poles+=(d.id==pole)
            if d.human:
                d.xp+=xp_for_finish(pos,d.id==pole,d.id in led,d.salary_xp)
        race_log.append({"round":20+idx,"phase":"PLAYOFF","track":track.name,"winner":finish[0].name})

    playoff_order=sorted([d for d in drivers if d.id in top8],
                         key=lambda d:(-playoff_points[d.id],-standings[d.id]["wins"],-standings[d.id]["top5"]))
    non=sorted([d for d in drivers if d.id not in top8],
               key=lambda d:(-standings[d.id]["points"],-standings[d.id]["wins"],-standings[d.id]["top5"]))
    final=playoff_order+non
    champion=final[0]

    for pos,d in enumerate(final,1):
        st=standings[d.id]
        if d.id in top8: d.playoff_appearances+=1
        if pos==1: d.championships+=1
        d.seasons+=1
        d.season_history.append({
            "season":season_no,"finish":pos,"team_id":d.team_id,"team":TM[d.team_id].name,
            "role":d.role,"wins":st["wins"],"top5":st["top5"],"top10":st["top10"],
            "poles":st["poles"],"points":st["points"],"made_playoffs":d.id in top8
        })
        d.contract_years-=1

    return final,standings,race_log,champion


# ============================================================
# DRIVER DEVELOPMENT / AGING
# ============================================================

def offseason_driver_development(drivers,rng,news):
    """v1.8 hidden career-arc model.
    Growth is strongest before peak, flat during peak, and declines afterward.
    Potential limits how high a driver can climb.
    """
    for d in drivers:
        if d.retired or d.human:
            continue

        age=d.age
        before=overall(d)
        ceiling=d.potential

        # Age-phase development rate.
        if age < d.peak_start-3:
            base=.72
        elif age < d.peak_start:
            base=.48
        elif age <= d.peak_end:
            base=.08
        elif age <= d.peak_end+2:
            base=-.42
        else:
            base=-.78

        # Development-type adjustment.
        if d.dev_type=="GENERATIONAL":
            if age < d.peak_start: base+=.22
            elif age<=d.peak_end: base+=.04
        elif d.dev_type=="HIGH":
            if age < d.peak_start: base+=.10
        elif d.dev_type=="EARLY_PEAK" and age>d.peak_end:
            base-=.16

        # Small season-to-season form; mean-reverts.
        d.form=max(-2.0,min(2.0,d.form*.45+rng.gauss(0,.75)))

        # Rare breakout/comeback/slump story beats.
        story=None
        r=rng.random()
        if age<d.peak_start and r<.035:
            d.form=min(2.0,d.form+1.25)
            story="breakout"
        elif age>d.peak_end and r<.025:
            d.form=min(2.0,d.form+1.10)
            story="comeback"
        elif r>.975:
            d.form=max(-2.0,d.form-1.0)
            story="slump"

        # Performance influences confidence slightly, not raw ceiling.
        recent=.0
        if d.season_history:
            last=d.season_history[-1]
            if last["finish"]<=5: recent=.10
            elif last["finish"]>=20: recent=-.06

        # Translate development into stat changes.
        for key in d.attrs:
            current=d.attrs[key]

            # Soft individual cap around potential, with small skill-specific variance.
            skill_cap=min(97,ceiling + (2 if key in ("CON","RCR") else 1 if key in ("CTL","TIR") else 0))
            delta=base+recent+d.form*.12+rng.gauss(0,.40)

            if key=="CON" and d.peak_start<=age<=d.peak_end+2:
                delta+=.10
            if key=="AGG" and age>=d.peak_end:
                delta-=.12

            if delta>=.72 and current<skill_cap:
                d.attrs[key]+=1
            elif delta<=-.72:
                d.attrs[key]=max(58,current-1)

        after=overall(d)

        if story=="breakout" and after>before:
            news.append(f"{d.name} is being called a breakout candidate after a strong offseason.")
        elif story=="comeback":
            news.append(f"{d.name} is generating comeback-season buzz around the garage.")
        elif story=="slump" and after<before:
            news.append(f"{d.name} enters next season trying to rebound from an offseason slump.")


def update_cpu_contracts(drivers,rng):
    """Successful CPU drivers gain stability; weak veterans become vulnerable."""
    for d in drivers:
        if d.human or d.retired:
            continue
        if d.contract_years>0:
            continue

        last=d.season_history[-1] if d.season_history else None
        if not last:
            d.contract_years=1
            continue

        if last["finish"]<=8 or last["wins"]>=2:
            d.contract_years=2 if rng.random()<.70 else 1
        elif last["finish"]<=16:
            d.contract_years=1
        # weak drivers stay expired and may be released

# ============================================================
# OFFSEASON / SILLY SEASON
# ============================================================

def performance_value(d):
    last=d.season_history[-1] if d.season_history else {"finish":24,"wins":0,"top5":0,"top10":0}
    return (
        overall(d)*1.8 +
        max(0,25-last["finish"])*2.0 +
        last["wins"]*6 +
        last["top5"]*1.8 +
        last["top10"]*.7 +
        d.championships*18
    )

def evolve_teams(rng):
    for t in TEAMS:
        old=t.performance_delta
        drift=rng.gauss(0,.7)-old*.16
        if rng.random()<.12:
            drift+=rng.choice([-1.3,-.9,.9,1.3])
        t.performance_delta=max(-3,min(3,old+drift))
        ch=t.performance_delta-old
        t.trend="RISING" if ch>.45 else "FALLING" if ch<-.45 else "STABLE"
        target=45+(team_equipment(t)-76)*4
        t.prestige=max(25,min(90,t.prestige*.8+target*.2))

def retirements(drivers,rng,news):
    retired=[]
    for d in list(drivers):
        if d.human: continue
        chance=0
        if d.age>=41: chance=.55
        elif d.age>=38: chance=.30
        elif d.age>=35: chance=.10

        # A driver well beyond the peak and losing competitiveness is likelier to leave.
        if d.age>d.peak_end+2 and overall(d)<74:
            chance+=.12
        if d.seasons>=10:
            chance+=.06
        if d.championships>=2 and d.age>=36:
            chance+=.04
        if rng.random()<chance:
            d.retired=True
            drivers.remove(d)
            retired.append(d)
            news.append(f"{d.name} announced retirement after {d.seasons} ESCL seasons.")
    return retired

def release_decisions(drivers,rng,news):
    released=[]
    for d in list(drivers):
        if d.human: continue
        if d.contract_years>0: continue
        last=d.season_history[-1] if d.season_history else None
        weak=last and last["finish"]>=18 and last["top10"]<=4
        aging=d.age>=34
        if weak and rng.random()<.55 or aging and rng.random()<.30:
            drivers.remove(d)
            released.append(d)
            news.append(f"{TM[d.team_id].name} released {d.name} from the #{d.number}.")
    return released

def team_roster(drivers,tid):
    return [d for d in drivers if d.team_id==tid]

def choose_role(roster,d):
    ordered=sorted(roster+[d],key=performance_value,reverse=True)
    idx=ordered.index(d)
    return ROLES[min(idx,2)]

def sign_free_agents(drivers,free_agents,rng,news):
    # Teams fill to exactly 3 cars.
    next_id=max([d.id for d in drivers+free_agents] or [1000])+1
    for tid in range(1,9):
        while len(team_roster(drivers,tid))<3:
            team=TM[tid]
            candidates=[d for d in free_agents if not d.retired]
            if candidates:
                # Team interest blends performance and age/value.
                def fit(d):
                    youth=max(0,31-d.age)*1.2
                    return performance_value(d)+youth+rng.uniform(-12,12)
                pick=max(candidates,key=fit)
                free_agents.remove(pick)
                old_team=pick.team_id
                pick.team_id=tid
                pick.role=choose_role(team_roster(drivers,tid),pick)
                pick.contract_years=2 if pick.role!="THIRD" and rng.random()<.55 else 1
                pick.salary_xp=role_salary(pick.role,tid)
                drivers.append(pick)
                news.append(f"{team.name} signed {pick.name} as its {pick.role.lower()} driver.")
            else:
                # Rookie
                name=f"{rng.choice(ROOKIE_FIRST)} {rng.choice(ROOKIE_LAST)}"
                existing={d.name for d in drivers}
                tries=0
                while name in existing and tries<20:
                    name=f"{rng.choice(ROOKIE_FIRST)} {rng.choice(ROOKIE_LAST)}";tries+=1
                role=ROLES[len(team_roster(drivers,tid))]
                age=18+rng.randint(0,2)
                start_mean,potential,dev=rookie_profile(rng)
                if dev=="GENERATIONAL":
                    pk1,pk2=rng.randint(23,26),rng.randint(31,34)
                elif dev=="HIGH":
                    pk1,pk2=rng.randint(24,27),rng.randint(30,33)
                elif dev=="EARLY_PEAK":
                    pk1,pk2=rng.randint(22,25),rng.randint(27,30)
                else:
                    pk1,pk2=rng.randint(25,28),rng.randint(29,32)

                rookie=Driver(
                    next_id,name,rng.randint(2,99),age,tid,role,1,role_salary(role,tid),
                    make_attrs(rng,start_mean),
                    potential=potential,peak_start=pk1,peak_end=pk2,dev_type=dev
                )
                next_id+=1
                drivers.append(rookie)
                news.append(f"Rookie {rookie.name} earned a full-time ESCL seat with {team.name}.")

def normalize_roles(drivers):
    for tid in range(1,9):
        roster=team_roster(drivers,tid)
        roster.sort(key=performance_value,reverse=True)
        for idx,d in enumerate(roster):
            d.role=ROLES[min(idx,2)]
            d.salary_xp=role_salary(d.role,tid)

def human_market(drivers,human,rng,news):
    if human.contract_years>0:
        return None

    offers=[]
    value=performance_value(human)
    for tid in range(1,9):
        roster=team_roster(drivers,tid)

        # Empty roster = immediate opportunity. This can happen temporarily
        # after retirements/releases before the rest of Silly Season is resolved.
        if not roster:
            threshold=0
            weakest=None
        else:
            weakest=min(roster,key=performance_value)
            threshold=performance_value(weakest)+rng.uniform(-8,8)

        # Incumbent gets continuity advantage.
        if tid==human.team_id: threshold-=10

        if value>=threshold:
            hypothetical=[d for d in roster if d.id!=human.id]
            role=choose_role(hypothetical,human)
            offers.append({
                "team_id":tid,"team":TM[tid].name,"role":role,
                "years":2 if role!="THIRD" and rng.random()<.55 else 1,
                "salary":role_salary(role,tid),
                "equipment":team_equipment(TM[tid]),
                "trend":TM[tid].trend,
                "would_replace":(weakest.name if weakest is not None and weakest.id!=human.id else None)
            })

    if not offers:
        offers=[{
            "team_id":human.team_id,"team":TM[human.team_id].name,"role":"THIRD","years":1,
            "salary":.25,"equipment":team_equipment(TM[human.team_id]),"trend":TM[human.team_id].trend,
            "would_replace":None
        }]
    return offers

def choose_human_offer(offers,rng):
    role_value={"LEAD":8,"SECOND":4,"THIRD":0}
    return max(offers,key=lambda o:o["equipment"]*2+role_value[o["role"]]+o["years"]*1.4+
                                  o["salary"]*10+(2 if o["trend"]=="RISING" else 0)+rng.uniform(-3,3))

def execute_human_signing(drivers,human,offer,news):
    old=human.team_id
    target=offer["team_id"]
    if target!=old:
        roster=team_roster(drivers,target)
        cpu_roster=[d for d in roster if not d.human]
        if len(roster)>=3 and cpu_roster:
            victim=min(cpu_roster,key=performance_value)
            drivers.remove(victim)
            news.append(f"{TM[target].name} released {victim.name} to open a seat for {human.name}.")
            victim.contract_years=0
        human.team_id=target

    human.role=offer["role"]
    human.contract_years=offer["years"]
    human.salary_xp=offer["salary"]
    news.append(f"{human.name} signed with {TM[target].name} as the {human.role.lower()} driver for {human.contract_years} season(s).")

def offseason(drivers,human,season_no,seed):
    rng=random.Random(seed)
    news=[f"--- ESCL SILLY SEASON {season_no} ---"]
    evolve_teams(rng)
    offseason_driver_development(drivers,rng,news)
    update_cpu_contracts(drivers,rng)

    free_agents=[]
    free_agents += retirements(drivers,rng,news)
    # retired drivers are not actually available
    free_agents=[d for d in free_agents if not d.retired]
    free_agents += release_decisions(drivers,rng,news)

    offers=human_market(drivers,human,rng,news)
    if offers:
        chosen=choose_human_offer(offers,rng)
        execute_human_signing(drivers,human,chosen,news)

    sign_free_agents(drivers,free_agents,rng,news)
    normalize_roles(drivers)

    # Age everyone.
    for d in drivers:
        d.age+=1

    return news,offers

# ============================================================
# TEAMMATE / REPORTING
# ============================================================

def teammate_summary(drivers,human):
    roster=team_roster(drivers,human.team_id)
    out=[]
    for d in sorted(roster,key=performance_value,reverse=True):
        last=d.season_history[-1] if d.season_history else {}
        out.append({
            "name":d.name,"role":d.role,"age":d.age,"overall":overall(d),
            "wins":last.get("wins",0),"top10":last.get("top10",0),
            "championship_finish":last.get("finish")
        })
    return out

def save_league(path,drivers,human,season_no,news):
    payload={
        "season":season_no,
        "teams":[asdict(t) for t in TEAMS],
        "drivers":[asdict(d) for d in drivers],
        "human_id":human.id,
        "news":news
    }
    Path(path).write_text(json.dumps(payload,indent=2))

# ============================================================
# DEMO
# ============================================================

def demo(seed=1616):
    drivers,human=build_initial_league(seed)
    all_news=[]
    print("ESCL v1.9 — LIVING LEAGUE DEMO\n")

    for season in range(1,9):
        final,st,races,champ=run_season(drivers,season,seed+season*101)
        pos=final.index(human)+1
        hs=st[human.id]
        print(
            f"S{season} | {TM[human.team_id].name:<24} {human.role:<6} "
            f"P{pos:>2} | W {hs['wins']} | T5 {hs['top5']:>2} | T10 {hs['top10']:>2} | "
            f"Champion: {champ.name}"
        )

        mates=teammate_summary(drivers,human)
        print("  Teammates:")
        for m in mates:
            tag="YOU" if m["name"]==human.name else "   "
            print(f"   {tag} {m['name']:<18} {m['role']:<6} OVR {m['overall']:>4} P{str(m['championship_finish']):>2}")

        # Human development
        for key in ["SPD","RCR","CTL","CON","TIR","QLF"]:
            if human.xp>=2.5 and human.attrs[key]<90:
                human.xp-=2.5
                human.attrs[key]+=1

        news,offers=offseason(drivers,human,season,seed+season*313)
        all_news.extend(news)
        print("  Silly Season:")
        for line in news[1:]:
            print("   -",line)
        print()

    save_league("/mnt/data/ESCL_v1_9_Living_League_Save.json",drivers,human,8,all_news)
    Path("/mnt/data/ESCL_v1_9_Silly_Season_News.txt").write_text("\n".join(all_news))

    print("FINAL HUMAN CAREER")
    print(f"{human.career_starts} starts | {human.career_wins} wins | {human.career_top5} Top 5 | "
          f"{human.career_top10} Top 10 | {human.playoff_appearances} playoffs | {human.championships} titles")
    print(f"Final team: {TM[human.team_id].name} | Role: {human.role} | Age: {human.age}")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--demo",action="store_true")
    args=ap.parse_args()
    demo()

if __name__=="__main__":
    main()
