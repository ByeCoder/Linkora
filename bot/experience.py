import json
import time
from datetime import datetime,timedelta
from urllib.parse import quote,urlencode
from zoneinfo import ZoneInfo
from .legacy import FIELDS,esc
from .i18n import LANGUAGES,tr
from .provider import AGES,MEMBERS,PLATFORMS,ProviderError,plain,safe_url
from .store import csv_bytes,period_starts

MENUS=[('search','search_prompt'),('platforms','platforms'),('filters','filters'),('featured','featured'),
 ('trending','trending'),('favorites','favorites'),('saved_searches','saved_searches'),('history','history'),
 ('invite','invite'),('profile','profile'),('daily','daily'),('leaderboard','leaderboard'),('support','support'),('my_tickets','my_tickets'),('language','language'),('help','help')]
BUTTON_KEYS=['menu_'+x[0] for x in MENUS]+['menu_register','search','detail','favorite','open','home','check_join','next','prev','export_page','save_search','similar',
    'join_channel','open_url','website','share','platform_set','filter_set','options','filters_clear','clear_prompt','clear_confirm','language_set','alert_toggle','saved_delete','redeem','board_toggle','user_ticket','user_ticket_close']


class Experience:
    def lang(self,uid):
        u=self.db.user(uid)
        return u['language'] if u and u['language'] in LANGUAGES else 'fa'

    def t(self,uid,key,**values):return tr(self.lang(uid),key,**values)

    async def say(self,uid,key,rows=None,**values):return await self.tg.send(uid,esc(self.t(uid,key,**values)),rows)

    def presentation(self,key):
        row=self.db.one('SELECT data FROM button_config WHERE key=?',(key,))
        return json.loads(row['data']) if row else {}

    def button(self,uid,text,action,**params):
        key=params.pop('_key',action)
        config=self.presentation(key)
        result={'text':config.get('labels',{}).get(self.lang(uid),text),'callback_data':self.db.callback(uid,{'a':action,**params})}
        color=config.get('style',self.presentation('default').get('style','primary'))
        if color in {'primary','success','danger'}:result['style']=color
        if config.get('emoji_id'):result['icon_custom_emoji_id']=config['emoji_id']
        return result

    def url_button(self,uid,text,url,key='website'):
        config=self.presentation(key)
        result={'text':config.get('labels',{}).get(self.lang(uid),text),'url':url}
        style=config.get('style',self.presentation('default').get('style','primary'))
        if style in {'primary','success','danger'}:result['style']=style
        if config.get('emoji_id'):result['icon_custom_emoji_id']=config['emoji_id']
        return result

    def back(self,uid,admin=False):return [self.button(uid,'🛠 پنل مدیریت' if admin else self.t(uid,'home'),'panel' if admin else 'home')]
    def link(self,uid):return f"https://t.me/{self.me.get('username','YOUR_BOT')}?start=r_{uid}"

    async def language_picker(self,uid):
        text=('🌍 Choose your language · زبان خود را انتخاب کنید\n'
              '请选择语言 · अपनी भाषा चुनें\nاختر لغتك · בחרו שפה\n'
              'Elige tu idioma · Choisis ta langue')
        buttons=[self.button(uid,label,'language_set',value=code) for code,label in LANGUAGES.items()]
        return await self.tg.send(uid,esc(text),[buttons[i:i+2] for i in range(0,len(buttons),2)])

    async def home(self,uid):
        u=self.db.user(uid)
        self.db.clear_state(uid)
        text=self.db.get('welcome_'+self.lang(uid),'') or (self.db.get('welcome','') if self.lang(uid)=='fa' else '') or self.t(uid,'welcome',name=u['name'] or '💚')
        if not u['onboarded']:text+='\n\n'+self.t(uid,'privacy')
        self.db.run('UPDATE users SET onboarded=1 WHERE id=?',(uid,))
        text+='\n\n'+self.t(uid,'platforms')+': '+(self.t(uid,'all') if u['platform']=='all' else u['platform'].title())
        entries=[]
        for i,(label,action) in enumerate(MENUS):
            key='menu_'+label
            config=self.presentation(key)
            if config.get('visible',True):entries.append((config.get('order',i*10),self.button(uid,self.t(uid,label),action,_key=key)))
        register=self.presentation('menu_register')
        if register.get('visible',True):entries.append((register.get('order',170),self.url_button(uid,self.t(uid,'register'),self.cfg.base_url+'/en/add-new-group','menu_register')))
        for link in self.db.all('SELECT * FROM custom_links WHERE enabled=1'):
            data=json.loads(link['data']); config=self.presentation('custom_'+str(link['id']))
            if config.get('visible',True):
                label=config.get('labels',{}).get(self.lang(uid),data['label'])
                b={'text':label,'url':data['url']}
                if config.get('style') in {'primary','success','danger'}:b['style']=config['style']
                if config.get('emoji_id'):b['icon_custom_emoji_id']=config['emoji_id']
                entries.append((config.get('order',link['position']),b))
        entries.sort(key=lambda x:x[0]); buttons=[x[1] for x in entries]
        n=int(self.db.get('menu_columns','2'))
        rows=[buttons[i:i+n] for i in range(0,len(buttons),n)]
        if self.is_admin(uid):rows.append([self.button(uid,'🛠 پنل مدیریت','panel')])
        await self.tg.send(uid,esc(text),rows)

    async def help(self,uid):return await self.say(uid,'help_text',[self.back(uid)])

    def quota_limit(self,uid):
        day=datetime.now(ZoneInfo(self.cfg.timezone)).date().isoformat()
        return int(self.db.get('daily_limit',self.cfg.daily_limit))+int(self.db.get(f'quota_bonus:{uid}:{day}','0'))

    async def search(self,uid,query,offset=0,platform=None,filters=None,remember=True):
        query=query.strip()
        if not 1<=len(query)<=200:return await self.say(uid,'query_limit')
        problem=self.db.quota(uid,self.cfg.interval,self.quota_limit(uid))
        if problem:return await self.say(uid,'wait' if 'ثانیه' in problem else 'quota',[[self.button(uid,self.t(uid,'profile'),'profile'),self.button(uid,self.t(uid,'invite'),'invite')]])
        platform=platform or self.db.user(uid)['platform']; filters=self.db.filters(uid) if filters is None else filters
        await self.say(uid,'fetching')
        base={'q':query,'platform':platform,'filters':filters}
        try:page=await self.site.search(query,platform,offset,filters)
        except ProviderError:
            self.db.event(uid,'search_error'); self.audit(uid,'site_search_error')
            return await self.say(uid,'site_error',[[self.button(uid,self.t(uid,'retry'),'search',offset=offset,**base)],self.back(uid)])
        self.db.event(uid,'search_ok',query)
        if remember:self.db.remember(uid,query)
        if page.cached:self.db.event(uid,'cache_hit')
        stamp=datetime.fromtimestamp(page.fetched_at,ZoneInfo(self.cfg.timezone)).strftime('%Y-%m-%d %H:%M')
        text=esc(self.t(uid,'results',query=query,total=f'{page.total:,}',page=offset//10+1,stamp=stamp,cache=self.t(uid,'cache') if page.cached else ''))
        rows=[]
        for i,row in enumerate(page.items,offset+1):
            title=plain(row.get('title'))[:85] or self.t(uid,'unknown')
            text+=f"\n\n{i}. <b>{esc(title)}</b>\n{esc(row.get('platform'))} • {self.t(uid,'members')}: {esc(row.get('count_members'))}"
            rows.append([self.button(uid,f'{i}. {title}'[:60],'detail',row=row)])
        if not page.items:text+='\n\n'+self.t(uid,'empty')
        nav=[]
        if offset:nav.append(self.button(uid,self.t(uid,'prev'),'search',_key='prev',offset=max(0,offset-10),**base))
        if page.items and offset+10<page.total:nav.append(self.button(uid,self.t(uid,'next'),'search',_key='next',offset=offset+10,**base))
        if nav:rows.append(nav)
        rows += [[self.button(uid,self.t(uid,'save_search'),'save_search',**base)],
            [self.button(uid,self.t(uid,'csv'),'export_page',items=page.items),self.url_button(uid,self.t(uid,'website'),page.url)],self.back(uid)]
        await self.tg.send(uid,text,rows)

    async def detail(self,uid,row):
        text=f"<b>{esc(plain(row.get('title'))[:180])}</b>\n\n{esc(plain(row.get('description'))[:1500])}\n\n"
        for field,label in [('platform','platforms'),('type_name','type'),('category','category'),('language','lang_label'),('location','country'),('count_members','members'),('creation_date','created')]:
            text+=self.t(uid,label)+': '+esc(row.get(field) if row.get(field) is not None else self.t(uid,'unknown'))+'\n'
        text+='\n'+esc(self.t(uid,'source_note'))
        rows=[[self.button(uid,self.t(uid,'open'),'open',row=row),self.button(uid,self.t(uid,'favorite'),'favorite',row=row)],
              [self.button(uid,self.t(uid,'similar'),'similar',row=row)]]
        if row.get('slug') and row.get('platform'):
            url=self.cfg.base_url+'/en/details/'+quote(str(row['platform']).lower(),safe='')+'/'+quote(str(row['slug']),safe='')
            rows.append([self.url_button(uid,self.t(uid,'website'),url),self.url_button(uid,self.t(uid,'share'),'https://t.me/share/url?'+urlencode({'url':url,'text':'Linkora ✨ '+plain(row.get('title'))[:100]}),'share')])
        await self.tg.send(uid,text,rows+[self.back(uid)])

    async def filters(self,uid):
        current=self.db.filters(uid); labels={'language':'lang_label','category':'category','code':'country','creation date':'age','member count':'members'}
        text=self.t(uid,'filters')+'\n'+'\n'.join(self.t(uid,labels[k])+': '+(self.t(uid,v) if k=='creation date' and v in AGES else str(v)) for k,v in current.items())
        rows=[[self.button(uid,self.t(uid,label),'options',kind=kind,page=0) for label,kind in pair] for pair in [[('lang_label','languages'),('category','categories')],[('country','locations'),('age','age')],[('members','members')]]]
        rows += [[self.button(uid,self.t(uid,'filters_clear'),'filters_clear')],self.back(uid)]
        await self.tg.send(uid,esc(text),rows)

    async def options(self,uid,kind,page):
        if kind=='age':key,options='creation date',[(x,self.t(uid,x)) for x in AGES]
        elif kind=='members':key,options='member count',[(x,'100K+' if x=='100001' else x) for x in MEMBERS]
        else:
            key={'languages':'language','categories':'category','locations':'code'}[kind]
            try:values=await self.site.options(kind)
            except ProviderError:return await self.say(uid,'site_error')
            options=[(str(x.get('code') if kind=='locations' else x['name']),x['name']) for x in values]
        start=max(0,page)*8
        rows=[[self.button(uid,str(label)[:60],'filter_set',key=key,value=value)] for value,label in options[start:start+8]]
        nav=[]
        if page:nav.append(self.button(uid,self.t(uid,'prev'),'options',kind=kind,page=page-1))
        if start+8<len(options):nav.append(self.button(uid,self.t(uid,'next'),'options',kind=kind,page=page+1))
        if nav:rows.append(nav)
        await self.say(uid,'choose',rows+[[self.button(uid,self.t(uid,'filter_clear'),'filter_set',key=key,value='')],self.back(uid)])

    async def user_action(self,uid,d):
        a=d['a']
        if a in {'home','check_join'}:return await self.home(uid)
        if a=='help':return await self.help(uid)
        if a=='privacy':return await self.say(uid,'privacy',[self.back(uid)])
        if a=='search_prompt':return await self.say(uid,'query_prompt')
        if a=='search':return await self.search(uid,d['q'],d.get('offset',0),d.get('platform'),d.get('filters'),False)
        if a=='language':return await self.say(uid,'choose',[[self.button(uid,label,'language_set',value=lang)] for lang,label in LANGUAGES.items()]+[self.back(uid)])
        if a=='language_set':
            if d['value'] in LANGUAGES:self.db.run('UPDATE users SET language=?,language_selected=1 WHERE id=?',(d['value'],uid))
            return await self.home(uid)
        if a=='platforms':return await self.say(uid,'choose',[[self.button(uid,self.t(uid,'all') if k=='all' else k.title(),'platform_set',value=k)] for k in PLATFORMS]+[self.back(uid)])
        if a=='platform_set':
            if d['value'] in PLATFORMS:self.db.run('UPDATE users SET platform=? WHERE id=?',(d['value'],uid))
            return await self.home(uid)
        if a=='filters':return await self.filters(uid)
        if a=='options':return await self.options(uid,d['kind'],d['page'])
        if a=='filter_set':
            self.db.filter_set(uid,d['key'],d['value']); return await self.filters(uid)
        if a=='filters_clear':
            self.db.run("UPDATE users SET filters='{}' WHERE id=?",(uid,)); return await self.filters(uid)
        if a=='detail':return await self.detail(uid,d['row'])
        if a=='similar':
            row=d['row']; return await self.search(uid,str(row.get('category') or row.get('title') or 'community')[:200],platform=str(row.get('platform') or 'all').lower(),filters={})
        if a=='open':
            url=safe_url(d['row'].get('url'))
            if not url:return await self.say(uid,'no_link')
            self.db.event(uid,'open_link',d['row'].get('id'))
            return await self.tg.send(uid,esc(plain(d['row'].get('title'))),[[self.url_button(uid,self.t(uid,'open'),url,'open_url')],self.back(uid)])
        if a=='favorite':
            try:saved=self.db.favorite(uid,d['row'])
            except ValueError:return await self.say(uid,'favorite_limit')
            return await self.say(uid,'saved' if saved else 'removed')
        if a in {'favorites','history'}:
            page=d.get('page',0); table=a
            count=self.db.one(f'SELECT COUNT(*) AS n FROM {table} WHERE uid=?',(uid,))['n']
            values=self.db.all(f"SELECT * FROM {table} WHERE uid=? ORDER BY {'at' if a=='favorites' else 'id'} DESC LIMIT 10 OFFSET ?",(uid,page*10))
            rows=[]
            for x in values:
                if a=='favorites':
                    row=json.loads(x['data']); rows.append([self.button(uid,plain(row.get('title'))[:60] or '—','detail',row=row)])
                else:rows.append([self.button(uid,x['query'][:60],'search',q=x['query'])])
            nav=[]
            if page:nav.append(self.button(uid,self.t(uid,'prev'),a,page=page-1))
            if (page+1)*10<count:nav.append(self.button(uid,self.t(uid,'next'),a,page=page+1))
            if nav:rows.append(nav)
            text=self.t(uid,a)+f': {count}'+('\n'+self.t(uid,'snapshot') if a=='favorites' else '')
            return await self.tg.send(uid,esc(text),rows+[[self.button(uid,self.t(uid,'clear'),'clear_prompt',kind=a)],self.back(uid)])
        if a=='clear_prompt':return await self.say(uid,'clear_confirm',[[self.button(uid,self.t(uid,'confirm'),'clear_confirm',kind=d['kind'])],self.back(uid)])
        if a=='clear_confirm':
            if d['kind'] in {'history','favorites'}:self.db.run(f"DELETE FROM {d['kind']} WHERE uid=?",(uid,))
            return await self.say(uid,'done')
        if a=='export_page':return await self.tg.document(uid,csv_bytes(d['items'],FIELDS),'linkora-results.csv',self.t(uid,'source_note'))
        if a=='support':
            self.db.state(uid,{'mode':'support'}); return await self.say(uid,'ticket_prompt',[self.back(uid)])
        if a=='invite':return await self.say(uid,'invite_text',[[self.url_button(uid,self.t(uid,'share'),'https://t.me/share/url?'+urlencode({'url':self.link(uid),'text':'Linkora 💚'}),'share')],self.back(uid)],count=self.db.referral_count(uid),points=self.db.user(uid)['points'],reward=self.db.get('referral_points','20'),link=self.link(uid))
        if a=='profile':
            u=self.db.user(uid); day=period_starts(time.time(),self.cfg.timezone)['روز']
            used=self.db.one("SELECT COUNT(*) AS n FROM events WHERE uid=? AND kind='search_request' AND at>=?",(uid,day))['n']
            return await self.say(uid,'profile_text',[[self.button(uid,self.t(uid,'redeem'),'redeem')],[self.button(uid,self.t(uid,'contact_share'),'contact'),self.button(uid,self.t(uid,'contact_remove'),'contact_remove')],self.back(uid)],name=u['name'],id=uid,points=u['points'],refs=self.db.referral_count(uid),streak=u['streak'],used=used,limit=self.quota_limit(uid))
        if a=='contact':return await self.tg.call('sendMessage',chat_id=uid,text=self.t(uid,'contact_note'),reply_markup={'keyboard':[[{'text':self.t(uid,'contact_share'),'request_contact':True}]],'one_time_keyboard':True,'resize_keyboard':True})
        if a=='contact_remove':
            self.db.run("UPDATE users SET phone='' WHERE id=?",(uid,)); self.audit(uid,'phone_removed'); return await self.say(uid,'done')
        if a=='redeem':
            u=self.db.user(uid); day=datetime.now(ZoneInfo(self.cfg.timezone)).date().isoformat(); key=f'quota_bonus:{uid}:{day}'; bonus=int(self.db.get(key,'0'))
            if u['points']<50 or bonus>=100:return await self.say(uid,'points_low')
            with self.db.db:
                self.db.db.execute('UPDATE users SET points=points-50 WHERE id=? AND points>=50',(uid,))
                self.db.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(bonus+20)))
            return await self.say(uid,'done')
        if a=='daily':
            now=datetime.now(ZoneInfo(self.cfg.timezone)).date(); u=self.db.user(uid)
            if u['last_checkin']==now.isoformat():return await self.say(uid,'daily_taken')
            streak=u['streak']+1 if u['last_checkin']==(now-timedelta(days=1)).isoformat() else 1
            points=max(0,min(100,int(self.db.get('daily_points','5'))))
            self.db.run('UPDATE users SET points=points+?,last_checkin=?,streak=? WHERE id=?',(points,now.isoformat(),streak,uid)); self.db.event(uid,'checkin')
            return await self.say(uid,'daily_won',[self.back(uid)],points=points,streak=streak)
        if a=='leaderboard':
            values=self.db.all('SELECT name,points FROM users WHERE leaderboard=1 AND banned=0 ORDER BY points DESC,id LIMIT 10')
            text=self.t(uid,'leaderboard')+'\n'+('\n'.join(f"{i}. {x['name']} • {x['points']} ✨" for i,x in enumerate(values,1)) or self.t(uid,'empty'))
            label='board_leave' if self.db.user(uid)['leaderboard'] else 'board_join'
            return await self.tg.send(uid,esc(text),[[self.button(uid,self.t(uid,label),'board_toggle')],self.back(uid)])
        if a=='board_toggle':
            self.db.run('UPDATE users SET leaderboard=1-leaderboard WHERE id=?',(uid,)); return await self.dispatch(uid,{'a':'leaderboard'})
        if a=='trending':
            values=self.db.all("SELECT detail AS query,COUNT(*) AS n FROM events WHERE kind='search_ok' AND at>? AND length(detail)>0 GROUP BY lower(detail) HAVING COUNT(DISTINCT uid)>=3 ORDER BY n DESC LIMIT 10",(time.time()-7*86400,))
            return await self.tg.send(uid,esc(self.t(uid,'trending')),[[self.button(uid,x['query'][:60],'search',q=x['query'])] for x in values]+[self.back(uid)])
        if a=='featured':
            values=self.db.all("SELECT * FROM featured WHERE enabled=1 AND (language='all' OR language=?) ORDER BY position,id",(self.lang(uid),)); rows=[]
            for x in values:
                titles=json.loads(x['title']); label=titles.get(self.lang(uid),titles.get('en',next(iter(titles.values())))); rows.append([self.button(uid,label[:60],'search',q=x['query'])])
            return await self.tg.send(uid,esc(self.t(uid,'featured') if rows else self.t(uid,'empty')),rows+[self.back(uid)])
        if a=='save_search':
            try:self.db.save_search(uid,d['q'],d.get('platform','all'),d.get('filters',{}))
            except ValueError:return await self.say(uid,'saved_limit')
            return await self.say(uid,'saved',[[self.button(uid,self.t(uid,'saved_searches'),'saved_searches')],self.back(uid)])
        if a=='saved_searches':
            rows=[]
            for x in self.db.all('SELECT * FROM saved_searches WHERE uid=? ORDER BY id',(uid,)):
                rows += [[self.button(uid,x['query'][:40],'search',q=x['query'],platform=x['platform'],filters=json.loads(x['filters']))],
                    [self.button(uid,self.t(uid,'alerts_off' if x['enabled'] else 'alerts_on'),'alert_toggle',id=x['id']),self.button(uid,self.t(uid,'delete'),'saved_delete',id=x['id'])]]
            return await self.tg.send(uid,esc(self.t(uid,'saved_searches')+'\n'+self.t(uid,'alert_note')),rows+[self.back(uid)])
        if a in {'alert_toggle','saved_delete'}:
            if a=='saved_delete':self.db.run('DELETE FROM saved_searches WHERE id=? AND uid=?',(d['id'],uid))
            else:self.db.run('UPDATE saved_searches SET enabled=1-enabled WHERE id=? AND uid=?',(d['id'],uid))
            return await self.dispatch(uid,{'a':'saved_searches'})
        return NotImplemented
