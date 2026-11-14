import asyncio
import html
import json
import logging
import re
import time
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote
from .provider import AGES, MEMBERS, PLATFORMS, ProviderError, plain, safe_url
from .store import csv_bytes
from .telegram import TelegramError

log = logging.getLogger(__name__)
ADMIN_ACTIONS = {"panel", "stats", "channels", "channel_add", "channel_delete", "toggle", "welcome",
    "limit", "users", "user_find", "user_detail", "ban", "admins", "admin_add", "admin_delete",
    "broadcast", "broadcast_send", "jobs", "job_cancel", "export_users", "export_stats", "backup", "health"}
FIELDS = ["id", "title", "platform", "description", "category", "language", "location", "count_members", "creation_date", "url"]


def esc(value):
    return html.escape(str(value if value is not None else "نامشخص"))


class BotApp:
    def __init__(self, config, store, telegram, provider):
        self.cfg, self.db, self.tg, self.site = config, store, telegram, provider
        self.me = None

    def is_admin(self, uid):
        return self.db.admin(uid, self.cfg.owners)

    def button(self, uid, text, action, **params):
        return {"text": text, "callback_data": self.db.callback(uid, {"a": action, **params})}

    def back(self, uid, admin=False):
        return [self.button(uid, "🏠 پنل مدیریت" if admin else "🏠 منوی اصلی", "panel" if admin else "home")]

    async def gate(self, uid):
        user = self.db.user(uid)
        if user["banned"]:
            await self.tg.send(uid, "دسترسی شما توسط مدیر مسدود شده است.")
            return False
        if self.is_admin(uid):
            return True
        if self.db.get("maintenance", "0") == "1":
            await self.tg.send(uid, "ربات موقتاً در حال نگهداری است؛ بعداً تلاش کنید.")
            return False
        if self.db.get("force_join", "1") != "1":
            return True
        missing, errors = [], False
        for channel in self.db.all("SELECT * FROM channels"):
            try:
                member = await self.tg.call("getChatMember", chat_id=channel["id"], user_id=uid)
                joined = member["status"] in {"creator", "administrator", "member"} or (member["status"] == "restricted" and member.get("is_member") is True)
                if not joined:
                    missing.append(channel)
            except (TelegramError, httpx.HTTPError):
                errors = True
        if missing or errors:
            self.db.event(uid, "membership_denied")
            rows = [[{"text": "عضویت در "+c["title"], "url": c["url"]}] for c in missing]
            rows.append([self.button(uid, "✅ بررسی دوبارهٔ عضویت", "check_join")])
            text = "برای استفاده از ربات ابتدا در کانال‌های زیر عضو شوید."
            if errors:
                text += "\nبررسی یکی از کانال‌ها ممکن نشد. مدیر باید دسترسی ادمین ربات را بررسی کند."
            await self.tg.send(uid, text, rows)
            return False
        return True

    async def home(self, uid):
        user = self.db.user(uid)
        rows = [
            [self.button(uid, "🔍 جست‌وجو", "search_prompt"), self.button(uid, "🌐 انتخاب پلتفرم", "platforms")],
            [self.button(uid, "⚙️ فیلترهای جست‌وجو", "filters")],
            [self.button(uid, "⭐ علاقه‌مندی‌ها", "favorites"), self.button(uid, "🕘 تاریخچه", "history")],
            [{"text": "➕ ثبت گروه در سایت", "url": self.cfg.base_url+"/en/add-new-group"},
             {"text": "حساب و امکانات سایت", "url": self.cfg.base_url+"/en/dashboard"}],
            [self.button(uid, "راهنما", "help")]]
        if self.is_admin(uid):
            rows.append([self.button(uid, "🛠 پنل مدیریت", "panel")])
        await self.tg.send(uid, esc(self.db.get("welcome", "سلام! موضوع دلخواهت را بفرست تا گروه‌ها و کانال‌های Waybien را پیدا کنم."))+
            "\n\nپلتفرم فعلی: "+PLATFORMS.get(user["platform"], "همه"), rows)

    async def help(self, uid):
        await self.tg.send(uid, "<b>راهنمای ربات</b>\nموضوع را تایپ کن یا /search python بفرست.\n"
            "/start منو • /filters فیلتر • /favorites علاقه‌مندی\n/history تاریخچه • /id شناسه • /cancel لغو ورودی\n"
            "هر صفحه ۱۰ نتیجهٔ واقعی سایت دارد؛ دکمه‌های قبلی/بعدی ادامهٔ فهرست را می‌آورند.\n"
            "برای جزئیات، ذخیره و لینک ورود، دکمهٔ هر نتیجه را بزن.\n"
            "ثبت، ویرایش، امتیازدهی و تبلیغات حساب Waybien از لینک سایت انجام می‌شود.\n"
            "آمار پنل مربوط به خود ربات است؛ تعداد اعضای گروه‌ها را سایت گزارش می‌کند.\n"
            "دادهٔ ذخیره‌شده: شناسه و نام تلگرام، زمان فعالیت، رویدادها و ۲۰ جست‌وجوی آخر؛ "
            "تاریخچه و علاقه‌مندی از منو پاک می‌شوند. پشتیبانی: مدیر همین ربات.", [self.back(uid)])

    async def search(self, uid, query, offset=0, platform=None, filters=None, remember=True):
        query = query.strip()
        if not 1 <= len(query) <= 200:
            return await self.tg.send(uid, "متن جست‌وجو باید بین ۱ تا ۲۰۰ نویسه باشد.")
        problem = self.db.quota(uid, self.cfg.interval, int(self.db.get("daily_limit", self.cfg.daily_limit)))
        if problem:
            return await self.tg.send(uid, problem)
        platform = platform or self.db.user(uid)["platform"]
        filters = self.db.filters(uid) if filters is None else filters
        await self.tg.send(uid, "🔎 در حال دریافت نتایج از Waybien…")
        try:
            page = await self.site.search(query, platform, offset, filters)
        except ProviderError as exc:
            self.db.event(uid, "search_error", type(exc).__name__)
            return await self.tg.send(uid, esc(str(exc)), [[self.button(uid, "تلاش دوباره", "search", q=query, offset=offset, platform=platform, filters=filters)], self.back(uid)])
        self.db.event(uid, "search_ok", query)
        if page.cached:
            self.db.event(uid, "cache_hit")
        if remember:
            self.db.remember(uid, query)
        stamp = datetime.fromtimestamp(page.fetched_at, ZoneInfo(self.cfg.timezone)).strftime("%H:%M:%S")
        text = f"<b>نتایج {esc(query)}</b>\nپلتفرم: {PLATFORMS[platform]} | کل اعلام‌شدهٔ سایت: {page.total:,}\n"
        text += "صفحه: "+str(offset//10+1)+f" | دریافت: {stamp}"+(" (از حافظهٔ موقت)" if page.cached else "")+"\n"
        if filters:
            text += "فیلترها: "+esc("، ".join(f"{k}: {v}" for k,v in filters.items()))+"\n"
        rows = []
        for i, row in enumerate(page.items, offset+1):
            title = plain(row.get("title"))[:85] or "بدون عنوان"
            text += f"\n{i}. <b>{esc(title)}</b>\n{esc(row.get('platform'))} | اعضا: {esc(row.get('count_members'))}\n"
            rows.append([self.button(uid, f"{i}. {title}"[:60], "detail", row=row)])
        if not page.items:
            text += "\nدر این صفحه نتیجه‌ای برنگشت. فیلترها یا عبارت را تغییر بده."
        nav = []
        base = {"q": query, "platform": platform, "filters": filters}
        if offset:
            nav.append(self.button(uid, "⬅️ قبلی", "search", offset=max(0, offset-10), **base))
        if page.items and offset+10 < page.total:
            nav.append(self.button(uid, "بعدی ➡️", "search", offset=offset+10, **base))
        if nav:
            rows.append(nav)
        rows.append([self.button(uid, "📥 CSV همین صفحه", "export_page", items=page.items), {"text": "نمایش در سایت", "url": page.url}])
        rows.append(self.back(uid))
        await self.tg.send(uid, text, rows)

    async def detail(self, uid, row):
        text = f"<b>{esc(plain(row.get('title'))[:200])}</b>\n\n{esc(plain(row.get('description'))[:1800])}\n\n"
        for key, label in [("platform","پلتفرم"),("type_name","نوع"),("category","دسته"),("language","زبان"),
                           ("location","کشور"),("count_members","اعضا طبق سایت"),("creation_date","تاریخ ایجاد طبق سایت"),
                           ("like","پسندهای سایت"),("dislike","نپسندهای سایت")]:
            text += f"{label}: {esc(row.get(key))}\n"
        text += "\nاین اطلاعات از سایت خوانده شده؛ تعداد اعضا تأیید مستقل نشده است."
        rows = [[self.button(uid, "🔗 دریافت لینک ورود", "open", row=row), self.button(uid, "⭐ ذخیره / حذف", "favorite", row=row)], self.back(uid)]
        if row.get("slug") and row.get("platform"):
            url=self.cfg.base_url+"/en/details/"+quote(str(row["platform"]).lower(),safe="")+"/"+quote(str(row["slug"]),safe="")
            rows.insert(1,[{"text":"جزئیات، امتیازدهی و گزارش در سایت","url":url}])
        await self.tg.send(uid, text, rows)

    async def filters(self, uid):
        current = self.db.filters(uid)
        text = "<b>فیلترهای جست‌وجو</b>\n"+(esc("\n".join(f"{k}: {v}" for k,v in current.items())) or "فیلتری فعال نیست.")
        rows = [[self.button(uid, "زبان", "options", kind="languages", page=0), self.button(uid, "دسته‌بندی", "options", kind="categories", page=0)],
                [self.button(uid, "کشور", "options", kind="locations", page=0), self.button(uid, "قدمت گروه", "options", kind="age", page=0)],
                [self.button(uid, "تعداد اعضا", "options", kind="members", page=0), self.button(uid, "پاک‌کردن همهٔ فیلترها", "filters_clear")], self.back(uid)]
        await self.tg.send(uid, text, rows)

    async def options(self, uid, kind, page):
        if kind == "age":
            key, options = "creation date", [(k,v) for k,v in AGES.items()]
        elif kind == "members":
            key, options = "member count", [(x, "100K+" if x == "100001" else x) for x in MEMBERS]
        else:
            key = {"languages":"language", "categories":"category", "locations":"code"}[kind]
            values = await self.site.options(kind)
            options = [(str(x.get("code") if kind == "locations" else x["name"]), x["name"]) for x in values]
        start = max(0,page)*8
        rows = [[self.button(uid, str(label)[:60], "filter_set", key=key, value=value)] for value,label in options[start:start+8]]
        nav = []
        if page:
            nav.append(self.button(uid, "قبلی", "options", kind=kind, page=page-1))
        if start+8 < len(options):
            nav.append(self.button(uid, "بعدی", "options", kind=kind, page=page+1))
        if nav:
            rows.append(nav)
        rows += [[self.button(uid, "حذف این فیلتر", "filter_set", key=key, value="")], [self.button(uid, "بازگشت به فیلترها", "filters")]]
        await self.tg.send(uid, "گزینهٔ موردنظر را انتخاب کن:", rows)

    async def panel(self, uid):
        total = self.db.one("SELECT COUNT(*) AS n FROM users")["n"]
        rows = [[self.button(uid, "📊 آمار دقیق", "stats"), self.button(uid, "👥 کاربران", "users", page=0)],
            [self.button(uid, "🔐 کانال‌های اجباری", "channels"), self.button(uid, "مدیران", "admins")],
            [self.button(uid, "📣 پیام همگانی", "broadcast"), self.button(uid, "صف ارسال‌ها", "jobs")],
            [self.button(uid, "متن خوشامد", "welcome"), self.button(uid, "سهمیهٔ روزانه", "limit")],
            [self.button(uid, "نگهداری: "+("روشن" if self.db.get("maintenance","0")=="1" else "خاموش"), "toggle", key="maintenance")],
            [self.button(uid, "CSV کاربران", "export_users"), self.button(uid, "CSV آمار", "export_stats")],
            [self.button(uid, "پشتیبان دیتابیس", "backup"), self.button(uid, "سلامت اتصال", "health")], self.back(uid)]
        await self.tg.send(uid, f"<b>پنل مدیریت</b>\nکاربران ثبت‌شده: {total:,}\nشناسهٔ شما: <code>{uid}</code>", rows)

    async def stats(self, uid):
        rows = self.db.stats()
        text = "<b>آمار ربات</b>\nمنطقهٔ زمانی: "+esc(self.cfg.timezone)+"\nهفته از شنبه؛ ماه از اول ماه میلادی؛ پایان بازه همین لحظه است.\n"
        for label, row in rows.items():
            text += f"\n<b>{label}</b>\nعضو جدید: {row['new']} | فعال یکتا: {row['active']}\nدرخواست جست‌وجو: {row['requests']} | موفق: {row['searches']} | خطا: {row['errors']}\nدرخواست لینک: {row['opens']} | رد عضویت: {row['denied']}\n"
        counts = self.db.one("SELECT SUM(banned) AS banned,SUM(reachable=0) AS unreachable FROM users")
        text += f"\nمسدود توسط مدیر: {counts['banned'] or 0} | دسترس‌ناپذیر طبق آخرین ارسال: {counts['unreachable'] or 0}\n"
        text += "درخواست لینک به معنی عضویت واقعی در گروه نیست. موفق‌ها شامل پاسخ حافظهٔ موقت و صفحه‌بندی هستند."
        await self.tg.send(uid, text, [[self.button(uid,"تازه‌سازی","stats")], self.back(uid,True)])

    async def channels(self, uid):
        rows, lines = [], []
        for c in self.db.all("SELECT * FROM channels"):
            lines.append(esc(c["title"])+" — <code>"+esc(c["id"])+"</code>")
            rows.append([self.button(uid, "حذف "+c["title"][:30], "channel_delete", id=c["id"])])
        rows += [[self.button(uid, "➕ افزودن کانال", "channel_add")],
                 [self.button(uid, "عضویت اجباری: "+("روشن" if self.db.get("force_join","1")=="1" else "خاموش"), "toggle", key="force_join")], self.back(uid,True)]
        await self.tg.send(uid, "<b>کانال‌های عضویت اجباری</b>\n"+("\n".join(lines) or "کانالی تعریف نشده است.")+"\nربات باید ادمین همهٔ این کانال‌ها باشد.",rows)

    async def users(self, uid, page):
        users = self.db.all("SELECT * FROM users ORDER BY created DESC LIMIT 10 OFFSET ?", (page*10,))
        count = self.db.one("SELECT COUNT(*) AS n FROM users")["n"]
        rows = [[self.button(uid, f"{'⛔ ' if u['banned'] else ''}{u['id']} {u['name']}"[:60], "user_detail", id=u["id"])] for u in users]
        nav = []
        if page:
            nav.append(self.button(uid,"قبلی","users",page=page-1))
        if (page+1)*10 < count:
            nav.append(self.button(uid,"بعدی","users",page=page+1))
        if nav:
            rows.append(nav)
        rows += [[self.button(uid,"جست‌وجوی شناسهٔ کاربر","user_find")],self.back(uid,True)]
        await self.tg.send(uid, f"کاربران: {count} | صفحهٔ {page+1}",rows)

    async def user_detail(self, uid, target):
        u = self.db.user(target)
        if not u:
            return await self.tg.send(uid,"کاربر هنوز ربات را شروع نکرده است.")
        await self.tg.send(uid, f"شناسه: <code>{u['id']}</code>\nنام: {esc(u['name'])}\nنام کاربری: {esc(u['username'])}\nمسدود: {bool(u['banned'])}",
            [[self.button(uid,"رفع مسدودی" if u["banned"] else "مسدودسازی","ban",id=target)],self.back(uid,True)])

    async def dispatch(self, uid, data):
        action = data["a"]
        if action in ADMIN_ACTIONS and not self.is_admin(uid):
            return await self.tg.send(uid,"این بخش مخصوص مدیر است.")
        if action in {"home", "check_join"}:
            self.db.clear_state(uid)
            return await self.home(uid)
        if action == "help":
            return await self.help(uid)
        if action == "search_prompt":
            return await self.tg.send(uid,"موضوع موردنظر را بنویس؛ مثلاً python یا کشاورزی.")
        if action == "search":
            return await self.search(uid,data["q"],data.get("offset",0),data.get("platform"),data.get("filters"),False)
        if action == "platforms":
            return await self.tg.send(uid,"کجا جست‌وجو کنم؟",[[self.button(uid,v,"platform_set",value=k)] for k,v in PLATFORMS.items()]+[self.back(uid)])
        if action == "platform_set":
            if data["value"] in PLATFORMS:
                self.db.run("UPDATE users SET platform=? WHERE id=?",(data["value"],uid))
            return await self.home(uid)
        if action == "filters":
            return await self.filters(uid)
        if action == "options":
            return await self.options(uid,data["kind"],data["page"])
        if action == "filter_set":
            self.db.filter_set(uid,data["key"],data["value"])
            return await self.filters(uid)
        if action == "filters_clear":
            self.db.run("UPDATE users SET filters='{}' WHERE id=?",(uid,))
            return await self.filters(uid)
        if action == "detail":
            return await self.detail(uid,data["row"])
        if action == "open":
            url = safe_url(data["row"].get("url"))
            if not url:
                return await self.tg.send(uid,"این نتیجه لینک معتبر ندارد.")
            self.db.event(uid,"open_link",data["row"].get("id"))
            return await self.tg.send(uid,esc(plain(data["row"].get("title"))),[[{"text":"بازکردن لینک گروه / کانال","url":url}],self.back(uid)])
        if action == "favorite":
            saved = self.db.favorite(uid,data["row"])
            return await self.tg.send(uid,"⭐ ذخیره شد." if saved else "از علاقه‌مندی‌ها حذف شد.")
        if action == "favorites":
            page = data.get("page",0)
            values = self.db.all("SELECT data FROM favorites WHERE uid=? ORDER BY at DESC LIMIT 10 OFFSET ?",(uid,page*10))
            count = self.db.one("SELECT COUNT(*) AS n FROM favorites WHERE uid=?",(uid,))["n"]
            rows = [[self.button(uid,plain(json.loads(x["data"]).get("title"))[:60] or "بدون عنوان","detail",row=json.loads(x["data"]))] for x in values]
            nav=[]
            if page:nav.append(self.button(uid,"قبلی","favorites",page=page-1))
            if (page+1)*10<count:nav.append(self.button(uid,"بعدی","favorites",page=page+1))
            if nav:rows.append(nav)
            rows += [[self.button(uid,"پاک‌کردن همه","clear_prompt",kind="favorites")],self.back(uid)]
            return await self.tg.send(uid,f"علاقه‌مندی‌ها: {count}\nاطلاعات ذخیره‌شده مربوط به زمان افزودن است.",rows)
        if action == "history":
            values = self.db.all("SELECT query FROM history WHERE uid=? ORDER BY id DESC LIMIT 20",(uid,))
            rows = [[self.button(uid,x["query"][:60],"search",q=x["query"])] for x in values]
            rows += [[self.button(uid,"پاک‌کردن تاریخچه","clear_prompt",kind="history")],self.back(uid)]
            return await self.tg.send(uid,"۲۰ جست‌وجوی آخر:" if values else "تاریخچه خالی است.",rows)
        if action == "clear_prompt":
            return await self.tg.send(uid,"همهٔ موارد این بخش پاک شوند؟",[[self.button(uid,"بله، پاک کن","clear_confirm",kind=data["kind"])],self.back(uid)])
        if action == "clear_confirm":
            if data["kind"] in {"favorites","history"}:
                self.db.run(f"DELETE FROM {data['kind']} WHERE uid=?",(uid,))
            return await self.tg.send(uid,"پاک شد.",[self.back(uid)])
        if action == "export_page":
            return await self.tg.document(uid,csv_bytes(data["items"],FIELDS),"search-page.csv","فقط نتایج همین صفحه؛ اطلاعات طبق Waybien")
        if action == "panel":
            self.db.clear_state(uid)
            return await self.panel(uid)
        if action == "stats":return await self.stats(uid)
        if action == "channels":return await self.channels(uid)
        if action == "channel_delete":
            self.db.run("DELETE FROM channels WHERE id=?",(data["id"],))
            self.db.event(uid,"admin",f"channel_delete:{data['id']}")
            return await self.channels(uid)
        if action == "toggle":
            if data["key"] in {"force_join","maintenance"}:
                self.db.set(data["key"],"0" if self.db.get(data["key"],"1" if data["key"]=="force_join" else "0")=="1" else "1")
                self.db.event(uid,"admin","toggle:"+data["key"])
            return await (self.channels(uid) if data["key"]=="force_join" else self.panel(uid))
        prompts = {"channel_add":"ابتدا ربات را ادمین کانال کنید، سپس یکی از این قالب‌ها را بفرستید:\n@channel_username\nیا برای کانال خصوصی:\n-1001234567890 https://t.me/+INVITE\n/cancel برای لغو",
                   "welcome":"متن خوشامد جدید (حداکثر ۱۵۰۰ نویسه) را بفرستید.",
                   "limit":"سقف جست‌وجوی روزانهٔ هر کاربر را به صورت عدد ۱ تا ۱۰۰۰۰۰ بفرستید.",
                   "user_find":"شناسهٔ عددی کاربر را بفرستید.",
                   "admin_add":"شناسهٔ عددی مدیر جدید را بفرستید؛ باید قبلاً ربات را شروع کرده باشد.",
                   "broadcast":"متن پیام همگانی را بفرستید (حداکثر ۳۵۰۰ نویسه). قبل از ارسال پیش‌نمایش تأیید می‌شود."}
        if action in prompts:
            if action=="admin_add" and uid not in self.cfg.owners:
                return await self.tg.send(uid,"فقط مالک می‌تواند مدیر اضافه کند.")
            self.db.state(uid,{"mode":action})
            return await self.tg.send(uid,prompts[action],html=False)
        if action == "users":return await self.users(uid,data["page"])
        if action == "user_detail":return await self.user_detail(uid,data["id"])
        if action == "ban":
            target=data["id"]
            if self.is_admin(target):return await self.tg.send(uid,"مدیر یا مالک قابل مسدودسازی نیست.")
            self.db.run("UPDATE users SET banned=1-banned WHERE id=?",(target,))
            self.db.event(uid,"admin",f"ban_toggle:{target}")
            return await self.user_detail(uid,target)
        if action == "admins":
            rows=[]
            text="مالکان: "+", ".join(map(str,sorted(self.cfg.owners)))+"\nمدیران از دسترسی به کاربران، آمار، پشتیبان و پیام همگانی برخوردارند."
            for x in self.db.all("SELECT id FROM admins"):
                rows.append([self.button(uid,f"حذف مدیر {x['id']}","admin_delete",id=x["id"])])
            rows += [[self.button(uid,"افزودن مدیر","admin_add")],self.back(uid,True)]
            return await self.tg.send(uid,esc(text),rows)
        if action == "admin_delete":
            if uid not in self.cfg.owners:return await self.tg.send(uid,"فقط مالک می‌تواند مدیر حذف کند.")
            self.db.run("DELETE FROM admins WHERE id=?",(data["id"],))
            self.db.event(uid,"admin",f"admin_delete:{data['id']}")
            return await self.dispatch(uid,{"a":"admins"})
        if action == "broadcast_send":
            state=self.db.state(uid)
            if state.get("mode")!="broadcast_confirm" or state.get("nonce")!=data.get("nonce"):
                return await self.tg.send(uid,"پیش‌نمایش منقضی یا قبلاً ارسال شده است.")
            job=self.db.broadcast(uid,state["text"])
            self.db.clear_state(uid)
            self.db.event(uid,"admin",f"broadcast:{job}")
            return await self.tg.send(uid,f"پیام در صف قرار گرفت. شماره: {job}",[self.back(uid,True)])
        if action == "jobs":
            jobs=self.db.all("SELECT * FROM jobs ORDER BY id DESC LIMIT 10")
            statuses={"running":"درحال ارسال","done":"تمام‌شده","cancelled":"متوقف‌شده"}
            lines=[f"#{j['id']} {statuses.get(j['state'],j['state'])} | موفق {j['sent']} | ناموفق {j['failed']}" for j in jobs]
            rows=[[self.button(uid,f"توقف ارسال #{j['id']}","job_cancel",id=j["id"])] for j in jobs if j["state"]=="running"]
            return await self.tg.send(uid,esc("\n".join(lines) or "ارسالی وجود ندارد."),rows+[self.back(uid,True)])
        if action == "job_cancel":
            self.db.run("UPDATE jobs SET state='cancelled' WHERE id=? AND state='running'",(data["id"],))
            return await self.dispatch(uid,{"a":"jobs"})
        if action == "export_users":
            return await self.tg.document(uid,csv_bytes(self.db.all("SELECT * FROM users"),["id","username","name","created","seen","banned","reachable"]),"users.csv","زمان‌ها به صورت Unix UTC هستند.")
        if action == "export_stats":
            rows=[{"period":k,**v} for k,v in self.db.stats().items()]
            return await self.tg.document(uid,csv_bytes(rows,["period","new","active","requests","searches","errors","opens","denied"]),"stats.csv")
        if action == "backup":
            return await self.tg.document(uid,self.db.backup(),"backup.sqlite3","نسخهٔ پشتیبان؛ شامل داده‌های کاربران است. توکن ربات داخل آن نیست.")
        if action == "health":
            started=time.monotonic()
            try:
                page=await self.site.search("python")
                message=f"✅ جست‌وجوی سایت سالم؛ {page.total} نتیجه، {len(page.items)} مورد در صفحه، {time.monotonic()-started:.1f} ثانیه."
            except ProviderError as exc:message="❌ "+str(exc)
            return await self.tg.send(uid,esc(message),[self.back(uid,True)])
        await self.tg.send(uid,"این گزینه شناخته نشد؛ /start را بزن.")

    async def input_state(self,uid,text,state):
        mode=state.get("mode")
        if not mode:return False
        if not self.is_admin(uid):
            self.db.clear_state(uid)
            return False
        try:
            if mode=="channel_add":
                if self.db.one("SELECT COUNT(*) AS n FROM channels")["n"]>=10:
                    raise ValueError("حداکثر ۱۰ کانال اجباری مجاز است.")
                parts=text.split()
                identity=parts[0]
                if not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}|-100\d{5,}",identity):
                    raise ValueError("شناسهٔ کانال معتبر نیست.")
                if len(parts)>2:raise ValueError("قالب ورودی معتبر نیست.")
                url=parts[1] if len(parts)==2 else "https://t.me/"+identity[1:] if identity.startswith("@") else None
                if not safe_url(url) or not re.match(r"https://t\.me/[^\s]+$",url):
                    raise ValueError("لینک معتبر https://t.me/ برای عضویت لازم است.")
                chat=await self.tg.call("getChat",chat_id=identity)
                if chat["type"]!="channel":raise ValueError("فقط کانال قابل افزودن است.")
                me=await self.tg.call("getChatMember",chat_id=chat["id"],user_id=self.me["id"])
                if me["status"] not in {"administrator","creator"}:raise ValueError("ابتدا ربات را ادمین این کانال کنید.")
                self.db.run("INSERT INTO channels(id,title,url) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url",(str(chat["id"]),chat.get("title",identity),url))
            elif mode=="welcome":
                if len(text)>1500:raise ValueError("متن حداکثر ۱۵۰۰ نویسه باشد.")
                self.db.set("welcome",text)
            elif mode=="limit":
                value=int(text)
                if not 1<=value<=100000:raise ValueError("عدد باید بین ۱ تا ۱۰۰۰۰۰ باشد.")
                self.db.set("daily_limit",value)
            elif mode=="user_find":
                target=int(text)
                self.db.clear_state(uid)
                await self.user_detail(uid,target)
                return True
            elif mode=="admin_add":
                if uid not in self.cfg.owners:raise ValueError("فقط مالک می‌تواند مدیر اضافه کند.")
                target=int(text)
                user=self.db.user(target)
                if not user:raise ValueError("کاربر باید ابتدا ربات را شروع کند.")
                self.db.run("INSERT OR IGNORE INTO admins VALUES(?)",(target,))
                self.db.run("UPDATE users SET banned=0 WHERE id=?",(target,))
            elif mode=="broadcast":
                import secrets
                if len(text)>3500:raise ValueError("پیام حداکثر ۳۵۰۰ نویسه باشد.")
                nonce=secrets.token_hex(8)
                self.db.state(uid,{"mode":"broadcast_confirm","text":text,"nonce":nonce})
                n=self.db.one("SELECT COUNT(*) AS n FROM users WHERE banned=0 AND reachable=1")["n"]
                await self.tg.send(uid,f"<b>پیش‌نمایش؛ گیرندگان فعلی: {n}</b>\n\n"+esc(text),
                    [[self.button(uid,"تأیید ارسال همگانی","broadcast_send",nonce=nonce)],self.back(uid,True)])
                return True
            elif mode=="broadcast_confirm":
                await self.tg.send(uid,"برای ارسال دکمهٔ تأیید را بزنید؛ /cancel برای لغو.")
                return True
            else:
                self.db.clear_state(uid)
                return False
        except (ValueError,IndexError):
            await self.tg.send(uid,"ورودی معتبر نیست؛ قالب و محدودیت گفته‌شده را رعایت کنید. /cancel برای لغو")
            return True
        except TelegramError:
            await self.tg.send(uid,"تلگرام کانال یا دسترسی ادمین ربات را تأیید نکرد. شناسه و دسترسی‌ها را بررسی کنید.")
            return True
        self.db.event(uid,"admin",mode)
        self.db.clear_state(uid)
        await self.tg.send(uid,"✅ ذخیره شد.",[self.back(uid,True)])
        return True

    async def handle(self,update):
        callback=update.get("callback_query")
        message=(callback or {}).get("message") or update.get("message")
        if not message or message.get("chat",{}).get("type")!="private":return
        user=callback["from"] if callback else message.get("from")
        if not user or user.get("is_bot"):return
        uid=user["id"]
        self.db.touch(user)
        self.db.event(uid,"activity")
        if callback:
            await self.tg.call("answerCallbackQuery",callback_query_id=callback["id"])
        if not await self.gate(uid):return
        if callback:
            data=self.db.resolve(callback.get("data",""),uid)
            if not data:return await self.tg.send(uid,"این دکمه منقضی شده است؛ /start را بزن.")
            if data["a"]!="broadcast_send":self.db.clear_state(uid)
            return await self.dispatch(uid,data)
        text=message.get("text","").strip()
        if not text:return await self.tg.send(uid,"لطفاً یک متن یا دستور بفرست.")
        parts=text.split(maxsplit=1)
        command=parts[0].split("@")[0].lower()
        commands={"/start":"home","/help":"help","/panel":"panel","/filters":"filters","/favorites":"favorites","/history":"history"}
        if command in commands:
            self.db.clear_state(uid)
            return await self.dispatch(uid,{"a":commands[command]})
        if command=="/id":return await self.tg.send(uid,f"شناسهٔ شما: <code>{uid}</code>")
        if command=="/cancel":
            self.db.clear_state(uid)
            return await self.home(uid)
        if command=="/search":
            self.db.clear_state(uid)
            return await self.search(uid,parts[1]) if len(parts)>1 else await self.tg.send(uid,"مثال: /search python")
        if text.startswith("/"):return await self.help(uid)
        if await self.input_state(uid,text,self.db.state(uid)):return
        await self.search(uid,text)

    async def broadcast_worker(self):
        while True:
            try:
                delivery=self.db.one('''SELECT d.job,d.uid,j.text FROM deliveries d JOIN jobs j ON j.id=d.job
                    WHERE d.state='pending' AND j.state='running' ORDER BY d.job,d.uid LIMIT 1''')
                if not delivery:
                    self.db.run("UPDATE jobs SET state='done' WHERE state='running' AND NOT EXISTS(SELECT 1 FROM deliveries WHERE job=jobs.id AND state='pending')")
                    await asyncio.sleep(1)
                    continue
                job,uid=delivery["job"],delivery["uid"]
                user=self.db.user(uid)
                if not user or user["banned"] or not user["reachable"]:
                    self.finish_delivery(job,uid,"skipped")
                    continue
                # Mark before transmission: a crash leaves 'unknown', never duplicate automatically.
                self.db.run("UPDATE deliveries SET state='sending' WHERE job=? AND uid=?",(job,uid))
                try:
                    await self.tg.send(uid,delivery["text"],html=False)
                    self.finish_delivery(job,uid,"sent")
                except TelegramError as exc:
                    if exc.code==403:self.db.run("UPDATE users SET reachable=0 WHERE id=?",(uid,))
                    self.finish_delivery(job,uid,"failed")
                except Exception:
                    self.finish_delivery(job,uid,"unknown")
                await asyncio.sleep(0.06)
            except asyncio.CancelledError:raise
            except Exception as exc:
                log.error("Broadcast worker error: %s",type(exc).__name__)
                await asyncio.sleep(3)

    def finish_delivery(self,job,uid,state):
        with self.db.db:
            self.db.db.execute("UPDATE deliveries SET state=? WHERE job=? AND uid=?",(state,job,uid))
            column="sent" if state=="sent" else "failed"
            self.db.db.execute(f"UPDATE jobs SET {column}={column}+1 WHERE id=?",(job,))
