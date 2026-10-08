#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OSINTMY v8.1 IMPROVED PERSON DOSSIER SYSTEM
Реальный поиск профилей | Быстрый | HTML-отчёт
"""

import requests
import json
import re
import sys
import time
import os
import warnings
import logging
import random
from datetime import datetime
from urllib.parse import quote
from collections import defaultdict
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings('ignore')
try:
    requests.packages.urllib3.disable_warnings()
except Exception:
    pass

class Config:
    VERSION = '8.1 IMPROVED'
    REQUEST_TIMEOUT = 10
    CONNECTION_RETRIES = 3
    DELAY_BETWEEN = 0.5
    MAX_WORKERS = 8
    MIN_RESPONSE_LEN = 30
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
    ]

CONFIG = Config()

def setup_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(h)
    return logger

logger = setup_logger('OSINTMy')

TRANSLIT_MAP = {
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y',
    'к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f',
    'х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
    'А':'A','Б':'B','В':'V','Г':'G','Д':'D','Е':'E','Ё':'E','Ж':'Zh','З':'Z','И':'I','Й':'Y',
    'К':'K','Л':'L','М':'M','Н':'N','О':'O','П':'P','Р':'R','С':'S','Т':'T','У':'U','Ф':'F',
    'Х':'H','Ц':'Ts','Ч':'Ch','Ш':'Sh','Щ':'Sch','Ъ':'','Ы':'Y','Ь':'','Э':'E','Ю':'Yu','Я':'Ya',
}

def translit(text):
    return ''.join(TRANSLIT_MAP.get(c, c) for c in text)

def generate_usernames(first, last=None):
    first = first.strip().lower()
    last = (last or '').strip().lower()
    candidates = set()
    if first: candidates.add(first)
    if last: candidates.add(last)
    first_t = translit(first)
    last_t = translit(last) if last else ''
    for f, l in [(first, last), (first_t, last_t)]:
        if not f: continue
        candidates.add(f)
        if l:
            candidates.add(l)
            candidates.add(f + l)
            candidates.add(f + '_' + l)
            candidates.add(f + '.' + l)
            candidates.add(f + '-' + l)
            candidates.add(l + f)
            candidates.add(l + '_' + f)
            candidates.add(l + '.' + f)
            if f: candidates.add(f[0] + l)
            if l: candidates.add(f + l[0])
    cleaned = []
    for u in candidates:
        u = re.sub(r'[^a-z0-9._-]', '', u.lower())
        if 3 <= len(u) <= 30:
            cleaned.append(u)
    return sorted(set(cleaned), key=len)[:35]

class HTTPSession:
    def __init__(self):
        self.session = requests.Session()
        self.success = 0
        self.fail = 0
    def get(self, url, timeout=None):
        timeout = timeout or CONFIG.REQUEST_TIMEOUT
        headers = {
            'User-Agent': random.choice(CONFIG.USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'DNT': '1',
        }
        for attempt in range(CONFIG.CONNECTION_RETRIES):
            try:
                r = self.session.get(url, headers=headers, timeout=timeout, verify=False, allow_redirects=True)
                if r.status_code == 200 and len(r.text) > CONFIG.MIN_RESPONSE_LEN:
                    self.success += 1
                    return r
                if r.status_code in (404, 410):
                    return r
            except Exception:
                pass
            time.sleep(0.25 * (attempt + 1))
        self.fail += 1
        return None

SOCIAL_CHECKERS = {
    'GitHub': {
        'url': 'https://github.com/{}',
        'exists': lambda r: r is not None and r.status_code == 200 and 'Not Found' not in r.text[:800],
        'profile': 'https://github.com/{}',
    },
    'GitLab': {
        'url': 'https://gitlab.com/{}',
        'exists': lambda r: r is not None and r.status_code == 200,
        'profile': 'https://gitlab.com/{}',
    },
    'Reddit': {
        'url': 'https://www.reddit.com/user/{}',
        'exists': lambda r: r is not None and r.status_code == 200 and 'nobody on Reddit goes by that name' not in r.text,
        'profile': 'https://www.reddit.com/user/{}',
    },
    'Telegram': {
        'url': 'https://t.me/{}',
        'exists': lambda r: r is not None and r.status_code == 200 and ('tgme_page_title' in r.text or 'tgme_page_photo' in r.text),
        'profile': 'https://t.me/{}',
    },
    'VK': {
        'url': 'https://vk.com/{}',
        'exists': lambda r: r is not None and r.status_code == 200 and ('page_name' in r.text or 'profile_info' in r.text or 'OwnerPageName' in r.text or 'owner_page_name' in r.text),
        'profile': 'https://vk.com/{}',
    },
    'Instagram': {
        'url': 'https://www.instagram.com/{}/',
        'exists': lambda r: r is not None and r.status_code == 200 and '"username"' in r.text and 'Page Not Found' not in r.text,
        'profile': 'https://www.instagram.com/{}/',
    },
    'TikTok': {
        'url': 'https://www.tiktok.com/@{}',
        'exists': lambda r: r is not None and r.status_code == 200 and ('"uniqueId"' in r.text or 'tiktok.com/@' in str(r.url)),
        'profile': 'https://www.tiktok.com/@{}',
    },
    'Twitch': {
        'url': 'https://www.twitch.tv/{}',
        'exists': lambda r: r is not None and r.status_code == 200 and 'content="Twitch"' in r.text,
        'profile': 'https://www.twitch.tv/{}',
    },
    'Steam': {
        'url': 'https://steamcommunity.com/id/{}',
        'exists': lambda r: r is not None and r.status_code == 200 and 'error_ctn' not in r.text and 'could not be found' not in r.text,
        'profile': 'https://steamcommunity.com/id/{}',
    },
    'Habr': {
        'url': 'https://habr.com/ru/users/{}/',
        'exists': lambda r: r is not None and r.status_code == 200 and 'page-error' not in r.text,
        'profile': 'https://habr.com/ru/users/{}/',
    },
    'Keybase': {
        'url': 'https://keybase.io/{}',
        'exists': lambda r: r is not None and r.status_code == 200,
        'profile': 'https://keybase.io/{}',
    },
    'About.me': {
        'url': 'https://about.me/{}',
        'exists': lambda r: r is not None and r.status_code == 200 and 'Page not found' not in r.text,
        'profile': 'https://about.me/{}',
    },
    'Dev.to': {
        'url': 'https://dev.to/{}',
        'exists': lambda r: r is not None and r.status_code == 200,
        'profile': 'https://dev.to/{}',
    },
    'Medium': {
        'url': 'https://medium.com/@{}',
        'exists': lambda r: r is not None and r.status_code == 200 and 'PAGE_NOT_FOUND' not in r.text,
        'profile': 'https://medium.com/@{}',
    },
}

SEARCH_SOURCES = [
    ('Yandex', 'https://yandex.ru/search/?text={}'),
    ('Google', 'https://www.google.com/search?q={}'),
    ('DuckDuckGo', 'https://html.duckduckgo.com/html/?q={}'),
    ('Bing', 'https://www.bing.com/search?q={}'),
    ('VK Search', 'https://vk.com/search?c%5Bq%5D={}&c%5Bsection%5D=people'),
    ('OK.ru', 'https://ok.ru/search?st.mode=Users&st.query={}'),
    ('GitHub Search', 'https://github.com/search?q={}&type=users'),
    ('Reddit Search', 'https://www.reddit.com/search/?q={}&type=user'),
]

class Extractor:
    EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
    PHONE_RE = re.compile(r'(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|\+?\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{2,4}[\s\-]?\d{2,4}[\s\-]?\d{0,4}')
    URL_RE = re.compile(r'https?://(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_+.~#?&/=]*)')
    PROFILE_PATTERNS = {
        'vk': re.compile(r'(?:vk\.com|vkontakte\.ru)/(?:id\d+|[a-zA-Z0-9_.]+)'),
        'instagram': re.compile(r'instagram\.com/([a-zA-Z0-9_.]+)'),
        'telegram': re.compile(r't\.me/([a-zA-Z0-9_]+)'),
        'github': re.compile(r'github\.com/([a-zA-Z0-9_-]+)'),
        'twitter': re.compile(r'(?:twitter|x)\.com/([a-zA-Z0-9_]+)'),
        'linkedin': re.compile(r'linkedin\.com/in/([a-zA-Z0-9_-]+)'),
        'facebook': re.compile(r'facebook\.com/(?:profile\.php\?id=\d+|[a-zA-Z0-9.]+)'),
        'tiktok': re.compile(r'tiktok\.com/@([a-zA-Z0-9_.]+)'),
        'youtube': re.compile(r'youtube\.com/(?:@|channel/|c/|user/)([a-zA-Z0-9_-]+)'),
        'ok': re.compile(r'ok\.ru/(?:profile/\d+|[a-zA-Z0-9_.]+)'),
    }
    @classmethod
    def emails(cls, text):
        return list(set(cls.EMAIL_RE.findall(text)))[:30]
    @classmethod
    def phones(cls, text):
        return list(set(m.strip() for m in cls.PHONE_RE.findall(text) if len(re.sub(r'\D','',m)) >= 10))[:20]
    @classmethod
    def urls(cls, text):
        return list(set(cls.URL_RE.findall(text)))[:40]
    @classmethod
    def profiles_from_text(cls, text):
        found = defaultdict(set)
        for platform, pattern in cls.PROFILE_PATTERNS.items():
            for m in pattern.findall(text):
                if isinstance(m, tuple): m = m[0]
                if len(m) > 1 and m.lower() not in ('search','login','share','help','about','home','explore'):
                    found[platform].add(m)
        return {k: list(v)[:15] for k,v in found.items()}
    @classmethod
    def extract_all(cls, html):
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for tag in soup(['script','style','noscript']): tag.decompose()
            text = soup.get_text(separator=' ', strip=True)[:15000]
        except Exception:
            text = (html or '')[:15000]
        return {
            'emails': cls.emails(text),
            'phones': cls.phones(text),
            'urls': cls.urls(text),
            'profiles': cls.profiles_from_text(text + ' ' + ((html or '')[:5000])),
        }

class PersonScraper:
    C = {'P':'\033[95m','G':'\033[92m','Y':'\033[93m','R':'\033[91m','C':'\033[96m','B':'\033[1m','E':'\033[0m'}
    def __init__(self, first_name, last_name=None):
        self.first = first_name.strip()
        self.last = (last_name or '').strip()
        self.full = f"{self.first} {self.last}".strip()
        self.http = HTTPSession()
        self.found_profiles = defaultdict(list)
        self.emails = set()
        self.phones = set()
        self.other_urls = set()
        self.search_hits = []
        self.usernames = generate_usernames(self.first, self.last)
        self.stats = {'checked':0,'found':0,'requests':0}
    def _p(self, text, color='E', end='\n'):
        print(f"{self.C.get(color,'')}{text}{self.C['E']}", end=end, flush=True)
    def banner(self):
        self._p(f"""
{self.C['P']}{self.C['B']}
╔══════════════════════════════════════════════════════════════════╗
║     💜 OSINTMY v8.1 — REAL PROFILE FINDER 💜                    ║
║     Реальные ссылки на профили | Быстрый поиск | HTML-отчёт     ║
╚══════════════════════════════════════════════════════════════════╝
{self.C['E']}""", 'P')
    def _check_one(self, platform, conf, username):
        url = conf['url'].format(username)
        r = self.http.get(url, timeout=8)
        self.stats['requests'] += 1
        try:
            if conf['exists'](r):
                return platform, username, conf['profile'].format(username)
        except Exception:
            pass
        return None
    def search_social_profiles(self):
        self._p(f"\n🔍 Проверка {len(self.usernames)} вариантов username на {len(SOCIAL_CHECKERS)} платформах...", 'C')
        self._p(f"   Варианты: {', '.join(self.usernames[:10])}{'...' if len(self.usernames)>10 else ''}\n", 'Y')
        tasks = [(p, c, u) for p, c in SOCIAL_CHECKERS.items() for u in self.usernames]
        found_count = 0
        with ThreadPoolExecutor(max_workers=CONFIG.MAX_WORKERS) as pool:
            futures = {pool.submit(self._check_one, p, c, u): (p, u) for p, c, u in tasks}
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    platform, username, url = result
                    if not any(x['username'] == username for x in self.found_profiles[platform]):
                        self.found_profiles[platform].append({'username': username, 'url': url})
                        found_count += 1
                        self.stats['found'] += 1
                        self._p(f"   ✅ {platform:<12} → {url}", 'G')
        self._p(f"\n   Найдено профилей: {found_count}", 'G')
        return found_count
    def search_engines(self):
        self._p(f"\n🌐 Поиск в поисковиках и соцсетях по ФИО «{self.full}»...", 'C')
        queries = [self.full]
        if self.last:
            queries.append(f'"{self.first}" "{self.last}"')
        for name, url_tmpl in SEARCH_SOURCES:
            for q in queries[:2]:
                try:
                    url = url_tmpl.format(quote(q))
                    self._p(f"   {name:<20}", 'Y', end=' ')
                    r = self.http.get(url)
                    self.stats['requests'] += 1
                    self.stats['checked'] += 1
                    if not r:
                        self._p('❌', 'R')
                        continue
                    data = Extractor.extract_all(r.text)
                    for e in data['emails']: self.emails.add(e)
                    for p in data['phones']: self.phones.add(p)
                    for u in data['urls']:
                        if any(x in u for x in ('vk.com','t.me','instagram','github','linkedin','facebook','ok.ru','tiktok','twitter','x.com')):
                            self.other_urls.add(u)
                    added = 0
                    for platform, users in data['profiles'].items():
                        for u in users:
                            mapping = {
                                'vk': f"https://vk.com/{u}",
                                'instagram': f"https://www.instagram.com/{u}/",
                                'telegram': f"https://t.me/{u}",
                                'github': f"https://github.com/{u}",
                                'twitter': f"https://x.com/{u}",
                                'linkedin': f"https://www.linkedin.com/in/{u}",
                                'facebook': f"https://www.facebook.com/{u}",
                                'tiktok': f"https://www.tiktok.com/@{u}",
                                'youtube': f"https://www.youtube.com/@{u}",
                                'ok': f"https://ok.ru/{u}",
                            }
                            url_p = mapping.get(platform)
                            if url_p and not any(x['url'] == url_p for x in self.found_profiles[platform]):
                                self.found_profiles[platform].append({'username': u, 'url': url_p})
                                added += 1
                    if added or data['emails'] or data['phones']:
                        self._p(f"✅ (+{added} проф., {len(data['emails'])} email)", 'G')
                        self.search_hits.append({'source': name, 'url': url, 'added': added})
                    else:
                        self._p('—', 'Y')
                    time.sleep(CONFIG.DELAY_BETWEEN)
                except Exception as e:
                    self._p(f'❌ {str(e)[:40]}', 'R')
    def run(self):
        start = datetime.now()
        self.banner()
        self._p(f"👤 Цель: {self.full}", 'B')
        self._p(f"🕐 Старт: {start.strftime('%Y-%m-%d %H:%M:%S')}\n", 'C')
        self.search_social_profiles()
        self.search_engines()
        elapsed = (datetime.now() - start).total_seconds()
        total_profiles = sum(len(v) for v in self.found_profiles.values())
        self._p(f"\n{'='*60}", 'B')
        self._p("✅ ПОИСК ЗАВЕРШЁН", 'G')
        self._p(f"{'='*60}", 'B')
        self._p(f"   Профилей найдено : {total_profiles}", 'G')
        self._p(f"   Email             : {len(self.emails)}", 'G')
        self._p(f"   Телефонов         : {len(self.phones)}", 'G')
        self._p(f"   Запросов          : {self.stats['requests']}", 'C')
        self._p(f"   Время             : {int(elapsed)} сек", 'C')
        return {
            'full_name': self.full,
            'profiles': dict(self.found_profiles),
            'emails': sorted(self.emails),
            'phones': sorted(self.phones),
            'other_urls': sorted(self.other_urls)[:50],
            'search_hits': self.search_hits,
            'usernames_tried': self.usernames,
            'elapsed': elapsed,
            'timestamp': datetime.now().isoformat(),
        }

class ReportGenerator:
    def __init__(self, data):
        self.data = data
    def generate(self, filename=None):
        name = self.data['full_name']
        safe = re.sub(r'[^\wа-яА-ЯёЁ-]', '_', name)[:40]
        if not filename:
            filename = f"OSINTMY_v81_{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        profiles = self.data.get('profiles', {})
        total_p = sum(len(v) for v in profiles.values())
        profile_sections = []
        for platform, items in sorted(profiles.items(), key=lambda x: -len(x[1])):
            if not items: continue
            cards = []
            for item in items:
                cards.append(
                    f'<a class="profile-card" href="{item["url"]}" target="_blank" rel="noopener">'
                    f'<div class="platform">{platform.upper()}</div>'
                    f'<div class="uname">@{item["username"]}</div>'
                    f'<div class="link">{item["url"]}</div></a>'
                )
            profile_sections.append(
                f'<div class="section"><h2>🔗 {platform.upper()} <span class="badge">{len(items)}</span></h2>'
                f'<div class="grid">{"".join(cards)}</div></div>'
            )
        emails_html = ''.join(f'<div class="data-item">📧 {e}</div>' for e in self.data.get('emails', [])) or '<div class="empty">не найдено</div>'
        phones_html = ''.join(f'<div class="data-item">☎️ {p}</div>' for p in self.data.get('phones', [])) or '<div class="empty">не найдено</div>'
        sections_html = ''.join(profile_sections) if profile_sections else '<div class="section"><h2>Профили</h2><div class="empty">Профили не найдены</div></div>'
        ts = self.data.get("timestamp", "")
        elapsed = int(self.data.get("elapsed", 0))
        n_emails = len(self.data.get("emails", []))
        n_phones = len(self.data.get("phones", []))
        n_plat = len(profiles)
        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OSINTMY v8.1 - {name}</title>
<style>
:root {{ --bg:#0f0c29; --card:#1a1635; --accent:#a855f7; --accent2:#c084fc; --text:#f1f5f9; --muted:#94a3b8; --success:#4ade80; --warn:#fbbf24; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'Segoe UI',system-ui,sans-serif; background:linear-gradient(145deg,#0f0c29 0%,#302b63 50%,#24243e 100%); color:var(--text); min-height:100vh; line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:24px; }}
header {{ background:linear-gradient(135deg,#7c3aed,#4c1d95); border-radius:16px; padding:40px 32px; margin-bottom:28px; box-shadow:0 20px 50px rgba(124,58,237,.4); text-align:center; }}
header h1 {{ font-size:2.2rem; margin-bottom:8px; }}
header .sub {{ opacity:.85; font-size:1.05rem; }}
.meta {{ display:flex; flex-wrap:wrap; gap:16px; justify-content:center; margin-top:24px; }}
.meta-item {{ background:rgba(255,255,255,.12); backdrop-filter:blur(8px); padding:12px 20px; border-radius:10px; min-width:140px; }}
.meta-item .label {{ font-size:.8rem; opacity:.7; }}
.meta-item .value {{ font-size:1.25rem; font-weight:700; color:var(--warn); }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:14px; margin-bottom:28px; }}
.stat {{ background:var(--card); border:1px solid rgba(168,85,247,.3); border-radius:12px; padding:20px; text-align:center; transition:transform .2s; }}
.stat:hover {{ transform:translateY(-3px); }}
.stat .num {{ font-size:2rem; font-weight:800; color:var(--accent2); }}
.stat .lbl {{ font-size:.85rem; color:var(--muted); margin-top:4px; }}
.section {{ background:var(--card); border-radius:14px; padding:24px; margin-bottom:22px; border-left:4px solid var(--accent); }}
.section h2 {{ font-size:1.3rem; margin-bottom:16px; color:var(--accent2); display:flex; align-items:center; gap:10px; }}
.badge {{ background:var(--accent); color:#fff; font-size:.75rem; padding:2px 10px; border-radius:20px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:12px; }}
.profile-card {{ display:block; background:rgba(0,0,0,.35); border-radius:10px; padding:14px 16px; text-decoration:none; color:inherit; border:1px solid transparent; transition:all .2s; }}
.profile-card:hover {{ border-color:var(--accent); background:rgba(168,85,247,.15); transform:translateX(4px); }}
.profile-card .platform {{ font-size:.75rem; color:var(--accent2); font-weight:600; }}
.profile-card .uname {{ font-size:1.05rem; font-weight:700; margin:4px 0; color:var(--success); }}
.profile-card .link {{ font-size:.8rem; color:var(--muted); word-break:break-all; }}
.data-item {{ background:rgba(0,0,0,.3); padding:10px 14px; border-radius:8px; margin-bottom:8px; font-family:ui-monospace,monospace; font-size:.95rem; }}
.empty {{ color:var(--muted); font-style:italic; }}
.disclaimer {{ background:rgba(239,68,68,.12); border-left:4px solid #ef4444; padding:16px 20px; border-radius:8px; margin:20px 0; color:#fca5a5; font-size:.9rem; }}
footer {{ text-align:center; padding:32px; color:var(--muted); font-size:.85rem; border-top:1px solid rgba(168,85,247,.2); margin-top:40px; }}
</style>
</head>
<body>
<div class="container">
<header>
<h1>💜 OSINTMY v8.1</h1>
<div class="sub">Real Profile Finder - Intelligence Report</div>
<div class="disclaimer">⚠️ Образовательные и исследовательские цели. Информация собрана только из открытых источников. Используйте ответственно и в соответствии с законодательством.</div>
<div class="meta">
<div class="meta-item"><div class="label">Цель</div><div class="value">{name}</div></div>
<div class="meta-item"><div class="label">Дата</div><div class="value">{ts[:10]}</div></div>
<div class="meta-item"><div class="label">Профилей</div><div class="value">{total_p}</div></div>
<div class="meta-item"><div class="label">Время</div><div class="value">{elapsed}с</div></div>
</div>
</header>
<div class="stats">
<div class="stat"><div class="num">{total_p}</div><div class="lbl">Профилей</div></div>
<div class="stat"><div class="num">{n_emails}</div><div class="lbl">Email</div></div>
<div class="stat"><div class="num">{n_phones}</div><div class="lbl">Телефонов</div></div>
<div class="stat"><div class="num">{n_plat}</div><div class="lbl">Платформ</div></div>
</div>
{sections_html}
<div class="section"><h2>📧 Email <span class="badge">{n_emails}</span></h2>{emails_html}</div>
<div class="section"><h2>☎️ Телефоны <span class="badge">{n_phones}</span></h2>{phones_html}</div>
<footer>
<p style="color:var(--accent2);font-weight:700;font-size:1.1rem;">💜 OSINTMY v8.1 IMPROVED</p>
<p>Сгенерировано: {ts}</p>
<p>Цель: {name} · Найдено профилей: {total_p}</p>
<p style="margin-top:12px;color:#f87171;">Образовательные цели · Соблюдайте законы</p>
</footer>
</div>
</body>
</html>"""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html)
        return filename

def main():
    os.system('clear' if os.name != 'nt' else 'cls')
    print("\033[95m\033[1m")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║     💜 OSINTMY v8.1 — REAL PROFILE FINDER 💜                ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print("\033[0m")
    first = input("\033[95m[>]\033[0m Имя: ").strip()
    if not first:
        print("\033[91mИмя обязательно\033[0m")
        return
    last = input("\033[95m[>]\033[0m Фамилия (можно пусто): ").strip()
    scraper = PersonScraper(first, last)
    data = scraper.run()
    report = ReportGenerator(data)
    fname = report.generate()
    print(f"\n\033[92m\033[1m✅ HTML-отчёт сохранён: {fname}\033[0m\n")
    print("Откройте файл в браузере — там прямые кликабельные ссылки на найденные профили.")
    json_name = fname.replace('.html', '.json')
    with open(json_name, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"JSON-данные: {json_name}\n")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\nОшибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
