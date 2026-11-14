import json
from pathlib import Path
import httpx
import pytest
from bot.config import Config
from bot.provider import Waybien, ProviderError, parse_search, safe_url

FIXTURE=Path(__file__).parent/"fixtures/search-python.html"


def test_actual_site_payload():
    page=parse_search(FIXTURE.read_text(),"python",0,"https://waybien.com")
    assert page.total==283 and len(page.items)==10
    assert page.items[0]["title"]=="Python"
    assert page.items[0]["platform"]=="Discord"
    assert page.items[0]["url"]=="https://discord.gg/invite/python"


def test_changed_markup_and_wrong_query_fail_instead_of_empty_results():
    for html,query,offset in [("<h1>Just a moment</h1>","python",0),(FIXTURE.read_text(),"other",0),(FIXTURE.read_text(),"python",10)]:
        with pytest.raises(ProviderError):parse_search(html,query,offset,"url")


def test_split_flight_chunks_and_empty_page():
    props={"search":"فارسی & more","data":[],"totalResult":0,"page":"0"}
    stream="1:"+json.dumps(["$","component",None,props])+"\n"
    markup="".join("<script>self.__next_f.push("+json.dumps([1,s])+")</script>" for s in [stream[:19],stream[19:]])
    page=parse_search(markup,props["search"],0,"url")
    assert page.items==[] and page.total==0


@pytest.mark.asyncio
async def test_provider_cache_filter_encoding_and_isolation():
    calls=[]
    def reply(request):
        calls.append(request)
        return httpx.Response(200,text=FIXTURE.read_text())
    client=httpx.AsyncClient(transport=httpx.MockTransport(reply))
    provider=Waybien(Config("fake",{1}),client)
    p=await provider.search("python",filters={"member count":"100001","category":"Programming"})
    p.items[0]["title"]="mutated"
    cached=await provider.search("python",filters={"member count":"100001","category":"Programming"})
    assert cached.cached and cached.items[0]["title"]=="Python" and len(calls)==1
    assert calls[0].url.params["member count"]=="100001"
    assert calls[0].url.params["category"]=="Programming"
    await provider.close()


@pytest.mark.asyncio
async def test_upstream_error_is_not_zero_results():
    provider=Waybien(Config("fake",{1}),httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))))
    with pytest.raises(ProviderError,match="403"):await provider.search("python")
    await provider.close()


def test_safe_url():
    for value in ["javascript:alert(1)","https://user:password@example.com","not a url",None,"https://["]:
        assert safe_url(value) is None
    assert safe_url("https://t.me/example")=="https://t.me/example"
