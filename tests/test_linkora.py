import asyncio
import json
import sqlite3
import string
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import httpx
import pytest
from bot.app import BotApp
from bot.config import Config
from bot.i18n import CATALOG,LANGUAGES,detect_language
from bot.provider import parse_search
from bot.store import Store,period_starts
from bot.telegram import Telegram,TelegramError


@pytest.fixture
def app(tmp_path):
    db=Store(str(tmp_path/'bot.sqlite3'))
    tg=AsyncMock(); tg.send.return_value={'message_id':101}; tg.call.return_value={'message_id':102}
    site=AsyncMock()
    obj=BotApp(Config('fake',{1},interval=1),db,tg,site); obj.me={'id':999,'username':'LinkoraExampleBot'}
    for uid in (1,2,3):db.touch({'id':uid,'first_name':f'User {uid}','language_code':'fa'})
    db.run("UPDATE users SET language_selected=1")
    yield obj
    db.close()


def message(uid,text='',mid=10,**extra):
    return {'update_id':mid,'message':{'message_id':mid,'chat':{'id':uid,'type':'private'},'from':{'id':uid,'first_name':f'User {uid}','language_code':'fa'},'text':text,**extra}}


def test_all_eight_languages_have_matching_placeholders():
    assert len(LANGUAGES)==8
    fields=lambda text:{x[1] for x in string.Formatter().parse(text) if x[1]}
    for language in LANGUAGES:
        assert CATALOG[language].keys()==CATALOG['en'].keys()
        for key,text in CATALOG[language].items():
            assert text and fields(text)==fields(CATALOG['en'][key]),(language,key)
    assert detect_language('zh-hans')=='zh'
    assert detect_language('iw')=='he'
    assert detect_language('ru')=='en'


@pytest.mark.asyncio
@pytest.mark.parametrize('language',list(LANGUAGES))
async def test_all_user_menus_render_in_selected_language(app,language):
    app.db.run('UPDATE users SET language=? WHERE id=2',(language,))
    for action in ['home','language','profile','filters','invite','favorites','history','saved_searches','leaderboard','help','privacy']:
        await app.dispatch(2,{'a':action})
        assert app.tg.send.call_args.args[1]


@pytest.mark.asyncio
async def test_support_available_even_during_maintenance_and_force_join(app):
    app.db.set('maintenance',1)
    app.db.run("INSERT INTO channels(id,title,url) VALUES('-1001','test','https://t.me/test')")
    await app.handle(message(2,'/support'))
    await app.handle(message(2,'hello',mid=11))
    assert app.db.one('SELECT COUNT(*) AS n FROM tickets')['n']==1
    assert app.db.one('SELECT COUNT(*) AS n FROM outbox')['n']==1


@pytest.mark.asyncio
async def test_support_outbox_and_group_reply_route_to_correct_user(app):
    app.db.set('support_chat',-1009)
    await app.support_message(2,message(2,'help')['message'])
    assert await app.outbox_once()
    assert app.db.one('SELECT ticket FROM support_map WHERE chat=-1009 AND message=102')['ticket']==1
    group={'update_id':12,'message':{'message_id':12,'chat':{'id':-1009,'type':'supergroup'},'from':{'id':1},'text':'Answer','reply_to_message':{'message_id':102}}}
    await app.handle(group)
    copy=[c for c in app.tg.call.call_args_list if c.args[0]=='copyMessage'][-1]
    assert copy.kwargs['chat_id']==2 and copy.kwargs['from_chat_id']==-1009
    assert app.db.stats()['کل']['replies']==1


@pytest.mark.asyncio
async def test_ordinary_group_member_cannot_reply_as_admin(app):
    app.db.set('support_chat',-1009)
    app.db.run('INSERT INTO support_map VALUES(-1009,102,1)')
    await app.handle({'message':{'message_id':11,'chat':{'id':-1009,'type':'supergroup'},'from':{'id':3},'text':'fake answer','reply_to_message':{'message_id':102}}})
    app.tg.call.assert_not_called()


@pytest.mark.asyncio
async def test_admin_private_reply_supports_media(app):
    tid=app.db.ticket(2)
    await app.dispatch(1,{'a':'ticket_reply','id':tid})
    await app.handle(message(1,mid=14,voice={'file_id':'voice'}))
    assert app.db.one("SELECT COUNT(*) AS n FROM ticket_messages WHERE direction='admin'")['n']==1


@pytest.mark.asyncio
async def test_viewing_profile_does_not_create_ticket(app):
    await app.user_detail(1,2)
    assert app.db.one('SELECT COUNT(*) AS n FROM tickets')['n']==0


@pytest.mark.asyncio
async def test_new_user_log_contains_available_full_profile_and_photo_request(app):
    app.db.set('log_chat',-1008)
    data=message(5,'/start'); data['message']['from'].update(last_name='Example',username='example',is_premium=True)
    await app.handle(data)
    payload=json.loads(app.db.one('SELECT payload FROM outbox')['payload'])
    assert payload['uid']==5 and payload['photo'] is True
    profile=app.profile_summary(5)
    assert 'Example' in profile and '@example' in profile and 'Premium: True' in profile


@pytest.mark.asyncio
async def test_only_self_contact_is_saved(app):
    await app.handle(message(2,contact={'user_id':3,'phone_number':'+123'}))
    assert app.db.user(2)['phone']==''
    await app.handle(message(2,contact={'user_id':2,'phone_number':'+123'}))
    assert app.db.user(2)['phone']=='+123'
    await app.dispatch(2,{'a':'contact_remove'})
    assert app.db.user(2)['phone']==''


def test_referral_bind_and_reward_are_idempotent_and_new_users_only(app):
    app.db.touch({'id':5})
    app.db.bind_source(5,'r_2',True)
    app.db.bind_source(5,'r_3',True)
    assert app.db.qualify(5)==2
    assert app.db.qualify(5) is None
    assert app.db.user(2)['points']==20 and app.db.user(5)['points']==10
    app.db.bind_source(3,'r_2',False)
    app.db.bind_source(2,'r_2',True)
    assert app.db.referral_count(2)==1
    assert app.db.one('SELECT COUNT(*) AS n FROM referrals')['n']==1


@pytest.mark.asyncio
async def test_goal_counts_unique_members_and_stops_gate_at_target(app):
    app.db.run("INSERT INTO channels(id,title,url,goal) VALUES('-1001','test','https://t.me/test',2)")
    app.tg.call.return_value={'status':'member'}
    assert await app.gate(2)
    assert await app.gate(2)
    assert len(app.db.active_channels())==1
    assert await app.gate(3)
    assert app.db.active_channels()==[]
    app.tg.call.side_effect=AssertionError('must not check a completed channel')
    assert await app.gate(2)


@pytest.mark.asyncio
async def test_referral_qualification_does_not_deadlock_with_unlock_requirement(app):
    app.db.set('required_referrals',1)
    app.db.touch({'id':5})
    app.db.bind_source(5,'r_2',True)
    assert not await app.gate(5) # Hasn't invited anybody, but its parent is rewarded.
    assert app.db.referral_count(2)==1
    assert await app.gate(2)
    assert await app.gate(5,'support')


@pytest.mark.asyncio
async def test_daily_reward_once_and_redemption_is_bounded(app):
    await app.dispatch(2,{'a':'daily'}); await app.dispatch(2,{'a':'daily'})
    assert app.db.user(2)['points']==5
    app.db.run('UPDATE users SET points=1000 WHERE id=2')
    for _ in range(6):await app.dispatch(2,{'a':'redeem'})
    assert app.db.user(2)['points']==750
    assert app.quota_limit(2)==app.cfg.daily_limit+100


@pytest.mark.asyncio
async def test_leaderboard_is_opt_in(app):
    await app.dispatch(2,{'a':'leaderboard'})
    assert 'User 3' not in app.tg.send.call_args.args[1]
    await app.dispatch(2,{'a':'board_toggle'})
    assert 'User 2' in app.tg.send.call_args.args[1]


@pytest.mark.asyncio
async def test_custom_button_label_color_order_visibility_and_emoji(app):
    app.set_design('menu_search','labels',{'fa':'بیا بگردیم'})
    app.set_design('menu_search','style','success')
    app.set_design('menu_search','emoji_id','123456')
    app.set_design('menu_search','order',0)
    await app.home(2)
    button=app.tg.send.call_args.args[2][0][0]
    assert button['text']=='بیا بگردیم' and button['style']=='success' and button['icon_custom_emoji_id']=='123456'
    app.set_design('menu_search','visible',False)
    await app.home(2)
    assert not any(b['text']=='بیا بگردیم' for r in app.tg.send.call_args.args[2] for b in r)


@pytest.mark.asyncio
async def test_telegram_rejecting_emoji_falls_back_without_losing_action():
    requests=[]
    def reply(request):
        payload=json.loads(request.content); requests.append(payload)
        if len(requests)==1:return httpx.Response(400,json={'ok':False,'error_code':400,'description':'CUSTOM_EMOJI_ID_INVALID'})
        return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
    tg=Telegram('fake',httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    await tg.send(2,'hi',[[{'text':'Search','callback_data':'token','style':'success','icon_custom_emoji_id':'invalid'}]])
    b=requests[1]['reply_markup']['inline_keyboard'][0][0]
    assert b=={'text':'Search','callback_data':'token'} and tg.presentation_fallbacks==1
    await tg.close()


def test_jalali_month_starts_in_tehran():
    now=datetime(2026,11,14,12,tzinfo=ZoneInfo('Asia/Tehran')).timestamp()
    start=period_starts(now,'Asia/Tehran','jalali')['ماه شمسی']
    assert datetime.fromtimestamp(start,ZoneInfo('Asia/Tehran')).date().isoformat()=='2026-10-23'


def test_migration_preserves_v1_users_and_channels(tmp_path):
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,name TEXT,created REAL,seen REAL,banned INTEGER DEFAULT 0,reachable INTEGER DEFAULT 1,platform TEXT DEFAULT 'all',filters TEXT DEFAULT '{}'); CREATE TABLE channels(id TEXT PRIMARY KEY,title TEXT,url TEXT); INSERT INTO users(id,name,created,seen) VALUES(42,'Old user',1,1); INSERT INTO channels VALUES('-1001','Old channel','https://t.me/old');")
    db=Store(str(path))
    assert db.user(42)['name']=='Old user' and db.user(42)['points']==0
    assert db.active_channels()[0]['goal']==0
    assert db.one('PRAGMA integrity_check')['integrity_check']=='ok'
    db.close()


@pytest.mark.asyncio
async def test_segmented_broadcast_only_selected_language(app):
    app.db.run("UPDATE users SET language='en' WHERE id=2")
    job=app.db.broadcast(1,'hello','en')
    assert app.db.all('SELECT uid FROM deliveries WHERE job=?',(job,))==[{'uid':2}]


@pytest.mark.asyncio
async def test_alerts_opt_in_seed_baseline_and_report_only_changes(app):
    sid=app.db.save_search(2,'python','all',{})
    fixture=(Path(__file__).parent/'fixtures/search-python.html').read_text()
    page=parse_search(fixture,'python',0,'https://waybien.com/en/search?q=python')
    app.site.search.return_value=page
    assert not await app.alert_once() # Off by default.
    app.db.run('UPDATE saved_searches SET enabled=1 WHERE id=?',(sid,))
    assert await app.alert_once()
    assert app.db.one('SELECT COUNT(*) AS n FROM outbox')['n']==0
    app.db.run('UPDATE saved_searches SET checked=? WHERE id=?',(time.time()-22000,sid))
    page.items=[{'id':999999}]
    assert await app.alert_once()
    assert app.db.one('SELECT COUNT(*) AS n FROM outbox')['n']==1


@pytest.mark.asyncio
async def test_saved_search_limit_and_cross_user_deletion(app):
    for i in range(5):app.db.save_search(2,str(i),'all',{})
    with pytest.raises(ValueError):app.db.save_search(2,'six','all',{})
    await app.dispatch(3,{'a':'saved_delete','id':1})
    assert app.db.one('SELECT COUNT(*) AS n FROM saved_searches WHERE uid=2')['n']==5


@pytest.mark.asyncio
async def test_only_owner_can_bind_groups(app):
    app.db.run('INSERT INTO admins VALUES(2)')
    with pytest.raises(ValueError):await app.bind_group(2,'log_chat',-1009)
    app.tg.call.side_effect=[{'type':'supergroup'},{'status':'administrator'}]
    await app.bind_group(1,'log_chat',-1009)
    assert app.db.get('log_chat')=='-1009'


@pytest.mark.asyncio
async def test_all_admin_menus_render_and_do_not_leak_to_user(app):
    for a in ['panel','stats','channels','growth','tickets','integrations','design','campaigns','featured_admin','broadcast_segment']:
        await app.dispatch(1,{'a':a})
        assert app.tg.send.call_args.args[1]
        await app.dispatch(2,{'a':a})
        assert 'مخصوص مدیر' in app.tg.send.call_args.args[1]


@pytest.mark.asyncio
async def test_real_member_target_disables_requirement_at_goal(app):
    app.db.run("INSERT INTO channels(id,title,url,goal,goal_mode) VALUES('-1001','test','https://t.me/test',500,'members')")
    app.tg.call.return_value=500
    assert await app.gate(2)
    assert app.db.one("SELECT enabled FROM channels WHERE id='-1001'")['enabled']==0
    assert len(app.tg.call.call_args_list)==1


@pytest.mark.asyncio
async def test_real_member_target_still_checks_membership_below_goal(app):
    app.db.run("INSERT INTO channels(id,title,url,goal,goal_mode) VALUES('-1001','test','https://t.me/test',500,'members')")
    app.tg.call.side_effect=[499,{'status':'left'}]
    assert not await app.gate(2)
    assert app.db.one("SELECT enabled FROM channels WHERE id='-1001'")['enabled']==1


@pytest.mark.asyncio
async def test_user_cannot_read_or_close_someone_elses_ticket(app):
    tid=app.db.ticket(2)
    await app.dispatch(3,{'a':'user_ticket_close','id':tid})
    assert app.db.one('SELECT status FROM tickets WHERE id=?',(tid,))['status']=='open'
    await app.dispatch(2,{'a':'user_ticket_close','id':tid})
    assert app.db.one('SELECT status FROM tickets WHERE id=?',(tid,))['status']=='closed'


@pytest.mark.asyncio
async def test_alert_unsubscribe_also_stops_a_queued_alert(app):
    sid=app.db.save_search(2,'python','all',{})
    app.db.enqueue(2,{'type':'alert','search_id':sid,'uid':2,'query':'python','platform':'all','filters':{}})
    assert await app.outbox_once()
    app.tg.send.assert_not_called()
    assert app.db.one('SELECT state FROM outbox')['state']=='skipped'
