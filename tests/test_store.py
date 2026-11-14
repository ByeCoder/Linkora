from datetime import datetime,timezone
import sqlite3
from zoneinfo import ZoneInfo
import pytest
from bot.store import Store,period_starts,csv_bytes


@pytest.fixture
def db(tmp_path):
    store=Store(str(tmp_path/"bot.sqlite3"))
    yield store
    store.close()


def test_iran_midnight_week_and_month():
    local=datetime(2026,11,14,0,1,tzinfo=ZoneInfo("Asia/Tehran")) # Saturday
    starts=period_starts(local.timestamp(),"Asia/Tehran")
    assert starts["روز"]==starts["هفته"]
    assert datetime.fromtimestamp(starts["روز"],timezone.utc).isoformat()=="2026-11-13T20:30:00+00:00"
    assert datetime.fromtimestamp(starts["ماه میلادی"],ZoneInfo("Asia/Tehran")).day==1


def test_unique_users_counts_and_event_boundaries(db):
    now=datetime(2026,11,14,12,tzinfo=ZoneInfo("Asia/Tehran")).timestamp()
    day=period_starts(now,db.tz)["روز"]
    for uid in [1,2]:
        db.touch({"id":uid,"first_name":"a"})
        db.run("UPDATE users SET created=? WHERE id=?",(day+1,uid))
    for uid,kind,at in [(1,"activity",day),(1,"activity",day+1),(2,"activity",day-1),(1,"search_ok",day+2),(2,"search_error",day-2)]:
        db.run("INSERT INTO events(uid,kind,at) VALUES(?,?,?)",(uid,kind,at))
    stats=db.stats(now)
    assert stats["روز"]["active"]==1 and stats["کل"]["active"]==2
    assert stats["روز"]["searches"]==1 and stats["روز"]["errors"]==0
    assert stats["کل"]["errors"]==1 and stats["روز"]["new"]==2


def test_persistent_owner_bound_callbacks(db):
    token=db.callback(1,{"a":"panel"})
    assert db.resolve(token,1)=={"a":"panel"}
    assert db.resolve(token,2) is None
    expired=db.callback(1,{"a":"panel"},ttl=-1)
    assert db.resolve(expired,1) is None


def test_quota_counts_requests_across_restart(db):
    assert db.quota(1,3,2) is None
    assert db.quota(1,3,2) is not None
    db.run("UPDATE events SET at=at-5")
    assert db.quota(1,3,2) is None
    assert db.quota(1,0,2) is not None


def test_backup_is_consistent_and_readable(db,tmp_path):
    db.touch({"id":42,"first_name":"Pouya"})
    path=tmp_path/"copy.sqlite3"
    path.write_bytes(db.backup())
    with sqlite3.connect(path) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert copy.execute("SELECT name FROM users WHERE id=42").fetchone()[0]=="Pouya"


def test_csv_formula_injection_protected():
    data=csv_bytes([{"title":"=HYPERLINK(\"bad\")","id":1}],["id","title"]).decode("utf-8-sig")
    assert "'=HYPERLINK" in data


def test_broadcast_snapshots_eligible_recipients(db):
    for uid in [1,2,3]:db.touch({"id":uid})
    db.run("UPDATE users SET banned=1 WHERE id=2")
    db.run("UPDATE users SET reachable=0 WHERE id=3")
    job=db.broadcast(1,"hello")
    assert db.all("SELECT uid FROM deliveries WHERE job=?",(job,))==[{"uid":1}]
