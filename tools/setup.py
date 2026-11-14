"""Interactive local setup. Secrets are never echoed or sent anywhere except Telegram."""
import asyncio
import argparse
import getpass
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot.telegram import Telegram,TelegramError


MESSAGES = {
    'en': {
        'exists': 'A .env file already exists. Edit it manually to avoid overwriting your configuration.',
        'token': 'BotFather token (hidden): ',
        'verified': 'Bot verified: @',
        'owner': 'Numeric owner ID (leave empty if you do not know it): ',
        'webhook': 'A webhook is active. Obtain your ID from the existing integration. No settings were changed.',
        'send': 'Send /id to this bot in a private Telegram chat, then press Enter here: ',
        'none': 'No private message found. Send a new message and run setup again.',
        'choose': 'Enter only the numeric ID belonging to YOUR account from the list: ',
        'listed': 'The ID must be one of the accounts listed above.',
        'positive': 'The owner ID must be a positive number.',
        'saved': 'Configuration saved. Run python -m bot, choose your language in Telegram, then send /panel.',
        'api': 'Telegram returned error {code}. Check the token and bot status.',
        'failed': 'Setup did not complete: ',
    },
    'fa': {
        'exists': 'فایل .env از قبل وجود دارد. برای جلوگیری از بازنویسی، آن را دستی ویرایش کنید.',
        'token': 'توکن BotFather (نمایش داده نمی‌شود): ',
        'verified': 'ربات تأیید شد: @',
        'owner': 'شناسهٔ عددی مالک (اگر نمی‌دانید خالی بگذارید): ',
        'webhook': 'webhook فعال است؛ شناسه را از محیط فعلی بگیرید. هیچ تنظیمی تغییر نکرد.',
        'send': 'در تلگرام به همین ربات پیام /id بفرستید، سپس Enter بزنید: ',
        'none': 'پیامی پیدا نشد؛ یک پیام تازه بفرستید و برنامهٔ تنظیم را دوباره اجرا کنید.',
        'choose': 'فقط شناسهٔ مربوط به حساب خودتان را از فهرست وارد کنید: ',
        'listed': 'شناسه باید در فهرست بالا باشد.',
        'positive': 'شناسهٔ مالک باید عدد مثبت باشد.',
        'saved': 'تنظیمات ذخیره شد. python -m bot را اجرا کنید؛ در تلگرام زبان را انتخاب و سپس /panel را بفرستید.',
        'api': 'تلگرام خطای {code} داد؛ توکن یا وضعیت ربات را بررسی کنید.',
        'failed': 'تنظیم انجام نشد: ',
    },
}


async def main(language='en'):
    t=MESSAGES[language]
    target=Path(__file__).resolve().parents[1]/".env"
    if target.exists():
        print(t['exists'])
        return
    token=getpass.getpass(t['token']).strip()
    tg=Telegram(token)
    try:
        me=await tg.call("getMe")
        print(t['verified']+me.get('username',''))
        raw=input(t['owner']).strip()
        if not raw:
            webhook=await tg.call("getWebhookInfo")
            if webhook.get("url"):
                print(t['webhook'])
                return
            input(t['send'])
            updates=await tg.call("getUpdates",timeout=5,allowed_updates=["message"])
            candidates={}
            for update in updates:
                msg=update.get("message",{})
                if msg.get("chat",{}).get("type")=="private":
                    user=msg.get("from",{})
                    if user.get("id"):candidates[user["id"]]=user
            for uid,user in candidates.items():
                print(uid,"|",user.get("first_name",""),"|",user.get("username",""))
            if not candidates:
                print(t['none'])
                return
            raw=input(t['choose']).strip()
            if not raw.isdecimal() or int(raw) not in candidates:
                print(t['listed'])
                return
        if not raw.isdecimal() or int(raw)<1:
            print(t['positive'])
            return
        # Exclusive creation prevents accidental overwrites, including races.
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as output:
            output.write(f"BOT_TOKEN={token}\nOWNER_IDS={int(raw)}\nDATABASE_PATH=data/bot.sqlite3\nTIMEZONE=Asia/Tehran\n")
        print(t['saved'])
    except TelegramError as exc:
        print(t['api'].format(code=exc.code))
    except Exception as exc:
        print(t['failed']+type(exc).__name__)
    finally:
        await tg.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description='Configure Linkora locally without exposing the bot token.')
    parser.add_argument('--language',choices=['en','fa'],default='en',help='Setup prompt language; bot users choose their own language separately.')
    asyncio.run(main(parser.parse_args().language))
