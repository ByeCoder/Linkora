"""Read public Next.js Flight search props; never invent empty results on errors."""
import asyncio
import copy
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode, urlsplit
import httpx

PLATFORMS = {"all": "همه", "telegram": "تلگرام", "whatsapp": "واتساپ",
             "discord": "دیسکورد", "facebook": "فیسبوک"}
FILTER_KEYS = {"language", "category", "code", "creation date", "member count"}
AGES = {"last24hours": "۲۴ ساعت", "pastWeek": "هفتهٔ قبل", "pastMonth": "ماه قبل",
        "past6Months": "۶ ماه قبل", "pastYear": "سال قبل", "older5Years": "بیش از ۵ سال"}
MEMBERS = ["0-50", "51-200", "201-1000", "1001-10000", "10001-50000", "50001-100000", "100001"]


class ProviderError(Exception):
    pass


def safe_url(value):
    value = str(value or "").strip()
    try:
        u = urlsplit(value)
        return value if u.scheme in {"https", "http"} and u.hostname and not u.username and not u.password else None
    except ValueError:
        return None


def plain(value):
    return unescape(re.sub(r"<[^>]*>", "", str(value or "")))


@dataclass
class SearchPage:
    items: list[dict]
    total: int
    offset: int
    url: str
    cached: bool = False
    fetched_at: float = 0


def parse_search(html: str, query: str, offset: int, url: str) -> SearchPage:
    decoder = json.JSONDecoder()
    chunks = []
    for match in re.finditer(r"self\.__next_f\.push\(", html):
        try:
            value, _ = decoder.raw_decode(html[match.end():])
            if isinstance(value, list) and len(value) > 1 and value[0] == 1 and isinstance(value[1], str):
                chunks.append(value[1])
        except ValueError:
            continue
    def walk(obj):
        if isinstance(obj, dict):
            if {"search", "data", "totalResult", "page"} <= obj.keys():
                yield obj
            for value in obj.values():
                yield from walk(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from walk(value)
    for line in "".join(chunks).splitlines():
        try:
            obj = json.loads(line.split(":", 1)[1])
        except (ValueError, IndexError):
            continue
        for props in walk(obj):
            if props["search"] != query or str(props["page"]) != str(offset):
                continue
            if not isinstance(props["data"], list) or not isinstance(props["totalResult"], int):
                raise ProviderError("ساختار پاسخ جست‌وجو تغییر کرده است.")
            rows = []
            for hit in props["data"]:
                if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
                    raise ProviderError("ساختار نتیجهٔ سایت تغییر کرده است.")
                row = hit["_source"]
                rows.append({k: row.get(k) for k in ("id", "title", "description", "platform", "category", "language", "location", "count_members", "creation_date", "url", "slug", "like", "dislike", "type_name")})
            return SearchPage(rows, max(0, props["totalResult"]), offset, url, fetched_at=time.time())
    raise ProviderError("پاسخ معتبر جست‌وجو دریافت نشد؛ احتمال تغییر سایت یا محدودیت دسترسی وجود دارد.")


class Waybien:
    def __init__(self, config, client=None):
        self.config = config
        self.client = client or httpx.AsyncClient(timeout=40, follow_redirects=True,
            headers={"User-Agent": "WaybienTelegramBot/1.0 (public-search client)", "Accept": "text/html,application/json"})
        self.cache = OrderedDict()
        self.semaphore = asyncio.Semaphore(3)

    def search_url(self, query, platform="all", offset=0, filters=None):
        params = {"q": query, "page": str(offset)}
        if platform != "all":
            params["platform"] = platform
        params.update({k: v for k, v in (filters or {}).items() if k in FILTER_KEYS and v})
        return f"{self.config.base_url}/en/search?{urlencode(params)}"

    async def search(self, query, platform="all", offset=0, filters=None):
        if not 1 <= len(query) <= 200 or platform not in PLATFORMS or offset < 0 or offset % 10:
            raise ProviderError("پارامتر جست‌وجو معتبر نیست.")
        url = self.search_url(query, platform, offset, filters)
        if url in self.cache:
            expires, page = self.cache[url]
            if time.monotonic() < expires:
                self.cache.move_to_end(url)
                result = copy.deepcopy(page)
                result.cached = True
                return result
            del self.cache[url]
        async with self.semaphore:
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                if len(response.content) > 8_000_000:
                    raise ProviderError("پاسخ سایت بیش از حد بزرگ است.")
                page = parse_search(response.text, query, offset, url)
            except httpx.HTTPStatusError as exc:
                raise ProviderError(f"سایت پاسخ HTTP {exc.response.status_code} داد؛ بعداً دوباره تلاش کنید.") from None
            except httpx.HTTPError:
                raise ProviderError("ارتباط با Waybien برقرار نشد؛ دوباره تلاش کنید.") from None
        self.cache[url] = (time.monotonic() + self.config.cache_seconds, page)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return copy.deepcopy(page)

    async def options(self, kind):
        if kind not in {"languages", "categories", "locations"}:
            raise ProviderError("نوع فیلتر معتبر نیست.")
        async with self.semaphore:
            try:
                r = await self.client.get(f"{self.config.api_url}/api/{kind}/list")
                r.raise_for_status()
                data = r.json()
                if data.get("success") is not True or not isinstance(data.get("data"), list):
                    raise ValueError()
                return sorted(data["data"], key=lambda x: x.get("name", ""))
            except (httpx.HTTPError, ValueError, AttributeError):
                raise ProviderError("فهرست فیلترها از سایت دریافت نشد.") from None

    async def close(self):
        await self.client.aclose()
