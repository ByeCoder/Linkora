import asyncio
import contextlib
import logging
import os
import signal
import time
from pathlib import Path
import httpx
from .app import BotApp
from .config import Config
from .provider import ProviderError, Waybien
from .store import Store
from .telegram import Telegram, TelegramError
from .i18n import LANGUAGES,tr

log=logging.getLogger(__name__)


async def main():
    config=Config.load()
    db=Store(config.database,config.timezone)
    # Linux lock prevents two pollers on the same database.
    import fcntl
    lock=open(str(Path(config.database))+".lock","w")
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit("این دیتابیس قبلاً در حال استفاده است.") from None
    tg,site=Telegram(config.token),Waybien(config)
    app=BotApp(config,db,tg,site)
    worker=None
    service=None
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
    heartbeat=Path(config.database).with_suffix(".heartbeat")
    try:
        app.me=await tg.call("getMe")
        webhook=await tg.call("getWebhookInfo")
        if webhook.get("url"):
            raise SystemExit("برای استفاده از polling ابتدا webhook همین ربات را با deleteWebhook حذف کنید؛ راهنما را بخوانید.")
        if db.get('brand_initialized','0')!='1':
            await tg.call('setMyName',name='لینکورا | Linkora')
            await tg.call('setMyShortDescription',short_description='Find your people ✨ گروه‌ها و جمع‌های هم‌علاقه‌ات را پیدا کن 💚')
            db.set('brand_initialized',1)
        await tg.call("setMyCommands",commands=[{"command":c,"description":d} for c,d in
            [("start","منوی اصلی"),("search","جست‌وجو"),("filters","فیلترها"),("favorites","علاقه‌مندی‌ها"),
             ("history","تاریخچه"),("id","شناسهٔ من"),("panel","پنل مدیریت"),("cancel","لغو ورودی")]])
        for language in LANGUAGES:
            await tg.call('setMyCommands',language_code=language,commands=[{'command':c,'description':tr(language,k)[:80]} for c,k in
                [('start','home'),('search','search'),('language','language'),('support','support'),('profile','profile'),('invite','invite'),('daily','daily'),('filters','filters'),('favorites','favorites'),('history','history')]])
        # Interrupted broadcast deliveries have an unknown outcome; never auto-repeat them.
        interrupted=db.all("SELECT job,uid FROM deliveries WHERE state='sending'")
        for item in interrupted:app.finish_delivery(item["job"],item["uid"],"unknown")
        db.run("UPDATE outbox SET state='unknown' WHERE state='sending'")
        worker=asyncio.create_task(app.broadcast_worker())
        service=asyncio.create_task(app.service_worker())
        offset=int(db.get("telegram_offset","0"))
        last_cleanup=0
        log.info("Bot started as @%s",app.me.get("username",""))
        while not stop.is_set():
            heartbeat.write_text(str(time.time()))
            try:
                updates=await tg.call("getUpdates",offset=offset,timeout=30,limit=20,allowed_updates=["message","callback_query"])
            except TelegramError as exc:
                if exc.code in {401,409}:raise SystemExit(f"Telegram error {exc.code}; check token or duplicate running instance.") from None
                log.warning("Polling API error %s",exc.code)
                await asyncio.sleep(3)
                continue
            except httpx.HTTPError:
                log.warning("Polling network error")
                await asyncio.sleep(3)
                continue
            for update in updates:
                if stop.is_set():break
                heartbeat.write_text(str(time.time()))
                try:
                    await app.handle(update)
                except (TelegramError,ProviderError,ValueError) as exc:
                    log.warning("Update %s failed: %s",update["update_id"],type(exc).__name__)
                    uid=(update.get("callback_query",{}).get("from") or update.get("message",{}).get("from") or {}).get("id")
                    if isinstance(exc,TelegramError) and exc.code==403 and uid:
                        db.run("UPDATE users SET reachable=0 WHERE id=?",(uid,))
                    elif uid:
                        if db.user(uid):
                            with contextlib.suppress(Exception):await app.say(uid,'error')
                except Exception as exc:
                    # Log types, not request objects/URLs that can contain the bot token.
                    log.error("Update %s unexpected error: %s",update["update_id"],type(exc).__name__)
                offset=update["update_id"]+1
                db.set("telegram_offset",offset)
            if time.time()-last_cleanup>3600:
                db.cleanup()
                last_cleanup=time.time()
    finally:
        if service:
            service.cancel()
            with contextlib.suppress(asyncio.CancelledError):await service
        if worker:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):await worker
        await tg.close()
        await site.close()
        db.close()
        lock.close()
        heartbeat.unlink(missing_ok=True)


if __name__=="__main__":
    os.umask(0o077)
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:asyncio.run(main())
    except (ValueError,TelegramError) as exc:raise SystemExit(str(exc)) from None
    except httpx.HTTPError:raise SystemExit("ارتباط با تلگرام برقرار نشد؛ شبکه یا پراکسی سرور را بررسی کنید.") from None
