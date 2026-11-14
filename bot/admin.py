import contextlib
import json
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from .legacy import esc,ADMIN_ACTIONS
from .experience import BUTTON_KEYS
from .i18n import LANGUAGES
from .provider import safe_url
from .store import csv_bytes
from .telegram import TelegramError

EXTRA_ADMIN={'growth','growth_setting','channel_goal','channel_status','channel_mode','tickets','ticket_view','ticket_reply','ticket_close',
 'integrations','integration_set','calendar','design','design_edit','design_field','design_style','design_visibility','design_reset',
 'custom_add','custom_delete','campaigns','campaign_add','featured_admin','featured_add','featured_delete',
 'broadcast_segment','broadcast_language','export_growth','log_photo','user_message','stats_history','export_daily'}


class Administration:
    def profile_summary(self,uid):
        u=self.db.user(uid)
        if not u:return f'Actor ID: {uid}'
        fmt=lambda at:datetime.fromtimestamp(at,ZoneInfo(self.cfg.timezone)).strftime('%Y-%m-%d %H:%M')
        stats=self.db.one("SELECT SUM(kind='search_ok') AS searches,SUM(kind='open_link') AS opens FROM events WHERE uid=?",(uid,))
        extra=json.loads(u['profile_extra'])
        details='\n'.join(f'{key}: {str(value)[:350]}' for key,value in extra.items())
        return (f"👤 {u['name']} {u['last_name']}\nID: {uid}\nUsername: @{u['username'] or '—'}\n"
            f"Telegram language: {u['tg_language'] or '—'}\nBot language: {u['language']}\nPremium: {bool(u['is_premium'])}\n"
            f"Phone (voluntary): {u['phone'] or '—'}\nFirst seen: {fmt(u['created'])}\nLast seen: {fmt(u['seen'])}\n"
            f"Points: {u['points']} | Verified referrals: {self.db.referral_count(uid)}\nSearches: {stats['searches'] or 0} | Link requests: {stats['opens'] or 0}\n"
            f"Banned: {bool(u['banned'])} | Reachable: {bool(u['reachable'])}\n{details}")

    async def refresh_profile(self,uid):
        try:
            chat=await self.tg.call('getChat',chat_id=uid)
            if isinstance(chat,dict):
                extra={k:chat[k] for k in ('bio','birthdate','active_usernames') if chat.get(k)}
                if chat.get('personal_chat'):extra['personal_chat']={k:v for k,v in chat['personal_chat'].items() if k in {'id','title','username'}}
                self.db.run('UPDATE users SET profile_extra=? WHERE id=?',(json.dumps(extra,ensure_ascii=False),uid))
        except (TelegramError,httpx.HTTPError):pass

    def audit(self,uid,event,detail='',photo=False):
        self.db.event(uid,'admin' if self.is_admin(uid) else 'log',event+':'+str(detail))
        chat=self.db.get('log_chat','')
        if chat:self.db.enqueue(chat,{'type':'log','uid':uid,'text':event+('\n'+str(detail) if detail else ''),'photo':photo})

    async def user_detail(self,uid,target):
        if not self.db.user(target):return await self.tg.send(uid,'این کاربر هنوز ربات را شروع نکرده است.')
        await self.refresh_profile(target)
        await self.tg.send(uid,esc(self.profile_summary(target)),[
            [self.button(uid,'پاسخ / پیام به کاربر','user_message',id=target),self.button(uid,'عکس پروفایل','log_photo',id=target)],
            [self.button(uid,'رفع مسدودی' if self.db.user(target)['banned'] else 'مسدودسازی','ban',id=target)],self.back(uid,True)])
        history=self.db.all('SELECT query FROM history WHERE uid=? ORDER BY id DESC LIMIT 10',(target,))
        if history:await self.tg.send(uid,'۱۰ جست‌وجوی آخر:\n'+esc('\n'.join(x['query'] for x in history)))

    async def stats(self,uid):
        text=f'📊 <b>آمار لینکورا</b>\nزمان: {esc(self.cfg.timezone)} • هفته از شنبه\n'
        for label,r in self.db.stats().items():
            text+=(f"\n<b>{label}</b>\nکاربر جدید: {r['new']} • فعال یکتا: {r['active']}\n"
                f"جست‌وجو: {r['requests']} درخواست / {r['searches']} موفق / {r['errors']} خطا\n"
                f"درخواست لینک: {r['opens']} • رد عضویت: {r['denied']}\nدعوت تأییدشده: {r['referrals']} • تیکت: {r['tickets']} • پاسخ مدیر: {r['replies']}\nورودی جدید کمپین: {r['campaigns']}\n")
        text+='\nآمار مربوط به خود ربات است. درخواست لینک، اثبات عضویت در گروه نیست.'
        await self.tg.send(uid,text,[[self.button(uid,'تازه‌سازی','stats'),self.button(uid,'تغییر شمسی / میلادی','calendar')],
            [self.button(uid,'📅 آمار روزبه‌روز','stats_history'),self.button(uid,'📥 CSV سی روز','export_daily')],self.back(uid,True)])

    async def stats_history(self,uid):
        text='📅 <b>هفت روز اخیر، تا همین لحظه</b>\n'
        field='jalali_date' if self.db.get('calendar','jalali')=='jalali' else 'date'
        for r in self.db.daily_series(7):
            text+=f"\n<b>{r[field]}</b>\nجدید: {r['new']} • فعال یکتا: {r['active']} • جست‌وجوی موفق: {r['searches']}\nدعوت: {r['referrals']} • کمپین: {r['campaigns']} • تیکت: {r['tickets']}\n"
        await self.tg.send(uid,text,[[self.button(uid,'📥 خروجی روزبه‌روزِ سی روز','export_daily')],self.back(uid,True)])

    async def panel(self,uid):
        self.db.clear_state(uid)
        pairs=[(('📊 آمار روز/هفته/ماه/کل','stats'),('👥 کاربران','users')), (('💬 تیکت‌ها','tickets'),('📡 لاگ و پشتیبانی','integrations')),
            (('🎨 دکمه‌ها و ظاهر','design'),('🎯 جذب و هدف‌ها','growth')), (('🔐 عضویت اجباری','channels'),('مدیران','admins')),
            (('📣 پیام همگانی هدفمند','broadcast_segment'),('صف ارسال‌ها','jobs')), (('✨ پیشنهادهای منتخب','featured_admin'),('📈 کمپین‌ها','campaigns')),
            (('خوشامد هر زبان','welcome'),('سهمیهٔ روزانه','limit')), (('CSV کاربران','export_users'),('CSV آمار و جذب','export_growth')),
            (('پشتیبان دیتابیس','backup'),('سلامت اتصال','health'))]
        rows=[[self.button(uid,label,action,**({'page':0} if action in {'users','tickets','design'} else {})) for label,action in pair] for pair in pairs]
        rows += [[self.button(uid,'نگهداری: '+('روشن' if self.db.get('maintenance','0')=='1' else 'خاموش'),'toggle',key='maintenance')],self.back(uid)]
        await self.tg.send(uid,'🛠 <b>اتاق فرمان لینکورا</b>\nکاربران: '+str(self.db.one('SELECT COUNT(*) AS n FROM users')['n']),rows)

    async def channels(self,uid):
        text='🔐 <b>عضویت اجباری و هدف‌ها</b>\nهدف ۰ یعنی بدون توقف. حالت «تأیید یکتا» کاربران بررسی‌شدهٔ ربات را می‌شمارد؛ حالت «کل اعضای کانال» تعداد واقعی اعضا را از تلگرام می‌گیرد و در رسیدن به هدف، شرط کانال را خاموش می‌کند.\n'; rows=[]
        for c in self.db.all('SELECT c.*,(SELECT COUNT(*) FROM joins j WHERE j.channel=c.id) AS verified FROM channels c'):
            text+=f"\n{esc(c['title'])} • تأیید یکتا: {c['verified']} • هدف: {c['goal'] or '∞'} • حالت: {c['goal_mode']} • {'روشن' if c['enabled'] else 'خاموش'}"
            rows.append([self.button(uid,'🎯 هدف '+c['title'][:15],'channel_goal',id=c['id']),self.button(uid,'روشن/خاموش','channel_status',id=c['id']),self.button(uid,'حذف','channel_delete',id=c['id'])])
            rows.append([self.button(uid,'تغییر حالت: تأیید یکتا / کل اعضا','channel_mode',id=c['id'])])
        await self.tg.send(uid,text,rows+[[self.button(uid,'افزودن کانال','channel_add')],[self.button(uid,'شرط کلی: '+self.db.get('force_join','1'),'toggle',key='force_join')],self.back(uid,True)])

    async def growth(self,uid):
        settings=[('required_referrals','دعوت لازم برای بازشدن جست‌وجو','0'),('referral_points','امتیاز هر دعوت معتبر','20'),('daily_points','جایزهٔ روزانه','5')]
        rows=[[self.button(uid,f'{label}: {self.db.get(key,default)}','growth_setting',key=key)] for key,label,default in settings]
        await self.tg.send(uid,'🎯 <b>تنظیمات جذب</b>\nدعوت کاربر قبلی، دعوت خود و تأیید تکراری پاداش ندارند. تأیید یعنی کاربر جدید مرحلهٔ عضویت را گذرانده؛ اثبات هویت انسانی نیست.\nهر ۵۰ امتیاز = ۲۰ جست‌وجوی اضافهٔ همان روز؛ حداکثر ۵ بار در روز.',rows+[self.back(uid,True)])

    async def integrations(self,uid):
        queue=self.db.all('SELECT state,COUNT(*) AS n FROM outbox GROUP BY state')
        status=' • '.join(f"{r['state']}: {r['n']}" for r in queue) or 'خالی'
        await self.tg.send(uid,f"📡 گروه لاگ: <code>{esc(self.db.get('log_chat','تنظیم نشده'))}</code>\nگروه پشتیبانی: <code>{esc(self.db.get('support_chat','تنظیم نشده'))}</code>\n"
            'در گروه، مالک می‌تواند /bindlog یا /bindsupport بفرستد. ربات باید ادمین گروه باشد. فقط مدیران ثبت‌شدهٔ ربات اجازهٔ پاسخ به تیکت دارند. اگر گروه پشتیبانی تعریف نشود، تیکت به مالکان می‌رسد.\nصف لاگ و پشتیبانی: '+esc(status),[
            [self.button(uid,'تنظیم گروه لاگ','integration_set',key='log_chat'),self.button(uid,'تنظیم گروه پشتیبانی','integration_set',key='support_chat')],self.back(uid,True)])

    async def design(self,uid,page=0):
        keys=['default']+BUTTON_KEYS+sorted((ADMIN_ACTIONS|EXTRA_ADMIN)-set(BUTTON_KEYS))+['custom_'+str(x['id']) for x in self.db.all('SELECT id FROM custom_links')]
        rows=[[self.button(uid,k,'design_edit',key=k)] for k in keys[page*8:page*8+8]]; nav=[]
        if page:nav.append(self.button(uid,'قبلی','design',page=page-1))
        if (page+1)*8<len(keys):nav.append(self.button(uid,'بعدی','design',page=page+1))
        if nav:rows.append(nav)
        rows += [[self.button(uid,'➕ دکمهٔ لینک جدید','custom_add')],[self.button(uid,'چیدمان ۱ / ۲ ستون','design_field',key='global',field='columns')],self.back(uid,True)]
        await self.tg.send(uid,'🎨 <b>طراحی دکمه‌ها</b>\nمتن هر زبان، ترتیب، نمایش در منو، ایموجی سفارشی و رنگ قابل تنظیم است. رنگ‌های رسمی آبی، سبز، قرمز و پیش‌فرض‌اند. ایموجی سفارشی به مجوز تلگرام وابسته است. در ردشدن تزئینات، دکمهٔ ساده ارسال می‌شود.\nعنوان نتیجه‌ها از سایت می‌آید؛ تغییر برچسب آن‌ها انتخاب مدیر است.',rows)

    async def design_edit(self,uid,key):
        rows=[[self.button(uid,'✍️ متن هر زبان','design_field',key=key,field='labels'),self.button(uid,'✨ ایموجی سفارشی','design_field',key=key,field='emoji_id')],
            [self.button(uid,'🔵 آبی','design_style',key=key,value='primary'),self.button(uid,'🟢 سبز','design_style',key=key,value='success'),self.button(uid,'🔴 قرمز','design_style',key=key,value='danger')],
            [self.button(uid,'پیش‌فرض تلگرام','design_style',key=key,value='default'),self.button(uid,'ترتیب','design_field',key=key,field='order')],
            [self.button(uid,'نمایش / پنهان در منو','design_visibility',key=key),self.button(uid,'بازنشانی','design_reset',key=key)]]
        if key.startswith('custom_'):rows.append([self.button(uid,'حذف این لینک','custom_delete',id=int(key[7:]))])
        await self.tg.send(uid,'دکمه: <code>'+esc(key)+'</code>\n'+esc(json.dumps(self.presentation(key),ensure_ascii=False,indent=2)),rows+[[self.button(uid,'بازگشت به طراحی','design',page=0)]])

    def set_design(self,key,field,value):
        config=self.presentation(key); config[field]=value
        self.db.run('INSERT OR REPLACE INTO button_config VALUES(?,?)',(key,json.dumps(config,ensure_ascii=False)))

    async def profile_photo(self,dest,target):
        try:
            photos=await self.tg.call('getUserProfilePhotos',user_id=target,limit=1)
            if not photos.get('photos'):return await self.tg.send(dest,'عکس پروفایل در دسترس ربات نیست.')
            await self.tg.call('sendPhoto',chat_id=dest,photo=photos['photos'][0][-1]['file_id'],caption=f'Profile • {target}')
        except (TelegramError,httpx.HTTPError):await self.tg.send(dest,'دریافت عکس ممکن نشد؛ محدودیت حریم خصوصی یا شبکه را بررسی کنید.')

    async def bind_group(self,uid,key,chat):
        if uid not in self.cfg.owners or key not in {'log_chat','support_chat'}:raise ValueError('owner_only')
        if chat==0:self.db.set(key,''); return
        group=await self.tg.call('getChat',chat_id=chat)
        member=await self.tg.call('getChatMember',chat_id=chat,user_id=self.me['id'])
        if group['type'] not in {'group','supergroup'} or member['status'] not in {'creator','administrator'}:raise ValueError()
        self.db.set(key,chat); self.audit(uid,'integration_configured',f'{key}: {chat}')

    async def admin_action(self,uid,d):
        a=d['a']
        if a=='user_message':return await self.admin_action(uid,{'a':'ticket_reply','id':self.db.ticket(d['id'])})
        if a=='panel':return await self.panel(uid)
        if a=='stats':return await self.stats(uid)
        if a=='stats_history':return await self.stats_history(uid)
        if a=='export_daily':
            rows=self.db.daily_series(30)
            return await self.tg.document(uid,csv_bytes(rows,list(rows[0])),'linkora-daily-30days.csv','آمار هر روز به تفکیک، با تاریخ شمسی و میلادی')
        if a=='channels':return await self.channels(uid)
        if a=='growth':return await self.growth(uid)
        if a=='tickets':return await self.tickets(uid,d.get('page',0))
        if a=='ticket_view':return await self.ticket_view(uid,d['id'])
        if a=='ticket_close':
            ticket=self.db.one('SELECT * FROM tickets WHERE id=?',(d['id'],))
            if ticket:
                self.db.run("UPDATE tickets SET status='closed',updated=? WHERE id=?",(time.time(),d['id']))
                with contextlib.suppress(TelegramError,httpx.HTTPError):await self.say(ticket['uid'],'ticket_closed',id=d['id'])
            return await self.tickets(uid)
        if a=='integrations':return await self.integrations(uid)
        if a=='calendar':
            self.db.set('calendar','gregorian' if self.db.get('calendar','jalali')=='jalali' else 'jalali'); return await self.stats(uid)
        if a=='channel_status':
            self.db.run('UPDATE channels SET enabled=1-enabled WHERE id=?',(d['id'],)); return await self.channels(uid)
        if a=='channel_mode':
            self.db.run("UPDATE channels SET goal_mode=CASE goal_mode WHEN 'verified' THEN 'members' ELSE 'verified' END WHERE id=?",(d['id'],))
            return await self.channels(uid)
        if a=='design':return await self.design(uid,d.get('page',0))
        if a=='design_edit':return await self.design_edit(uid,d['key'])
        if a in {'design_style','design_visibility','design_reset'}:
            key=d['key']
            if a=='design_style' and d['value'] in {'primary','success','danger','default'}:self.set_design(key,'style',d['value'])
            elif a=='design_visibility':self.set_design(key,'visible',not self.presentation(key).get('visible',True))
            elif a=='design_reset':self.db.run('DELETE FROM button_config WHERE key=?',(key,))
            self.audit(uid,a,key); return await self.design_edit(uid,key)
        if a=='custom_delete':
            self.db.run('DELETE FROM custom_links WHERE id=?',(d['id'],)); return await self.design(uid)
        if a=='featured_delete':
            self.db.run('DELETE FROM featured WHERE id=?',(d['id'],)); return await self.dispatch(uid,{'a':'featured_admin'})
        if a=='campaigns':
            text='📈 <b>کمپین‌ها؛ فقط کاربر جدید شمرده می‌شود</b>\n'
            for c in self.db.all('SELECT c.*,COUNT(u.uid) AS n FROM campaigns c LEFT JOIN campaign_users u ON u.token=c.token GROUP BY c.token ORDER BY c.created DESC LIMIT 20'):
                text+=f"\n{esc(c['title'])} • {c['n']} کاربر\nhttps://t.me/{self.me.get('username','YOUR_BOT')}?start=c_{c['token']}\n"
            return await self.tg.send(uid,text,[[self.button(uid,'ساخت کمپین','campaign_add')],self.back(uid,True)])
        if a=='featured_admin':
            values=self.db.all('SELECT id,query FROM featured ORDER BY position,id')
            return await self.tg.send(uid,'پیشنهادهای منتخب؛ عبارات و عنوان‌ها را مدیر انتخاب می‌کند.',[[self.button(uid,'حذف '+x['query'][:30],'featured_delete',id=x['id'])] for x in values]+[[self.button(uid,'افزودن پیشنهاد','featured_add')],self.back(uid,True)])
        if a=='broadcast_segment':return await self.tg.send(uid,'زبان مخاطبان را انتخاب کنید؛ متن پیام را خودتان برای همین گروه زبانی می‌نویسید.',[[self.button(uid,label,'broadcast_language',value=lang)] for lang,label in {'':'همهٔ زبان‌ها',**LANGUAGES}.items()]+[self.back(uid,True)])
        if a=='broadcast_send':
            state=self.db.state(uid)
            if state.get('mode')!='broadcast_confirm' or state.get('nonce')!=d.get('nonce'):return await self.tg.send(uid,'پیش‌نمایش منقضی یا قبلاً ارسال شده است.')
            job=self.db.broadcast(uid,state['text'],state.get('language','')); self.db.clear_state(uid); self.audit(uid,'broadcast',str(job))
            return await self.tg.send(uid,f'پیام در صف قرار گرفت: #{job}',[self.back(uid,True)])
        if a=='export_growth':
            rows=[{'period':k,**v} for k,v in self.db.stats().items()]
            return await self.tg.document(uid,csv_bytes(rows,list(rows[0])),'linkora-stats.csv')
        if a=='export_users':
            fields=['id','name','last_name','username','phone','language','tg_language','is_premium','created','seen','points','streak','banned','reachable']
            return await self.tg.document(uid,csv_bytes(self.db.all('SELECT * FROM users'),fields),'linkora-users.csv')
        if a=='log_photo':return await self.profile_photo(uid,d['id'])
        prompts={'ticket_reply':'پیام، عکس، فایل یا ویس پاسخ را بفرستید. /cancel برای لغو.',
            'channel_goal':'عدد هدف را بفرستید؛ ۰ یعنی بدون پایان. از منوی کانال حالت هدف را هم انتخاب کنید: تأیید یکتای کاربران ربات یا کل اعضای واقعی کانال.',
            'growth_setting':'عدد را بفرستید: ۰ تا ۱۰۰۰. شرط دعوت و پاداش روزانه حداکثر ۱۰۰.',
            'integration_set':'شناسهٔ عددی گروه را بفرستید؛ ۰ برای خاموش‌کردن. تنظیم فقط توسط مالک؛ ربات باید ادمین گروه باشد.',
            'custom_add':'متن و لینک https را در دو خط بفرستید؛ حداکثر ۱۰ لینک. متن هر زبان، رنگ و ترتیب بعداً از طراحی تغییر می‌کند.\nمثال:\nکانال ما 💚\nhttps://t.me/example',
            'campaign_add':'نام کمپین را بفرستید (حداکثر ۸۰ نویسه).',
            'featured_add':'عنوان و عبارت جست‌وجو را در دو خط بفرستید.\nمثال:\nدنیای پایتون 🐍\npython\nعنوان چندزبان می‌تواند در خط اول JSON باشد: {"fa":"پایتون","en":"Python"}',
            'welcome':'کد زبان و متن خوشامد را با یک فاصله بفرستید؛ حداکثر ۱۵۰۰ نویسه.\nمثال: fa سلام رفیق 👋\nبازنشانی: fa reset',
            'broadcast_language':'متن پیام را بفرستید؛ حداکثر ۳۵۰۰ نویسه. پیش از ارسال تأیید می‌کنید.','design_field':''}
        if a=='design_field':prompts[a]={'labels':'کد زبان و متن را بفرستید. مثال: fa بیا بگردیم 🔎\nکدها: fa en zh hi ar he es fr\nبازنشانی زبان: fa reset','emoji_id':'یک پیام شامل ایموجی سفارشی بفرستید یا custom_emoji_id عددی را وارد کنید. حذف: 0','order':'ترتیب ۰ تا ۱۰۰۰؛ عدد کمتر بالاتر است.','columns':'تعداد ستون منو: ۱ یا ۲.'}[d['field']]
        if a in prompts:
            self.db.state(uid,{'mode':'v2_'+a,**{k:v for k,v in d.items() if k!='a'}})
            return await self.tg.send(uid,prompts[a],html=False)
        return NotImplemented

    async def admin_input(self,uid,message,state):
        mode=state.get('mode','')
        if not mode.startswith('v2_'):return False
        if not self.is_admin(uid):self.db.clear_state(uid); return True
        a=mode[3:]; text=message.get('text','').strip()
        if a=='ticket_reply':await self.reply_ticket(uid,state['id'],message); return True
        try:
            if a=='channel_goal':
                goal=int(text)
                if not 0<=goal<=10_000_000:raise ValueError()
                self.db.run('UPDATE channels SET goal=? WHERE id=?',(goal,state['id']))
            elif a=='growth_setting':
                key=state['key']; maximum=100 if key in {'required_referrals','daily_points'} else 1000; value=int(text)
                if key not in {'required_referrals','daily_points','referral_points'} or not 0<=value<=maximum:raise ValueError()
                self.db.set(key,value)
            elif a=='integration_set':await self.bind_group(uid,state['key'],int(text))
            elif a=='design_field':
                key,field=state['key'],state['field']
                if field=='labels':
                    lang,label=text.split(maxsplit=1)
                    if lang not in LANGUAGES or not 1<=len(label)<=60:raise ValueError()
                    labels=self.presentation(key).get('labels',{})
                    if label=='reset':labels.pop(lang,None)
                    else:labels[lang]=label
                    self.set_design(key,'labels',labels)
                elif field=='emoji_id':
                    entities=message.get('entities',[])+message.get('caption_entities',[])
                    emoji=next((e['custom_emoji_id'] for e in entities if e['type']=='custom_emoji'),text)
                    if not emoji.isdecimal() or len(emoji)>30:raise ValueError()
                    self.set_design(key,'emoji_id','' if emoji=='0' else emoji)
                elif field=='columns':
                    value=int(text)
                    if value not in {1,2}:raise ValueError()
                    self.db.set('menu_columns',value)
                elif field=='order':
                    value=int(text)
                    if not 0<=value<=1000:raise ValueError()
                    self.set_design(key,'order',value)
            elif a=='custom_add':
                if self.db.one('SELECT COUNT(*) AS n FROM custom_links')['n']>=10:raise ValueError()
                label,url=text.splitlines()
                if not 1<=len(label)<=60 or not safe_url(url) or not url.startswith('https://'):raise ValueError()
                self.db.run('INSERT INTO custom_links(data,position) VALUES(?,200)',(json.dumps({'label':label,'url':url},ensure_ascii=False),))
            elif a=='campaign_add':
                if not 1<=len(text)<=80:raise ValueError()
                self.db.run('INSERT INTO campaigns VALUES(?,?,?)',(secrets.token_urlsafe(8),text,time.time()))
            elif a=='featured_add':
                title,query=text.splitlines(); labels=json.loads(title) if title.startswith('{') else {'fa':title,'en':title}
                if not isinstance(labels,dict) or not labels or any(k not in LANGUAGES or not isinstance(v,str) or not 1<=len(v)<=60 for k,v in labels.items()) or not 1<=len(query)<=200:raise ValueError()
                self.db.run('INSERT INTO featured(title,query) VALUES(?,?)',(json.dumps(labels,ensure_ascii=False),query))
            elif a=='welcome':
                lang,value=text.split(maxsplit=1)
                if lang not in LANGUAGES or len(value)>1500:raise ValueError()
                self.db.set('welcome_'+lang,'' if value=='reset' else value)
                if lang=='fa':self.db.set('welcome','')
            elif a=='broadcast_language':
                if not 1<=len(text)<=3500 or (state['value'] and state['value'] not in LANGUAGES):raise ValueError()
                lang=state['value']; nonce=secrets.token_hex(8)
                self.db.state(uid,{'mode':'broadcast_confirm','text':text,'nonce':nonce,'language':lang})
                count=self.db.one("SELECT COUNT(*) AS n FROM users WHERE banned=0 AND reachable=1 AND (?='' OR language=?)",(lang,lang))['n']
                await self.tg.send(uid,f"پیش‌نمایش • زبان: {lang or 'همه'} • مخاطب: {count}\n\n"+esc(text),[[self.button(uid,'تأیید ارسال','broadcast_send',nonce=nonce)],self.back(uid,True)])
                return True
        except (ValueError,IndexError,KeyError):
            await self.tg.send(uid,'ورودی معتبر نیست؛ قالب گفته‌شده را رعایت کنید. /cancel برای لغو.'); return True
        except (TelegramError,httpx.HTTPError):
            await self.tg.send(uid,'گروه یا دسترسی ربات تأیید نشد؛ ربات باید ادمین گروه باشد.'); return True
        self.db.clear_state(uid); self.audit(uid,a)
        await self.tg.send(uid,'✅ ذخیره شد.',[self.back(uid,True)])
        return True
