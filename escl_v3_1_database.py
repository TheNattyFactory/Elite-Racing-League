#!/usr/bin/env python3
"""ESCL v3.1 — Authoritative persistent game database.

This layer is intended to become the single source of truth for the browser,
race engine, career systems, Silly Season, standings and commissioner actions.
"""

import sqlite3, json, uuid, datetime
from pathlib import Path

BASE_DIR=Path(__file__).resolve().parent
DB_PATH="/mnt/data/escl_v3_1.db"

def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"

def conn():
    c=sqlite3.connect(DB_PATH)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

SCHEMA="""
CREATE TABLE IF NOT EXISTS meta(
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users(
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT,
    role TEXT NOT NULL DEFAULT 'PLAYER',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    engine INTEGER NOT NULL,
    aero INTEGER NOT NULL,
    handling INTEGER NOT NULL,
    reliability INTEGER NOT NULL,
    pit INTEGER NOT NULL,
    prestige REAL NOT NULL DEFAULT 50,
    trend TEXT NOT NULL DEFAULT 'STABLE',
    performance_delta REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tracks(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    laps INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS drivers(
    id TEXT PRIMARY KEY,
    user_id TEXT,
    name TEXT NOT NULL,
    number INTEGER NOT NULL,
    age INTEGER NOT NULL,
    team_id INTEGER,
    role TEXT NOT NULL DEFAULT 'THIRD',
    is_cpu INTEGER NOT NULL DEFAULT 1,
    retired INTEGER NOT NULL DEFAULT 0,
    xp REAL NOT NULL DEFAULT 0,
    potential INTEGER NOT NULL DEFAULT 80,
    peak_start INTEGER NOT NULL DEFAULT 26,
    peak_end INTEGER NOT NULL DEFAULT 31,
    dev_type TEXT NOT NULL DEFAULT 'NORMAL',
    form REAL NOT NULL DEFAULT 0,
    spd INTEGER NOT NULL,
    rcr INTEGER NOT NULL,
    qlf INTEGER NOT NULL,
    con INTEGER NOT NULL,
    tir INTEGER NOT NULL,
    drf INTEGER NOT NULL,
    ctl INTEGER NOT NULL,
    agg INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(team_id) REFERENCES teams(id)
);

CREATE TABLE IF NOT EXISTS contracts(
    id TEXT PRIMARY KEY,
    driver_id TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    season_start INTEGER NOT NULL,
    seasons INTEGER NOT NULL,
    role TEXT NOT NULL,
    salary_xp REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(driver_id) REFERENCES drivers(id),
    FOREIGN KEY(team_id) REFERENCES teams(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_contract_per_driver
ON contracts(driver_id) WHERE active=1;

CREATE TABLE IF NOT EXISTS seasons(
    id INTEGER PRIMARY KEY,
    phase TEXT NOT NULL DEFAULT 'REGULAR',
    current_round INTEGER NOT NULL DEFAULT 1,
    completed INTEGER NOT NULL DEFAULT 0,
    champion_driver_id TEXT
);

CREATE TABLE IF NOT EXISTS races(
    id TEXT PRIMARY KEY,
    season INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    phase TEXT NOT NULL,
    track_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    seed INTEGER,
    pole_driver_id TEXT,
    winner_driver_id TEXT,
    caution_count INTEGER NOT NULL DEFAULT 0,
    pass_count INTEGER NOT NULL DEFAULT 0,
    events_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY(season) REFERENCES seasons(id),
    FOREIGN KEY(track_id) REFERENCES tracks(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS season_round_unique ON races(season,round_no);

CREATE TABLE IF NOT EXISTS race_results(
    race_id TEXT NOT NULL,
    driver_id TEXT NOT NULL,
    start_pos INTEGER NOT NULL,
    finish_pos INTEGER NOT NULL,
    points INTEGER NOT NULL,
    laps_led INTEGER NOT NULL DEFAULT 0,
    pit_stops INTEGER NOT NULL DEFAULT 0,
    dnf INTEGER NOT NULL DEFAULT 0,
    damage REAL NOT NULL DEFAULT 0,
    xp_earned REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(race_id,driver_id),
    FOREIGN KEY(race_id) REFERENCES races(id),
    FOREIGN KEY(driver_id) REFERENCES drivers(id)
);

CREATE TABLE IF NOT EXISTS standings(
    season INTEGER NOT NULL,
    driver_id TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    top5 INTEGER NOT NULL DEFAULT 0,
    top10 INTEGER NOT NULL DEFAULT 0,
    poles INTEGER NOT NULL DEFAULT 0,
    laps_led INTEGER NOT NULL DEFAULT 0,
    dnfs INTEGER NOT NULL DEFAULT 0,
    playoff INTEGER NOT NULL DEFAULT 0,
    playoff_points INTEGER NOT NULL DEFAULT 0,
    championship_finish INTEGER,
    PRIMARY KEY(season,driver_id)
);

CREATE TABLE IF NOT EXISTS career_stats(
    driver_id TEXT PRIMARY KEY,
    starts INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    top5 INTEGER NOT NULL DEFAULT 0,
    top10 INTEGER NOT NULL DEFAULT 0,
    poles INTEGER NOT NULL DEFAULT 0,
    laps_led INTEGER NOT NULL DEFAULT 0,
    championships INTEGER NOT NULL DEFAULT 0,
    playoff_appearances INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS driver_seasons(
    season INTEGER NOT NULL,
    driver_id TEXT NOT NULL,
    team_id INTEGER,
    role TEXT,
    finish INTEGER,
    wins INTEGER NOT NULL DEFAULT 0,
    top5 INTEGER NOT NULL DEFAULT 0,
    top10 INTEGER NOT NULL DEFAULT 0,
    poles INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    made_playoffs INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(season,driver_id)
);

CREATE TABLE IF NOT EXISTS weekend_settings(
    race_id TEXT NOT NULL,
    driver_id TEXT NOT NULL,
    practice_done INTEGER NOT NULL DEFAULT 0,
    practice_score REAL,
    practice_feedback TEXT,
    setup_bias TEXT NOT NULL DEFAULT 'BALANCED',
    driving_style TEXT NOT NULL DEFAULT 'BALANCED',
    pit_plan TEXT NOT NULL DEFAULT 'STANDARD',
    qualifying_done INTEGER NOT NULL DEFAULT 0,
    qualifying_pos INTEGER,
    qualifying_score REAL,
    PRIMARY KEY(race_id,driver_id)
);

CREATE TABLE IF NOT EXISTS contract_offers(
    id TEXT PRIMARY KEY,
    driver_id TEXT NOT NULL,
    team_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    role TEXT NOT NULL,
    seasons INTEGER NOT NULL,
    salary_xp REAL NOT NULL,
    replacement_driver_id TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news(
    id TEXT PRIMARY KEY,
    season INTEGER,
    kind TEXT NOT NULL,
    headline TEXT NOT NULL,
    body TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS league_state(
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""

TEAMS=[
(1,"Blue Ridge Motorsports",81,81,80,82,80),(2,"Peachtree Racing",79,80,82,80,83),
(3,"Ironhorse Motorsports",83,79,78,81,79),(4,"Liberty Speedworks",80,82,79,79,81),
(5,"Great Lakes Racing",78,79,81,84,82),(6,"Lone Star Competition",82,78,80,80,78),
(7,"Appalachian Racing",77,78,79,83,80),(8,"Pacific Coast Motorsports",80,81,81,78,81)
]
TRACKS=[
("CAR","Carolina Motor Speedway","INTERMEDIATE",100),("GULF","Gulf Coast Speedway","SUPERSPEEDWAY",80),
("PINE","Pine Ridge Raceway","SHORT_TRACK",160),("MID","Midland Raceway","FLAT_OVAL",120),
("RIDGE","Ridgeview Speedway","HIGH_BANK",110),("COAST","Coastal Grand Prix","ROAD_COURSE",55)
]
CALENDAR=["CAR","GULF","PINE","MID","RIDGE","COAST","CAR","PINE","RIDGE","MID","GULF","COAST",
          "CAR","MID","PINE","RIDGE","GULF","CAR","COAST","MID","PINE","GULF","MID","RIDGE","COAST","CAR"]

def init_db(reset=False):
    p=Path(DB_PATH)
    if reset and p.exists(): p.unlink()
    c=conn(); c.executescript(SCHEMA)
    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version','3.1')")
    for row in TEAMS:
        c.execute("""INSERT OR IGNORE INTO teams(id,name,engine,aero,handling,reliability,pit)
                     VALUES(?,?,?,?,?,?,?)""",row)
    for row in TRACKS:
        c.execute("INSERT OR IGNORE INTO tracks(id,name,kind,laps) VALUES(?,?,?,?)",row)
    c.commit(); c.close()

def create_calendar(c,season):
    c.execute("INSERT OR IGNORE INTO seasons(id,phase,current_round,completed) VALUES(?,?,?,0)",(season,"REGULAR",1))
    for i,track in enumerate(CALENDAR,1):
        phase="REGULAR" if i<=20 else "PLAYOFF"
        c.execute("""INSERT OR IGNORE INTO races(id,season,round_no,phase,track_id,status)
                     VALUES(?,?,?,?,?,'SCHEDULED')""",(f"S{season:02d}R{i:02d}",season,i,phase,track))

def bootstrap_living_league(seed=3100):
    """Import canonical v1.9 league once into the authoritative DB."""
    import importlib.util, sys
    sys.path.insert(0,"/mnt/data")
    spec=importlib.util.spec_from_file_location("living",str(BASE_DIR/"escl_living_league_v1_9.py"))
    living=importlib.util.module_from_spec(spec);spec.loader.exec_module(living)
    drivers,human=living.build_initial_league(seed)

    c=conn()
    if c.execute("SELECT COUNT(*) n FROM drivers").fetchone()["n"]:
        c.close(); return

    user_id="user_genesis"
    c.execute("INSERT INTO users(id,username,role,created_at) VALUES(?,?,?,?)",(user_id,"genesis","COMMISSIONER",now()))
    for d in drivers:
        a=d.attrs
        did=str(d.id)
        c.execute("""INSERT INTO drivers(
            id,user_id,name,number,age,team_id,role,is_cpu,retired,xp,potential,peak_start,peak_end,dev_type,form,
            spd,rcr,qlf,con,tir,drf,ctl,agg,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (did,user_id if d.human else None,d.name,d.number,d.age,d.team_id,d.role,0 if d.human else 1,0,d.xp,
             d.potential,d.peak_start,d.peak_end,d.dev_type,d.form,
             a["SPD"],a["RCR"],a["QLF"],a["CON"],a["TIR"],a["DRF"],a["CTL"],a["AGG"],now()))
        c.execute("""INSERT INTO contracts(id,driver_id,team_id,season_start,seasons,role,salary_xp,active,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  (uid("ctr"),did,d.team_id,1,d.contract_years,d.role,d.salary_xp,1,now()))
        c.execute("INSERT INTO career_stats(driver_id) VALUES(?)",(did,))
    create_calendar(c,1)
    for d in drivers:
        c.execute("INSERT INTO standings(season,driver_id) VALUES(1,?)",(str(d.id),))
    for k,v in {"season":"1","phase":"REGULAR","current_round":"1"}.items():
        c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES(?,?)",(k,v))
    c.commit(); c.close()

if __name__=="__main__":
    init_db(reset=True)
    bootstrap_living_league()
    c=conn()
    print("schema",c.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()["v"])
    print("drivers",c.execute("SELECT COUNT(*) n FROM drivers").fetchone()["n"])
    print("teams",c.execute("SELECT COUNT(*) n FROM teams").fetchone()["n"])
    print("races",c.execute("SELECT COUNT(*) n FROM races").fetchone()["n"])
    c.close()
