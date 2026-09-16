#!/usr/bin/env python3
"""ESCL v3.3 — Player-facing browser API."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
import json,sys,os,time,secrets,hashlib,hmac,urllib.request,urllib.error
sys.path.insert(0,"/mnt/data")
import escl_v3_1_database as db
import escl_v6_3_service as svc
db.DB_PATH=svc.GAME_DB

HOST=os.environ.get("HOST","0.0.0.0")
PORT=int(os.environ.get("PORT","8093"))
INDEX=Path(os.environ.get("ESCL_INDEX",str(Path(__file__).with_name("escl_v6_3_index.html"))))
ELITE_CORE_URL=os.environ.get("ELITE_CORE_URL","https://elitesportsleagues.com").rstrip("/")
RATE={}
DEV_OUTBOX=[]


def pw_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),310000)
    return salt,digest.hex()

def rate_ok(ip,bucket,limit,window):
    svc.ensure_v63_tables()
    now=int(time.time());ws=now-(now%window);key=f"{ip}:{bucket}"
    c=db.conn()
    row=c.execute("SELECT hits FROM rate_limits WHERE bucket_key=? AND window_start=?",(key,ws)).fetchone()
    hits=int(row["hits"]) if row else 0
    if hits>=limit:c.close();return False
    c.execute("""INSERT INTO rate_limits(bucket_key,window_start,hits) VALUES(?,?,1)
                 ON CONFLICT(bucket_key,window_start) DO UPDATE SET hits=hits+1""",(key,ws))
    c.execute("DELETE FROM rate_limits WHERE window_start<?",(now-86400*2,))
    c.commit();c.close();return True

def new_session(user_id):
    token=secrets.token_urlsafe(32);th=hashlib.sha256(token.encode()).hexdigest();now=int(time.time())
    c=db.conn();c.execute("INSERT INTO app_sessions(token_hash,user_id,expires_at,created_at,last_seen_at) VALUES(?,?,?,?,?)",
      (th,user_id,now+60*60*24*14,db.now(),db.now()));c.commit();c.close();return token

def session_user(headers):
    auth=headers.get("Authorization","")
    if not auth.startswith("Bearer "):return None
    th=hashlib.sha256(auth[7:].encode()).hexdigest();now=int(time.time());c=db.conn()
    row=c.execute("""SELECT u.* FROM app_sessions s JOIN users u ON u.id=s.user_id
                     WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0""",(th,now)).fetchone()
    c.close();return row



def elite_core_post(path,payload):
    raw=json.dumps(payload).encode("utf-8")
    req=urllib.request.Request(ELITE_CORE_URL+path,data=raw,method="POST",headers={
        "Content-Type":"application/json","Accept":"application/json","User-Agent":"ESCL-Elite-Bridge/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try: detail=json.loads(e.read().decode("utf-8"))
        except Exception: detail={"error":"ELITE_CORE_HTTP_ERROR"}
        raise ValueError(detail.get("error") or "ELITE_CORE_HTTP_ERROR")

def ensure_elite_identity_columns():
    svc.ensure_v63_tables();c=db.conn()
    cols={r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    if "elite_user_id" not in cols:c.execute("ALTER TABLE users ADD COLUMN elite_user_id TEXT")
    if "elite_display_name" not in cols:c.execute("ALTER TABLE users ADD COLUMN elite_display_name TEXT")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_elite_user_id_uq ON users(elite_user_id) WHERE elite_user_id IS NOT NULL")
    c.commit();c.close()

def elite_local_user(identity):
    ensure_elite_identity_columns()
    elite_id=str(identity["elite_user_id"]);core_user=identity.get("user") or {};membership=identity.get("membership") or {}
    linked=identity.get("external_account")
    c=db.conn()
    if linked and linked.get("external_user_id"):
        u=c.execute("SELECT * FROM users WHERE id=?",(str(linked["external_user_id"]),)).fetchone()
        if not u:c.close();raise ValueError("ELITE_LINK_TARGET_MISSING")
        if u["elite_user_id"] not in (None,"",elite_id):c.close();raise ValueError("ELITE_IDENTITY_CONFLICT")
        c.execute("UPDATE users SET elite_user_id=?,elite_display_name=? WHERE id=?",(elite_id,core_user.get("display_name") or core_user.get("username"),u["id"]))
        c.commit();out=dict(c.execute("SELECT * FROM users WHERE id=?",(u["id"],)).fetchone());c.close();return out,False
    u=c.execute("SELECT * FROM users WHERE elite_user_id=?",(elite_id,)).fetchone()
    if u:c.close();return dict(u),False
    base=(core_user.get("username") or f"elite_{elite_id}").strip().lower()[:30]
    username=base
    if c.execute("SELECT 1 FROM users WHERE username=?",(username,)).fetchone():username=(f"elite_{elite_id}")[:30]
    uid="elite_"+elite_id
    role=(membership.get("role") or "PLAYER").upper()
    if role not in {"PLAYER","COACH","COMMISSIONER"}:role="PLAYER"
    c.execute("INSERT INTO users(id,username,email,email_verified,role,created_at,elite_user_id,elite_display_name) VALUES(?,?,?,?,?,?,?,?)",
              (uid,username,core_user.get("email"),1,role,db.now(),elite_id,core_user.get("display_name") or core_user.get("username")))
    c.commit();out=dict(c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone());c.close();return out,True

def send_account_mail(to,subject,body):
    provider=os.environ.get("ESCL_MAIL_PROVIDER","").upper()
    key=os.environ.get("ESCL_MAIL_API_KEY","")
    sender=os.environ.get("ESCL_MAIL_FROM","ESCL <noreply@example.invalid>")
    if provider=="RESEND" and key:
        import urllib.request
        payload=json.dumps({"from":sender,"to":[to],"subject":subject,"text":body}).encode()
        req=urllib.request.Request("https://api.resend.com/emails",data=payload,method="POST",
          headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=10) as resp:
            if resp.status not in (200,201):raise RuntimeError("MAIL_DELIVERY_FAILED")
        return True
    DEV_OUTBOX.append({"to":to,"subject":subject,"body":body,"created_at":db.now()})
    print(f"[ESCL DEV MAIL] to={to} subject={subject}")
    return True

def public_base():
    return os.environ.get("ESCL_PUBLIC_BASE_URL","http://127.0.0.1:8093").rstrip("/")

class App(BaseHTTPRequestHandler):
    def log_message(self,*args): return
    def send_bytes(self,raw,ctype,status=200):
        self.send_response(status); self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def out(self,obj,status=200):
        self.send_bytes(json.dumps(obj,default=str).encode(),"application/json; charset=utf-8",status)
    def body(self):
        n=int(self.headers.get("Content-Length","0") or 0)
        return json.loads(self.rfile.read(n) or b"{}")
    def user(self): return session_user(self.headers)
    def require_user(self):
        u=self.user()
        if not u:self.out({"error":"AUTH_REQUIRED"},401);return None
        return u
    def require_role(self,*roles):
        u=self.require_user()
        if not u:return None
        if u["role"] not in roles:self.out({"error":"FORBIDDEN"},403);return None
        return u
    def require_verified(self):
        u=self.require_user()
        if not u:return None
        if not u["email_verified"]:self.out({"error":"EMAIL_VERIFICATION_REQUIRED"},403);return None
        return u
    def do_GET(self):
        p=urlparse(self.path).path
        try:
            if p=="/": return self.send_bytes(INDEX.read_bytes(),"text/html; charset=utf-8")
            if p=="/api/health": return self.out({"ok":True,"version":"6.3","backend":"database-native","security":"production-foundation"})
            if p=="/api/readiness": return self.out(svc.readiness(),200 if svc.readiness()["ready"] else 503)
            if p=="/api/auth/verify":
                from urllib.parse import parse_qs
                token=(parse_qs(urlparse(self.path).query).get("token") or [""])[0]
                tok=svc.consume_auth_token(token,"VERIFY_EMAIL")
                if not tok:return self.out({"error":"INVALID_OR_EXPIRED_TOKEN"},400)
                c=db.conn();c.execute("UPDATE users SET email_verified=1 WHERE id=?",(tok["user_id"],));c.commit();c.close()
                return self.out({"ok":True,"verified":True})
            if p=="/api/auth/me":
                u=self.require_user()
                return self.out({"id":u["id"],"username":u["username"],"email":u["email"],"role":u["role"],"email_verified":bool(u["email_verified"])}) if u else None
            if p.startswith("/api/") and not self.require_user(): return
            if p=="/api/beta-readiness":
                u=self.require_role("COMMISSIONER")
                return self.out(svc.beta_readiness()) if u else None
            if p=="/api/legal/status":
                u=self.require_user()
                return self.out(svc.legal_status(u["id"])) if u else None
            if p=="/api/admin/audit":
                u=self.require_role("COMMISSIONER")
                return self.out(svc.audit_recent()) if u else None
            if p.startswith("/api/") and p not in ("/api/auth/me","/api/legal/status","/api/beta-readiness","/api/admin/audit"):
                if not self.require_verified():return
            if p=="/api/dashboard": return self.out(svc.dashboard())
            if p=="/api/driver-creation": return self.out(svc.creation_state())
            if p=="/api/progression": return self.out(svc.progression())
            if p=="/api/market-preview": return self.out(svc.market_preview())
            if p=="/api/career-timeline": return self.out(svc.career_timeline())
            if p=="/api/team-relationships": return self.out(svc.relationship_summary())
            if p=="/api/contracts/extension": return self.out(svc.extension_offer())
            if p=="/api/silly-season/board": return self.out(svc.silly_season_board())
            if p=="/api/career-hub": return self.out(svc.career_hub())
            if p=="/api/championship-center": return self.out(svc.championship_center())
            if p=="/api/awards": return self.out(svc.calculate_season_awards())
            if p=="/api/my-trophies": return self.out(svc.driver_trophy_case())
            if p=="/api/my-achievements": return self.out(svc.award_achievements())
            if p=="/api/record-book": return self.out(svc.record_book())
            if p=="/api/standings": return self.out(svc.standings())
            if p=="/api/schedule": return self.out(svc.schedule())
            if p=="/api/teams": return self.out(svc.teams())
            if p=="/api/weekend": return self.out(svc.weekend())
            if p=="/api/news": return self.out(svc.news())
            if p=="/api/contracts/offers": return self.out(svc.offers())
            if p=="/api/race/latest": return self.out(svc.latest_race() or {"race":None,"results":[]})
            if p.startswith("/api/race/"): return self.out(svc.race(p.rsplit("/",1)[-1]))
            if p.startswith("/api/driver/"): return self.out(svc.driver(p.rsplit("/",1)[-1]))
            return self.out({"error":"NOT_FOUND"},404)
        except ValueError as e: return self.out({"error":str(e)},404)
        except Exception as e: return self.out({"error":type(e).__name__,"detail":str(e)},500)
    def do_POST(self):
        p=urlparse(self.path).path
        try:
            b=self.body()
            ip=self.client_address[0]
            if p=="/api/auth/elite":
                token=(b.get("token") or "").strip()
                if not token:return self.out({"error":"MISSING_ELITE_TOKEN"},400)
                identity=elite_core_post("/api/gateway/consume",{"token":token,"sport":"racing"})
                if not identity.get("ok"):return self.out({"error":identity.get("error","ELITE_GATEWAY_FAILED")},401)
                local,created=elite_local_user(identity)
                if identity.get("external_account") is None:
                    linked=elite_core_post("/api/gateway/link-external",{
                        "link_ticket":identity.get("link_ticket"),"sport":"racing",
                        "external_user_id":local["id"],"external_username":local["username"],"sport_role":local["role"]})
                    if not linked.get("ok"):return self.out({"error":linked.get("error","ELITE_LINK_FAILED")},409)
                return self.out({"ok":True,"session":new_session(local["id"]),"role":local["role"],
                                 "elite_user_id":str(identity["elite_user_id"]),"created":created})
            if p=="/api/auth/register":
                if not rate_ok(ip,"register",5,3600):return self.out({"error":"RATE_LIMIT"},429)
                username=(b.get("username") or "").strip();email=(b.get("email") or "").strip().lower();password=b.get("password") or ""
                if len(username)<3 or len(username)>30 or "@" not in email or len(password)<10:return self.out({"error":"INVALID_REGISTRATION"},400)
                svc.ensure_v60_tables();c=db.conn()
                if c.execute("SELECT 1 FROM users WHERE username=? OR email=?",(username,email)).fetchone():c.close();return self.out({"error":"ACCOUNT_EXISTS"},409)
                salt,ph=pw_hash(password);uid=db.uid("user")
                c.execute("INSERT INTO users(id,username,password_hash,password_salt,email,email_verified,role,created_at) VALUES(?,?,?,?,?,0,'PLAYER',?)",(uid,username,ph,salt,email,db.now()))
                c.commit();c.close()
                token=svc.issue_auth_token(uid,"VERIFY_EMAIL",60*60*24)
                send_account_mail(email,"Verify your ESCL account",f"Verify your account: {public_base()}/api/auth/verify?token={token}")
                return self.out({"ok":True,"session":new_session(uid),"email_verified":False},201)
            if p=="/api/auth/login":
                if not rate_ok(ip,"login",10,900):return self.out({"error":"RATE_LIMIT"},429)
                ident=(b.get("login") or "").strip();password=b.get("password") or "";c=db.conn()
                u=c.execute("SELECT * FROM users WHERE (username=? OR email=?) AND disabled=0",(ident,ident.lower())).fetchone();c.close()
                if not u or not u["password_salt"]:return self.out({"error":"BAD_CREDENTIALS"},401)
                _,ph=pw_hash(password,u["password_salt"])
                if not hmac.compare_digest(ph,u["password_hash"]):return self.out({"error":"BAD_CREDENTIALS"},401)
                return self.out({"ok":True,"session":new_session(u["id"]),"role":u["role"]})
            if p=="/api/auth/forgot-password":
                if not rate_ok(ip,"forgot",5,3600):return self.out({"error":"RATE_LIMIT"},429)
                email=(b.get("email") or "").strip().lower();u=svc.account_by_email(email)
                if u:
                    token=svc.issue_auth_token(u["id"],"RESET_PASSWORD",60*30)
                    send_account_mail(email,"Reset your ESCL password",f"Reset your password: {public_base()}/reset-password?token={token}")
                return self.out({"ok":True,"message":"If that account exists, a reset message has been sent."})
            if p=="/api/auth/reset-password":
                if not rate_ok(ip,"reset",8,3600):return self.out({"error":"RATE_LIMIT"},429)
                token=b.get("token") or "";password=b.get("password") or ""
                if len(password)<10:return self.out({"error":"PASSWORD_TOO_SHORT"},400)
                tok=svc.consume_auth_token(token,"RESET_PASSWORD")
                if not tok:return self.out({"error":"INVALID_OR_EXPIRED_TOKEN"},400)
                salt,ph=pw_hash(password);c=db.conn()
                c.execute("UPDATE users SET password_hash=?,password_salt=?,session_version=session_version+1 WHERE id=?",(ph,salt,tok["user_id"]))
                c.commit();c.close();svc.revoke_user_sessions(tok["user_id"])
                return self.out({"ok":True,"password_reset":True})
            if p=="/api/auth/logout":
                u=self.require_user()
                if not u:return
                auth=self.headers.get("Authorization","");th=hashlib.sha256(auth[7:].encode()).hexdigest()
                c=db.conn();c.execute("DELETE FROM app_sessions WHERE token_hash=?",(th,));c.commit();c.close()
                return self.out({"ok":True})
            if p=="/api/auth/logout-all":
                u=self.require_user()
                if not u:return
                return self.out({"ok":True,"revoked":svc.revoke_user_sessions(u["id"])})
            if p=="/api/auth/resend-verification":
                u=self.require_user()
                if not u:return
                if u["email_verified"]:return self.out({"ok":True,"already_verified":True})
                if not rate_ok(ip,"resend",3,3600):return self.out({"error":"RATE_LIMIT"},429)
                token=svc.issue_auth_token(u["id"],"VERIFY_EMAIL",60*60*24)
                send_account_mail(u["email"],"Verify your ESCL account",f"Verify your account: {public_base()}/api/auth/verify?token={token}")
                return self.out({"ok":True})
            if p=="/api/legal/accept":
                u=self.require_user()
                if not u:return
                return self.out(svc.accept_legal(u["id"],{"TERMS":"1.0","PRIVACY":"1.0","COMMUNITY":"1.0"}))
            if p=="/api/admin/moderate":
                u=self.require_role("COMMISSIONER")
                if not u:return
                return self.out(svc.moderate(u["id"],b.get("target_user_id"),b.get("action"),b.get("reason","")))
            if p.startswith("/api/commish/"):
                cu=self.require_role("COMMISSIONER")
                if not cu:return
                svc.audit(cu["id"],"COMMISSIONER_ACTION",p,{"body_keys":sorted(list(b.keys()))})
            if p.startswith("/api/commish/") and not self.require_role("COMMISSIONER"):return
            if p.startswith("/api/") and p not in ("/api/auth/register","/api/auth/login","/api/auth/elite") and not self.require_user():return
            if p.startswith("/api/") and p not in ("/api/auth/register","/api/auth/login","/api/auth/elite","/api/auth/forgot-password","/api/auth/reset-password","/api/auth/logout","/api/auth/logout-all","/api/auth/resend-verification","/api/legal/accept","/api/admin/moderate") and not p.startswith("/api/commish/"):
                if not self.require_verified():return
            if p=="/api/driver/create": return self.out(svc.create_driver(b.get("name"),b.get("number"),b.get("attributes",{}),b.get("hometown",""),b.get("team_id")))
            if p=="/api/commish/reset-for-creation": return self.out(svc.reset_for_creation())
            if p=="/api/weekend/practice": return self.out(svc.practice(b.get("setup_bias","BALANCED"),b.get("driving_style","BALANCED"),b.get("pit_plan","STANDARD")))
            if p=="/api/weekend/qualify": return self.out(svc.qualify(int(b.get("seed",1))))
            if p=="/api/commish/sim-race": return self.out(svc.sim_race(b.get("seed")))
            if p=="/api/my-driver/upgrade": return self.out(svc.upgrade(b.get("attribute","SPD")))
            if p=="/api/offseason/prepare":
                if not self.require_role("COMMISSIONER"):return
                return self.out(svc.prepare_offseason(int(b.get("seed",3200))))
            if p=="/api/_old_offseason_prepare": return self.out(svc.prepare_offseason(int(b.get("seed",3200))))
            if p=="/api/contracts/accept": return self.out(svc.accept_offer(b.get("offer_id")))
            if p=="/api/contracts/accept-extension": return self.out(svc.accept_extension())
            if p=="/api/offseason/finalize":
                if not self.require_role("COMMISSIONER"):return
                svc.finalize_legacy_for_season()
                return self.out(svc.finalize_offseason(int(b.get("seed",3300))))
            if p=="/api/_old_offseason_finalize": return self.out(svc.finalize_offseason(int(b.get("seed",3300))))
            if p=="/api/commish/reset-demo": return self.out(svc.reset_demo())
            return self.out({"error":"NOT_FOUND"},404)
        except ValueError as e: return self.out({"error":str(e)},400)
        except Exception as e: return self.out({"error":type(e).__name__,"detail":str(e)},500)

if __name__=="__main__":
    db.init_db(); db.bootstrap_living_league(); svc.ensure_v63_tables()
    print(f"ESCL v6.3 Elite bridge on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST,PORT),App).serve_forever()
