from unittest.mock import AsyncMock
import pytest
from bot.app import BotApp
from bot.config import Config
from bot.i18n import LANGUAGES
from bot.store import Store


@pytest.fixture
def app(tmp_path):
    db=Store(str(tmp_path/'language.sqlite3'))
    tg=AsyncMock(); tg.call.return_value=True
    obj=BotApp(Config('fake',{1}),db,tg,AsyncMock()); obj.me={'id':999,'username':'LinkoraExampleBot'}
    yield obj
    db.close()


def message(text='/start'):
    return {'message':{'message_id':1,'chat':{'id':2,'type':'private'},'from':{'id':2,'first_name':'New friend','language_code':'fa'},'text':text}}


def click(token,uid=2):
    return {'callback_query':{'id':'query','from':{'id':uid},'data':token,'message':{'chat':{'id':uid,'type':'private'}}}}


@pytest.mark.asyncio
async def test_first_screen_only_offers_all_eight_languages(app):
    await app.handle(message())
    assert app.db.user(2)['language_selected']==0
    rows=app.tg.send.call_args.args[2]
    actions=[app.db.resolve(b['callback_data'],2) for r in rows for b in r]
    assert len(actions)==8
    assert {x['value'] for x in actions}==set(LANGUAGES)
    assert all(x['a']=='language_set' for x in actions)
    assert not app.db.user(2)['onboarded']


@pytest.mark.asyncio
@pytest.mark.parametrize('language',list(LANGUAGES))
async def test_explicit_language_choice_opens_translated_home_and_persists(app,language):
    await app.handle(message())
    token=next(b['callback_data'] for row in app.tg.send.call_args.args[2] for b in row
               if app.db.resolve(b['callback_data'],2)['value']==language)
    await app.handle(click(token))
    assert app.db.user(2)['language']==language
    assert app.db.user(2)['language_selected']==1
    assert app.db.user(2)['onboarded']==1
    await app.handle(message())
    rows=app.tg.send.call_args.args[2]
    assert any(app.db.resolve(b['callback_data'],2).get('a')=='search_prompt' for r in rows for b in r if 'callback_data' in b)


@pytest.mark.asyncio
async def test_search_before_choice_cannot_skip_language_screen(app):
    await app.handle(message('/search python'))
    app.site.search.assert_not_called()
    assert 'Choose your language' in app.tg.send.call_args.args[1]


@pytest.mark.asyncio
async def test_referral_source_survives_language_onboarding(app):
    app.db.touch({'id':3})
    await app.handle(message('/start r_3'))
    assert app.db.one('SELECT inviter FROM referrals WHERE invitee=2')['inviter']==3
    assert app.db.user(2)['language_selected']==0
