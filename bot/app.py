import asyncio
import contextlib
import json
import time
from .legacy import BotApp as LegacyApp,ADMIN_ACTIONS,esc
from .experience import Experience
from .admin import Administration,EXTRA_ADMIN
from .provider import ProviderError
from .telegram import TelegramError
from .store import period_starts
import httpx

SAFE_ACTIONS={'home','help','language','language_set','support','support_send','privacy','invite','profile','contact','contact_remove','check_join','my_tickets','user_ticket','user_ticket_close'}
COMMANDS={'/start':'home','/help':'help','/panel':'panel','/language':'language','/support':'support',
 '/privacy':'privacy','/filters':'filters','/favorites':'favorites','/history':'history','/invite':'invite','/profile':'profile','/daily':'daily'}


class BotApp(Experience,Administration,LegacyApp):
    async def gate(self,uid,action='search'):
        if self.db.user(uid)['banned']:
            await self.say(uid,'blocked'); return False
        if self.is_admin(uid):return True
        if action in SAFE_ACTIONS and action!='check_join':return True
        if self.db.get('maintenance','0')=='1':
            await self.say(uid,'maintenance',[[self.button(uid,self.t(uid,'support'),'support')]]); return False
        missing,errors=[],False
        if self.db.get('force_join','1')=='1':
            for c in self.db.active_channels():
                try:
                    if c['goal_mode']=='members' and c['goal']:
                        current=await self.tg.call('getChatMemberCount',chat_id=c['id'])
                        if not isinstance(current,int):raise TelegramError(500,'Invalid member count')
                        if current>=c['goal']:
                            self.db.run('UPDATE channels SET enabled=0 WHERE id=?',(c['id'],))
                            self.audit(uid,'channel_goal_reached',f"{c['id']}: {current}/{c['goal']}")
                            continue
                    member=await self.tg.call('getChatMember',chat_id=c['id'],user_id=uid)
                    joined=member['status'] in {'creator','administrator','member'} or (member['status']=='restricted' and member.get('is_member') is True)
                    if joined:self.db.join_verified(c['id'],uid)
                    else:missing.append(c)
                except (TelegramError,httpx.HTTPError):errors=True
        if missing or errors:
            self.db.event(uid,'membership_denied')
            rows=[[self.url_button(uid,self.t(uid,'channel_join',title=c['title'])[:64],c['url'],'join_channel')] for c in missing]
            rows += [[self.button(uid,self.t(uid,'join_check'),'check_join')],[self.button(uid,self.t(uid,'support'),'support'),self.button(uid,self.t(uid,'language'),'language')]]
            await self.tg.send(uid,esc(self.t(uid,'join_prompt')+('\n'+self.t(uid,'join_error') if errors else '')),rows)
            return False
        parent=self.db.qualify(uid)
        if parent:self.audit(uid,'referral_qualified',f'Inviter: {parent}')
        required=int(self.db.get('required_referrals','0')); count=self.db.referral_count(uid)
        if action not in SAFE_ACTIONS and count<required:
            await self.say(uid,'ref_unlock',[[self.button(uid,self.t(uid,'invite'),'invite')],self.back(uid)],required=required,count=count)
            return False
        return True

    async def dispatch(self,uid,data):
        a=data['a']
        if a=='my_tickets':
            values=self.db.all('SELECT * FROM tickets WHERE uid=? ORDER BY id DESC LIMIT 20',(uid,))
            rows=[[self.button(uid,f"#{r['id']} • {self.t(uid,'open_status' if r['status']=='open' else 'closed_status')}",'user_ticket',id=r['id'])] for r in values]
            return await self.tg.send(uid,esc(self.t(uid,'my_tickets')),rows+[self.back(uid)])
        if a in {'user_ticket','user_ticket_close'}:
            ticket=self.db.one('SELECT * FROM tickets WHERE id=? AND uid=?',(data['id'],uid))
            if not ticket:return await self.say(uid,'expired')
            if a=='user_ticket_close':
                self.db.run("UPDATE tickets SET status='closed',updated=? WHERE id=? AND uid=?",(time.time(),data['id'],uid))
                self.audit(uid,'ticket_closed',data['id'])
                return await self.say(uid,'ticket_closed',[self.back(uid)],id=data['id'])
            return await self.tg.send(uid,f"#{ticket['id']} • "+esc(self.t(uid,'open_status' if ticket['status']=='open' else 'closed_status')),
                [[self.button(uid,self.t(uid,'continue_ticket'),'support'),self.button(uid,self.t(uid,'close_ticket'),'user_ticket_close',id=ticket['id'])],self.back(uid)])
        if a in ADMIN_ACTIONS|EXTRA_ADMIN:
            if not self.is_admin(uid):return await self.tg.send(uid,'این بخش مخصوص مدیر است.')
            handled=await self.admin_action(uid,data)
            if handled is not NotImplemented:return handled
            self.audit(uid,a)
            return await super().dispatch(uid,data)
        handled=await self.user_action(uid,data)
        if handled is NotImplemented:await self.say(uid,'expired')
        return handled

    async def tickets(self,uid,page=0):
        values=self.db.all("SELECT * FROM tickets ORDER BY (status='open') DESC,updated DESC LIMIT 10 OFFSET ?",(page*10,))
        rows=[[self.button(uid,f"#{x['id']} • {x['uid']} • {x['status']}",'ticket_view',id=x['id'])] for x in values]; nav=[]
        if page:nav.append(self.button(uid,'قبلی','tickets',page=page-1))
        if len(values)==10:nav.append(self.button(uid,'بعدی','tickets',page=page+1))
        if nav:rows.append(nav)
        await self.tg.send(uid,'💬 تیکت‌های پشتیبانی',rows+[self.back(uid,True)])

    async def ticket_view(self,uid,tid):
        ticket=self.db.one('SELECT * FROM tickets WHERE id=?',(tid,))
        if not ticket:return await self.tg.send(uid,'تیکت پیدا نشد.')
        await self.tg.send(uid,f"تیکت #{tid} • {ticket['status']}\n"+esc(self.profile_summary(ticket['uid'])),[
            [self.button(uid,'✍️ پاسخ','ticket_reply',id=tid),self.button(uid,'✅ بستن تیکت','ticket_close',id=tid)],self.back(uid,True)])
        for item in self.db.all('SELECT * FROM ticket_messages WHERE ticket=? ORDER BY id DESC LIMIT 5',(tid,))[::-1]:
            with contextlib.suppress(TelegramError,httpx.HTTPError):
                result=await self.tg.call('copyMessage',chat_id=uid,from_chat_id=item['chat'],message_id=item['message'])
                self.db.run('INSERT OR REPLACE INTO support_map VALUES(?,?,?)',(uid,result['message_id'],tid))

    async def support_message(self,uid,message):
        last=self.db.one("SELECT MAX(created) AS at FROM ticket_messages WHERE direction='user' AND ticket IN (SELECT id FROM tickets WHERE uid=?)",(uid,))['at']
        if last and time.time()-last<3:return await self.say(uid,'support_wait')
        day=period_starts(time.time(),self.cfg.timezone)['روز']
        used=self.db.one("SELECT COUNT(*) AS n FROM ticket_messages WHERE direction='user' AND created>=? AND ticket IN (SELECT id FROM tickets WHERE uid=?)",(day,uid))['n']
        if used>=100:return await self.say(uid,'support_limit')
        if not any(k in message for k in ('text','photo','document','voice','audio','video','sticker','animation','video_note')):return await self.say(uid,'ticket_prompt')
        tid=self.db.ticket(uid)
        self.db.run("INSERT INTO ticket_messages(ticket,direction,chat,message,created) VALUES(?,'user',?,?,?)",(tid,uid,message['message_id'],time.time()))
        self.db.run("UPDATE tickets SET updated=?,status='open' WHERE id=?",(time.time(),tid))
        destinations=[int(self.db.get('support_chat'))] if self.db.get('support_chat') else sorted(self.cfg.owners)
        for dest in destinations:self.db.enqueue(dest,{'type':'ticket','uid':uid,'ticket':tid,'message_id':message['message_id']})
        self.audit(uid,'support_ticket',f'Ticket #{tid}')
        await self.say(uid,'ticket_sent',[self.back(uid)],id=tid)

    async def reply_ticket(self,admin,tid,message):
        ticket=self.db.one('SELECT * FROM tickets WHERE id=?',(tid,))
        if not ticket:return await self.tg.send(message['chat']['id'],'تیکت وجود ندارد.')
        target=ticket['uid']
        try:
            await self.say(target,'ticket_reply',id=tid)
            result=await self.tg.call('copyMessage',chat_id=target,from_chat_id=message['chat']['id'],message_id=message['message_id'])
        except (TelegramError,httpx.HTTPError):
            self.audit(admin,'support_reply_failed',f'Ticket #{tid}')
            return await self.tg.send(message['chat']['id'],'ارسال تأیید نشد؛ وضعیت گیرنده یا دسترسی را بررسی کنید. در خطای شبکه احتمال رسیدن پیام هست؛ خودکار تکرار نمی‌شود.')
        self.db.run("INSERT INTO ticket_messages(ticket,direction,chat,message,created) VALUES(?,'admin',?,?,?)",(tid,message['chat']['id'],message['message_id'],time.time()))
        self.db.run('UPDATE tickets SET updated=? WHERE id=?',(time.time(),tid)); self.db.clear_state(admin)
        self.audit(admin,'support_reply',f"Ticket #{tid} → {target}; message {result['message_id']}")
        await self.tg.send(message['chat']['id'],f'✅ پاسخ تیکت #{tid} ارسال شد.')

    async def handle(self,update):
        callback=update.get('callback_query'); message=(callback or {}).get('message') or update.get('message')
        if not message:return
        if not callback:
            old=message.get('migrate_from_chat_id')
            new=message.get('migrate_to_chat_id')
            chat=message.get('chat',{})
            if chat.get('type') in {'group','supergroup'} and (old or new):
                self.db.migrate_group(old or chat.get('id'),new or chat.get('id'))
                return
        user=callback['from'] if callback else message.get('from')
        if not user or user.get('is_bot'):return
        uid=user['id']; text=message.get('text','').strip(); parts=text.split(maxsplit=1)
        command=parts[0].split('@')[0].lower() if parts else ''
        if message.get('chat',{}).get('type')!='private':
            if callback:return
            if command in {'/bindlog','/bindsupport'} and uid in self.cfg.owners:
                await self.bind_group(uid,'log_chat' if command=='/bindlog' else 'support_chat',message['chat']['id'])
                return await self.tg.send(message['chat']['id'],'✅ این گروه متصل شد. مدیران ربات می‌توانند روی پیام تیکت ریپلای کنند.')
            if self.is_admin(uid) and str(message['chat']['id'])==self.db.get('support_chat'):
                replied=message.get('reply_to_message',{}).get('message_id')
                mapped=self.db.one('SELECT ticket FROM support_map WHERE chat=? AND message=?',(message['chat']['id'],replied)) if replied else None
                if mapped:return await self.reply_ticket(uid,mapped['ticket'],message)
            return
        previous=self.db.user(uid)
        fresh=self.db.touch(user); self.db.event(uid,'activity')
        if command=='/start':self.db.bind_source(uid,parts[1] if len(parts)>1 else '',fresh)
        if fresh:self.audit(uid,'new_user',photo=True)
        elif previous and any(previous[k]!=self.db.user(uid)[k] for k in ('name','last_name','username','is_premium')):self.audit(uid,'profile_changed')
        if fresh and command!='/start' and self.db.user(uid)['language_selected']:await self.say(uid,'privacy')
        if callback:
            try:
                await self.tg.call('answerCallbackQuery',callback_query_id=callback['id'])
            except TelegramError as exc:
                # The notification's expiry must not discard an otherwise valid,
                # owner-bound button action. Other API failures remain visible.
                reason=exc.description.lower()
                if exc.code!=400 or not any(s in reason for s in ('query is too old','query_id_invalid','query id is invalid')):raise
            data=self.db.resolve(callback.get('data',''),uid)
            if not data:
                if not self.db.user(uid)['language_selected']:return await self.language_picker(uid)
                return await self.say(uid,'expired')
            action=data['a']
        else:
            action=COMMANDS.get(command,'home' if command in {'/cancel','/id'} else 'support_send' if self.db.state(uid).get('mode')=='support' else 'contact' if message.get('contact') else 'search')
            data={'a':action}
        if not self.db.user(uid)['language_selected'] and action!='language_set':
            return await self.language_picker(uid)
        if not await self.gate(uid,action):return
        if callback:
            if action!='broadcast_send':self.db.clear_state(uid)
            return await self.dispatch(uid,data)
        if message.get('contact'):
            contact=message['contact']
            if contact.get('user_id')!=uid:return await self.say(uid,'contact_invalid')
            self.db.run('UPDATE users SET phone=? WHERE id=?',(contact['phone_number'],uid)); self.audit(uid,'phone_shared')
            return await self.tg.call('sendMessage',chat_id=uid,text=self.t(uid,'done'),reply_markup={'remove_keyboard':True})
        if command=='/id':return await self.tg.send(uid,f'<code>{uid}</code>')
        if command=='/cancel':self.db.clear_state(uid); return await self.home(uid)
        if command in COMMANDS:
            self.db.clear_state(uid); return await self.dispatch(uid,data)
        if command=='/reply' and self.is_admin(uid):
            try:tid=int(parts[1])
            except (ValueError,IndexError):return await self.tg.send(uid,'قالب: /reply 123')
            return await self.admin_action(uid,{'a':'ticket_reply','id':tid})
        replied=message.get('reply_to_message',{}).get('message_id')
        if replied and self.is_admin(uid):
            mapped=self.db.one('SELECT ticket FROM support_map WHERE chat=? AND message=?',(uid,replied))
            if mapped:return await self.reply_ticket(uid,mapped['ticket'],message)
        if command=='/search':
            self.db.clear_state(uid)
            return await self.search(uid,parts[1]) if len(parts)>1 else await self.say(uid,'query_prompt')
        state=self.db.state(uid)
        if state.get('mode')=='support':return await self.support_message(uid,message)
        if await self.admin_input(uid,message,state):return
        if text and await super().input_state(uid,text,state):return
        if text.startswith('/'):return await self.help(uid)
        if text:return await self.search(uid,text)
        await self.say(uid,'query_prompt')

    async def outbox_once(self):
        item=self.db.one("SELECT * FROM outbox WHERE state='pending' ORDER BY id LIMIT 1")
        if not item:return False
        self.db.run("UPDATE outbox SET state='sending',attempts=attempts+1 WHERE id=?",(item['id'],)); p=json.loads(item['payload'])
        try:
            uid,chat=p.get('uid'),item['chat']
            if p['type']=='ticket':
                tid=p['ticket']; header=await self.tg.send(chat,f'💬 Ticket #{tid}\n'+esc(self.profile_summary(uid)))
                self.db.run('INSERT OR REPLACE INTO support_map VALUES(?,?,?)',(chat,header['message_id'],tid))
                copied=await self.tg.call('copyMessage',chat_id=chat,from_chat_id=uid,message_id=p['message_id'])
                self.db.run('INSERT OR REPLACE INTO support_map VALUES(?,?,?)',(chat,copied['message_id'],tid))
            elif p['type']=='alert':
                active=self.db.one('SELECT enabled FROM saved_searches WHERE id=? AND uid=?',(p.get('search_id'),uid))
                user=self.db.user(uid)
                if not active or not active['enabled'] or not user or user['banned'] or not user['reachable']:
                    self.db.run("UPDATE outbox SET state='skipped' WHERE id=?",(item['id'],))
                    return True
                await self.say(uid,'alert_new',[[self.button(uid,self.t(uid,'search'),'search',q=p['query'],platform=p['platform'],filters=p['filters'])],self.back(uid)],query=p['query'])
            else:
                if p.get('photo') and self.db.user(uid):await self.refresh_profile(uid)
                await self.tg.send(chat,esc(p['text']+'\n\n'+self.profile_summary(uid)))
                if p.get('photo'):await self.profile_photo(chat,uid)
            self.db.run("UPDATE outbox SET state='sent' WHERE id=?",(item['id'],))
        except (TelegramError,httpx.HTTPError):self.db.run("UPDATE outbox SET state='failed' WHERE id=?",(item['id'],))
        return True

    async def alert_once(self):
        row=self.db.one('''SELECT s.* FROM saved_searches s JOIN users u ON u.id=s.uid
          WHERE s.enabled=1 AND u.banned=0 AND u.reachable=1 AND s.checked<? ORDER BY s.checked,s.id LIMIT 1''',(time.time()-21600,))
        if not row:return False
        self.db.run('UPDATE saved_searches SET checked=? WHERE id=?',(time.time(),row['id']))
        try:page=await self.site.search(row['query'],row['platform'],0,json.loads(row['filters']))
        except ProviderError:return True
        ids=[str(x['id']) for x in page.items]; old=json.loads(row['seen'])
        self.db.run('UPDATE saved_searches SET seen=? WHERE id=?',(json.dumps(ids),row['id']))
        if row['checked'] and set(ids)-set(old) and self.db.get('maintenance','0')!='1':
            self.db.enqueue(row['uid'],{'type':'alert','search_id':row['id'],'uid':row['uid'],'query':row['query'],'platform':row['platform'],'filters':json.loads(row['filters'])})
        return True

    async def service_worker(self):
        last_alert=0
        while True:
            try:
                await asyncio.sleep(0.1 if await self.outbox_once() else 1)
                if time.monotonic()-last_alert>30:await self.alert_once(); last_alert=time.monotonic()
            except asyncio.CancelledError:raise
            except Exception:
                self.db.run("UPDATE outbox SET state='failed' WHERE state='sending'"); await asyncio.sleep(3)
