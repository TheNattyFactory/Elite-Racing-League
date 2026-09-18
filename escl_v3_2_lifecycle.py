#!/usr/bin/env python3
import importlib.util,sys,random
sys.path.insert(0,'/mnt/data')

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

db=load('/mnt/data/escl_v3_1_database.py','db')
ATTRS=('spd','rcr','qlf','con','tir','drf','ctl','agg');ROLES=('LEAD','SECOND','THIRD')
FIRST=('Eli','Mason','Wyatt','Caleb','Cole','Ryan','Luke','Nate','Grant','Blake','Chase','Hunter','Dylan','Noah','Gavin','Trevor','Austin','Marcus','Jace','Logan','Tyler')
LAST=('Rivers','Bishop','Hale','Dalton','Foster','Nash','Baker','Sutton','Mills','Sharp','Carter','Fleming','Parker','Holt','Warren','Knox','Dawson','Reed','Price','Stone')

def current_season(c): return int(c.execute("SELECT v FROM league_state WHERE k='season'").fetchone()['v'])
def phase(c): return c.execute("SELECT v FROM league_state WHERE k='phase'").fetchone()['v']
def overall(d): return sum(d[a] for a in ATTRS)/8
def upgrade_cost(v): return 1.0 if v<60 else 1.5 if v<70 else 2.0 if v<80 else 3.0 if v<90 else 4.5

def upgrade_driver(driver_id,attr):
    attr=attr.lower()
    if attr not in ATTRS: raise ValueError('BAD_ATTRIBUTE')
    c=db.conn();d=c.execute('SELECT * FROM drivers WHERE id=?',(driver_id,)).fetchone()
    if not d: c.close();raise ValueError('DRIVER_NOT_FOUND')
    cost=upgrade_cost(d[attr])
    if d[attr]>=99: c.close();raise ValueError('ATTRIBUTE_MAXED')
    if d['xp']<cost: c.close();raise ValueError('NOT_ENOUGH_XP')
    c.execute(f'UPDATE drivers SET {attr}={attr}+1,xp=xp-? WHERE id=?',(cost,driver_id));c.commit();out=dict(c.execute('SELECT * FROM drivers WHERE id=?',(driver_id,)).fetchone());c.close();return {'driver':out,'spent':cost}

def determine_playoffs(season):
    c=db.conn();rows=c.execute('SELECT * FROM standings WHERE season=? ORDER BY points DESC,wins DESC,top5 DESC,top10 DESC',(season,)).fetchall();top=rows[:8]
    for r in top:c.execute('UPDATE standings SET playoff=1,playoff_points=? WHERE season=? AND driver_id=?',(2000+3*r['wins'],season,r['driver_id']))
    c.execute("UPDATE seasons SET phase='PLAYOFF' WHERE id=?",(season,));c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES('phase','PLAYOFF')");c.commit();c.close();return [r['driver_id'] for r in top]

def apply_playoff_points(season,race_id):
    c=db.conn();rows=c.execute('SELECT rr.driver_id,rr.points,st.playoff FROM race_results rr JOIN standings st ON st.driver_id=rr.driver_id AND st.season=? WHERE rr.race_id=?',(season,race_id)).fetchall()
    for r in rows:
        if r['playoff']:c.execute('UPDATE standings SET playoff_points=playoff_points+? WHERE season=? AND driver_id=?',(r['points'],season,r['driver_id']))
    c.commit();c.close()

def crown_champion(season):
    c=db.conn();po=c.execute('SELECT * FROM standings WHERE season=? AND playoff=1 ORDER BY playoff_points DESC,wins DESC,top5 DESC,top10 DESC',(season,)).fetchall();non=c.execute('SELECT * FROM standings WHERE season=? AND playoff=0 ORDER BY points DESC,wins DESC,top5 DESC,top10 DESC',(season,)).fetchall();order=po+non
    if not po:c.close();raise ValueError('NO_PLAYOFF_FIELD')
    champ=po[0]['driver_id']
    for i,r in enumerate(order,1):
        d=c.execute('SELECT * FROM drivers WHERE id=?',(r['driver_id'],)).fetchone();c.execute('UPDATE standings SET championship_finish=? WHERE season=? AND driver_id=?',(i,season,r['driver_id']))
        c.execute('INSERT OR REPLACE INTO driver_seasons(season,driver_id,team_id,role,finish,wins,top5,top10,poles,points,made_playoffs) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(season,r['driver_id'],d['team_id'],d['role'],i,r['wins'],r['top5'],r['top10'],r['poles'],r['points'],1 if r['playoff'] else 0))
        if r['playoff']:c.execute('UPDATE career_stats SET playoff_appearances=playoff_appearances+1 WHERE driver_id=?',(r['driver_id'],))
    c.execute('UPDATE career_stats SET championships=championships+1 WHERE driver_id=?',(champ,));c.execute("UPDATE seasons SET phase='OFFSEASON',completed=1,champion_driver_id=? WHERE id=?",(champ,season));c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES('phase','OFFSEASON')");name=c.execute('SELECT name FROM drivers WHERE id=?',(champ,)).fetchone()['name'];c.commit();c.close();return {'champion_id':champ,'champion':name}

def after_race(race_id):
    c=db.conn();r=c.execute('SELECT * FROM races WHERE id=?',(race_id,)).fetchone();c.close();out={}
    if r['round_no']==20:out['playoff_field']=determine_playoffs(r['season'])
    if r['round_no']>=21:apply_playoff_points(r['season'],race_id)
    if r['round_no']==26:out['champion']=crown_champion(r['season'])
    return out

def team_roster(c,tid): return c.execute('SELECT * FROM drivers WHERE team_id=? AND retired=0 ORDER BY role,name',(tid,)).fetchall()
def human_driver(c): return c.execute('SELECT * FROM drivers WHERE is_cpu=0 AND retired=0 LIMIT 1').fetchone()
def team_eq(t): return (t['engine']+t['aero']+t['handling'])/3

def normalize_roles(c,tid):
    rows=c.execute('SELECT d.*,COALESCE(cs.wins,0) w,COALESCE(cs.top5,0) t5 FROM drivers d LEFT JOIN career_stats cs ON cs.driver_id=d.id WHERE d.team_id=? AND d.retired=0 ORDER BY w DESC,t5 DESC',(tid,)).fetchall()
    for role,d in zip(ROLES,rows[:3]):c.execute('UPDATE drivers SET role=? WHERE id=?',(role,d['id']));c.execute('UPDATE contracts SET role=? WHERE driver_id=? AND active=1',(role,d['id']))

def evolve_teams(c,rng):
    for t in c.execute('SELECT * FROM teams ORDER BY id').fetchall():
        base=team_eq(t);delta=max(-1,min(1,rng.gauss(0,.55)+(80-base)*.10));vals={}
        for col in ('engine','aero','handling'):vals[col]=max(73,min(86,round(t[col]+delta+rng.uniform(-.35,.35))))
        rel=max(73,min(86,round(t['reliability']+delta*.55+rng.uniform(-.25,.25))));pit=max(73,min(86,round(t['pit']+delta*.55+rng.uniform(-.25,.25))));trend='RISING' if delta>.35 else 'FALLING' if delta<-.35 else 'STABLE'
        c.execute('UPDATE teams SET engine=?,aero=?,handling=?,reliability=?,pit=?,performance_delta=?,trend=? WHERE id=?',(vals['engine'],vals['aero'],vals['handling'],rel,pit,delta,trend,t['id']))

def develop_and_age(c,rng):
    for d in c.execute('SELECT * FROM drivers WHERE is_cpu=1 AND retired=0').fetchall():
        age=d['age'];base=.70 if age<=22 else .45 if age<=25 else .18 if age<=29 else 0 if age<=32 else -.28 if age<=35 else -.58
        if rng.random()<.045:base+=1.1
        elif rng.random()<.035:base-=1
        for a in ATTRS:
            adj=base+(.10 if a in ('con','rcr') and 25<=age<=32 else 0)-(.12 if a=='agg' and age>=32 else 0);roll=adj+rng.uniform(-.55,.55);chg=1 if roll>=.75 else -1 if roll<=-.75 else 0
            if chg:c.execute(f'UPDATE drivers SET {a}=? WHERE id=?',(max(50,min(d['potential'] if chg>0 else 99,d[a]+chg)),d['id']))
        c.execute('UPDATE drivers SET age=age+1,form=form*.55 WHERE id=?',(d['id'],))
    h=human_driver(c)
    if h:c.execute('UPDATE drivers SET age=age+1 WHERE id=?',(h['id'],))

def expire_contracts(c):
    for x in c.execute('SELECT * FROM contracts WHERE active=1').fetchall():
        left=x['seasons']-1;c.execute('UPDATE contracts SET seasons=?,active=? WHERE id=?',(max(0,left),1 if left>0 else 0,x['id']))

def retire_and_release(c,rng):
    season=current_season(c)
    for d in c.execute('SELECT * FROM drivers WHERE is_cpu=1 AND retired=0').fetchall():
        p=.48 if d['age']>=40 else .25 if d['age']>=37 else .08 if d['age']>=34 else 0
        if rng.random()<p:c.execute('UPDATE drivers SET retired=1,team_id=NULL WHERE id=?',(d['id'],));c.execute('UPDATE contracts SET active=0 WHERE driver_id=?',(d['id'],));continue
        active=c.execute('SELECT 1 FROM contracts WHERE driver_id=? AND active=1',(d['id'],)).fetchone();ds=c.execute('SELECT finish FROM driver_seasons WHERE season=? AND driver_id=?',(season,d['id'])).fetchone();finish=ds['finish'] if ds else 24
        if not active and (finish>=19 or overall(d)<72) and rng.random()<.55:c.execute('UPDATE drivers SET team_id=NULL WHERE id=?',(d['id'],))

def role_salary(role,t):
    base=.25+max(0,min(.12,(team_eq(t)-76)*.015));return round(min(.45,base+(.08 if role=='LEAD' else .04 if role=='SECOND' else 0)),2)

def build_human_offers(c,rng):
    h=human_driver(c)
    if not h or c.execute('SELECT 1 FROM contracts WHERE driver_id=? AND active=1',(h['id'],)).fetchone():return []
    season=current_season(c);offers=[];hv=overall(h)
    for t in c.execute('SELECT * FROM teams ORDER BY id').fetchall():
        roster=team_roster(c,t['id']);weak=min(roster,key=overall) if roster else None;threshold=(overall(weak) if weak else 0)+rng.uniform(-5,5)-(3 if t['id']==h['team_id'] else 0)
        if hv>=threshold:
            others=[x for x in roster if x['id']!=h['id']];role='LEAD' if not others or hv>=max(map(overall,others))+3 else 'SECOND' if len(others)<2 else 'THIRD';years=2 if role!='THIRD' and rng.random()<.55 else 1;oid=db.uid('offer');rep=weak['id'] if weak and len(roster)>=3 and weak['id']!=h['id'] else None
            c.execute('INSERT INTO contract_offers(id,driver_id,team_id,season,role,seasons,salary_xp,replacement_driver_id,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(oid,h['id'],t['id'],season,role,years,role_salary(role,t),rep,'OPEN',db.now()));offers.append(oid)
    return offers

def accept_offer(offer_id):
    c=db.conn();o=c.execute("SELECT * FROM contract_offers WHERE id=? AND status='OPEN'",(offer_id,)).fetchone()
    if not o:c.close();raise ValueError('OFFER_NOT_FOUND')
    if o['replacement_driver_id']:c.execute('UPDATE drivers SET team_id=NULL WHERE id=?',(o['replacement_driver_id'],));c.execute('UPDATE contracts SET active=0 WHERE driver_id=?',(o['replacement_driver_id'],))
    c.execute('UPDATE contracts SET active=0 WHERE driver_id=?',(o['driver_id'],));c.execute('UPDATE drivers SET team_id=?,role=? WHERE id=?',(o['team_id'],o['role'],o['driver_id']));c.execute('INSERT INTO contracts(id,driver_id,team_id,season_start,seasons,role,salary_xp,active,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.uid('ctr'),o['driver_id'],o['team_id'],current_season(c)+1,o['seasons'],o['role'],o['salary_xp'],1,db.now()));c.execute("UPDATE contract_offers SET status=CASE WHEN id=? THEN 'ACCEPTED' ELSE 'DECLINED' END WHERE driver_id=? AND status='OPEN'",(offer_id,o['driver_id']));normalize_roles(c,o['team_id']);c.commit();c.close();return {'accepted':offer_id}

def create_rookie(c,tid,role,rng):
    did=db.uid('drv');name=f'{rng.choice(FIRST)} {rng.choice(LAST)}';mean=rng.randint(70,75);attrs={a:max(60,min(82,round(rng.gauss(mean,3)))) for a in ATTRS};pot=rng.randint(82,92)
    c.execute('INSERT INTO drivers(id,name,number,age,team_id,role,is_cpu,retired,xp,potential,peak_start,peak_end,dev_type,form,spd,rcr,qlf,con,tir,drf,ctl,agg,created_at) VALUES(?,?,?,?,?,?,1,0,0,?,?,?,?,0,?,?,?,?,?,?,?,?,?)',(did,name,rng.randint(2,99),rng.choice([18,19,20]),tid,role,pot,rng.randint(24,27),rng.randint(30,33),'HIGH' if pot>=89 else 'NORMAL',attrs['spd'],attrs['rcr'],attrs['qlf'],attrs['con'],attrs['tir'],attrs['drf'],attrs['ctl'],attrs['agg'],db.now()));c.execute('INSERT INTO career_stats(driver_id) VALUES(?)',(did,));c.execute('INSERT INTO contracts(id,driver_id,team_id,season_start,seasons,role,salary_xp,active,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.uid('ctr'),did,tid,current_season(c)+1,1,role,.25,1,db.now()))

def fill_rosters(c,rng):
    free=list(c.execute('SELECT * FROM drivers WHERE is_cpu=1 AND retired=0 AND team_id IS NULL').fetchall())
    for tid in range(1,9):
        roster=list(team_roster(c,tid))
        while len(roster)<3:
            role=ROLES[len(roster)]
            if free:
                fa=max(free,key=overall);free.remove(fa);c.execute('UPDATE drivers SET team_id=?,role=? WHERE id=?',(tid,role,fa['id']));c.execute('INSERT INTO contracts(id,driver_id,team_id,season_start,seasons,role,salary_xp,active,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.uid('ctr'),fa['id'],tid,current_season(c)+1,1,role,.25,1,db.now()))
            else:create_rookie(c,tid,role,rng)
            roster=list(team_roster(c,tid))
        normalize_roles(c,tid)

def prepare_offseason(seed=3200):
    c=db.conn()
    if phase(c)!='OFFSEASON':c.close();raise ValueError('NOT_OFFSEASON')
    rng=random.Random(seed);evolve_teams(c,rng);expire_contracts(c);develop_and_age(c,rng);retire_and_release(c,rng);ids=build_human_offers(c,rng);c.commit();offers=[dict(x) for x in c.execute("SELECT o.*,t.name team_name FROM contract_offers o JOIN teams t ON t.id=o.team_id WHERE o.status='OPEN'").fetchall()];c.close();return {'offers':offers}

def finalize_offseason(seed=3300):
    c=db.conn()
    if phase(c)!='OFFSEASON':c.close();raise ValueError('NOT_OFFSEASON')
    h=human_driver(c)
    if h and not c.execute('SELECT 1 FROM contracts WHERE driver_id=? AND active=1',(h['id'],)).fetchone():c.close();raise ValueError('HUMAN_NEEDS_CONTRACT')
    rng=random.Random(seed);fill_rosters(c,rng);ns=current_season(c)+1;db.create_calendar(c,ns);active=c.execute('SELECT * FROM drivers WHERE retired=0 AND team_id IS NOT NULL').fetchall()
    for d in active:c.execute('INSERT OR IGNORE INTO standings(season,driver_id) VALUES(?,?)',(ns,d['id']))
    c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES('season',?)",(str(ns),));c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES('phase','REGULAR')");c.execute("INSERT OR REPLACE INTO league_state(k,v) VALUES('current_round','1')");c.commit();counts={tid:len(team_roster(c,tid)) for tid in range(1,9)};c.close();return {'season':ns,'rosters':counts}
