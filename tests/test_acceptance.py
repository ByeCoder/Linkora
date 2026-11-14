"""Acceptance flows through Telegram-shaped updates, with no real messages sent."""
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import pytest
from bot.app import BotApp
from bot.config import Config
from bot.experience import MENUS
from bot.i18n import LANGUAGES
from bot.provider import parse_search
from bot.store import Store,period_starts
from bot.telegram import TelegramError


@pytest.fixture
def app(tmp_path):
    store=Store(str(tmp_path/'acceptance.sqlite3'))
    tg=AsyncMock(); tg.send.return_value={'message_id':100}; tg.call.return_value={'message_id':101}
    provider=AsyncMock()
    provider.search.return_value=parse_search((Path(__file__).parent/'fixtures/search-python.html').read_text(),'python',0,'https://waybien.com/en/search?q=python')
    obj=BotApp(Config('fake',{1},interval=1),store,tg,provider)
    obj.me={'id':999,'username':'LinkoraAcceptanceBot'}
    for uid in [1,2,3]:store.touch({'id':uid,'first_name':f'Person {uid}'})
    store.run("UPDATE users SET language_selected=1")
    yield obj
    store.close()


def update(uid,text,mid=1):
    return {'update_id':mid,'message':{'message_id':mid,'chat':{'id':uid,'type':'private'},'from':{'id':uid,'first_name':f'Person {uid}'},'text':text}}


def callback(uid,token):
    return {'callback_query':{'id':'query','from':{'id':uid,'first_name':f'Person {uid}'},'data':token,'message':{'message_id':1,'chat':{'id':uid,'type':'private'}}}}


def find_button(app,uid,rows,action):
    for row in rows:
        for button in row:
            if 'callback_data' in button:
                data=app.db.resolve(button['callback_data'],uid)
                if data and data['a']==action:return button['callback_data']
    raise AssertionError('Missing button '+action)


@pytest.mark.asyncio
@pytest.mark.parametrize('lang',list(LANGUAGES))
async def test_each_home_button_routes_through_real_callback_shape_in_all_languages(app,lang):
    app.db.run('UPDATE users SET language=? WHERE id=2',(lang,))
    for _,action in MENUS:
        await app.handle(update(2,'/start'))
        rows=app.tg.send.call_args.args[2]
        token=find_button(app,2,rows,action)
        await app.handle(callback(2,token))
        assert app.tg.send.call_args.args[1]
        assert app.lang(2)==lang


@pytest.mark.asyncio
async def test_search_detail_favorite_and_saved_search_flow(app):
    await app.handle(update(2,'/search python'))
    rows=app.tg.send.call_args.args[2]
    detail=find_button(app,2,rows,'detail')
    save=find_button(app,2,rows,'save_search')
    await app.handle(callback(2,detail))
    favorite=find_button(app,2,app.tg.send.call_args.args[2],'favorite')
    await app.handle(callback(2,favorite))
    await app.handle(callback(2,save))
    assert app.db.one('SELECT COUNT(*) AS n FROM favorites WHERE uid=2')['n']==1
    assert app.db.one('SELECT COUNT(*) AS n FROM saved_searches WHERE uid=2')['n']==1
    await app.handle(callback(3,save))
    assert app.db.one('SELECT COUNT(*) AS n FROM saved_searches WHERE uid=3')['n']==0


@pytest.mark.asyncio
async def test_callback_notification_expiry_does_not_discard_owner_bound_action(app):
    token=app.db.callback(2,{'a':'support'})
    app.tg.call.side_effect=TelegramError(400,'Bad Request: query is too old and response timeout expired')
    await app.handle(callback(2,token))
    assert app.db.state(2)['mode']=='support'


@pytest.mark.asyncio
async def test_non_expiry_callback_error_is_not_hidden(app):
    token=app.db.callback(2,{'a':'support'})
    app.tg.call.side_effect=TelegramError(401,'Unauthorized')
    with pytest.raises(TelegramError):await app.handle(callback(2,token))
    assert app.db.state(2)=={}


def test_daily_stats_do_not_double_count_midnight_events(app):
    now=datetime(2026,11,14,12,tzinfo=ZoneInfo('Asia/Tehran')).timestamp()
    day=period_starts(now,app.cfg.timezone)['روز']
    for at in [day-0.01,day,day+1]:
        app.db.run("INSERT INTO events(uid,kind,at) VALUES(2,'activity',?)",(at,))
        app.db.run("INSERT INTO events(uid,kind,at) VALUES(2,'search_ok',?)",(at,))
    app.db.run('UPDATE users SET created=?',(day-86400,))
    app.db.run('UPDATE users SET created=? WHERE id=2',(day,))
    yesterday,today=app.db.daily_series(2,now)
    assert yesterday['searches']==1 and today['searches']==2
    assert yesterday['active']==1 and today['active']==1
    assert today['new']==1
    assert today['jalali_date']=='1405-08-23'


@pytest.mark.asyncio
async def test_group_upgrade_preserves_configured_destinations_and_pending_tickets(app):
    app.db.set('log_chat',-55); app.db.set('support_chat',-55)
    app.db.enqueue(-55,{'type':'ticket','uid':2,'ticket':1,'message_id':10})
    await app.handle({'message':{'message_id':2,'chat':{'id':-55,'type':'group'},'from':{'id':999,'is_bot':True},'migrate_to_chat_id':-10055}})
    assert app.db.get('log_chat')=='-10055' and app.db.get('support_chat')=='-10055'
    assert app.db.one('SELECT chat FROM outbox')['chat']==-10055
    await app.handle({'message':{'message_id':3,'chat':{'id':-10055,'type':'supergroup'},'migrate_from_chat_id':-55}})
    assert app.db.get('support_chat')=='-10055'


@pytest.mark.asyncio
async def test_daily_report_and_csv_are_admin_only(app):
    await app.dispatch(2,{'a':'export_daily'})
    app.tg.document.assert_not_called()
    await app.dispatch(1,{'a':'export_daily'})
    data=app.tg.document.call_args.args[1].decode('utf-8-sig')
    assert len(data.strip().splitlines())==31
    assert 'jalali_date' in data and 'campaigns' in data
    await app.dispatch(1,{'a':'stats_history'})
    assert 'هفت روز اخیر' in app.tg.send.call_args.args[1]
