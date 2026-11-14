from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from bot.app import BotApp
from bot.config import Config
from bot.provider import parse_search
from bot.store import Store
from bot.telegram import TelegramError


@pytest.fixture
def app(tmp_path):
    db=Store(str(tmp_path/"bot.sqlite3"))
    tg=AsyncMock()
    site=AsyncMock()
    app=BotApp(Config("fake",{1},interval=1),db,tg,site)
    app.me={"id":999}
    for uid in [1,2]:db.touch({"id":uid,"first_name":f"User {uid}"})
    db.run("UPDATE users SET language_selected=1")
    yield app
    db.close()


def update(uid,text="/start"):
    return {"update_id":1,"message":{"chat":{"id":uid,"type":"private"},"from":{"id":uid,"first_name":"Name"},"text":text}}


@pytest.mark.asyncio
async def test_panel_enforces_admin_for_command_and_callback(app):
    await app.handle(update(2,"/panel"))
    assert "مخصوص مدیر" in app.tg.send.call_args.args[1]
    await app.dispatch(2,{"a":"backup"})
    app.tg.document.assert_not_called()
    await app.handle(update(1,"/panel"))
    assert "اتاق فرمان لینکورا" in app.tg.send.call_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,is_member,allowed",[("member",False,True),("administrator",False,True),("creator",False,True),("restricted",True,True),("restricted",False,False),("left",False,False),("kicked",False,False)])
async def test_force_join_statuses(app,status,is_member,allowed):
    app.db.run("INSERT INTO channels(id,title,url) VALUES('-100123','Test','https://t.me/test')")
    app.tg.call.return_value={"status":status,"is_member":is_member}
    assert await app.gate(2) is allowed


@pytest.mark.asyncio
async def test_membership_api_failure_fails_closed(app):
    app.db.run("INSERT INTO channels(id,title,url) VALUES('-100123','Test','https://t.me/test')")
    app.tg.call.side_effect=TelegramError(400,"not admin")
    await app.handle(update(2,"/search python"))
    app.site.search.assert_not_called()


@pytest.mark.asyncio
async def test_favorites_and_pagination_recheck_membership(app):
    token=app.db.callback(2,{"a":"favorites"})
    app.db.run("INSERT INTO channels(id,title,url) VALUES('-100123','Test','https://t.me/test')")
    app.tg.call.return_value={"status":"left"}
    await app.handle({"callback_query":{"id":"abc","from":{"id":2},"data":token,"message":{"chat":{"id":2,"type":"private"}}}})
    assert app.t(2,"join_prompt") in app.tg.send.call_args.args[1]


@pytest.mark.asyncio
async def test_search_success_pagination_and_stats(app):
    page=parse_search((Path(__file__).parent/"fixtures/search-python.html").read_text(),"python",0,"https://waybien.com/en/search?q=python")
    app.site.search.return_value=page
    await app.handle(update(2,"/search python"))
    rows=app.tg.send.call_args.args[2]
    assert len(rows)==14
    nav=rows[-4][0]
    data=app.db.resolve(nav["callback_data"],2)
    assert data["offset"]==10 and data["q"]=="python"
    assert app.db.stats()["کل"]["searches"]==1


@pytest.mark.asyncio
async def test_non_owner_cannot_promote_and_admin_cannot_be_banned(app):
    app.db.run("INSERT INTO admins VALUES(2)")
    await app.dispatch(2,{"a":"admin_add"})
    assert "فقط مالک" in app.tg.send.call_args.args[1]
    await app.dispatch(1,{"a":"ban","id":2})
    assert app.db.user(2)["banned"]==0


@pytest.mark.asyncio
async def test_broadcast_confirmation_cannot_send_twice(app):
    app.db.state(1,{"mode":"broadcast_confirm","text":"hello","nonce":"nonce"})
    data={"a":"broadcast_send","nonce":"nonce"}
    await app.dispatch(1,data)
    await app.dispatch(1,data)
    assert app.db.one("SELECT COUNT(*) AS n FROM jobs")["n"]==1


@pytest.mark.asyncio
async def test_home_cancels_stale_admin_state(app):
    app.db.state(1,{"mode":"welcome"})
    await app.dispatch(1,{"a":"home"})
    assert app.db.state(1)=={}


@pytest.mark.asyncio
async def test_banned_user_cannot_use_old_buttons(app):
    app.db.run("UPDATE users SET banned=1 WHERE id=2")
    await app.handle(update(2,"/search python"))
    app.site.search.assert_not_called()


@pytest.mark.asyncio
async def test_html_in_result_is_escaped(app):
    await app.detail(2,{"id":1,"title":"<b>bad</b>","description":"<script>oops</script> & text"})
    text=app.tg.send.call_args.args[1]
    assert "<script>" not in text and "&amp;" in text
