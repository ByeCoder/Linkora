import csv
import io
import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def period_starts(now, tz, calendar="gregorian"):
    local = datetime.fromtimestamp(now, ZoneInfo(tz))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    # Persian week starts on Saturday; month uses the Gregorian calendar.
    week = day - timedelta(days=(day.weekday() - 5) % 7)
    month = day.replace(day=1)
    label="ماه میلادی"
    if calendar == "jalali":
        import jdatetime
        persian=jdatetime.date.fromgregorian(date=local.date())
        first=persian.replace(day=1).togregorian()
        month=day.replace(year=first.year,month=first.month,day=first.day)
        label="ماه شمسی"
    return {"روز": day.timestamp(), "هفته": week.timestamp(), label: month.timestamp(), "کل": 0}


def csv_bytes(rows, fields):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {}
        for key in fields:
            value = row.get(key, "")
            # Keep spreadsheet applications from evaluating user-controlled cells.
            safe[key] = "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value
        writer.writerow(safe)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


class Store:
    def __init__(self, path, tz="Asia/Tehran"):
        self.path, self.tz = path, tz
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, name TEXT,
          created REAL NOT NULL, seen REAL NOT NULL, banned INTEGER DEFAULT 0,
          reachable INTEGER DEFAULT 1, platform TEXT DEFAULT 'all', filters TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY, title TEXT, url TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, uid INTEGER, kind TEXT NOT NULL,
          at REAL NOT NULL, detail TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS events_time ON events(at,kind,uid);
        CREATE INDEX IF NOT EXISTS events_user ON events(uid,kind,at);
        CREATE TABLE IF NOT EXISTS callbacks(token TEXT PRIMARY KEY, uid INTEGER NOT NULL,
          data TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS states(uid INTEGER PRIMARY KEY, data TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS favorites(uid INTEGER, gid TEXT, data TEXT NOT NULL, at REAL,
          PRIMARY KEY(uid,gid));
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, uid INTEGER, query TEXT, at REAL);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, owner INTEGER, text TEXT, state TEXT,
          created REAL, sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS deliveries(job INTEGER, uid INTEGER, state TEXT DEFAULT 'pending',
          PRIMARY KEY(job,uid), FOREIGN KEY(job) REFERENCES jobs(id));
        ''')
        self.migrate()

    def migrate(self):
        additions={"users":{"last_name":"TEXT DEFAULT ''","tg_language":"TEXT DEFAULT ''","language":"TEXT DEFAULT 'fa'",
            "is_premium":"INTEGER DEFAULT 0","phone":"TEXT DEFAULT ''","points":"INTEGER DEFAULT 0",
            "streak":"INTEGER DEFAULT 0","last_checkin":"TEXT DEFAULT ''","leaderboard":"INTEGER DEFAULT 0","onboarded":"INTEGER DEFAULT 0","profile_extra":"TEXT DEFAULT '{}'","language_selected":"INTEGER DEFAULT 0"},
            "channels":{"goal":"INTEGER DEFAULT 0","enabled":"INTEGER DEFAULT 1","goal_mode":"TEXT DEFAULT 'verified'"},
            "jobs":{"language":"TEXT DEFAULT ''"}}
        for table,columns in additions.items():
            existing={x[1] for x in self.db.execute(f"PRAGMA table_info({table})")}
            for column,definition in columns.items():
                if column not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    if table=='users' and column=='language_selected':
                        self.db.execute('UPDATE users SET language_selected=1 WHERE onboarded=1')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS referrals(invitee INTEGER PRIMARY KEY,inviter INTEGER NOT NULL,created REAL,qualified REAL);
        CREATE INDEX IF NOT EXISTS referrals_parent ON referrals(inviter,qualified);
        CREATE TABLE IF NOT EXISTS joins(channel TEXT,uid INTEGER,at REAL,PRIMARY KEY(channel,uid));
        CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY,uid INTEGER NOT NULL,status TEXT DEFAULT 'open',created REAL,updated REAL);
        CREATE INDEX IF NOT EXISTS tickets_user ON tickets(uid,status);
        CREATE TABLE IF NOT EXISTS ticket_messages(id INTEGER PRIMARY KEY,ticket INTEGER,direction TEXT,chat INTEGER,message INTEGER,created REAL);
        CREATE TABLE IF NOT EXISTS support_map(chat INTEGER,message INTEGER,ticket INTEGER,PRIMARY KEY(chat,message));
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,chat INTEGER,payload TEXT,state TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,created REAL);
        CREATE TABLE IF NOT EXISTS saved_searches(id INTEGER PRIMARY KEY,uid INTEGER,query TEXT,platform TEXT,filters TEXT,
            enabled INTEGER DEFAULT 0,seen TEXT DEFAULT '[]',checked REAL DEFAULT 0,created REAL);
        CREATE TABLE IF NOT EXISTS featured(id INTEGER PRIMARY KEY,title TEXT,query TEXT,language TEXT DEFAULT 'all',position INTEGER DEFAULT 0,enabled INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS campaigns(token TEXT PRIMARY KEY,title TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS campaign_users(uid INTEGER PRIMARY KEY,token TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS button_config(key TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS custom_links(id INTEGER PRIMARY KEY,data TEXT NOT NULL,position INTEGER DEFAULT 0,enabled INTEGER DEFAULT 1);
        ''')
        self.db.commit()
        self.set("schema_version",2)

    def run(self, sql, args=()):
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row is not None else None

    def all(self, sql, args=()):
        return [dict(x) for x in self.db.execute(sql, args).fetchall()]

    def get(self, key, default=""):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set(self, key, value):
        self.run("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(value)))

    def touch(self, user):
        now = time.time()
        fresh=self.user(user["id"]) is None
        self.run('''INSERT INTO users(id,username,name,created,seen) VALUES(?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET username=excluded.username,name=excluded.name,
          seen=excluded.seen,reachable=1''',
          (user["id"], user.get("username", ""), user.get("first_name", ""), now, now))
        from .i18n import detect_language
        self.run("UPDATE users SET last_name=?,tg_language=?,is_premium=? WHERE id=?",
            (user.get("last_name",""),user.get("language_code",""),int(user.get("is_premium",False)),user["id"]))
        if fresh:self.run("UPDATE users SET language=? WHERE id=?",(detect_language(user.get("language_code","fa")),user["id"]))
        return fresh

    def user(self, uid):
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def admin(self, uid, owners):
        return uid in owners or bool(self.one("SELECT id FROM admins WHERE id=?", (uid,)))

    def event(self, uid, kind, detail=""):
        self.run("INSERT INTO events(uid,kind,at,detail) VALUES(?,?,?,?)", (uid, kind, time.time(), str(detail)[:500]))

    def callback(self, uid, data, ttl=86400):
        token = secrets.token_urlsafe(12)
        self.run("INSERT INTO callbacks VALUES(?,?,?,?)", (token, uid, json.dumps(data, ensure_ascii=False), time.time()+ttl))
        return token

    def resolve(self, token, uid):
        row = self.one("SELECT data FROM callbacks WHERE token=? AND uid=? AND expires>?", (token, uid, time.time()))
        return json.loads(row["data"]) if row else None

    def state(self, uid, data=None):
        if data is not None:
            self.run("INSERT OR REPLACE INTO states VALUES(?,?,?)", (uid, json.dumps(data, ensure_ascii=False), time.time()+1800))
            return data
        row = self.one("SELECT data FROM states WHERE uid=? AND expires>?", (uid, time.time()))
        return json.loads(row["data"]) if row else {}

    def clear_state(self, uid):
        self.run("DELETE FROM states WHERE uid=?", (uid,))

    def filters(self, uid):
        return json.loads(self.user(uid)["filters"])

    def filter_set(self, uid, key, value):
        filters = self.filters(uid)
        if value:
            filters[key] = value
        else:
            filters.pop(key, None)
        self.run("UPDATE users SET filters=? WHERE id=?", (json.dumps(filters), uid))

    def quota(self, uid, interval, limit):
        now = time.time()
        last = self.one("SELECT MAX(at) AS at FROM events WHERE uid=? AND kind='search_request'", (uid,))["at"]
        if last and now-last < interval:
            return "لطفاً چند ثانیه بین درخواست‌ها فاصله بگذارید."
        day = period_starts(now, self.tz)["روز"]
        n = self.one("SELECT COUNT(*) AS n FROM events WHERE uid=? AND kind='search_request' AND at>=?", (uid, day))["n"]
        if n >= limit:
            return "سهمیهٔ جست‌وجوی امروز تمام شده است."
        self.event(uid, "search_request")
        return None

    def stats(self, now=None):
        now = time.time() if now is None else now
        return {name:self.aggregate(start,now,inclusive=True)
                for name,start in period_starts(now,self.tz,self.get('calendar','jalali')).items()}

    def aggregate(self,start,end,inclusive=False):
        upper='<=' if inclusive else '<'
        row = self.one(f'''SELECT COUNT(DISTINCT CASE WHEN kind='activity' THEN uid END) AS active,
              SUM(kind='search_request') AS requests,SUM(kind='search_ok') AS searches,
              SUM(kind='search_error') AS errors,SUM(kind='open_link') AS opens,
              SUM(kind='membership_denied') AS denied FROM events WHERE at>=? AND at{upper}?''',(start,end))
        row={k:v or 0 for k,v in row.items()}
        row['new']=self.one(f'SELECT COUNT(*) AS n FROM users WHERE created>=? AND created{upper}?',(start,end))['n']
        row['referrals']=self.one(f'SELECT COUNT(*) AS n FROM referrals WHERE qualified>=? AND qualified{upper}?',(start,end))['n']
        row['tickets']=self.one(f'SELECT COUNT(*) AS n FROM tickets WHERE created>=? AND created{upper}?',(start,end))['n']
        row['replies']=self.one(f"SELECT COUNT(*) AS n FROM ticket_messages WHERE direction='admin' AND created>=? AND created{upper}?",(start,end))['n']
        row['campaigns']=self.one(f'SELECT COUNT(*) AS n FROM campaign_users WHERE at>=? AND at{upper}?',(start,end))['n']
        return row

    def daily_series(self,days=30,now=None):
        if not isinstance(days,int) or not 1<=days<=90:raise ValueError('days must be between 1 and 90')
        import jdatetime
        now=time.time() if now is None else now
        today=datetime.fromtimestamp(now,ZoneInfo(self.tz)).replace(hour=0,minute=0,second=0,microsecond=0)
        rows=[]
        for n in range(days-1,-1,-1):
            day=today-timedelta(days=n)
            end=now if n==0 else (day+timedelta(days=1)).timestamp()
            rows.append({'date':day.date().isoformat(),'jalali_date':jdatetime.date.fromgregorian(date=day.date()).isoformat(),
                         **self.aggregate(day.timestamp(),end,inclusive=n==0)})
        return rows

    def migrate_group(self,old,new):
        if not all(isinstance(x,int) and x<0 for x in (old,new)) or old==new:return False
        # Historical message IDs are not assumed to be identical after migration.
        # Only configured destinations and unsent outbox items change atomically.
        with self.db:
            configured=self.db.execute("UPDATE settings SET value=? WHERE key IN ('log_chat','support_chat') AND value=?",(str(new),str(old))).rowcount
            if configured:self.db.execute("UPDATE outbox SET chat=? WHERE chat=? AND state='pending'",(new,old))
        return bool(configured)

    def remember(self, uid, query):
        self.run("INSERT INTO history(uid,query,at) VALUES(?,?,?)", (uid, query, time.time()))
        self.run("DELETE FROM history WHERE uid=? AND id NOT IN (SELECT id FROM history WHERE uid=? ORDER BY id DESC LIMIT 20)", (uid, uid))

    def favorite(self, uid, row):
        gid = str(row["id"])
        if self.one("SELECT gid FROM favorites WHERE uid=? AND gid=?", (uid, gid)):
            self.run("DELETE FROM favorites WHERE uid=? AND gid=?", (uid, gid))
            return False
        count = self.one("SELECT COUNT(*) AS n FROM favorites WHERE uid=?", (uid,))["n"]
        if count >= 200:
            raise ValueError("حداکثر ۲۰۰ علاقه‌مندی مجاز است.")
        self.run("INSERT INTO favorites VALUES(?,?,?,?)", (uid, gid, json.dumps(row, ensure_ascii=False), time.time()))
        return True

    def broadcast(self, uid, text, language=""):
        # Snapshot recipients and create the job atomically.
        with self.db:
            job = self.db.execute("INSERT INTO jobs(owner,text,state,created,language) VALUES(?,?,'running',?,?)", (uid, text, time.time(),language)).lastrowid
            self.db.execute("INSERT INTO deliveries(job,uid) SELECT ?,id FROM users WHERE banned=0 AND reachable=1 AND (?='' OR language=?)", (job,language,language))
        return job

    def bind_source(self,uid,payload,fresh):
        if not fresh or not payload:return
        if payload.startswith("r_") and payload[2:].isdigit():
            parent=int(payload[2:])
            if parent!=uid and self.user(parent) and not self.user(parent)["banned"]:
                self.run("INSERT OR IGNORE INTO referrals VALUES(?,?,?,NULL)",(uid,parent,time.time()))
        elif payload.startswith("c_"):
            token=payload[2:]
            if self.one("SELECT token FROM campaigns WHERE token=?",(token,)):
                self.run("INSERT OR IGNORE INTO campaign_users VALUES(?,?,?)",(uid,token,time.time()))

    def qualify(self,uid):
        ref=self.one("SELECT * FROM referrals WHERE invitee=? AND qualified IS NULL",(uid,))
        if not ref:return None
        parent=self.user(ref['inviter'])
        if not parent or parent['banned']:return None
        with self.db:
            changed=self.db.execute("UPDATE referrals SET qualified=? WHERE invitee=? AND qualified IS NULL",(time.time(),uid)).rowcount
            if changed:
                reward=max(0,min(1000,int(self.get("referral_points","20"))))
                self.db.execute("UPDATE users SET points=points+? WHERE id=?",(reward,ref["inviter"]))
                self.db.execute("UPDATE users SET points=points+? WHERE id=?",(reward//2,uid))
                return ref["inviter"]
        return None

    def referral_count(self,uid):
        return self.one("SELECT COUNT(*) AS n FROM referrals WHERE inviter=? AND qualified IS NOT NULL",(uid,))["n"]

    def active_channels(self):
        return self.all('''SELECT c.*, (SELECT COUNT(*) FROM joins j WHERE j.channel=c.id) AS verified
            FROM channels c WHERE enabled=1 AND (goal_mode='members' OR goal=0 OR (SELECT COUNT(*) FROM joins j WHERE j.channel=c.id)<goal)''')

    def join_verified(self,channel,uid):
        self.run("INSERT OR IGNORE INTO joins VALUES(?,?,?)",(channel,uid,time.time()))

    def enqueue(self,chat,payload):
        if chat:self.run("INSERT INTO outbox(chat,payload,created) VALUES(?,?,?)",(int(chat),json.dumps(payload,ensure_ascii=False),time.time()))

    def ticket(self,uid):
        existing=self.one("SELECT * FROM tickets WHERE uid=? AND status='open' ORDER BY id DESC LIMIT 1",(uid,))
        if existing:return existing["id"]
        now=time.time()
        return self.run("INSERT INTO tickets(uid,created,updated) VALUES(?,?,?)",(uid,now,now)).lastrowid

    def save_search(self,uid,query,platform,filters):
        encoded=json.dumps(filters,sort_keys=True)
        old=self.one("SELECT id FROM saved_searches WHERE uid=? AND query=? AND platform=? AND filters=?",(uid,query,platform,encoded))
        if old:return old["id"]
        if self.one("SELECT COUNT(*) AS n FROM saved_searches WHERE uid=?",(uid,))["n"]>=5:raise ValueError("saved_limit")
        return self.run("INSERT INTO saved_searches(uid,query,platform,filters,created) VALUES(?,?,?,?,?)",(uid,query,platform,encoded,time.time())).lastrowid

    def backup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"backup.sqlite3"
            dest = sqlite3.connect(path)
            self.db.backup(dest)
            dest.close()
            return path.read_bytes()

    def cleanup(self):
        for table in ("callbacks", "states"):
            self.run(f"DELETE FROM {table} WHERE expires<?", (time.time(),))

    def close(self):
        self.db.close()
