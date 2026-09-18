#!/usr/bin/env python3
"""ESCL v3.1 — Persistent service layer.

The DB is authoritative. The lap engine receives snapshots and returns results.
No race engine globals are allowed to own career state.
"""

import importlib.util, sys, random, json
sys.path.insert(0,"/mnt/data")

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

db=load("/mnt/data/escl_v3_1_database.py","db")
eng=load("/mnt/data/escl_v2_8_lap_engine.py","eng")

def current_season(c):
    r=c.execute("SELECT v FROM league_state WHERE k='season'").fetchone()
    return int(r["v"])

def human_driver(c):
    return c.execute("SELECT * FROM drivers WHERE is_cpu=0 AND retired=0 LIMIT 1").fetchone()

def next_race(c):
    s=current_season(c)
    return c.execute("""SELECT r.*,t.name track_name,t.kind,t.laps
                       FROM races r JOIN tracks t ON t.id=r.track_id
                       WHERE r.season=? AND r.status='SCHEDULED'
                       ORDER BY r.round_no LIMIT 1""",(s,)).fetchone()

def field_snapshot(c):
    ds=c.execute("SELECT * FROM drivers WHERE retired=0 AND team_id IS NOT NULL ORDER BY team_id,name").fetchall()
    ts=c.execute("SELECT * FROM teams ORDER BY id").fetchall()
    drivers=[eng.Driver(r["id"],r["name"],r["number"],r["team_id"],r["spd"],r["rcr"],r["qlf"],r["con"],r["tir"],r["drf"],r["ctl"],r["agg"]) for r in ds]
    teams=[eng.Team(r["id"],r["name"],r["engine"],r["aero"],r["handling"],r["reliability"],r["pit"]) for r in ts]
    return drivers,teams

def set_weekend(race_id,driver_id,setup_bias,driving_style,pit_plan):
    c=db.conn()
    c.execute("""INSERT INTO weekend_settings(race_id,driver_id,practice_done,setup_bias,driving_style,pit_plan)
                 VALUES(?,?,1,?,?,?)
                 ON CONFLICT(race_id,driver_id) DO UPDATE SET
                 practice_done=1,setup_bias=excluded.setup_bias,driving_style=excluded.driving_style,pit_plan=excluded.pit_plan""",
              (race_id,driver_id,setup_bias,driving_style,pit_plan))
    c.commit(); c.close()

def qualify_current(seed=1):
    c=db.conn(); r=next_race(c); h=human_driver(c)
    if not r or not h: c.close(); raise ValueError("NO_RACE_OR_DRIVER")
    ws=c.execute("SELECT * FROM weekend_settings WHERE race_id=? AND driver_id=?",(r["id"],h["id"])).fetchone()
    if not ws or not ws["practice_done"]: c.close(); raise ValueError("PRACTICE_REQUIRED")
    drivers,teams=field_snapshot(c); tm={t.id:t for t in teams}; rng=random.Random(seed)
    scores=[]
    for d in drivers:
        setup=ws["setup_bias"] if d.id==h["id"] else "BALANCED"
        q=eng.qualify_score(d,tm[d.team_id],eng.TRACK_LIBRARY[r["track_id"]],rng,setup)
        scores.append((q,d.id))
    scores.sort(reverse=True)
    pos=next(i+1 for i,(_,did) in enumerate(scores) if did==h["id"])
    score=next(q for q,did in scores if did==h["id"])
    c.execute("""UPDATE weekend_settings SET qualifying_done=1,qualifying_pos=?,qualifying_score=?
                 WHERE race_id=? AND driver_id=?""",(pos,score,r["id"],h["id"]))
    c.commit(); c.close()
    return {"race_id":r["id"],"position":pos,"score":round(score,2)}

def simulate_current(seed=None):
    seed=seed or random.randrange(1,10**9)
    c=db.conn(); r=next_race(c); h=human_driver(c)
    if not r: c.close(); return {"status":"SEASON_COMPLETE"}
    ws=c.execute("SELECT * FROM weekend_settings WHERE race_id=? AND driver_id=?",(r["id"],h["id"])).fetchone()
    if not ws or not ws["qualifying_done"]: c.close(); raise ValueError("QUALIFYING_REQUIRED")
    drivers,teams=field_snapshot(c)
    strategy={"setup_bias":ws["setup_bias"],"driving_style":ws["driving_style"],"pit_plan":ws["pit_plan"]}
    result=eng.simulate_race(drivers,teams,r["track_id"],seed,human_id=h["id"],human_strategy=strategy,racecast=True)
    pole=result["grid"][0][1].id
    starts={d.id:i+1 for i,(_,d) in enumerate(result["grid"])}
    season=r["season"]

    for pos,s in enumerate(result["finish"],1):
        did=s["d"].id
        pts=eng.POINTS[pos-1]+(2 if pos==1 else 0)+(1 if s["led"] else 0)+(1 if did==pole else 0)
        xp=0
        if did==h["id"]:
            contract=c.execute("SELECT salary_xp FROM contracts WHERE driver_id=? AND active=1",(did,)).fetchone()
            salary=contract["salary_xp"] if contract else .25
            xp=salary+.25+(0.25 if pos<=10 else 0)+(0.25 if pos<=5 else 0)+(0.50 if pos==1 else 0)+(0.10 if did==pole else 0)+(0.10 if s["led"] else 0)
        c.execute("""INSERT INTO race_results(race_id,driver_id,start_pos,finish_pos,points,laps_led,pit_stops,dnf,damage,xp_earned)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (r["id"],did,starts[did],pos,pts,s["led"],s["pits"],1 if s["out"] else 0,s["damage"],xp))
        c.execute("""UPDATE standings SET points=points+?,wins=wins+?,top5=top5+?,top10=top10+?,
                     poles=poles+?,laps_led=laps_led+?,dnfs=dnfs+? WHERE season=? AND driver_id=?""",
                  (pts,1 if pos==1 else 0,1 if pos<=5 else 0,1 if pos<=10 else 0,1 if did==pole else 0,s["led"],1 if s["out"] else 0,season,did))
        c.execute("""UPDATE career_stats SET starts=starts+1,wins=wins+?,top5=top5+?,top10=top10+?,
                     poles=poles+?,laps_led=laps_led+? WHERE driver_id=?""",
                  (1 if pos==1 else 0,1 if pos<=5 else 0,1 if pos<=10 else 0,1 if did==pole else 0,s["led"],did))
        if xp:
            c.execute("UPDATE drivers SET xp=xp+? WHERE id=?",(xp,did))

    winner=result["finish"][0]["d"]
    c.execute("""UPDATE races SET status='FINAL',seed=?,pole_driver_id=?,winner_driver_id=?,caution_count=?,pass_count=?,events_json=?
                 WHERE id=?""",(seed,pole,winner.id,result["meta"]["cautions"],result["meta"]["passes"],json.dumps(result["events"]),r["id"]))
    next_round=r["round_no"]+1
    c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES('current_round',?)",(str(next_round),))
    c.execute("UPDATE seasons SET current_round=? WHERE id=?",(next_round,season))
    c.execute("INSERT INTO news(id,season,kind,headline,body,created_at) VALUES(?,?,?,?,?,?)",
              (db.uid("news"),season,"RACE",f"{winner.name} wins at {r['track_name']}",f"Round {r['round_no']} is complete.",db.now()))
    c.commit(); c.close()
    return {"status":"RACE_FINAL","race_id":r["id"],"winner":winner.name,"pole":next(d.name for _,d in result["grid"] if d.id==pole),
            "cautions":result["meta"]["cautions"],"passes":result["meta"]["passes"],"events":result["events"]}

def dashboard():
    c=db.conn(); h=human_driver(c); r=next_race(c); s=current_season(c)
    st=c.execute("SELECT * FROM standings WHERE season=? AND driver_id=?",(s,h["id"])).fetchone()
    cs=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(h["id"],)).fetchone()
    contract=c.execute("""SELECT c.*,t.name team_name FROM contracts c JOIN teams t ON t.id=c.team_id
                          WHERE c.driver_id=? AND c.active=1""",(h["id"],)).fetchone()
    out={"season":s,"driver":dict(h),"standings":dict(st) if st else None,"career":dict(cs),"contract":dict(contract) if contract else None,
         "next_race":dict(r) if r else None}
    c.close(); return out

if __name__=="__main__":
    db.init_db(reset=True); db.bootstrap_living_league()
    c=db.conn(); r=next_race(c); h=human_driver(c); c.close()
    set_weekend(r["id"],h["id"],"BALANCED","BALANCED","STANDARD")
    print(qualify_current(311))
    print(simulate_current(312))
    print(json.dumps(dashboard(),indent=2))
