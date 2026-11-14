import httpx
import pytest
from bot.telegram import Telegram,TelegramError


@pytest.mark.asyncio
async def test_send_escapes_not_interprets_when_plain_text():
    calls=[]
    def reply(request):
        import json
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={"ok":True,"result":{"message_id":1}})
    tg=Telegram("test",httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    await tg.send(1,"<b>plain</b>",html=False)
    assert "parse_mode" not in calls[0]
    assert calls[0]["text"]=="<b>plain</b>"
    await tg.close()


@pytest.mark.asyncio
async def test_telegram_error_code_preserved():
    tg=Telegram("test",httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403,json={"ok":False,"error_code":403,"description":"blocked"}))))
    with pytest.raises(TelegramError) as exc:await tg.send(1,"hi")
    assert exc.value.code==403
    await tg.close()


@pytest.mark.asyncio
async def test_network_ambiguous_send_is_not_retried():
    calls=[]
    def fail(request):
        calls.append(1)
        raise httpx.ReadTimeout("ambiguous")
    tg=Telegram("test",httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    with pytest.raises(httpx.ReadTimeout):await tg.send(1,"hi")
    assert len(calls)==1
    await tg.close()
