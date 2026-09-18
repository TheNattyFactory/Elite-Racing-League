#!/usr/bin/env python3
"""ESCL v3.3 — Player-facing service layer over the authoritative v3.2 backend."""

import sys, json
sys.path.insert(0,"/mnt/data")
import escl_v3_1_database as db
import escl_v3_1_service as base
import escl_v5_0_locked_race_engine as eng
import escl_v3_2_lifecycle as life

GAME_DB="/mnt/data/escl_v5_0.db"
db.DB_PATH=GAME_DB
base.db.DB_PATH=GAME_DB
life.db.DB_PATH=GAME_DB


def ensure_v33_tables():
    db.init_db()
    c=db.conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS qualifying_results(
        race_id TEXT NOT NULL,
        driver_id TEXT NOT NULL,
        position INTEGER NOT NULL,
        score REAL NOT NULL,
        lap_time REAL NOT NULL,
        PRIMARY KEY(race_id,driver_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS qualifying_race_position
    ON qualifying_results(race_id,position);
    """)
    cols={r["name"] for r in c.execute("PRAGMA table_info(races)").fetchall()}
    if "telemetry_json" not in cols:
        c.execute("ALTER TABLE races ADD COLUMN telemetry_json TEXT NOT NULL DEFAULT '[]'")
    if "structured_events_json" not in cols:
        c.execute("ALTER TABLE races ADD COLUMN structured_events_json TEXT NOT NULL DEFAULT '[]'")
    if "sector_telemetry_json" not in cols:
        c.execute("ALTER TABLE races ADD COLUMN sector_telemetry_json TEXT NOT NULL DEFAULT '[]'")
    c.commit(); c.close()

ensure_v33_tables()

def league_state():
    c=db.conn()
    rows=c.execute("SELECT k,v FROM league_state").fetchall()
    s={r["k"]:r["v"] for r in rows}
    c.close()
    return s


ATTRS=("SPD","RCR","QLF","CON","TIR","DRF","CTL","AGG")
START_BASE=60
START_POOL=100

def ensure_v51_tables():
    c=db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS driver_profiles(
        driver_id TEXT PRIMARY KEY,
        hometown TEXT,
        bio TEXT,
        rookie_season INTEGER NOT NULL DEFAULT 1,
        career_status TEXT NOT NULL DEFAULT 'ROOKIE',
        created_at TEXT NOT NULL
    )""")
    c.commit();c.close()

def creation_state():
    ensure_v51_tables()
    c=db.conn();h=base.human_driver(c)
    teams=c.execute("SELECT id,name,engine,aero,handling,reliability,pit FROM teams ORDER BY id").fetchall()
    nums={r["number"] for r in c.execute("SELECT number FROM drivers WHERE retired=0").fetchall()}
    out={"has_driver":bool(h),"driver":dict(h) if h else None,"starting_base":START_BASE,
         "starting_pool":START_POOL,"attributes":list(ATTRS),"teams":[dict(x) for x in teams],
         "used_numbers":sorted(nums)}
    c.close();return out

def create_driver(name,number,attributes,hometown="",team_id=None,user_id=None):
    ensure_v51_tables()
    name=(name or "").strip()
    if len(name)<2 or len(name)>40: raise ValueError("DRIVER_NAME_INVALID")
    try:number=int(number)
    except:raise ValueError("CAR_NUMBER_INVALID")
    if number<0 or number>99:raise ValueError("CAR_NUMBER_MUST_BE_0_TO_99")
    attrs={k:int((attributes or {}).get(k,START_BASE)) for k in ATTRS}
    if any(v<START_BASE or v>95 for v in attrs.values()):raise ValueError("ATTRIBUTE_OUT_OF_RANGE")
    spent=sum(v-START_BASE for v in attrs.values())
    if spent!=START_POOL:raise ValueError(f"SPEND_EXACTLY_{START_POOL}_POINTS")

    c=db.conn()
    old=base.human_driver(c)
    if old: c.close();raise ValueError("DRIVER_ALREADY_EXISTS")
    if c.execute("SELECT 1 FROM drivers WHERE number=? AND retired=0",(number,)).fetchone():
        c.close();raise ValueError("CAR_NUMBER_IN_USE")

    # Rookie enters as a free agent unless a specific open CPU seat is selected.
    chosen=None
    if team_id not in (None,""):
        team_id=int(team_id)
        chosen=c.execute("""SELECT id,team_id,role,number FROM drivers
                            WHERE team_id=? AND is_cpu=1 AND retired=0
                            ORDER BY CASE role WHEN 'THIRD' THEN 0 WHEN 'SECOND' THEN 1 ELSE 2 END,id LIMIT 1""",(team_id,)).fetchone()
        if not chosen:c.close();raise ValueError("NO_OPEN_CPU_SEAT")
    did="player_"+db.uid("drv")
    uid=user_id
    if not uid:
        uidrow=c.execute("SELECT id FROM users WHERE username!='genesis' ORDER BY created_at LIMIT 1").fetchone()
        uid=uidrow["id"] if uidrow else None
    if not uid:
        c.close()
        raise ValueError("AUTHENTICATED_USER_REQUIRED")
    role=chosen["role"] if chosen else "ROOKIE"
    tid=chosen["team_id"] if chosen else None
    c.execute("""INSERT INTO drivers(id,user_id,name,number,age,team_id,role,is_cpu,retired,xp,potential,peak_start,peak_end,dev_type,form,
              spd,rcr,qlf,con,tir,drf,ctl,agg,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (did,uid,name,number,18,tid,role,0,0,0,90,26,32,"ROOKIE",0,
               attrs["SPD"],attrs["RCR"],attrs["QLF"],attrs["CON"],attrs["TIR"],attrs["DRF"],attrs["CTL"],attrs["AGG"],db.now()))
    c.execute("INSERT INTO career_stats(driver_id) VALUES(?)",(did,))
    season=base.current_season(c)
    c.execute("INSERT OR IGNORE INTO standings(season,driver_id) VALUES(?,?)",(season,did))
    c.execute("INSERT INTO driver_profiles(driver_id,hometown,rookie_season,career_status,created_at) VALUES(?,?,?,?,?)",
              (did,(hometown or "").strip()[:60],season,"ROOKIE",db.now()))
    if chosen:
        # Retire the placeholder from active competition and give the rookie its seat.
        c.execute("UPDATE contracts SET active=0 WHERE driver_id=? AND active=1",(chosen["id"],))
        c.execute("UPDATE drivers SET retired=1,team_id=NULL WHERE id=?",(chosen["id"],))
        c.execute("""INSERT INTO contracts(id,driver_id,team_id,season_start,seasons,role,salary_xp,active,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?)""",(db.uid("ctr"),did,tid,season,1,role,.25,1,db.now()))
    c.commit();c.close()
    return driver(did)

def reset_for_creation():
    db.init_db(reset=True);db.bootstrap_living_league();ensure_v33_tables();ensure_v51_tables()
    c=db.conn();h=base.human_driver(c)
    if h:
        c.execute("DELETE FROM weekend_settings WHERE driver_id=?",(h["id"],))
        c.execute("DELETE FROM contracts WHERE driver_id=?",(h["id"],))
        c.execute("DELETE FROM career_stats WHERE driver_id=?",(h["id"],))
        c.execute("DELETE FROM standings WHERE driver_id=?",(h["id"],))
        c.execute("DELETE FROM drivers WHERE id=?",(h["id"],))
    c.commit();c.close()
    return creation_state()


XP_BASE_START=.25
XP_TOP10=.25
XP_TOP5=.25
XP_WIN=.50
XP_POLE=.10
XP_LED=.10
SEASON_UPGRADE_CAP=12

def ensure_v52_tables():
    ensure_v51_tables()
    c=db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS progression_ledger(
        id TEXT PRIMARY KEY,driver_id TEXT NOT NULL,season INTEGER NOT NULL,race_id TEXT,
        kind TEXT NOT NULL,amount REAL NOT NULL,attribute TEXT,before_value INTEGER,after_value INTEGER,
        note TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS season_progression(
        season INTEGER NOT NULL,driver_id TEXT NOT NULL,upgrades INTEGER NOT NULL DEFAULT 0,
        xp_earned REAL NOT NULL DEFAULT 0,PRIMARY KEY(season,driver_id))""")
    c.commit();c.close()

def upgrade_cost_v52(value):
    if value<65:return 1.0
    if value<70:return 1.5
    if value<75:return 2.0
    if value<80:return 2.75
    if value<85:return 3.75
    if value<90:return 5.0
    if value<95:return 7.0
    return 10.0

def progression():
    ensure_v52_tables()
    c=db.conn();h=base.human_driver(c)
    if not h:c.close();return {"driver":None}
    season=base.current_season(c)
    row=c.execute("SELECT * FROM season_progression WHERE season=? AND driver_id=?",(season,h["id"])).fetchone()
    ledger=c.execute("SELECT * FROM progression_ledger WHERE driver_id=? ORDER BY created_at DESC LIMIT 30",(h["id"],)).fetchall()
    costs={a.upper():upgrade_cost_v52(h[a]) for a in ("spd","rcr","qlf","con","tir","drf","ctl","agg")}
    out={"driver":dict(h),"season":season,"upgrades_used":row["upgrades"] if row else 0,
         "upgrade_cap":SEASON_UPGRADE_CAP,"xp_earned_season":row["xp_earned"] if row else 0,
         "costs":costs,"ledger":[dict(x) for x in ledger]}
    c.close();return out

def upgrade_v52(attr):
    ensure_v52_tables();attr=(attr or "").lower()
    if attr not in ("spd","rcr","qlf","con","tir","drf","ctl","agg"):raise ValueError("BAD_ATTRIBUTE")
    c=db.conn();h=base.human_driver(c)
    if not h:c.close();raise ValueError("NO_HUMAN_DRIVER")
    season=base.current_season(c)
    c.execute("INSERT OR IGNORE INTO season_progression(season,driver_id) VALUES(?,?)",(season,h["id"]))
    pr=c.execute("SELECT * FROM season_progression WHERE season=? AND driver_id=?",(season,h["id"])).fetchone()
    if pr["upgrades"]>=SEASON_UPGRADE_CAP:c.close();raise ValueError("SEASON_UPGRADE_CAP_REACHED")
    before=h[attr]
    if before>=99:c.close();raise ValueError("ATTRIBUTE_MAXED")
    cost=upgrade_cost_v52(before)
    if h["xp"]<cost:c.close();raise ValueError("NOT_ENOUGH_XP")
    c.execute(f"UPDATE drivers SET {attr}={attr}+1,xp=xp-? WHERE id=?",(cost,h["id"]))
    c.execute("UPDATE season_progression SET upgrades=upgrades+1 WHERE season=? AND driver_id=?",(season,h["id"]))
    c.execute("""INSERT INTO progression_ledger(id,driver_id,season,kind,amount,attribute,before_value,after_value,note,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",(db.uid("prog"),h["id"],season,"UPGRADE",-cost,attr.upper(),before,before+1,
                 f"{attr.upper()} upgraded {before} → {before+1}",db.now()))
    c.commit();out=progression();c.close() if False else None
    return out


def driver_market_value(d,career=None,standing=None):
    attrs=("spd","rcr","qlf","con","tir","drf","ctl","agg")
    overall=sum(d[a] for a in attrs)/8
    wins=(career["wins"] if career else 0);titles=(career["championships"] if career else 0)
    season_wins=(standing["wins"] if standing else 0);top5=(standing["top5"] if standing else 0)
    score=overall + min(8,wins*.35+titles*3+season_wins*.7+top5*.12)
    return round(score,2)

def salary_for_market(role,team,market):
    # Guaranteed XP per race: role/status matters, but equipment strength can trade salary for opportunity.
    eq=(team["engine"]+team["aero"]+team["handling"])/3
    role_bonus={"LEAD":.10,"SECOND":.06,"THIRD":.03}.get(role,.02)
    market_bonus=max(0,min(.10,(market-72)*.006))
    contender_discount=max(0,min(.04,(eq-79)*.008))
    return round(max(.25,min(.45,.25+role_bonus+market_bonus-contender_discount)),2)

def role_for_market(market,roster):
    others=sorted([sum(x[a] for a in ("spd","rcr","qlf","con","tir","drf","ctl","agg"))/8 for x in roster],reverse=True)
    if not others or market>=others[0]+2:return "LEAD"
    if len(others)<2 or market>=others[min(1,len(others)-1)]-1:return "SECOND"
    return "THIRD"

def market_preview():
    ensure_v52_tables();c=db.conn();h=base.human_driver(c)
    if not h:c.close();return {"driver":None,"teams":[]}
    season=base.current_season(c);career=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(h["id"],)).fetchone()
    st=c.execute("SELECT * FROM standings WHERE season=? AND driver_id=?",(season,h["id"])).fetchone()
    market=driver_market_value(h,career,st);teams=[]
    for t in c.execute("SELECT * FROM teams ORDER BY id").fetchall():
        roster=c.execute("SELECT * FROM drivers WHERE team_id=? AND retired=0 AND id<>?",(t["id"],h["id"])).fetchall()
        role=role_for_market(market,roster);salary=salary_for_market(role,t,market)
        eq=round((t["engine"]+t["aero"]+t["handling"])/3,1)
        teams.append({"team_id":t["id"],"team_name":t["name"],"equipment":eq,"prestige":t["prestige"],
                      "projected_role":role,"projected_salary_xp":salary,
                      "fit":"CONTENDER" if eq>=80 else "BUILDING" if eq<=78 else "COMPETITIVE"})
    c.close();return {"driver_id":h["id"],"market_value":market,"teams":teams}

def build_v53_offers(seed=5300):
    import random
    c=db.conn();h=base.human_driver(c)
    if not h:c.close();raise ValueError("NO_HUMAN_DRIVER")
    season=base.current_season(c);career=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(h["id"],)).fetchone()
    st=c.execute("SELECT * FROM standings WHERE season=? AND driver_id=?",(season,h["id"])).fetchone()
    market=driver_market_value(h,career,st);rng=random.Random(seed)
    c.execute("DELETE FROM contract_offers WHERE driver_id=? AND status='OPEN'",(h["id"],))
    candidates=[]
    for t in c.execute("SELECT * FROM teams ORDER BY id").fetchall():
        roster=list(c.execute("SELECT * FROM drivers WHERE team_id=? AND retired=0 AND id<>?",(t["id"],h["id"])).fetchall())
        weakest=min(roster,key=lambda x:sum(x[a] for a in ("spd","rcr","qlf","con","tir","drf","ctl","agg"))/8) if roster else None
        weak_ov=(sum(weakest[a] for a in ("spd","rcr","qlf","con","tir","drf","ctl","agg"))/8) if weakest else 0
        interest=market-weak_ov+rng.uniform(-2.5,2.5)
        if interest>=-2:
            role=role_for_market(market,roster);salary=salary_for_market(role,t,market)
            years=2 if market>=78 and role!="THIRD" else 1
            candidates.append((interest,t,role,salary,years,weakest))
    candidates.sort(key=lambda x:x[0],reverse=True)
    # 3–5 real choices rather than every team offering a contract.
    for _,t,role,salary,years,weakest in candidates[:5]:
        c.execute("""INSERT INTO contract_offers(id,driver_id,team_id,season,role,seasons,salary_xp,replacement_driver_id,status,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",(db.uid("offer"),h["id"],t["id"],season,role,years,salary,
                     weakest["id"] if weakest and len(c.execute("SELECT id FROM drivers WHERE team_id=? AND retired=0",(t["id"],)).fetchall())>=3 else None,
                     "OPEN",db.now()))
    c.commit()
    rows=c.execute("""SELECT o.*,t.name team_name,t.engine,t.aero,t.handling,t.prestige FROM contract_offers o
                      JOIN teams t ON t.id=o.team_id WHERE o.driver_id=? AND o.status='OPEN'
                      ORDER BY o.salary_xp DESC,t.prestige DESC""",(h["id"],)).fetchall()
    out=[dict(x) for x in rows];c.close();return {"market_value":market,"offers":out}


def career_timeline():
    c=db.conn();h=base.human_driver(c)
    if not h:c.close();return {"driver":None,"seasons":[]}
    rows=c.execute("""SELECT ds.*,t.name team_name FROM driver_seasons ds
                      LEFT JOIN teams t ON t.id=ds.team_id WHERE ds.driver_id=? ORDER BY ds.season""",(h["id"],)).fetchall()
    career=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(h["id"],)).fetchone()
    out={"driver":{"id":h["id"],"name":h["name"],"age":h["age"],"status":"ACTIVE" if not h["retired"] else "RETIRED"},
         "career":dict(career) if career else None,"seasons":[dict(x) for x in rows]}
    c.close();return out


def ensure_v55_tables():
    ensure_v52_tables();c=db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS team_relationships(
      driver_id TEXT NOT NULL,team_id INTEGER NOT NULL,seasons INTEGER NOT NULL DEFAULT 0,
      starts INTEGER NOT NULL DEFAULT 0,wins INTEGER NOT NULL DEFAULT 0,loyalty REAL NOT NULL DEFAULT 50,
      PRIMARY KEY(driver_id,team_id))""")
    c.commit();c.close()

def relationship_summary():
    ensure_v55_tables();c=db.conn();h=base.human_driver(c)
    if not h:c.close();return {"driver":None}
    rows=c.execute("""SELECT r.*,t.name team_name FROM team_relationships r JOIN teams t ON t.id=r.team_id
                      WHERE r.driver_id=? ORDER BY r.seasons DESC,r.wins DESC""",(h["id"],)).fetchall()
    c.close();return {"driver_id":h["id"],"relationships":[dict(x) for x in rows]}

def extension_offer():
    ensure_v55_tables();c=db.conn();h=base.human_driver(c)
    if not h or not h["team_id"]:c.close();return {"eligible":False}
    t=c.execute("SELECT * FROM teams WHERE id=?",(h["team_id"],)).fetchone()
    rel=c.execute("SELECT * FROM team_relationships WHERE driver_id=? AND team_id=?",(h["id"],h["team_id"])).fetchone()
    career=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(h["id"],)).fetchone()
    st=c.execute("SELECT * FROM standings WHERE season=? AND driver_id=?",(base.current_season(c),h["id"])).fetchone()
    mv=driver_market_value(h,career,st);loyalty=rel["loyalty"] if rel else 50;tenure=rel["seasons"] if rel else 0
    roster=c.execute("SELECT * FROM drivers WHERE team_id=? AND retired=0 AND id<>?",(h["team_id"],h["id"])).fetchall()
    role=role_for_market(mv,roster);salary=salary_for_market(role,t,mv)
    # Loyalty rewards continuity with modest salary protection, not pace bonuses.
    salary=round(min(.45,salary+min(.03,tenure*.005+(loyalty-50)*.0005)),2)
    years=3 if tenure>=3 and mv>=80 else 2 if tenure>=1 else 1
    c.close();return {"eligible":True,"team_id":t["id"],"team_name":t["name"],"role":role,"salary_xp":salary,
                      "seasons":years,"tenure":tenure,"loyalty":round(loyalty,1),"market_value":mv}

def accept_extension():
    offer=extension_offer()
    if not offer.get("eligible"):raise ValueError("NO_EXTENSION_AVAILABLE")
    c=db.conn();h=base.human_driver(c);season=base.current_season(c)
    c.execute("UPDATE contracts SET active=0 WHERE driver_id=? AND active=1",(h["id"],))
    c.execute("""INSERT INTO contracts(id,driver_id,team_id,season_start,seasons,role,salary_xp,active,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?)""",(db.uid("ctr"),h["id"],offer["team_id"],season+1,offer["seasons"],offer["role"],offer["salary_xp"],1,db.now()))
    c.execute("UPDATE drivers SET role=? WHERE id=?",(offer["role"],h["id"]))
    c.execute("""INSERT INTO team_relationships(driver_id,team_id,seasons,loyalty) VALUES(?,?,0,55)
                 ON CONFLICT(driver_id,team_id) DO UPDATE SET loyalty=MIN(100,loyalty+8)""",(h["id"],offer["team_id"]))
    c.commit();c.close();return {"accepted":True,"extension":offer}

def apply_human_veteran_curve():
    # Standard 8–12 season careers are protected. Decline begins only at age 31 for optional extensions.
    c=db.conn();h=base.human_driver(c)
    if not h or h["age"]<31:c.close();return {"changed":False}
    age=h["age"];losses=1 if age<=33 else 2 if age<=36 else 3
    attrs=["spd","rcr","qlf","con","tir","drf","ctl","agg"]
    # Experience skills RCR/CON decline later/slower.
    ordered=["spd","qlf","ctl","tir","drf","agg"]+(["rcr","con"] if age>=35 else [])
    changed=[]
    for a in ordered[:losses]:
        before=h[a];after=max(60,before-1)
        if after!=before:
            c.execute(f"UPDATE drivers SET {a}=? WHERE id=?",(after,h["id"]));changed.append((a.upper(),before,after))
    c.commit();c.close();return {"changed":bool(changed),"changes":changed}


def sync_team_relationships_for_season(season=None):
    ensure_v55_tables();c=db.conn()
    if season is None:season=base.current_season(c)
    # Archived driver_seasons is authoritative for completed-season team/results.
    rows=c.execute("""SELECT ds.driver_id,ds.team_id,ds.wins,ds.starts FROM driver_seasons ds
                      WHERE ds.season=? AND ds.team_id IS NOT NULL""",(season,)).fetchall()
    touched=0
    for r in rows:
        c.execute("""INSERT INTO team_relationships(driver_id,team_id,seasons,starts,wins,loyalty)
                     VALUES(?,?,?,?,?,55)
                     ON CONFLICT(driver_id,team_id) DO UPDATE SET
                       seasons=seasons+1,starts=starts+excluded.starts,wins=wins+excluded.wins,
                       loyalty=MIN(100,loyalty+5+MIN(5,excluded.wins))""",
                  (r["driver_id"],r["team_id"],1,r["starts"] or 0,r["wins"] or 0))
        touched+=1
    c.commit();c.close();return {"season":season,"relationships_updated":touched}

def silly_season_board(seed=5600):
    ensure_v55_tables()
    ext=extension_offer()
    # Existing v5.3 market builder supplies external opportunities.
    ext_team=ext.get("team_id") if ext.get("eligible") else None
    market=build_v53_offers(seed)
    external=[o for o in market.get("offers",[]) if o["team_id"]!=ext_team]
    return {"extension":ext if ext.get("eligible") else None,"external_offers":external[:4],
            "market_value":market.get("market_value"),"decision_required":bool(ext.get("eligible") or external)}

def complete_offseason_v56(seed=5600):
    # Sync the just-completed season before lifecycle advances the calendar.
    c=db.conn();season=base.current_season(c);c.close()
    rel=sync_team_relationships_for_season(season)
    result=life.complete_offseason(seed)
    veteran=apply_human_veteran_curve()
    return {"offseason":result,"relationship_sync":rel,"veteran_curve":veteran}


def career_hub():
    ensure_v55_tables();c=db.conn();h=base.human_driver(c)
    if not h:c.close();return {"driver":None}
    season=base.current_season(c)
    team=c.execute("SELECT * FROM teams WHERE id=?",(h["team_id"],)).fetchone() if h["team_id"] else None
    contract=c.execute("""SELECT * FROM contracts WHERE driver_id=? AND active=1 ORDER BY season_start DESC LIMIT 1""",(h["id"],)).fetchone()
    career=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(h["id"],)).fetchone()
    standing=c.execute("SELECT * FROM standings WHERE season=? AND driver_id=?",(season,h["id"])).fetchone()
    seasons=c.execute("""SELECT ds.*,t.name team_name FROM driver_seasons ds LEFT JOIN teams t ON t.id=ds.team_id
                         WHERE ds.driver_id=? ORDER BY ds.season DESC""",(h["id"],)).fetchall()
    rels=c.execute("""SELECT r.*,t.name team_name FROM team_relationships r JOIN teams t ON t.id=r.team_id
                      WHERE r.driver_id=? ORDER BY r.seasons DESC,r.wins DESC""",(h["id"],)).fetchall()
    trophies=[]
    if career and career["championships"]:
        trophies += [{"type":"CHAMPIONSHIP","label":"ESCL Champion","count":career["championships"]}]
    if career and career["wins"]:
        trophies += [{"type":"WIN","label":"Career Victories","count":career["wins"]}]
    attrs={a.upper():h[a] for a in ("spd","rcr","qlf","con","tir","drf","ctl","agg")}
    out={"season":season,"driver":dict(h),"attributes":attrs,"team":dict(team) if team else None,
         "contract":dict(contract) if contract else None,"career":dict(career) if career else None,
         "standing":dict(standing) if standing else None,"season_history":[dict(x) for x in seasons],
         "relationships":[dict(x) for x in rels],"trophies":trophies}
    c.close();return out


def ensure_v58_tables():
    ensure_v55_tables();c=db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS season_awards(
      season INTEGER NOT NULL,award_key TEXT NOT NULL,award_name TEXT NOT NULL,driver_id TEXT NOT NULL,
      value TEXT,created_at TEXT NOT NULL,PRIMARY KEY(season,award_key))""")
    c.commit();c.close()

def calculate_season_awards(season=None):
    ensure_v58_tables();c=db.conn()
    if season is None:season=base.current_season(c)
    rows=c.execute("""SELECT st.*,d.name FROM standings st JOIN drivers d ON d.id=st.driver_id
                      WHERE st.season=? ORDER BY st.points DESC""",(season,)).fetchall()
    if not rows:c.close();return {"season":season,"awards":[]}
    specs=[
      ("MOST_WINS","Most Wins",lambda x:(x["wins"],x["top5"],x["points"]),"wins"),
      ("POLE_AWARD","Pole Award",lambda x:(x["poles"],x["wins"],x["points"]),"poles"),
      ("TOP_FIVE","Top Five Award",lambda x:(x["top5"],x["wins"],x["points"]),"top5"),
    ]
    for key,name,score,col in specs:
        winner=max(rows,key=score)
        c.execute("""INSERT OR REPLACE INTO season_awards(season,award_key,award_name,driver_id,value,created_at)
                     VALUES(?,?,?,?,?,?)""",(season,key,name,winner["driver_id"],str(winner[col]),db.now()))
    champ=c.execute("SELECT champion_driver_id FROM seasons WHERE id=?",(season,)).fetchone()
    if champ and champ["champion_driver_id"]:
        c.execute("""INSERT OR REPLACE INTO season_awards(season,award_key,award_name,driver_id,value,created_at)
                     VALUES(?,?,?,?,?,?)""",(season,"CHAMPION","ESCL Series Champion",champ["champion_driver_id"],"Champion",db.now()))
    c.commit()
    awards=c.execute("""SELECT a.*,d.name driver_name,d.number,t.name team_name FROM season_awards a
                        JOIN drivers d ON d.id=a.driver_id LEFT JOIN teams t ON t.id=d.team_id
                        WHERE a.season=? ORDER BY CASE a.award_key WHEN 'CHAMPION' THEN 0 ELSE 1 END,a.award_name""",(season,)).fetchall()
    out=[dict(x) for x in awards];c.close();return {"season":season,"awards":out}

def championship_center():
    ensure_v58_tables();c=db.conn();season=base.current_season(c)
    state={r["k"]:r["v"] for r in c.execute("SELECT * FROM league_state").fetchall()}
    standings=c.execute("""SELECT st.*,d.name,d.number,t.name team_name FROM standings st JOIN drivers d ON d.id=st.driver_id
                            LEFT JOIN teams t ON t.id=d.team_id WHERE st.season=?
                            ORDER BY CASE WHEN st.playoff=1 THEN 0 ELSE 1 END,
                            CASE WHEN st.playoff=1 THEN st.playoff_points ELSE st.points END DESC,
                            st.wins DESC,st.top5 DESC""",(season,)).fetchall()
    completed=c.execute("SELECT COUNT(*) n FROM races WHERE season=? AND status='FINAL'",(season,)).fetchone()["n"]
    cutoff=None
    regular=sorted(standings,key=lambda x:(x["points"],x["wins"],x["top5"]),reverse=True)
    if len(regular)>=8:cutoff=regular[7]["points"]
    rows=[]
    for i,r in enumerate(standings,1):
        x=dict(r);x["display_rank"]=i
        x["status"]="PLAYOFF" if r["playoff"] else ("BUBBLE" if completed<20 and cutoff is not None and r["points"]>=cutoff-40 else "REGULAR")
        rows.append(x)
    champ=state.get("champion","")
    c.close()
    awards=calculate_season_awards(season)["awards"]
    return {"season":season,"phase":state.get("phase","REGULAR"),"round":int(state.get("current_round","1")),
            "completed_races":completed,"playoff_cutoff_points":cutoff,"standings":rows,"champion":champ,"awards":awards}

def driver_trophy_case(driver_id=None):
    ensure_v58_tables();c=db.conn()
    if driver_id is None:
        h=base.human_driver(c);driver_id=h["id"] if h else None
    if not driver_id:c.close();return {"trophies":[]}
    rows=c.execute("""SELECT a.*,s.name season_name FROM season_awards a LEFT JOIN seasons s ON s.id=a.season
                      WHERE a.driver_id=? ORDER BY a.season DESC,a.award_name""",(driver_id,)).fetchall()
    c.close();return {"driver_id":driver_id,"trophies":[dict(x) for x in rows]}


def ensure_v59_tables():
    ensure_v58_tables();c=db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS career_achievements(
      driver_id TEXT NOT NULL,achievement_key TEXT NOT NULL,label TEXT NOT NULL,
      earned_season INTEGER,value TEXT,created_at TEXT NOT NULL,
      PRIMARY KEY(driver_id,achievement_key))""")
    c.execute("""CREATE TABLE IF NOT EXISTS season_records(
      season INTEGER NOT NULL,record_key TEXT NOT NULL,record_name TEXT NOT NULL,
      driver_id TEXT NOT NULL,value REAL NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(season,record_key))""")
    c.commit();c.close()

def calculate_legacy_awards(season=None):
    ensure_v59_tables();c=db.conn()
    if season is None: season=base.current_season(c)
    rows=c.execute("""SELECT st.*,d.name,d.age,d.created_at FROM standings st
                      JOIN drivers d ON d.id=st.driver_id WHERE st.season=?""",(season,)).fetchall()
    if not rows:c.close();return {"season":season,"awards":[]}
    # Rookie = career began this season according to driver_profiles.
    rookies=c.execute("""SELECT st.*,d.name FROM standings st JOIN drivers d ON d.id=st.driver_id
                         JOIN driver_profiles p ON p.driver_id=d.id
                         WHERE st.season=? AND p.rookie_season=?""",(season,season)).fetchall()
    if rookies:
        r=max(rookies,key=lambda x:(x["points"],x["wins"],x["top5"]))
        c.execute("""INSERT OR REPLACE INTO season_awards(season,award_key,award_name,driver_id,value,created_at)
                     VALUES(?,?,?,?,?,?)""",(season,"ROOKIE","Rookie of the Year",r["driver_id"],str(r["points"]),db.now()))
    # Most Improved compares archived previous-season points finish to current.
    prev={r["driver_id"]:r for r in c.execute("SELECT * FROM driver_seasons WHERE season=?",(season-1,)).fetchall()}
    candidates=[]
    ordered=sorted(rows,key=lambda x:(x["points"],x["wins"],x["top5"]),reverse=True)
    for rank,r in enumerate(ordered,1):
        p=prev.get(r["driver_id"])
        if p and p["points_finish"]:
            improvement=int(p["points_finish"])-rank
            candidates.append((improvement,r))
    if candidates:
        imp,r=max(candidates,key=lambda x:(x[0],x[1]["points"]))
        c.execute("""INSERT OR REPLACE INTO season_awards(season,award_key,award_name,driver_id,value,created_at)
                     VALUES(?,?,?,?,?,?)""",(season,"MOST_IMPROVED","Most Improved Driver",r["driver_id"],f"+{max(0,imp)} positions",db.now()))
    c.commit();c.close()
    return calculate_season_awards(season)

def update_season_records(season=None):
    ensure_v59_tables();c=db.conn()
    if season is None:season=base.current_season(c)
    rows=c.execute("SELECT * FROM standings WHERE season=?",(season,)).fetchall()
    specs=[("WINS","Most Wins in Season","wins"),("TOP5","Most Top 5s in Season","top5"),
           ("TOP10","Most Top 10s in Season","top10"),("POLES","Most Poles in Season","poles"),
           ("POINTS","Most Regular Points","points")]
    for key,name,col in specs:
        if rows:
            r=max(rows,key=lambda x:x[col])
            c.execute("""INSERT OR REPLACE INTO season_records(season,record_key,record_name,driver_id,value,created_at)
                         VALUES(?,?,?,?,?,?)""",(season,key,name,r["driver_id"],float(r[col]),db.now()))
    c.commit();c.close()
    return record_book()

def award_achievements(driver_id=None):
    ensure_v59_tables();c=db.conn()
    if driver_id is None:
        h=base.human_driver(c);driver_id=h["id"] if h else None
    if not driver_id:c.close();return {"achievements":[]}
    car=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(driver_id,)).fetchone()
    season=base.current_season(c)
    milestones=[]
    if car:
        for n in (1,5,10,25):
            if car["wins"]>=n:milestones.append((f"WINS_{n}",f"{n} Career Win"+("" if n==1 else "s"),str(car["wins"])))
        for n in (50,100,200):
            if car["starts"]>=n:milestones.append((f"STARTS_{n}",f"{n} Career Starts",str(car["starts"])))
        if car["championships"]>=1:milestones.append(("CHAMPION","ESCL Champion",str(car["championships"])))
    for key,label,value in milestones:
        c.execute("""INSERT OR IGNORE INTO career_achievements(driver_id,achievement_key,label,earned_season,value,created_at)
                     VALUES(?,?,?,?,?,?)""",(driver_id,key,label,season,value,db.now()))
    c.commit()
    rows=c.execute("SELECT * FROM career_achievements WHERE driver_id=? ORDER BY earned_season,created_at",(driver_id,)).fetchall()
    c.close();return {"driver_id":driver_id,"achievements":[dict(x) for x in rows]}

def record_book():
    ensure_v59_tables();c=db.conn()
    # Best all-time value for each stored season record.
    rows=c.execute("""SELECT sr.*,d.name driver_name,d.number FROM season_records sr
                      JOIN drivers d ON d.id=sr.driver_id ORDER BY sr.record_key,sr.value DESC""").fetchall()
    best={}
    for r in rows:
        if r["record_key"] not in best:best[r["record_key"]]=dict(r)
    champs=c.execute("""SELECT a.*,d.name driver_name,d.number FROM season_awards a JOIN drivers d ON d.id=a.driver_id
                        WHERE a.award_key='CHAMPION' ORDER BY a.season DESC""").fetchall()
    c.close();return {"records":list(best.values()),"champions":[dict(x) for x in champs]}


def ensure_v60_tables():
    ensure_v59_tables();c=db.conn()
    cols={r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    for col,ddl in [("email","TEXT"),("email_verified","INTEGER NOT NULL DEFAULT 0"),("password_salt","TEXT"),
                    ("session_version","INTEGER NOT NULL DEFAULT 1"),("disabled","INTEGER NOT NULL DEFAULT 0")]:
        if col not in cols:c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    c.execute("""CREATE TABLE IF NOT EXISTS auth_tokens(
      token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,kind TEXT NOT NULL,expires_at INTEGER NOT NULL,
      used INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS app_sessions(
      token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at INTEGER NOT NULL,
      created_at TEXT NOT NULL,last_seen_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS operation_locks(
      lock_key TEXT PRIMARY KEY,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
      version TEXT PRIMARY KEY,applied_at TEXT NOT NULL)""")
    c.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES('6.0',?)",(db.now(),))
    c.commit();c.close()

def field_integrity():
    c=db.conn()
    active=c.execute("SELECT * FROM drivers WHERE retired=0 AND team_id IS NOT NULL").fetchall()
    by_team={}
    for d in active:by_team.setdefault(d["team_id"],[]).append(d)
    duplicate_numbers=c.execute("""SELECT number,COUNT(*) n FROM drivers WHERE retired=0 GROUP BY number HAVING COUNT(*)>1""").fetchall()
    duplicate_contracts=c.execute("""SELECT driver_id,COUNT(*) n FROM contracts WHERE active=1 GROUP BY driver_id HAVING COUNT(*)>1""").fetchall()
    bad_teams=[{"team_id":tid,"active_drivers":len(ds)} for tid,ds in by_team.items() if len(ds)!=3]
    out={"ok":len(active)==24 and not duplicate_numbers and not duplicate_contracts and not bad_teams,
         "active_field":len(active),"bad_teams":bad_teams,
         "duplicate_numbers":[dict(x) for x in duplicate_numbers],
         "duplicate_active_contracts":[dict(x) for x in duplicate_contracts]}
    c.close();return out

def finalize_legacy_for_season(season=None):
    ensure_v60_tables();c=db.conn()
    if season is None:season=base.current_season(c)
    c.close()
    awards=calculate_season_awards(season)
    legacy=calculate_legacy_awards(season)
    records=update_season_records(season)
    return {"season":season,"awards":legacy.get("awards",awards.get("awards",[])),"records":records.get("records",[])}


def issue_auth_token(user_id,kind,ttl_seconds):
    ensure_v60_tables()
    import secrets,hashlib,time
    raw=secrets.token_urlsafe(32);th=hashlib.sha256(raw.encode()).hexdigest()
    c=db.conn();c.execute("""INSERT INTO auth_tokens(token_hash,user_id,kind,expires_at,used,created_at)
                            VALUES(?,?,?,?,0,?)""",(th,user_id,kind,int(time.time())+ttl_seconds,db.now()))
    c.commit();c.close();return raw

def consume_auth_token(raw,kind):
    import hashlib,time
    th=hashlib.sha256((raw or "").encode()).hexdigest();c=db.conn()
    row=c.execute("""SELECT * FROM auth_tokens WHERE token_hash=? AND kind=? AND used=0 AND expires_at>?""",
                  (th,kind,int(time.time()))).fetchone()
    if not row:c.close();return None
    c.execute("UPDATE auth_tokens SET used=1 WHERE token_hash=?",(th,));c.commit();out=dict(row);c.close();return out

def revoke_user_sessions(user_id):
    c=db.conn();n=c.execute("DELETE FROM app_sessions WHERE user_id=?",(user_id,)).rowcount;c.commit();c.close()
    return n

def account_by_email(email):
    c=db.conn();r=c.execute("SELECT * FROM users WHERE lower(email)=lower(?)",(email,)).fetchone();c.close();return dict(r) if r else None


def ensure_v62_tables():
    ensure_v60_tables();c=db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS audit_log(
      id INTEGER PRIMARY KEY AUTOINCREMENT,actor_user_id TEXT,action TEXT NOT NULL,
      target TEXT,detail_json TEXT,created_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS rate_limits(
      bucket_key TEXT NOT NULL,window_start INTEGER NOT NULL,hits INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(bucket_key,window_start))""")
    c.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES('6.2',?)",(db.now(),))
    c.commit();c.close()

def audit(actor_user_id,action,target=None,detail=None):
    ensure_v62_tables();import json
    c=db.conn();c.execute("""INSERT INTO audit_log(actor_user_id,action,target,detail_json,created_at)
                            VALUES(?,?,?,?,?)""",(actor_user_id,action,target,json.dumps(detail or {},default=str),db.now()))
    c.commit();c.close()

def audit_recent(limit=100):
    ensure_v62_tables();c=db.conn()
    rows=c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",(max(1,min(int(limit),500)),)).fetchall()
    c.close();return {"events":[dict(x) for x in rows]}

def readiness():
    ensure_v62_tables();import os
    c=db.conn()
    try:
        c.execute("SELECT 1").fetchone()
        integrity=field_integrity()
        season=base.current_season(c)
        phase=c.execute("SELECT v FROM league_state WHERE k='phase'").fetchone()
        db_ok=True
    except Exception:
        db_ok=False;integrity={"ok":False};season=None;phase=None
    finally:c.close()
    return {"ready":bool(db_ok),"database":db_ok,"field_integrity":integrity,
            "season":season,"phase":phase["v"] if phase else None,
            "mail_configured":bool(os.environ.get("ESCL_MAIL_PROVIDER")),
            "public_base_configured":bool(os.environ.get("ESCL_PUBLIC_BASE_URL"))}


def ensure_v63_tables():
    ensure_v62_tables();c=db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS legal_acceptance(
      user_id TEXT NOT NULL,document_key TEXT NOT NULL,version TEXT NOT NULL,accepted_at TEXT NOT NULL,
      PRIMARY KEY(user_id,document_key,version))""")
    c.execute("""CREATE TABLE IF NOT EXISTS moderation_actions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,actor_user_id TEXT NOT NULL,target_user_id TEXT NOT NULL,
      action TEXT NOT NULL,reason TEXT,created_at TEXT NOT NULL)""")
    c.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES('6.3',?)",(db.now(),))
    c.commit();c.close()

def accept_legal(user_id,docs):
    ensure_v63_tables();c=db.conn()
    for key,version in docs.items():
        c.execute("INSERT OR IGNORE INTO legal_acceptance(user_id,document_key,version,accepted_at) VALUES(?,?,?,?)",
                  (user_id,key,version,db.now()))
    c.commit();c.close();return legal_status(user_id)

def legal_status(user_id):
    ensure_v63_tables();c=db.conn();rows=c.execute("SELECT * FROM legal_acceptance WHERE user_id=?",(user_id,)).fetchall();c.close()
    accepted={(r["document_key"],r["version"]) for r in rows}
    required={("TERMS","1.0"),("PRIVACY","1.0"),("COMMUNITY","1.0")}
    return {"complete":required.issubset(accepted),"accepted":[dict(x) for x in rows]}

def moderate(actor,target,action,reason=""):
    ensure_v63_tables()
    if action not in ("WARN","SUSPEND","RESTORE"):raise ValueError("INVALID_MODERATION_ACTION")
    c=db.conn()
    if action=="SUSPEND":c.execute("UPDATE users SET disabled=1 WHERE id=?",(target,))
    if action=="RESTORE":c.execute("UPDATE users SET disabled=0 WHERE id=?",(target,))
    c.execute("INSERT INTO moderation_actions(actor_user_id,target_user_id,action,reason,created_at) VALUES(?,?,?,?,?)",
              (actor,target,action,reason[:500],db.now()))
    c.commit();c.close();audit(actor,"MODERATION",target,{"action":action})
    return {"ok":True,"action":action,"target_user_id":target}

def beta_readiness():
    ensure_v63_tables();import os
    r=readiness()
    gates={
      "database":bool(r["database"]),
      "field_integrity":bool(r["field_integrity"].get("ok")),
      "public_base_url":bool(os.environ.get("ESCL_PUBLIC_BASE_URL")),
      "mail_provider":bool(os.environ.get("ESCL_MAIL_PROVIDER")),
      "mail_api_key":bool(os.environ.get("ESCL_MAIL_API_KEY")),
      "legal_documents":True,
      "moderation":True,
      "audit_log":True,
      "persistent_rate_limits":True
    }
    blockers=[k for k,v in gates.items() if not v]
    return {"go":not blockers,"status":"GO" if not blockers else "NO-GO","gates":gates,"blockers":blockers,
            "requires_external_tests":["deployed smoke test","backup/restore drill","real verification email delivery"]}

def dashboard():
    ensure_v51_tables()
    c0=db.conn();h0=base.human_driver(c0);c0.close()
    if not h0:
        return {"needs_driver_creation":True,"creation":creation_state(),"season":1,
                "league_state":league_state(),"driver":None,"standings":None,"career":None,
                "contract":None,"next_race":None,"weekend":None}
    out=base.dashboard()
    out["needs_driver_creation"]=False
    ensure_v52_tables()
    c=db.conn()
    d=out["driver"]
    s=out["season"]
    posrow=c.execute("""SELECT COUNT(*)+1 pos FROM standings
                        WHERE season=? AND (
                          points > ? OR
                          (points=? AND wins>?) OR
                          (points=? AND wins=? AND top5>?)
                        )""",
                     (s,out["standings"]["points"],out["standings"]["points"],out["standings"]["wins"],
                      out["standings"]["points"],out["standings"]["wins"],out["standings"]["top5"])).fetchone()
    out["championship_position"]=posrow["pos"] if posrow else None
    out["league_state"]=league_state()
    if out["next_race"]:
        ws=c.execute("SELECT * FROM weekend_settings WHERE race_id=? AND driver_id=?",
                     (out["next_race"]["id"],d["id"])).fetchone()
        out["weekend"]=dict(ws) if ws else None
    else:
        out["weekend"]=None
    c.close()
    return out

def standings():
    c=db.conn(); s=base.current_season(c)
    rows=c.execute("""SELECT st.*,d.name,d.number,d.role,d.is_cpu,t.name team_name
                      FROM standings st JOIN drivers d ON d.id=st.driver_id
                      LEFT JOIN teams t ON t.id=d.team_id
                      WHERE st.season=?
                      ORDER BY
                        CASE WHEN st.playoff=1 THEN 0 ELSE 1 END,
                        CASE WHEN st.playoff=1 THEN st.playoff_points ELSE st.points END DESC,
                        st.wins DESC,st.top5 DESC""",(s,)).fetchall()
    c.close()
    return {"season":s,"rows":[dict(r) for r in rows]}

def schedule():
    c=db.conn(); s=base.current_season(c)
    rows=c.execute("""SELECT r.*,t.name track_name,t.kind,t.laps,
                      dw.name winner_name,dp.name pole_name
                      FROM races r JOIN tracks t ON t.id=r.track_id
                      LEFT JOIN drivers dw ON dw.id=r.winner_driver_id
                      LEFT JOIN drivers dp ON dp.id=r.pole_driver_id
                      WHERE r.season=? ORDER BY r.round_no""",(s,)).fetchall()
    c.close()
    return {"season":s,"rows":[dict(r) for r in rows]}

def teams():
    c=db.conn()
    teams=c.execute("SELECT * FROM teams ORDER BY id").fetchall()
    result=[]
    for t in teams:
        roster=c.execute("""SELECT d.id,d.name,d.number,d.role,d.age,d.is_cpu,
                            d.spd,d.rcr,d.qlf,d.con,d.tir,d.drf,d.ctl,d.agg,
                            COALESCE(cs.wins,0) career_wins,COALESCE(cs.championships,0) championships
                            FROM drivers d LEFT JOIN career_stats cs ON cs.driver_id=d.id
                            WHERE d.team_id=? AND d.retired=0
                            ORDER BY CASE d.role WHEN 'LEAD' THEN 1 WHEN 'SECOND' THEN 2 ELSE 3 END""",(t["id"],)).fetchall()
        row=dict(t); row["roster"]=[dict(x) for x in roster]; result.append(row)
    c.close()
    return {"teams":result}

def driver(driver_id):
    c=db.conn()
    d=c.execute("""SELECT d.*,t.name team_name FROM drivers d LEFT JOIN teams t ON t.id=d.team_id WHERE d.id=?""",(driver_id,)).fetchone()
    if not d: c.close(); raise ValueError("DRIVER_NOT_FOUND")
    career=c.execute("SELECT * FROM career_stats WHERE driver_id=?",(driver_id,)).fetchone()
    seasons=c.execute("""SELECT ds.*,t.name team_name FROM driver_seasons ds
                         LEFT JOIN teams t ON t.id=ds.team_id
                         WHERE ds.driver_id=? ORDER BY season DESC""",(driver_id,)).fetchall()
    contract=c.execute("""SELECT c.*,t.name team_name FROM contracts c JOIN teams t ON t.id=c.team_id
                          WHERE c.driver_id=? AND c.active=1""",(driver_id,)).fetchone()
    c.close()
    return {"driver":dict(d),"career":dict(career) if career else None,
            "seasons":[dict(x) for x in seasons],"contract":dict(contract) if contract else None}

def race(race_id):
    c=db.conn()
    r=c.execute("""SELECT r.*,t.name track_name,t.kind,t.laps,
                   dw.name winner_name,dp.name pole_name
                   FROM races r JOIN tracks t ON t.id=r.track_id
                   LEFT JOIN drivers dw ON dw.id=r.winner_driver_id
                   LEFT JOIN drivers dp ON dp.id=r.pole_driver_id
                   WHERE r.id=?""",(race_id,)).fetchone()
    if not r: c.close(); raise ValueError("RACE_NOT_FOUND")
    results=c.execute("""SELECT rr.*,d.name,d.number,t.name team_name
                         FROM race_results rr JOIN drivers d ON d.id=rr.driver_id
                         LEFT JOIN teams t ON t.id=d.team_id
                         WHERE rr.race_id=? ORDER BY rr.finish_pos""",(race_id,)).fetchall()
    row=dict(r)
    try: row["events"]=json.loads(row.get("events_json") or "[]")
    except Exception: row["events"]=[]
    try: row["telemetry"]=json.loads(row.get("telemetry_json") or "[]")
    except Exception: row["telemetry"]=[]
    try: row["structured_events"]=json.loads(row.get("structured_events_json") or "[]")
    except Exception: row["structured_events"]=[]
    try: row["sector_telemetry"]=json.loads(row.get("sector_telemetry_json") or "[]")
    except Exception: row["sector_telemetry"]=[]
    c.close()
    return {"race":row,"results":[dict(x) for x in results]}

def latest_race():
    c=db.conn(); s=base.current_season(c)
    r=c.execute("SELECT id FROM races WHERE season=? AND status='FINAL' ORDER BY round_no DESC LIMIT 1",(s,)).fetchone()
    c.close()
    return race(r["id"]) if r else None

def weekend():
    c=db.conn(); r=base.next_race(c); h=base.human_driver(c)
    if not r:
        c.close(); return {"race":None,"settings":None}
    ws=c.execute("SELECT * FROM weekend_settings WHERE race_id=? AND driver_id=?",(r["id"],h["id"])).fetchone()
    c.close()
    return {"race":dict(r),"settings":dict(ws) if ws else None}

def news():
    c=db.conn()
    rows=c.execute("SELECT * FROM news ORDER BY created_at DESC LIMIT 50").fetchall()
    c.close()
    return {"news":[dict(x) for x in rows]}

def offers():
    c=db.conn(); h=base.human_driver(c)
    rows=c.execute("""SELECT o.*,t.name team_name,t.engine,t.aero,t.handling,t.reliability,t.pit,t.trend
                      FROM contract_offers o JOIN teams t ON t.id=o.team_id
                      WHERE o.driver_id=? AND o.status='OPEN'
                      ORDER BY o.salary_xp DESC,t.prestige DESC""",(h["id"],)).fetchall() if h else []
    c.close()
    return {"offers":[dict(x) for x in rows]}

def upgrade(attr):
    return upgrade_v52(attr)

def practice(setup_bias,driving_style,pit_plan):
    c=db.conn(); r=base.next_race(c); h=base.human_driver(c); c.close()
    if not r: raise ValueError("NO_SCHEDULED_RACE")
    base.set_weekend(r["id"],h["id"],setup_bias,driving_style,pit_plan)
    return weekend()

def qualify(seed=1):
    import random
    c=db.conn(); r=base.next_race(c); h=base.human_driver(c)
    if not r or not h:
        c.close(); raise ValueError("NO_RACE_OR_DRIVER")
    ws=c.execute("SELECT * FROM weekend_settings WHERE race_id=? AND driver_id=?",(r["id"],h["id"])).fetchone()
    if not ws or not ws["practice_done"]:
        c.close(); raise ValueError("PRACTICE_REQUIRED")
    drivers,teams=base.field_snapshot(c)
    team_map={t.id:t for t in teams}
    track=eng.TRACK_LIBRARY[r["track_id"]]
    rng=random.Random(seed)
    rows=[]
    for d in drivers:
        setup=ws["setup_bias"] if d.id==h["id"] else "BALANCED"
        score=eng.qualify_score(d,team_map[d.team_id],track,rng,setup)
        lap=track.base_lap-(score-80)*.010+rng.gauss(0,.018 if track.kind!="ROAD_COURSE" else .035)
        rows.append({"driver_id":d.id,"score":score,"lap_time":lap})
    rows.sort(key=lambda x:x["lap_time"])
    c.execute("DELETE FROM qualifying_results WHERE race_id=?",(r["id"],))
    for pos,row in enumerate(rows,1):
        c.execute("INSERT INTO qualifying_results(race_id,driver_id,position,score,lap_time) VALUES(?,?,?,?,?)",
                  (r["id"],row["driver_id"],pos,row["score"],row["lap_time"]))
    human_row=next((pos,row) for pos,row in enumerate(rows,1) if row["driver_id"]==h["id"])
    c.execute("""UPDATE weekend_settings SET qualifying_done=1,qualifying_pos=?,qualifying_score=?
                 WHERE race_id=? AND driver_id=?""",
              (human_row[0],human_row[1]["score"],r["id"],h["id"]))
    c.commit(); c.close()
    return {"race_id":r["id"],"position":human_row[0],"score":round(human_row[1]["score"],2),
            "grid":[{"position":i+1,**row} for i,row in enumerate(rows)]}

def sim_race(seed=None):
    ensure_v60_tables()
    integrity=field_integrity()
    if not integrity["ok"]:raise ValueError("FIELD_INTEGRITY_FAILED")
    c_lock=db.conn()
    r_lock=base.next_race(c_lock)
    if not r_lock:c_lock.close();raise ValueError("NO_NEXT_RACE")
    lock_key=f"RACE:{r_lock['id']}"
    try:c_lock.execute("INSERT INTO operation_locks(lock_key,created_at) VALUES(?,?)",(lock_key,db.now()));c_lock.commit()
    except Exception:c_lock.close();raise ValueError("RACE_ALREADY_PROCESSING_OR_COMPLETE")
    c_lock.close()
    import random, json
    seed=seed or random.randrange(1,10**9)
    ensure_v52_tables()
    c=db.conn(); r=base.next_race(c); h=base.human_driver(c)
    if not r:
        c.close(); return {"status":"SEASON_COMPLETE"}
    ws=c.execute("SELECT * FROM weekend_settings WHERE race_id=? AND driver_id=?",(r["id"],h["id"])).fetchone()
    if not ws or not ws["qualifying_done"]:
        c.close(); raise ValueError("QUALIFYING_REQUIRED")
    qrows=c.execute("SELECT * FROM qualifying_results WHERE race_id=? ORDER BY position",(r["id"],)).fetchall()
    if len(qrows)!=24:
        c.close(); raise ValueError("FULL_QUALIFYING_GRID_REQUIRED")

    drivers,teams=base.field_snapshot(c)
    strategy={"setup_bias":ws["setup_bias"],"driving_style":ws["driving_style"],"pit_plan":ws["pit_plan"]}
    qgrid=[dict(x) for x in qrows]
    result=eng.simulate_race(drivers,teams,r["track_id"],seed,human_id=h["id"],
                             human_strategy=strategy,racecast=True,qualifying_grid=qgrid,capture_telemetry=True)
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
            xp=salary+XP_BASE_START+(XP_TOP10 if pos<=10 else 0)+(XP_TOP5 if pos<=5 else 0)+(XP_WIN if pos==1 else 0)+(XP_POLE if did==pole else 0)+(XP_LED if s["led"] else 0)

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
            c.execute("INSERT OR IGNORE INTO season_progression(season,driver_id) VALUES(?,?)",(season,did))
            c.execute("UPDATE season_progression SET xp_earned=xp_earned+? WHERE season=? AND driver_id=?",(xp,season,did))
            c.execute("""INSERT INTO progression_ledger(id,driver_id,season,race_id,kind,amount,note,created_at)
                         VALUES(?,?,?,?,?,?,?,?)""",(db.uid("prog"),did,season,r["id"],"RACE_XP",xp,
                         f"Race XP: P{pos} • salary {salary:.2f}",db.now()))

    winner=result["finish"][0]["d"]
    c.execute("""UPDATE races SET status='FINAL',seed=?,pole_driver_id=?,winner_driver_id=?,caution_count=?,pass_count=?,events_json=?,telemetry_json=?,structured_events_json=?,sector_telemetry_json=?
                 WHERE id=?""",(seed,pole,winner.id,result["meta"]["cautions"],result["meta"]["passes"],json.dumps(result["events"]),json.dumps(result["telemetry"]),json.dumps(result.get("structured_events",[])),json.dumps(result.get("sector_telemetry",[])),r["id"]))
    next_round=r["round_no"]+1
    c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES('current_round',?)",(str(next_round),))
    c.execute("UPDATE seasons SET current_round=? WHERE id=?",(next_round,season))
    c.execute("INSERT INTO news(id,season,kind,headline,body,created_at) VALUES(?,?,?,?,?,?)",
              (db.uid("news"),season,"RACE",f"{winner.name} wins at {r['track_name']}",f"Round {r['round_no']} is complete.",db.now()))
    c.commit(); c.close()
    transition=life.after_race(r["id"])
    return {"status":"RACE_FINAL","race_id":r["id"],"winner":winner.name,
            "pole":next(d.name for _,d in result["grid"] if d.id==pole),
            "cautions":result["meta"]["cautions"],"passes":result["meta"]["passes"],
            "events":result["events"],"transition":transition}

def prepare_offseason(seed=3200):
    base_result=life.prepare_offseason(seed)
    # Rebuild the human market with v5.3 value/role/salary logic.
    return build_v53_offers(seed+53)

def accept_offer(offer_id):
    return life.accept_offer(offer_id)

def finalize_offseason(seed=3300):
    return life.finalize_offseason(seed)

def reset_demo():
    db.init_db(reset=True)
    db.bootstrap_living_league()
    ensure_v33_tables()
    return {"ok":True,"dashboard":dashboard()}
