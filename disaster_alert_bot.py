#!/usr/bin/env python3
"""
Disaster Alert Bot — Thailand (TMD) + Indonesia (BMKG) -> Telegram

Polls official warning feeds and sends new alerts to Telegram:

  Thailand  (Thai Meteorological Department, TMD)
    - CAP warning feed (heavy rain, storms, etc.)   www.tmd.go.th/en/api/xml/CAP
    - Storm / weather warning bulletins             www.tmd.go.th/en/api/xml/storm-tracking
  Indonesia (BMKG)
    - CAP weather early-warning feed                cuaca.bmkg.go.id/data/public/cap/feed/en/rss.xml
    - InaTEWS earthquake/tsunami CAP (Atom)         bmkg-content-inatews.storage.googleapis.com/capatomlast40event.xml
    - Latest earthquake + M5.0+ list (JSON)         data.bmkg.go.id/DataMKG/TEWS/
  Optional (not Thai/Indonesian government)
    - USGS earthquakes around Thailand / Myanmar    earthquake.usgs.gov

No third-party packages needed: Python 3.9+ standard library only.

Usage:
  python disaster_alert_bot.py --get-chat-id     # find your Telegram chat id
  python disaster_alert_bot.py --test            # send a test message
  python disaster_alert_bot.py --dry-run --once  # print what would be sent
  python disaster_alert_bot.py --once            # one check (cron / GitHub Actions)
  python disaster_alert_bot.py                   # loop forever (Docker / server)
  python disaster_alert_bot.py --command "/country_list_province"   # preview a command reply

Groups: add the bot to a Telegram group and it starts posting alerts there by
itself (no TELEGRAM_CHAT_ID or commands needed). New members get a welcome with
the warnings active right now.

Telegram commands (optional, answered while the bot runs):
  /weather <province>              weather now + 3 days + warnings (or just send the name)
  /warnings                        official warnings active right now
  /country_list_province [TH|ID]   provinces you can use in AREA_KEYWORDS
  /help

Data credit: TMD (Thailand) and BMKG (Indonesia). BMKG requires that BMKG is
credited as the source when its data is shown.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

VERSION = "1.0.0"
USER_AGENT = f"disaster-alert-bot/{VERSION} (+personal early-warning notifier)"

# --------------------------------------------------------------------------
# Feed URLs
# --------------------------------------------------------------------------
FEEDS = {
    "tmd_cap": {
        "en": "https://www.tmd.go.th/en/api/xml/CAP",
        "local": "https://www.tmd.go.th/api/xml/CAP",
    },
    "tmd_storm": {
        "en": "https://www.tmd.go.th/en/api/xml/storm-tracking",
        "local": "https://www.tmd.go.th/en/api/xml/storm-tracking",
    },
    "bmkg_cap": {
        "en": "https://cuaca.bmkg.go.id/data/public/cap/feed/en/rss.xml",
        "local": "https://cuaca.bmkg.go.id/data/public/cap/feed/id/rss.xml",
    },
    "bmkg_inatews": "https://bmkg-content-inatews.storage.googleapis.com/capatomlast40event.xml",
    "bmkg_quake_latest": "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json",
    "bmkg_quake_m5": "https://data.bmkg.go.id/DataMKG/TEWS/gempaterkini.json",
    "usgs": "https://earthquake.usgs.gov/fdsnws/event/1/query",
    # Weather now + next days for /weather (model forecast, free, no key; not a government source)
    "open_meteo": "https://api.open-meteo.com/v1/forecast",
}

SOURCE_LABEL = {
    "tmd_cap": "🇹🇭 Thai Meteorological Department (TMD)",
    "tmd_storm": "🇹🇭 TMD weather warning bulletin",
    "bmkg_cap": "🇮🇩 BMKG weather early warning",
    "bmkg_inatews": "🇮🇩 BMKG InaTEWS (earthquake / tsunami)",
    "bmkg_quake": "🇮🇩 BMKG earthquake report",
    "usgs": "🌏 USGS earthquake (international)",
}

# Country flag for each source, shown in the "active right now" lists
SOURCE_FLAG = {"tmd_cap": "🇹🇭", "tmd_storm": "🇹🇭", "bmkg_cap": "🇮🇩", "bmkg_inatews": "🇮🇩",
               "bmkg_quake": "🇮🇩", "usgs": "🌏"}

SEVERITY_RANK = {"unknown": 0, "minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
SEVERITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🟡", 1: "🔵", 0: "⚪"}

# Rough box around Thailand + Myanmar + Laos for USGS when no home location is set
TH_REGION_BOX = {"minlatitude": 5.0, "maxlatitude": 28.5, "minlongitude": 92.0, "maxlongitude": 106.5}

# --------------------------------------------------------------------------
# Provinces (for the /country_list_province command and AREA_KEYWORDS)
# Thailand: 77 provinces, grouped like TMD forecast regions.
# Indonesia: 38 provinces, listed by the Indonesian name BMKG uses in its
# feeds (English in brackets), grouped by island region.
# --------------------------------------------------------------------------
PROVINCES = {
    "TH": {
        "name": "Thailand",
        "flag": "🇹🇭",
        "regions": [
            ("North", ["Chiang Mai", "Chiang Rai", "Kamphaeng Phet", "Lampang", "Lamphun", "Mae Hong Son",
                       "Nakhon Sawan", "Nan", "Phayao", "Phetchabun", "Phichit", "Phitsanulok", "Phrae",
                       "Sukhothai", "Tak", "Uthai Thani", "Uttaradit"]),
            ("Northeast", ["Amnat Charoen", "Bueng Kan", "Buriram", "Chaiyaphum", "Kalasin", "Khon Kaen", "Loei",
                           "Maha Sarakham", "Mukdahan", "Nakhon Phanom", "Nakhon Ratchasima", "Nong Bua Lamphu",
                           "Nong Khai", "Roi Et", "Sakon Nakhon", "Sisaket", "Surin", "Ubon Ratchathani",
                           "Udon Thani", "Yasothon"]),
            ("Bangkok and vicinity", ["Bangkok", "Nakhon Pathom", "Nonthaburi", "Pathum Thani", "Samut Prakan",
                                      "Samut Sakhon"]),
            ("Central", ["Ang Thong", "Ayutthaya", "Chai Nat", "Kanchanaburi", "Lopburi", "Nakhon Nayok",
                         "Ratchaburi", "Samut Songkhram", "Saraburi", "Sing Buri", "Suphan Buri"]),
            ("East", ["Chachoengsao", "Chanthaburi", "Chonburi", "Prachinburi", "Rayong", "Sa Kaeo", "Trat"]),
            ("South (east coast)", ["Chumphon", "Nakhon Si Thammarat", "Narathiwat", "Pattani", "Phatthalung",
                                    "Phetchaburi", "Prachuap Khiri Khan", "Songkhla", "Surat Thani", "Yala"]),
            ("South (west coast)", ["Krabi", "Phang Nga", "Phuket", "Ranong", "Satun", "Trang"]),
        ],
    },
    "ID": {
        "name": "Indonesia",
        "flag": "🇮🇩",
        "regions": [
            ("Sumatra", ["Aceh", "Sumatera Utara (North Sumatra)", "Sumatera Barat (West Sumatra)", "Riau",
                         "Kep. Riau (Riau Islands)", "Jambi", "Bengkulu", "Sumatera Selatan (South Sumatra)",
                         "Kep. Bangka Belitung (Bangka Belitung Islands)", "Lampung"]),
            ("Java", ["Banten", "DKI Jakarta", "Jawa Barat (West Java)", "Jawa Tengah (Central Java)",
                      "DI Yogyakarta", "Jawa Timur (East Java)"]),
            ("Bali & Nusa Tenggara", ["Bali", "Nusa Tenggara Barat (West Nusa Tenggara)",
                                      "Nusa Tenggara Timur (East Nusa Tenggara)"]),
            ("Kalimantan", ["Kalimantan Barat (West)", "Kalimantan Tengah (Central)", "Kalimantan Selatan (South)",
                            "Kalimantan Timur (East)", "Kalimantan Utara (North)"]),
            ("Sulawesi", ["Sulawesi Utara (North)", "Gorontalo", "Sulawesi Tengah (Central)",
                          "Sulawesi Barat (West)", "Sulawesi Selatan (South)", "Sulawesi Tenggara (Southeast)"]),
            ("Maluku", ["Maluku", "Maluku Utara (North Maluku)"]),
            ("Papua", ["Papua", "Papua Barat (West Papua)", "Papua Barat Daya (Southwest Papua)",
                       "Papua Tengah (Central Papua)", "Papua Pegunungan (Highland Papua)",
                       "Papua Selatan (South Papua)"]),
        ],
    },
}


# Where to read the weather for each province: its capital city (lat, lon).
PROVINCE_COORDS = {
    "Chiang Mai": (18.79, 98.98), "Chiang Rai": (19.91, 99.83), "Kamphaeng Phet": (16.48, 99.52),
    "Lampang": (18.29, 99.49), "Lamphun": (18.58, 99.01), "Mae Hong Son": (19.3, 97.97),
    "Nakhon Sawan": (15.7, 100.14), "Nan": (18.78, 100.78), "Phayao": (19.19, 99.88),
    "Phetchabun": (16.42, 101.16), "Phichit": (16.44, 100.35), "Phitsanulok": (16.82, 100.26),
    "Phrae": (18.15, 100.14), "Sukhothai": (17.01, 99.82), "Tak": (16.87, 99.13), "Uthai Thani": (15.38, 100.02),
    "Uttaradit": (17.63, 100.09), "Amnat Charoen": (15.86, 104.63), "Bueng Kan": (18.36, 103.65),
    "Buriram": (14.99, 103.1), "Chaiyaphum": (15.81, 102.03), "Kalasin": (16.43, 103.51),
    "Khon Kaen": (16.45, 102.83), "Loei": (17.49, 101.73), "Maha Sarakham": (16.18, 103.3),
    "Mukdahan": (16.55, 104.72), "Nakhon Phanom": (17.41, 104.78), "Nakhon Ratchasima": (14.97, 102.1),
    "Nong Bua Lamphu": (17.2, 102.44), "Nong Khai": (17.88, 102.74), "Roi Et": (16.06, 103.65),
    "Sakon Nakhon": (17.16, 104.15), "Sisaket": (15.11, 104.33), "Surin": (14.88, 103.49),
    "Ubon Ratchathani": (15.24, 104.85), "Udon Thani": (17.42, 102.79), "Yasothon": (15.79, 104.15),
    "Bangkok": (13.75, 100.5), "Nakhon Pathom": (13.82, 100.04), "Nonthaburi": (13.86, 100.51),
    "Pathum Thani": (14.01, 100.53), "Samut Prakan": (13.6, 100.6), "Samut Sakhon": (13.55, 100.27),
    "Ang Thong": (14.59, 100.45), "Ayutthaya": (14.35, 100.58), "Chai Nat": (15.19, 100.12),
    "Kanchanaburi": (14.0, 99.55), "Lopburi": (14.8, 100.65), "Nakhon Nayok": (14.2, 101.21),
    "Ratchaburi": (13.54, 99.82), "Samut Songkhram": (13.41, 100.0), "Saraburi": (14.53, 100.92),
    "Sing Buri": (14.89, 100.4), "Suphan Buri": (14.47, 100.12), "Chachoengsao": (13.69, 101.07),
    "Chanthaburi": (12.61, 102.1), "Chonburi": (13.36, 100.98), "Prachinburi": (14.05, 101.37),
    "Rayong": (12.68, 101.26), "Sa Kaeo": (13.81, 102.07), "Trat": (12.24, 102.52), "Chumphon": (10.5, 99.18),
    "Nakhon Si Thammarat": (8.43, 99.97), "Narathiwat": (6.43, 101.82), "Pattani": (6.87, 101.25),
    "Phatthalung": (7.62, 100.08), "Phetchaburi": (13.11, 99.94), "Prachuap Khiri Khan": (11.82, 99.78),
    "Songkhla": (7.2, 100.6), "Surat Thani": (9.14, 99.33), "Yala": (6.54, 101.28), "Krabi": (8.07, 98.91),
    "Phang Nga": (8.45, 98.53), "Phuket": (7.89, 98.4), "Ranong": (9.97, 98.63), "Satun": (6.62, 100.07),
    "Trang": (7.56, 99.61), "Aceh": (5.54, 95.33), "Sumatera Utara": (3.58, 98.67),
    "Sumatera Barat": (-0.95, 100.35), "Riau": (0.52, 101.44), "Kep. Riau": (0.92, 104.46),
    "Jambi": (-1.6, 103.62), "Bengkulu": (-3.8, 102.27), "Sumatera Selatan": (-2.92, 104.75),
    "Lampung": (-5.43, 105.26), "Banten": (-6.12, 106.15), "DKI Jakarta": (-6.21, 106.85),
    "Jawa Barat": (-6.92, 107.61), "Jawa Tengah": (-6.99, 110.42), "DI Yogyakarta": (-7.8, 110.36),
    "Jawa Timur": (-7.25, 112.75), "Bali": (-8.65, 115.22), "Nusa Tenggara Barat": (-8.58, 116.12),
    "Nusa Tenggara Timur": (-10.17, 123.61), "Kalimantan Barat": (-0.03, 109.33),
    "Kalimantan Tengah": (-2.21, 113.92), "Kalimantan Selatan": (-3.32, 114.59),
    "Kalimantan Timur": (-0.49, 117.15), "Kalimantan Utara": (2.84, 117.37), "Sulawesi Utara": (1.48, 124.85),
    "Gorontalo": (0.54, 123.06), "Sulawesi Tengah": (-0.91, 119.87), "Sulawesi Barat": (-2.68, 118.89),
    "Sulawesi Selatan": (-5.15, 119.43), "Sulawesi Tenggara": (-3.98, 122.52), "Maluku": (-3.7, 128.18),
    "Maluku Utara": (0.74, 127.56), "Papua": (-2.53, 140.72), "Papua Barat": (-0.86, 134.06),
    "Papua Barat Daya": (-0.88, 131.26), "Papua Tengah": (-3.36, 135.5), "Papua Pegunungan": (-4.1, 138.95),
    "Papua Selatan": (-8.5, 140.41), "Kep. Bangka Belitung": (-2.13, 106.11),
}

# Other names people may type -> province (capitals, big towns, short forms)
PLACE_ALIASES = {
    "Banda Aceh": "Aceh", "Medan": "Sumatera Utara", "Padang": "Sumatera Barat", "Pekanbaru": "Riau",
    "Tanjung Pinang": "Kep. Riau", "Batam": "Kep. Riau", "Palembang": "Sumatera Selatan",
    "Pangkal Pinang": "Kep. Bangka Belitung", "Bandar Lampung": "Lampung", "Serang": "Banten",
    "Jakarta": "DKI Jakarta", "Bandung": "Jawa Barat", "Semarang": "Jawa Tengah", "Yogyakarta": "DI Yogyakarta",
    "Jogja": "DI Yogyakarta", "Surabaya": "Jawa Timur", "Denpasar": "Bali", "Mataram": "Nusa Tenggara Barat",
    "Lombok": "Nusa Tenggara Barat", "Kupang": "Nusa Tenggara Timur", "Pontianak": "Kalimantan Barat",
    "Palangkaraya": "Kalimantan Tengah", "Banjarmasin": "Kalimantan Selatan", "Samarinda": "Kalimantan Timur",
    "Balikpapan": "Kalimantan Timur", "Manado": "Sulawesi Utara", "Palu": "Sulawesi Tengah",
    "Mamuju": "Sulawesi Barat", "Makassar": "Sulawesi Selatan", "Kendari": "Sulawesi Tenggara", "Ambon": "Maluku",
    "Ternate": "Maluku Utara", "Jayapura": "Papua", "Manokwari": "Papua Barat", "Sorong": "Papua Barat Daya",
    "Nabire": "Papua Tengah", "Wamena": "Papua Pegunungan", "Merauke": "Papua Selatan", "Pattaya": "Chonburi",
    "Hat Yai": "Songkhla", "Koh Samui": "Surat Thani", "Hua Hin": "Prachuap Khiri Khan",
    "Korat": "Nakhon Ratchasima", "Phra Nakhon Si Ayutthaya": "Ayutthaya",
}


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        sys.exit(f"Config error: {name} must be a number, got {raw!r}")


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real env vars win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


class Config:
    def __init__(self) -> None:
        self.token = _env("TELEGRAM_BOT_TOKEN")
        self.chat_ids = [c.strip() for c in _env("TELEGRAM_CHAT_ID").split(",") if c.strip()]
        self.countries = {c.strip().upper() for c in _env("COUNTRIES", "TH,ID").split(",") if c.strip()}
        self.lang = "local" if _env("ALERT_LANG", "en").lower() in ("local", "th", "id") else "en"

        sev = _env("MIN_SEVERITY", "Severe").lower()
        if sev not in SEVERITY_RANK:
            sys.exit(f"Config error: MIN_SEVERITY must be one of Minor/Moderate/Severe/Extreme, got {sev!r}")
        self.min_severity = SEVERITY_RANK[sev]

        self.area_keywords = [k.strip().lower() for k in _env("AREA_KEYWORDS").split(",") if k.strip()]
        self.include_bulletins = _env_bool("INCLUDE_BULLETINS", True)
        self.skip_updates = _env_bool("SKIP_CAP_UPDATES", False)

        self.min_quake_mag = _env_float("MIN_QUAKE_MAG", 5.0)
        self.home_lat = _env_float("HOME_LAT", float("nan"))
        self.home_lon = _env_float("HOME_LON", float("nan"))
        self.quake_radius_km = _env_float("QUAKE_RADIUS_KM", 0.0)
        self.enable_usgs = _env_bool("ENABLE_USGS", True)

        self.poll_seconds = max(30, int(_env_float("POLL_SECONDS", 120)))
        self.state_file = _env("STATE_FILE", "state.json")
        self.heartbeat_hours = _env_float("HEARTBEAT_HOURS", 0)
        self.source_fail_alert = max(1, int(_env_float("SOURCE_FAIL_ALERT_AFTER", 15)))
        self.insecure_ssl = _env_bool("INSECURE_SSL", False)

        # Groups/channels the bot is added to get alerts automatically
        self.auto_subscribe = _env_bool("AUTO_SUBSCRIBE", True)
        # Greet people who join a subscribed group, with the warnings active now
        self.welcome_new_members = _env_bool("WELCOME_NEW_MEMBERS", True)

    @property
    def has_home(self) -> bool:
        return not (math.isnan(self.home_lat) or math.isnan(self.home_lon))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
_SSL_CTX: ssl.SSLContext | None = None


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    global _SSL_CTX
    if _SSL_CTX is None:
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        _SSL_CTX = ctx
    return _SSL_CTX


def http_get(url: str, cfg: Config, params: dict | None = None, retries: int = 2, timeout: int = 25) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(cfg.insecure_ssl)) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 — network errors vary a lot
            last_err = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last_err}")


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------
def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text(el: ET.Element | None, path: str, default: str = "") -> str:
    if el is None:
        return default
    found = el.find(path)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def _clean(text: str) -> str:
    """Strip HTML tags and squeeze whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(value)
        except Exception:  # noqa: BLE001
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_feed(data: bytes) -> list[dict]:
    """Parse an RSS 2.0 or Atom feed (or a bare CAP alert) into entries."""
    root = _strip_ns(ET.fromstring(data))
    entries: list[dict] = []

    if root.tag == "alert":
        ident = _text(root, "identifier")
        return [{"id": ident, "title": "", "link": "", "description": "", "embedded": root}]

    if root.tag == "rss" or root.find("channel") is not None:
        for item in root.iter("item"):
            link = _text(item, "link")
            guid = _text(item, "guid")
            title = _text(item, "title")
            entries.append({
                "id": guid or link or title,
                "title": title,
                "link": link,
                "description": _text(item, "description"),
                "pubdate": _text(item, "pubDate"),
                "embedded": item.find(".//alert"),
            })
        return entries

    if root.tag == "feed":
        for entry in root.findall("entry"):
            link = ""
            for ln in entry.findall("link"):
                href = ln.get("href", "")
                typ = (ln.get("type") or "").lower()
                if "cap" in typ or href.lower().endswith(".xml"):
                    link = href
                    break
                if not link and ln.get("rel", "alternate") == "alternate":
                    link = href
            title = _text(entry, "title")
            entries.append({
                "id": _text(entry, "id") or link or title,
                "title": title,
                "link": link,
                "description": _text(entry, "summary") or _text(entry, "content"),
                "pubdate": _text(entry, "updated") or _text(entry, "published"),
                "embedded": entry.find(".//alert"),
            })
        return entries

    raise ValueError(f"Unknown feed format (root <{root.tag}>)")


def parse_cap(alert: ET.Element, lang: str) -> dict:
    """Extract the useful bits of a CAP 1.x alert."""
    infos = alert.findall("info")
    info = None
    want = "en" if lang == "en" else None
    if want:
        for i in infos:
            if _text(i, "language", "").lower().startswith(want):
                info = i
                break
    if info is None and infos:
        info = infos[0]

    areas = []
    points: list[tuple[float, float]] = []   # every polygon corner, for the map link
    circle_center: tuple[float, float] | None = None
    if info is not None:
        for area in info.findall("area"):
            desc = _text(area, "areaDesc")
            if desc:
                areas.append(desc)
            for poly in area.findall("polygon"):
                points += _parse_points((poly.text or "").split())
            for circle in area.findall("circle"):
                center = _parse_points((circle.text or "").split()[:1])
                if center and circle_center is None:
                    circle_center = center[0]

    return {
        "identifier": _text(alert, "identifier"),
        "status": _text(alert, "status", "Actual"),
        "msg_type": _text(alert, "msgType", "Alert"),
        "event": _text(info, "event"),
        "urgency": _text(info, "urgency"),
        "severity": _text(info, "severity", "Unknown"),
        "certainty": _text(info, "certainty"),
        "effective": _text(info, "effective") or _text(info, "onset") or _text(alert, "sent"),
        "expires": _text(info, "expires"),
        "headline": _text(info, "headline"),
        "description": _text(info, "description"),
        "instruction": _text(info, "instruction"),
        "web": _text(info, "web"),
        "area": "; ".join(areas),
        "map": _map_spot(points, circle_center),
    }


def _parse_points(pairs: list[str]) -> list[tuple[float, float]]:
    """CAP "lat,lon" pairs -> [(lat, lon)], skipping anything malformed."""
    out = []
    for pair in pairs:
        try:
            lat, lon = (float(x) for x in pair.split(",")[:2])
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out.append((lat, lon))
    return out


def _map_spot(points: list[tuple[float, float]], circle_center: tuple[float, float] | None) -> dict | None:
    """Where the map link points: a pin for a circle (e.g. a quake epicentre), otherwise
    the middle of all the warning polygons, zoomed so the whole area fits."""
    if circle_center:
        return {"lat": circle_center[0], "lon": circle_center[1], "zoom": None}
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    span = max(max(lats) - min(lats), max(lons) - min(lons), 0.05)
    zoom = max(5, min(12, int(math.log2(360 / span))))
    return {"lat": (min(lats) + max(lats)) / 2, "lon": (min(lons) + max(lons)) / 2, "zoom": zoom}


def google_maps_url(lat: float, lon: float, zoom: int | None = None) -> str:
    """A pin at the point, or (with zoom) the area around it."""
    if zoom is None:
        return f"https://www.google.com/maps/search/?api=1&query={lat:.4f},{lon:.4f}"
    return f"https://www.google.com/maps/@?api=1&map_action=map&center={lat:.4f},{lon:.4f}&zoom={zoom}"


def alert_map_url(alert: dict) -> str:
    if alert.get("lat") is not None and alert.get("lon") is not None:  # quake epicentre
        return google_maps_url(alert["lat"], alert["lon"])
    spot = alert.get("map")
    return google_maps_url(spot["lat"], spot["lon"], spot.get("zoom")) if spot else ""


# --------------------------------------------------------------------------
# Collectors — each returns (alerts, seen_ids_to_add)
# Only entries not already in `seen` are processed, so linked CAP files are
# downloaded once per alert, not every poll.
# --------------------------------------------------------------------------
def collect_cap_feed(source: str, url: str, cfg: Config, seen: dict) -> tuple[list[dict], list[str]]:
    entries = parse_feed(http_get(url, cfg))
    alerts, ids = [], []
    for e in entries:
        key = f"{source}:{e['id']}"
        if not e["id"] or key in seen:
            continue
        ids.append(key)

        cap = None
        alert_el = e.get("embedded")
        if alert_el is None and e["link"]:
            try:
                linked = _strip_ns(ET.fromstring(http_get(e["link"], cfg, retries=1)))
                if linked.tag == "alert":
                    alert_el = linked
            except Exception:  # noqa: BLE001 — link may be an HTML page; fall back to feed text
                alert_el = None
        if alert_el is not None:
            cap = parse_cap(alert_el, cfg.lang)

        if cap:
            if cap["status"].lower() != "actual":
                continue  # skip Test / Exercise / Draft messages
            alerts.append({
                "id": key,
                "source": source,
                "kind": "warning",
                "title": cap["event"] or cap["headline"] or e["title"],
                "headline": cap["headline"],
                "severity": SEVERITY_RANK.get(cap["severity"].lower(), 0),
                "severity_label": cap["severity"] or "Unknown",
                "urgency": cap["urgency"],
                "certainty": cap["certainty"],
                "msg_type": cap["msg_type"],
                "area": cap["area"],
                "effective": cap["effective"],
                "expires": cap["expires"],
                "description": cap["description"] or _clean(e["description"]),
                "instruction": cap["instruction"],
                "link": cap["web"] or e["link"],
                "map": cap["map"],
            })
        else:
            # No machine-readable CAP behind this item: it has no severity, so
            # treat it like an official bulletin (area filter still applies)
            # rather than silently dropping it.
            alerts.append({
                "id": key,
                "source": source,
                "kind": "bulletin",
                "title": e["title"],
                "headline": "",
                "severity": 0,
                "severity_label": "Warning",
                "msg_type": "Alert",
                "area": "",
                "effective": e.get("pubdate", ""),
                "expires": "",
                "description": _clean(e["description"]),
                "instruction": "",
                "link": e["link"],
            })
    return alerts, ids


def collect_tmd_bulletins(cfg: Config, seen: dict) -> tuple[list[dict], list[str]]:
    url = FEEDS["tmd_storm"][cfg.lang]
    entries = parse_feed(http_get(url, cfg))
    alerts, ids = [], []
    for e in entries:
        key = f"tmd_storm:{e['id']}"
        if not e["id"] or key in seen:
            continue
        ids.append(key)
        alerts.append({
            "id": key,
            "source": "tmd_storm",
            "kind": "bulletin",
            "title": e["title"],
            "headline": "",
            "severity": 0,
            "severity_label": "Bulletin",
            "msg_type": "Alert",
            "area": "",
            "effective": e.get("pubdate", ""),
            "expires": "",
            "description": _clean(e["description"]),
            "instruction": "",
            "link": e["link"] or "https://www.tmd.go.th/en/warning-and-events/warning-storm",
        })
    return alerts, ids


def _is_tsunami_text(text: str) -> bool:
    t = (text or "").lower()
    if "tsunami" not in t:
        return False
    return not any(neg in t for neg in ("tidak berpotensi", "tidak", "no tsunami", "not ", "no potential"))


def collect_bmkg_quakes(cfg: Config, seen: dict) -> tuple[list[dict], list[str]]:
    quakes: list[dict] = []
    for feed in ("bmkg_quake_latest", "bmkg_quake_m5"):
        data = json.loads(http_get(FEEDS[feed], cfg))
        g = data.get("Infogempa", {}).get("gempa", [])
        quakes.extend(g if isinstance(g, list) else [g])

    alerts, ids = [], []
    for q in quakes:
        qid = q.get("DateTime") or f"{q.get('Tanggal')} {q.get('Jam')}"
        key = f"bmkg_quake:{qid}"
        if key in seen or key in ids:
            continue
        ids.append(key)
        try:
            mag = float(str(q.get("Magnitude", "0")).replace(",", "."))
        except ValueError:
            mag = 0.0
        lat = lon = None
        coords = q.get("Coordinates", "")
        if "," in coords:
            try:
                lat, lon = (float(x) for x in coords.split(",", 1))
            except ValueError:
                pass
        shakemap = q.get("Shakemap")
        alerts.append({
            "id": key,
            "source": "bmkg_quake",
            "kind": "quake",
            "title": f"M{mag:.1f} earthquake — {q.get('Wilayah', '')}",
            "magnitude": mag,
            "lat": lat,
            "lon": lon,
            "depth": q.get("Kedalaman", ""),
            "place": q.get("Wilayah", ""),
            "time": f"{q.get('Tanggal', '')} {q.get('Jam', '')}".strip(),
            "tsunami": _is_tsunami_text(q.get("Potensi", "")),
            "potential": q.get("Potensi", ""),
            "felt": q.get("Dirasakan", ""),
            "link": f"https://data.bmkg.go.id/DataMKG/TEWS/{shakemap}" if shakemap else "https://www.bmkg.go.id/gempabumi",
        })
    return alerts, ids


def collect_usgs(cfg: Config, seen: dict) -> tuple[list[dict], list[str]]:
    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    params: dict = {"format": "geojson", "starttime": start, "minmagnitude": cfg.min_quake_mag, "orderby": "time"}
    if cfg.has_home and cfg.quake_radius_km > 0:
        params.update({"latitude": cfg.home_lat, "longitude": cfg.home_lon, "maxradiuskm": cfg.quake_radius_km})
    else:
        params.update(TH_REGION_BOX)
    data = json.loads(http_get(FEEDS["usgs"], cfg, params=params))

    alerts, ids = [], []
    for f in data.get("features", []):
        key = f"usgs:{f.get('id')}"
        if key in seen:
            continue
        ids.append(key)
        p = f.get("properties", {})
        lon, lat, depth = (f.get("geometry", {}).get("coordinates") or [None, None, None])[:3]
        ts = p.get("time")
        when = datetime.fromtimestamp(ts / 1000, tz=timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M (UTC+7)") if ts else ""
        mag = float(p.get("mag") or 0)
        alerts.append({
            "id": key,
            "source": "usgs",
            "kind": "quake",
            "title": f"M{mag:.1f} earthquake — {p.get('place', '')}",
            "magnitude": mag,
            "lat": lat,
            "lon": lon,
            "depth": f"{depth:.0f} km" if isinstance(depth, (int, float)) else "",
            "place": p.get("place", ""),
            "time": when,
            "tsunami": bool(p.get("tsunami")),
            "potential": "Tsunami flag set by USGS — check official TMD/NDWC notices" if p.get("tsunami") else "",
            "felt": "",
            "link": p.get("url", ""),
        })
    return alerts, ids


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------
def is_expired(alert: dict, now: datetime | None = None) -> bool:
    exp = parse_time(alert.get("expires", ""))
    return bool(exp and exp < (now or datetime.now(timezone.utc)))


# TMD / BMKG spell some places differently from common usage
# ("Nakhon Sri Thammarat", "Pattalung", "Kep. Riau"). Matching ignores spaces,
# dots and hyphens, and maps these variants to one form on both sides.
_AREA_ALIASES = (
    ("nakhonsri", "nakhonsi"),
    ("pattalung", "phatthalung"),
    ("phatalung", "phatthalung"),
    ("srisaket", "sisaket"),
    ("kepulauan", "kep"),
)


def _norm_area(text: str) -> str:
    out = re.sub(r"[\s.\-]+", "", (text or "").lower())
    for variant, canonical in _AREA_ALIASES:
        out = out.replace(variant, canonical)
    return out


def matches_area(alert: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = _norm_area(" ".join(str(alert.get(k, "")) for k in ("area", "title", "headline", "description", "place")))
    return any(_norm_area(k) in hay for k in keywords)


def should_notify(alert: dict, cfg: Config) -> bool:
    if alert["kind"] == "quake":
        if alert.get("tsunami"):
            return True  # always send tsunami-potential quakes
        if alert["magnitude"] < cfg.min_quake_mag:
            return False
        if cfg.has_home and cfg.quake_radius_km > 0 and alert.get("lat") is not None:
            return haversine_km(cfg.home_lat, cfg.home_lon, alert["lat"], alert["lon"]) <= cfg.quake_radius_km
        return True

    if is_expired(alert):
        return False
    if cfg.skip_updates and alert.get("msg_type", "").lower() == "update":
        return False
    if alert["kind"] == "bulletin":
        if not cfg.include_bulletins:
            return False
    elif alert["severity"] < cfg.min_severity:
        return False
    return matches_area(alert, cfg.area_keywords)


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------
def _trim(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_alert(alert: dict, cfg: Config) -> str:
    e = html.escape
    src = SOURCE_LABEL.get(alert["source"], alert["source"])
    lines: list[str] = []

    if alert["kind"] == "quake":
        icon = "🌊🚨" if alert.get("tsunami") else ("🔴" if alert["magnitude"] >= 6 else "🟠")
        lines.append(f"{icon} <b>{e(alert['title'])}</b>")
        lines.append(e(src))
        if alert.get("time"):
            lines.append(f"🕒 {e(alert['time'])}")
        if alert.get("depth"):
            lines.append(f"↧ Depth: {e(str(alert['depth']))}")
        if cfg.has_home and alert.get("lat") is not None:
            d = haversine_km(cfg.home_lat, cfg.home_lon, alert["lat"], alert["lon"])
            lines.append(f"📍 About {d:,.0f} km from your location")
        if alert.get("potential"):
            lines.append(f"⚠️ {e(alert['potential'])}")
        if alert.get("felt"):
            lines.append(f"Felt: {e(_trim(alert['felt'], 300))}")
    else:
        icon = "📢" if alert["kind"] == "bulletin" else SEVERITY_EMOJI.get(alert["severity"], "⚪")
        tag = ""
        mt = (alert.get("msg_type") or "").lower()
        if mt == "update":
            tag = " (update)"
        elif mt == "cancel":
            tag = " (CANCELLED)"
        label = alert["severity_label"].upper() if alert["kind"] == "warning" else "WARNING"
        lines.append(f"{icon} <b>{e(label)} — {e(alert['title'])}</b>{e(tag)}")
        lines.append(e(src))
        if alert.get("headline") and alert["headline"] != alert["title"]:
            lines.append(f"<i>{e(alert['headline'])}</i>")
        if alert.get("area"):
            lines.append(f"📍 {e(_trim(alert['area'], 500))}")
        when = alert.get("effective", "")
        if alert.get("expires"):
            when = f"{when} → {alert['expires']}" if when else f"until {alert['expires']}"
        if when:
            lines.append(f"🕒 {e(when)}")
        meta = " · ".join(x for x in (alert.get("urgency"), alert.get("certainty")) if x)
        if meta:
            lines.append(f"Urgency/certainty: {e(meta)}")
        if alert.get("description"):
            lines.append("")
            lines.append(e(_trim(alert["description"], 1200)))
        if alert.get("instruction"):
            lines.append("")
            lines.append(f"👉 <b>What to do:</b> {e(_trim(alert['instruction'], 800))}")

    links = []
    map_url = alert_map_url(alert)
    if map_url:
        links.append(f'🗺 <a href="{e(map_url, quote=True)}">Google Maps</a>')
    if alert.get("link"):
        links.append(f'<a href="{e(alert["link"], quote=True)}">Official details</a>')
    if links:
        lines.append("")
        lines.append(" · ".join(links))
    return _trim("\n".join(lines), 4000)


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def telegram_api(cfg: Config, method: str, payload: dict | None = None, timeout: int = 30) -> dict:
    if not cfg.token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    url = f"https://api.telegram.org/bot{cfg.token}/{method}"
    data = urllib.parse.urlencode(payload or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"Telegram {method} HTTP {err.code}: {body}") from err


class Notifier:
    def __init__(self, cfg: Config, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.sent: list[str] = []
        self.subscribers: set[str] = set()   # groups/chats that joined by themselves
        self.muted: set[str] = set()         # chats told to stop (/stop): never sent to
        self.gone: set[str] = set()          # chats that removed or blocked the bot
        self.migrated: dict[str, str] = {}   # group id -> new supergroup id

    def targets(self) -> list[str]:
        return [c for c in dict.fromkeys([*self.cfg.chat_ids, *sorted(self.subscribers)]) if c not in self.muted]

    def send(self, text: str, chat_ids: list[str] | None = None) -> bool:
        """Send to every chat (TELEGRAM_CHAT_ID + subscribed groups), or only to `chat_ids`."""
        self.sent.append(text)
        if self.dry_run:
            print("----- would send -----")
            print(text)
            print("----------------------")
            return True
        targets = chat_ids or self.targets()
        if not targets:
            log("No chat to send to yet: add the bot to a Telegram group (or set TELEGRAM_CHAT_ID)")
            return False
        ok = True
        for chat_id in targets:
            for _ in range(3):
                try:
                    res = telegram_api(self.cfg, "sendMessage", {
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": "true",
                    })
                except Exception as exc:  # noqa: BLE001 — network error: retry, never crash the caller
                    res = {"ok": False, "description": str(exc)}
                    time.sleep(2)
                    continue
                if res.get("ok"):
                    break
                params = res.get("parameters") or {}
                if params.get("retry_after"):
                    time.sleep(int(params["retry_after"]) + 1)
                    continue
                if params.get("migrate_to_chat_id"):  # group became a supergroup: new id
                    new_id = str(params["migrate_to_chat_id"])
                    self.migrated[chat_id] = new_id
                    chat_id = new_id
                    continue
                if res.get("error_code") == 403 and chat_id in self.subscribers:
                    self.gone.add(chat_id)  # bot was removed / blocked
                log(f"Telegram error for chat {chat_id}: {res.get('description')}")
                ok = False
                break
            time.sleep(0.4)  # stay well under Telegram rate limits
        return ok


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def subscribers_path(state_path: str) -> str:
    """Groups/chats that subscribed are also kept in their own file next to the state file,
    so deleting state.json for a fresh start does not forget them (Telegram does not
    resend the "bot added to group" event, so a forgotten group would stay silent)."""
    return f"{os.path.splitext(state_path)[0]}.subscribers.json"


def load_subscribers(state_path: str) -> dict:
    try:
        with open(subscribers_path(state_path), encoding="utf-8") as fh:
            subs = json.load(fh)
        return {str(k): v for k, v in subs.items()} if isinstance(subs, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def muted_path(state_path: str) -> str:
    """Chats told to stop (/stop) are kept in their own file too, so a fresh state.json
    does not make the bot start posting in them again."""
    return f"{os.path.splitext(state_path)[0]}.muted.json"


def load_muted(state_path: str) -> dict:
    try:
        with open(muted_path(state_path), encoding="utf-8") as fh:
            muted = json.load(fh)
        return {str(k): v for k, v in muted.items()} if isinstance(muted, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_state(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        log(f"State file {path} unreadable — starting fresh")
        return None


def save_state(path: str, state: dict) -> None:
    cutoff = time.time() - 45 * 86400
    state["seen"] = {k: v for k, v in state.get("seen", {}).items() if v >= cutoff}
    tmp = f"{path}.tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    if "subscribers" in state:
        subs_path = subscribers_path(path)
        with open(f"{subs_path}.tmp", "w", encoding="utf-8") as fh:
            json.dump(state["subscribers"], fh, ensure_ascii=False, indent=1)
        os.replace(f"{subs_path}.tmp", subs_path)
    if "muted" in state:
        m_path = muted_path(path)
        with open(f"{m_path}.tmp", "w", encoding="utf-8") as fh:
            json.dump(state["muted"], fh, ensure_ascii=False, indent=1)
        os.replace(f"{m_path}.tmp", m_path)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------
# Bot commands (Telegram)
# Telegram command names allow only a-z, 0-9 and "_", so the menu shows
# /country_list_province. Typed variants with "-" (e.g. /country-list-provience)
# are accepted too.
# --------------------------------------------------------------------------
PROVINCE_COMMANDS = {"country_list_province", "country_list_provinces", "country_list_provience",
                     "provinces", "province"}
HELP_COMMANDS = {"start", "help"}
WEATHER_COMMANDS = {"weather", "w", "forecast"}
WARNING_COMMANDS = {"warnings", "warning", "alerts", "now"}
STOP_COMMANDS = {"stop", "mute", "unsubscribe", "stop_listening"}
RESUME_COMMANDS = {"resume", "unmute", "subscribe"}
CHATS_COMMANDS = {"chats", "groups"}
COUNTRY_ARGS = {"th": "TH", "thai": "TH", "thailand": "TH", "id": "ID", "indo": "ID", "indonesia": "ID"}
BOT_COMMAND_MENU = [
    {"command": "weather", "description": "Weather + warnings for a province, e.g. /weather Chiang Mai"},
    {"command": "warnings", "description": "Official warnings active right now"},
    {"command": "country_list_province", "description": "Thailand + Indonesia provinces (add TH or ID for one)"},
    {"command": "stop", "description": "Stop alerts and replies in this chat (admins)"},
    {"command": "resume", "description": "Turn alerts back on in this chat"},
    {"command": "help", "description": "What this bot does"},
]


def parse_command(text: str) -> tuple[str, list[str]] | None:
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    head, *args = text.split()
    name = head[1:].split("@", 1)[0].lower().replace("-", "_")
    return name, args


def province_list_messages(cfg: Config, only: list[str] | None = None) -> list[str]:
    """One message per country (keeps each under Telegram's 4096-char limit)."""
    keys = [_norm_area(k) for k in cfg.area_keywords]

    def mark(name: str) -> str:
        # "Jawa Barat (West Java)": match keywords against the feed name only
        feed_name, _, english = name.partition(" (")
        hit = keys and any(k in _norm_area(feed_name) for k in keys)
        out = f"✅ {html.escape(feed_name)}" if hit else html.escape(feed_name)
        return f"{out} ({html.escape(english)}" if english else out

    msgs = []
    for code in ("TH", "ID"):
        if only and code not in only:
            continue
        country = PROVINCES[code]
        total = sum(len(p) for _, p in country["regions"])
        status = "watching" if code in cfg.countries else "not watched (add to COUNTRIES)"
        lines = [f"{country['flag']} <b>{country['name']}: {total} provinces</b> · {status}"]
        for region, provinces in country["regions"]:
            lines.append(f"\n<b>{mark(region)}</b> ({len(provinces)})")
            lines.append(", ".join(mark(p) for p in provinces))
        msgs.append("\n".join(lines))

    if cfg.area_keywords:
        current = f"📍 Your AREA_KEYWORDS: {html.escape(', '.join(cfg.area_keywords))}\n✅ = matches your keywords"
    else:
        current = "📍 Your AREA_KEYWORDS: none, so you get warnings for the whole country"
    msgs.append(
        f"{current}\n\n"
        "To get warnings only for some places, set <code>AREA_KEYWORDS</code> in .env with the names above, e.g.\n"
        "<code>AREA_KEYWORDS=Chiang Mai,Songkhla,Jawa Barat</code>\n"
        "Spaces and Si/Sri spellings don't matter. A short word covers more: <i>Papua</i> matches all 6 Papua "
        "provinces, and region words like <i>North</i> or <i>South</i> match TMD bulletins. "
        "Restart the bot after changing .env.\n\n"
        "🌦 <b>Weather for a province:</b> send its name, e.g. <code>Chiang Mai</code>, "
        "or <code>/weather Jawa Barat</code>."
    )
    return msgs


def handle_command(text: str, cfg: Config, state: dict | None = None) -> list[str] | None:
    """Reply messages for a command, or None if it is not ours (stay quiet in groups)."""
    parsed = parse_command(text)
    if not parsed:
        return None
    name, args = parsed
    if name in WEATHER_COMMANDS:
        if not args:
            return [WEATHER_USAGE]
        query = " ".join(args)
        found = find_province(query)
        if isinstance(found, list):
            return [f"Which one? {', '.join(html.escape(f[1]) for f in found[:8])}\n"
                    f"e.g. <code>/weather {html.escape(found[0][1])}</code>"]
        if not found:
            return [f"I don't know a province called {html.escape(query)!r}.\n{WEATHER_USAGE}"]
        return [weather_message(found, cfg, state or {})]
    if name in WARNING_COMMANDS:
        lines = ["⚠️ <b>Official warnings</b>", filters_line(cfg)]
        lines += active_lines(active_warnings(state or {}), limit=None)
        lines.append("\nWeather for one province: send its name, e.g. <code>Chiang Mai</code>.")
        return split_message(lines)
    if name in PROVINCE_COMMANDS:
        only = []
        for a in args:
            code = COUNTRY_ARGS.get(a.lower())
            if not code:
                return [f"Unknown country {html.escape(a)!r}. Use <code>/country_list_province</code>, "
                        "<code>/country_list_province TH</code> or <code>/country_list_province ID</code>."]
            only.append(code)
        return province_list_messages(cfg, only or None)
    if name in HELP_COMMANDS:
        return ["🛟 <b>Disaster alert bot</b>\n"
                "Official TMD 🇹🇭 and BMKG 🇮🇩 warnings and earthquake reports, posted here automatically.\n\n"
                "🌦 <b>Weather</b>\n"
                "• /weather <code>Chiang Mai</code> — now, next 3 days and warnings\n"
                "• Or just send a province name: <code>Phuket</code>, <code>Jawa Barat</code>\n\n"
                "⚠️ <b>Warnings</b>\n"
                "• /warnings — everything active right now\n\n"
                "📍 <b>Provinces</b>\n"
                "• /country_list_province — all provinces\n"
                "• /country_list_province <code>TH</code> — Thailand only\n"
                "• /country_list_province <code>ID</code> — Indonesia only\n\n"
                "🔕 <b>This chat</b> (group admins)\n"
                "• /stop — no alerts or replies here\n"
                "• /resume — turn them back on"]
    return None


WEATHER_USAGE = ("🌦 Send a province name to get its weather, e.g. <code>Chiang Mai</code>, <code>Phuket</code>, "
                 "<code>Jawa Barat</code>, <code>Bali</code>, or <code>/weather Bangkok</code>.\n"
                 "/country_list_province lists them all.")

# WMO weather codes (Open-Meteo) -> icon + words
WMO = {
    0: ("☀️", "Clear"), 1: ("🌤", "Mostly clear"), 2: ("⛅", "Partly cloudy"), 3: ("☁️", "Cloudy"),
    45: ("🌫", "Fog"), 48: ("🌫", "Fog"), 51: ("🌦", "Light drizzle"), 53: ("🌦", "Drizzle"), 55: ("🌦", "Heavy drizzle"),
    56: ("🌦", "Freezing drizzle"), 57: ("🌦", "Freezing drizzle"), 61: ("🌧", "Light rain"), 63: ("🌧", "Rain"),
    65: ("🌧", "Heavy rain"), 66: ("🌧", "Freezing rain"), 67: ("🌧", "Freezing rain"), 71: ("🌨", "Light snow"),
    73: ("🌨", "Snow"), 75: ("🌨", "Heavy snow"), 77: ("🌨", "Snow grains"), 80: ("🌦", "Light showers"),
    81: ("🌧", "Showers"), 82: ("⛈", "Violent showers"), 85: ("🌨", "Snow showers"), 86: ("🌨", "Snow showers"),
    95: ("⛈", "Thunderstorm"), 96: ("⛈", "Thunderstorm, hail"), 99: ("⛈", "Thunderstorm, heavy hail"),
}


def _province_index() -> dict[str, tuple[str, str, str]]:
    """Normalised name -> (country, feed name, label). Feed name, English name and aliases all count."""
    index: dict[str, tuple[str, str, str]] = {}
    for code, country in PROVINCES.items():
        for _, provinces in country["regions"]:
            for label in provinces:
                feed_name, _, english = label.partition(" (")
                entry = (code, feed_name, label)
                index[_norm_area(feed_name)] = entry
                english = english.rstrip(")")
                if english and code == "ID" and not feed_name.startswith("Kalimantan") and not feed_name.startswith("Sulawesi"):
                    index.setdefault(_norm_area(english), entry)  # "West Java"; "North" alone is too vague
                elif english:
                    index.setdefault(_norm_area(f"{english} {feed_name.split()[0]}"), entry)  # "North Kalimantan"
    by_feed = {e[1]: e for e in index.values()}
    for alias, feed_name in PLACE_ALIASES.items():
        index.setdefault(_norm_area(alias), by_feed[feed_name])
    return index


_PROVINCE_INDEX: dict[str, tuple[str, str, str]] = {}


def find_province(text: str, exact_only: bool = False) -> tuple[str, str, str] | list | None:
    """Province for what someone typed: (country, feed name, label); a list if it is ambiguous; None if unknown."""
    global _PROVINCE_INDEX
    if not _PROVINCE_INDEX:
        _PROVINCE_INDEX = _province_index()
    q = _norm_area(text)
    if not q:
        return None
    if q in _PROVINCE_INDEX:
        return _PROVINCE_INDEX[q]
    if exact_only or len(q) < 3:
        return None
    hits = list({e[1]: e for k, e in _PROVINCE_INDEX.items() if q in k}.values())
    if len(hits) == 1:
        return hits[0]
    return hits or None


def fetch_weather(lat: float, lon: float, cfg: Config) -> dict:
    data = http_get(FEEDS["open_meteo"], cfg, {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
    }, retries=1, timeout=15)
    return json.loads(data)


def province_warnings(state: dict, province: tuple[str, str, str]) -> list[dict]:
    """Active warnings that name this province, or its region (TMD bulletins say e.g. "in the South")."""
    code, feed_name, _ = province
    find_province(feed_name)  # builds the index
    keys = {k for k, e in _PROVINCE_INDEX.items() if e[1] == feed_name and len(k) >= 4}  # "jakarta" for DKI Jakarta
    region_word = ""
    for region, provinces in PROVINCES[code]["regions"]:
        if any(p.partition(" (")[0] == feed_name for p in provinces):
            region_word = region.split()[0].lower()
    out = []
    for a in active_warnings(state):
        text = f"{a.get('title', '')} {a.get('area', '')}"
        if any(k in _norm_area(text) for k in keys):
            out.append(a)
        elif code == "TH" and region_word and re.search(rf"\b{re.escape(region_word)}\b", text.lower()):
            out.append(a)
    return out


def weather_message(province: tuple[str, str, str], cfg: Config, state: dict) -> str:
    code, feed_name, label = province
    e = html.escape
    lines = [f"{PROVINCES[code]['flag']} <b>{e(label)}</b>"]
    lat, lon = PROVINCE_COORDS[feed_name]
    try:
        w = fetch_weather(lat, lon, cfg)
        cur, daily = w["current"], w["daily"]
        icon, words = WMO.get(cur.get("weather_code"), ("🌡", "Weather"))
        lines[0] += f" · now {cur['time'][-5:]}"
        lines.append(f"{icon} {words}, <b>{cur['temperature_2m']:.0f}°C</b> (feels {cur['apparent_temperature']:.0f}°C)")
        extra = f"💧 Humidity {cur['relative_humidity_2m']}% · 💨 Wind {cur['wind_speed_10m']:.0f} km/h"
        if cur.get("precipitation"):
            extra += f" · rain {cur['precipitation']:g} mm"
        lines.append(extra)
        lines.append("")
        for i, day in enumerate(daily["time"]):
            name = "Today" if i == 0 else datetime.strptime(day, "%Y-%m-%d").strftime("%a %d")
            d_icon, d_words = WMO.get(daily["weather_code"][i], ("🌡", ""))
            rain = daily["precipitation_sum"][i] or 0
            chance = daily["precipitation_probability_max"][i]
            rain_txt = f"rain {rain:g} mm" + (f" ({chance}%)" if chance is not None else "")
            lines.append(f"<b>{name}</b> {d_icon} {e(d_words)} · {daily['temperature_2m_min'][i]:.0f}–"
                         f"{daily['temperature_2m_max'][i]:.0f}°C · {rain_txt}")
    except Exception as exc:  # noqa: BLE001
        log(f"Weather for {feed_name} failed: {exc}")
        lines.append("Weather data is not available right now, try again in a minute.")

    warnings = province_warnings(state, province)
    if warnings:
        lines.append(f"\n⚠️ <b>Official warnings for this area ({len(warnings)}):</b>")
        lines += [f"{a['icon']} {e(_trim(a['title'], 110))}" for a in warnings[:8]]
    else:
        lines.append("\n✅ No official warnings for this area right now.")
    lines.append(f"\n<i>Forecast: Open-Meteo model data. Warnings: {'TMD' if code == 'TH' else 'BMKG'}.</i>")
    return "\n".join(lines)


ACTIVE_BULLETIN_HOURS = 24      # bulletins have no expiry: show them as active this long
BACKLOG_UNDATED_BULLETINS = 3   # on first read, only the newest undated bulletins count as active
WELCOME_COOLDOWN_SECONDS = 60   # at most one welcome per group per minute (people joined together are greeted together)


def remember_active(state: dict, alerts: list[dict], now_ts: float, backlog: bool = False) -> None:
    """Keep warnings that passed the filters, so new groups/members can be shown what is active.

    backlog: first read of a source. Undated bulletins (TMD lists its whole archive,
    newest first, without dates) are then limited to the latest few."""
    active: dict = state.setdefault("active", {})
    undated_left = BACKLOG_UNDATED_BULLETINS
    for a in alerts:
        if a["kind"] == "quake":
            continue
        expires = a.get("expires", "")
        if not parse_time(expires):
            # Bulletins have no expiry: count them as active for a while after they were issued.
            # Feeds also list old archived bulletins; those are already past this window.
            issued = parse_time(a.get("effective", ""))
            if not issued and backlog:
                if undated_left <= 0:
                    continue
                undated_left -= 1
            issued_ts = issued.timestamp() if issued else now_ts
            if now_ts - issued_ts > ACTIVE_BULLETIN_HOURS * 3600:
                continue
            expires = datetime.fromtimestamp(issued_ts + ACTIVE_BULLETIN_HOURS * 3600, timezone.utc).isoformat()
        title = a["title"]
        if a.get("area") and _norm_area(a["area"]) not in _norm_area(title):
            title = f"{title} · {_trim(a['area'], 60)}"
        active[a["id"]] = {
            "title": title,
            "icon": "📢" if a["kind"] == "bulletin" else SEVERITY_EMOJI.get(a["severity"], "⚪"),
            "expires": expires,
            "area": _trim(a.get("area", ""), 600),  # full area list, for /weather <province>
        }


def active_warnings(state: dict) -> list[dict]:
    """Active warnings, most severe icons first; prunes expired ones from the state."""
    now = datetime.now(timezone.utc)
    active: dict = state.get("active", {})
    for key, a in list(active.items()):
        exp = parse_time(a.get("expires", ""))
        if not exp or exp < now:
            del active[key]
    order = {icon: i for i, icon in enumerate(["🔴", "🟠", "📢", "🟡", "🔵", "⚪"])}
    # The key is "<source>:<id>", so the country comes from the source (works for old state files too)
    warnings = [{**a, "flag": SOURCE_FLAG.get(key.split(":", 1)[0], "")} for key, a in active.items()]
    # Most severe first; within the same level, one country's warnings stay together
    return sorted(warnings, key=lambda a: (order.get(a["icon"], 9), a["flag"]))


def filters_line(cfg: Config) -> str:
    sev = [k for k, v in SEVERITY_RANK.items() if v == cfg.min_severity][0].title()
    line = f"Watching: {', '.join(sorted(cfg.countries))} · min severity {sev} · quakes ≥ M{cfg.min_quake_mag:g}"
    if cfg.area_keywords:
        line += f"\nAreas: {html.escape(', '.join(cfg.area_keywords))}"
    return line


def active_lines(warnings: list[dict], limit: int | None = 15) -> list[str]:
    """Lines listing active warnings; `limit=None` lists all of them."""
    if not warnings:
        return ["\nNo warnings matching the filters right now."]
    shown = warnings if limit is None else warnings[:limit]
    lines = [f"\n<b>{len(warnings)} warning(s) active right now:</b>"]
    lines += [f"{a.get('flag') or ''}{a['icon']} {html.escape(_trim(a['title'], 90))}" for a in shown]
    if len(warnings) > len(shown):
        lines.append(f"…and {len(warnings) - len(shown)} more. Send /warnings for the full list.")
    return lines


def split_message(lines: list[str], max_len: int = 3900) -> list[str]:
    """Join lines into as few messages as fit under Telegram's 4096-character limit."""
    chunks: list[str] = []
    cur = ""
    for line in lines:
        if cur and len(cur) + 1 + len(line) > max_len:
            chunks.append(cur)
            cur = line.lstrip("\n")
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def group_welcome(cfg: Config, state: dict) -> str:
    lines = ["✅ <b>Disaster alerts are on for this chat</b>",
             "I'll post official TMD (Thailand) and BMKG (Indonesia) warnings and big earthquakes here "
             "automatically. Nobody needs to type anything.",
             filters_line(cfg)]
    lines += active_lines(active_warnings(state))
    lines.append("\nOptional: /country_list_province shows the provinces you can filter on.")
    return "\n".join(lines)


def member_welcome(names: list[str], state: dict) -> str:
    who = ", ".join(html.escape(n) for n in names[:5]) + (" and others" if len(names) > 5 else "")
    lines = [f"👋 Welcome {who}!",
             "This group gets official disaster warnings (TMD 🇹🇭 / BMKG 🇮🇩) automatically. "
             "No need to type anything, they will show up here."]
    lines += active_lines(active_warnings(state), limit=10)
    return "\n".join(lines)


def _subscribe(state: dict, chat: dict) -> bool:
    subs: dict = state.setdefault("subscribers", {})
    cid = str(chat["id"])
    new = cid not in subs
    subs[cid] = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
    if new:
        log(f"Subscribed chat {cid} ({subs[cid]})")
    return new


def _unsubscribe(state: dict, chat_id: str) -> None:
    if state.setdefault("subscribers", {}).pop(chat_id, None) is not None:
        log(f"Unsubscribed chat {chat_id} (bot removed or blocked)")


def _mute(state: dict, chat_id: str, title: str = "") -> None:
    subs: dict = state.setdefault("subscribers", {})
    state.setdefault("muted", {})[chat_id] = subs.pop(chat_id, None) or title or chat_id
    log(f"Muted chat {chat_id} ({state['muted'][chat_id]}): no alerts or replies there until /resume")


def _unmute(state: dict, chat_id: str) -> str | None:
    """Returns the chat's title if it was muted."""
    return state.setdefault("muted", {}).pop(chat_id, None)


def _is_admin(cfg: Config, chat: dict, user: dict) -> bool:
    """Private chats: always. Groups: only the group's admins may /stop or /resume the bot."""
    if chat.get("type") == "private" or str(chat.get("id")) in cfg.chat_ids:
        return True
    if chat.get("type") == "channel":
        return True  # only admins can post in a channel
    if not user.get("id"):
        return True  # anonymous group admin
    res = telegram_api(cfg, "getChatMember", {"chat_id": chat["id"], "user_id": user["id"]})
    return (res.get("result") or {}).get("status") in ("creator", "administrator")


def chats_message(state: dict) -> str:
    lines = ["💬 <b>Chats</b>"]
    subs, muted = state.get("subscribers", {}), state.get("muted", {})
    lines.append(f"\n<b>Getting alerts ({len(subs)}):</b>")
    lines += [f"• {html.escape(t or '?')} <code>{c}</code>" for c, t in subs.items()] or ["(none)"]
    lines.append(f"\n<b>Stopped ({len(muted)}):</b>")
    lines += [f"• {html.escape(t or '?')} <code>{c}</code>" for c, t in muted.items()] or ["(none)"]
    lines.append("\nStop one: <code>/stop -100123…</code> · turn back on: <code>/resume -100123…</code>")
    return "\n".join(lines)


def owner_chat_command(cfg: Config, state: dict, name: str, args: list[str]) -> str | None:
    """/chats, /stop <id>, /resume <id> from the owner (TELEGRAM_CHAT_ID): manage any chat by id."""
    if name in CHATS_COMMANDS:
        return chats_message(state)
    if not args:
        return None
    target = args[0]
    known = {**state.get("subscribers", {}), **state.get("muted", {})}
    if target not in known and target not in cfg.chat_ids:
        return f"Unknown chat <code>{html.escape(target)}</code>.\n\n{chats_message(state)}"
    if name in STOP_COMMANDS:
        _mute(state, target, known.get(target, ""))
        return f"🔕 Stopped: {html.escape(state['muted'][target])}. No alerts or replies there until /resume."
    title = _unmute(state, target)
    if title is None:
        return f"{html.escape(known.get(target) or target)} is not stopped."
    if target not in cfg.chat_ids:
        state.setdefault("subscribers", {})[target] = title
    return f"🔔 Alerts are back on for {html.escape(title)}."


def apply_chat_changes(state: dict, notifier: Notifier) -> None:
    """Carry chat changes noticed while sending (supergroup upgrade, bot removed) into the state."""
    subs: dict = state.setdefault("subscribers", {})
    for old, new in notifier.migrated.items():
        if old in subs:
            subs[new] = subs.pop(old)
    for cid in notifier.gone:
        _unsubscribe(state, cid)
    notifier.migrated.clear()
    notifier.gone.clear()
    notifier.subscribers = set(subs)
    notifier.muted = set(state.get("muted", {}))


def poll_updates(cfg: Config, notifier: Notifier, state: dict, wait: int) -> None:
    """Handle Telegram updates: bot added/removed, new members, commands.

    Updates the state in place (subscribers, getUpdates offset)."""
    bot_id = cfg.token.split(":", 1)[0]
    if state.get("tg_bot_id") != bot_id:
        # New bot token: the old offset belongs to the old bot (update ids are per bot)
        state["tg_bot_id"] = bot_id
        state["tg_offset"] = 0
    offset = int(state.get("tg_offset", 0))
    res = telegram_api(cfg, "getUpdates", {
        "offset": offset,
        "timeout": wait,
        "allowed_updates": json.dumps(["message", "channel_post", "my_chat_member"]),
    }, timeout=wait + 15)
    if not res.get("ok"):
        if res.get("error_code") == 409:
            raise RuntimeError("getUpdates conflict: another copy of this bot is running with the same "
                               "token (other terminal, Docker or GitHub Actions). Stop the extra one.")
        raise RuntimeError(f"getUpdates: {res.get('description')}")
    subs: dict = state.setdefault("subscribers", {})

    for upd in res.get("result", []):
        # Advance the offset before handling, so an update is answered at most once
        # even if handling it fails half way (otherwise it is re-read and re-answered).
        # Not max(): after a token change the stored offset can be above this bot's ids,
        # and then it would never move and the same updates would come back every poll.
        state["tg_offset"] = int(upd["update_id"]) + 1
        notifier.subscribers = set(subs)
        notifier.muted = set(state.get("muted", {}))
        try:
            _handle_update(cfg, notifier, state, upd)
        except Exception as exc:  # noqa: BLE001
            log(f"Update {upd.get('update_id')} failed: {exc}")

    apply_chat_changes(state, notifier)


def _handle_update(cfg: Config, notifier: Notifier, state: dict, upd: dict) -> None:
    subs: dict = state.setdefault("subscribers", {})
    last_welcome: dict = state.setdefault("last_welcome", {})
    # Bot added to / removed from a chat (or a user blocked/unblocked it)
    member = upd.get("my_chat_member")
    if member:
        chat = member.get("chat") or {}
        cid = str(chat.get("id", ""))
        status = (member.get("new_chat_member") or {}).get("status", "")
        if status in ("left", "kicked"):
            _unsubscribe(state, cid)
        elif status in ("member", "administrator") and cfg.auto_subscribe and chat.get("type") != "private":
            if cid in state.get("muted", {}):
                if (member.get("old_chat_member") or {}).get("status") not in ("left", "kicked"):
                    return  # only promoted/demoted while stopped: stay stopped
                _unmute(state, cid)  # removed and added again: start over
                notifier.muted.discard(cid)
            if _subscribe(state, chat):
                notifier.send(group_welcome(cfg, state), chat_ids=[cid])
        return

    msg = upd.get("message") or upd.get("channel_post") or {}
    chat = msg.get("chat") or {}
    cid = str(chat.get("id", ""))
    if not cid:
        return
    text = msg.get("text") or ""
    cmd = (parse_command(text) or ("", []))
    sender = msg.get("from") or {}

    # Owner chat (TELEGRAM_CHAT_ID) can list chats and stop/resume any of them by id
    if cid in cfg.chat_ids and (cmd[0] in CHATS_COMMANDS or (cmd[0] in STOP_COMMANDS | RESUME_COMMANDS and cmd[1])):
        reply = owner_chat_command(cfg, state, cmd[0], cmd[1])
        if reply:
            notifier.muted = set(state.get("muted", {}))
            notifier.send(reply, chat_ids=[cid])
            return

    if cid in state.get("muted", {}):
        # Stopped with /stop: stay silent here, only /resume (or /start) turns it back on
        if cmd[0] in RESUME_COMMANDS | {"start"} and _is_admin(cfg, chat, sender):
            title = _unmute(state, cid)
            notifier.muted.discard(cid)
            if cid not in cfg.chat_ids:
                state.setdefault("subscribers", {})[cid] = chat.get("title") or title or ""
            notifier.subscribers = set(state.get("subscribers", {}))
            log(f"Resumed chat {cid} ({title})")
            notifier.send("🔔 Alerts are back on for this chat.\n\n" + group_welcome(cfg, state), chat_ids=[cid])
        return

    if cmd[0] in STOP_COMMANDS and not cmd[1]:
        if not _is_admin(cfg, chat, sender):
            notifier.send("Only group admins can stop the bot here.", chat_ids=[cid])
            return
        notifier.send("🔕 OK, I'll stop here: no more alerts and I won't answer messages in this chat.\n"
                      "An admin can turn it back on with /resume.", chat_ids=[cid])
        _mute(state, cid, chat.get("title") or chat.get("username") or chat.get("first_name") or "")
        notifier.muted.add(cid)
        notifier.subscribers = set(state.get("subscribers", {}))
        return

    if (cfg.auto_subscribe and chat.get("type") in ("group", "supergroup", "channel")
            and cid not in subs and not msg.get("left_chat_member")):
        # A group the bot is in but has not recorded (e.g. the "bot added" event was
        # missed while the bot was offline): subscribe it on its first message.
        _subscribe(state, chat)
        notifier.subscribers = set(subs)
        notifier.send(group_welcome(cfg, state), chat_ids=[cid])
        last_welcome[cid] = time.time()
        if (parse_command(msg.get("text") or "") or ("",))[0] in HELP_COMMANDS:
            return  # the welcome already answers /start and /help
    allowed = cid in cfg.chat_ids or cid in subs

    if msg.get("migrate_to_chat_id"):  # group upgraded to supergroup
        new_id = str(msg["migrate_to_chat_id"])
        if cid in subs:
            subs[new_id] = subs.pop(cid)
        return

    # Someone joined a subscribed group: greet them with what is active now
    joined = [u for u in msg.get("new_chat_members") or [] if not u.get("is_bot")]
    if joined:
        if allowed and cfg.welcome_new_members and time.time() - last_welcome.get(cid, 0) >= WELCOME_COOLDOWN_SECONDS:
            names = [u.get("first_name") or u.get("username") or "there" for u in joined]
            notifier.send(member_welcome(names, state), chat_ids=[cid])
            last_welcome[cid] = time.time()
        return

    if not text.startswith("/"):
        # A message that is just a province name ("Chiang Mai", "Jawa Barat") gets its weather.
        # Exact names only, so normal chat in the group is left alone.
        if allowed and 0 < len(text) <= 40 and "\n" not in text:
            found = find_province(text.strip(" ?!.,"), exact_only=True)
            if isinstance(found, tuple):
                log(f"Weather for {found[1]} from chat {cid}")
                notifier.send(weather_message(found, cfg, state), chat_ids=[cid])
        return
    # /start in a private chat subscribes that person
    if chat.get("type") == "private" and (parse_command(text) or ("",))[0] == "start" and cfg.auto_subscribe:
        if _subscribe(state, chat):
            notifier.send(group_welcome(cfg, state), chat_ids=[cid])
            return
        allowed = True
    if not allowed:
        log(f"Ignoring command from chat {cid} (not subscribed)")
        return
    replies = handle_command(text, cfg, state)
    if not replies:
        return
    log(f"Command {text.split()[0]} from chat {cid}")
    for reply in replies:
        notifier.send(reply, chat_ids=[cid])


def handle_telegram_updates(cfg: Config, notifier: Notifier, wait: int = 0) -> None:
    """Poll Telegram once; subscribers and the getUpdates offset are kept in the state file."""
    state = load_state(cfg.state_file)
    if state is None:
        return  # first feed check has not run yet
    before = json.dumps([state.get("tg_offset"), state.get("tg_bot_id"), state.get("subscribers"), state.get("muted"), state.get("last_welcome")], sort_keys=True)
    poll_updates(cfg, notifier, state, wait)
    after = json.dumps([state.get("tg_offset"), state.get("tg_bot_id"), state.get("subscribers"), state.get("muted"), state.get("last_welcome")], sort_keys=True)
    if after != before:
        save_state(cfg.state_file, state)


def register_commands(cfg: Config) -> None:
    """Show the commands in Telegram's "/" menu."""
    res = telegram_api(cfg, "setMyCommands", {"commands": json.dumps(BOT_COMMAND_MENU)})
    if not res.get("ok"):
        log(f"setMyCommands failed: {res.get('description')}")


# --------------------------------------------------------------------------
# Main cycle
# --------------------------------------------------------------------------
def build_collectors(cfg: Config) -> list[tuple[str, callable]]:
    cols: list[tuple[str, callable]] = []
    if "TH" in cfg.countries:
        cols.append(("tmd_cap", lambda seen: collect_cap_feed("tmd_cap", FEEDS["tmd_cap"][cfg.lang], cfg, seen)))
        if cfg.include_bulletins:
            cols.append(("tmd_storm", lambda seen: collect_tmd_bulletins(cfg, seen)))
        if cfg.enable_usgs:
            cols.append(("usgs", lambda seen: collect_usgs(cfg, seen)))
    if "ID" in cfg.countries:
        cols.append(("bmkg_cap", lambda seen: collect_cap_feed("bmkg_cap", FEEDS["bmkg_cap"][cfg.lang], cfg, seen)))
        cols.append(("bmkg_inatews", lambda seen: collect_cap_feed("bmkg_inatews", FEEDS["bmkg_inatews"], cfg, seen)))
        cols.append(("bmkg_quake", lambda seen: collect_bmkg_quakes(cfg, seen)))
    return cols


def run_cycle(cfg: Config, state: dict, notifier: Notifier, baseline: bool) -> int:
    seen: dict = state.setdefault("seen", {})
    failures: dict = state.setdefault("failures", {})
    fail_notified: dict = state.setdefault("fail_notified", {})
    now_ts = time.time()

    to_send: list[dict] = []
    for name, collector in build_collectors(cfg):
        try:
            alerts, ids = collector(seen)
        except Exception as exc:  # noqa: BLE001
            failures[name] = failures.get(name, 0) + 1
            log(f"{name}: error ({failures[name]} in a row): {exc}")
            if failures[name] >= cfg.source_fail_alert and not fail_notified.get(name) and not baseline:
                notifier.send(f"⚠️ <b>Alert source unreachable</b>\n{html.escape(SOURCE_LABEL.get(name, name))} has failed "
                              f"{failures[name]} checks in a row. You may miss warnings from it until it recovers.",
                              chat_ids=cfg.chat_ids)
                fail_notified[name] = True
            continue

        if fail_notified.get(name):
            notifier.send(f"✅ Source back online: {html.escape(SOURCE_LABEL.get(name, name))}", chat_ids=cfg.chat_ids)
        failures[name] = 0
        fail_notified[name] = False

        for key in ids:
            seen[key] = now_ts
        picked = [a for a in alerts if should_notify(a, cfg)]
        log(f"{name}: {len(ids)} new item(s), {len(picked)} match filters")
        remember_active(state, picked, now_ts, backlog=baseline or name not in state.get("primed", []))

        primed: list = state.setdefault("primed", [])
        if name not in primed:
            # First successful read of this source (e.g. it was down on the very
            # first run): record its backlog quietly instead of flooding the chat.
            primed.append(name)
            if not baseline:
                log(f"{name}: first successful read — backlog recorded without sending")
                continue
        to_send.extend(picked)

    if baseline:
        lines = ["✅ <b>Disaster alert bot is running</b>", filters_line(cfg)]
        lines += active_lines(active_warnings(state))
        lines.append("\nNew warnings will be sent here as they are issued.")
        notifier.send("\n".join(lines))
        return 0

    for alert in to_send:
        notifier.send(format_alert(alert, cfg))
    return len(to_send)


def backfill_active(cfg: Config, state: dict) -> None:
    """One-time: rebuild the "active right now" list for a state file from before it existed."""
    log("Building the list of active warnings (one time)")
    now_ts = time.time()
    state["active"] = {}
    state["active_ready"] = True
    for name, collector in build_collectors(cfg):
        try:
            alerts, _ = collector({})
        except Exception as exc:  # noqa: BLE001
            log(f"{name}: backfill skipped: {exc}")
            continue
        remember_active(state, [a for a in alerts if should_notify(a, cfg)], now_ts, backlog=True)


def maybe_heartbeat(cfg: Config, state: dict, notifier: Notifier) -> None:
    if cfg.heartbeat_hours <= 0 or not cfg.chat_ids:
        return  # heartbeat goes to the owner chat only, never to groups
    last = state.get("last_heartbeat", 0)
    if time.time() - last >= cfg.heartbeat_hours * 3600:
        notifier.send("💓 Disaster alert bot is still running.", chat_ids=cfg.chat_ids)
        state["last_heartbeat"] = time.time()


def check_once(cfg: Config, notifier: Notifier) -> int:
    state = load_state(cfg.state_file)
    baseline = state is None
    state = state or {}
    if baseline:
        log("First run: recording current items without sending each one (summary only)")
        state["last_heartbeat"] = time.time()
        # Fresh state: keep the groups that subscribed before, so they get the summary too
        state["subscribers"] = load_subscribers(cfg.state_file)
        if state["subscribers"]:
            log(f"Kept {len(state['subscribers'])} subscribed chat(s) from {subscribers_path(cfg.state_file)}")
    if baseline and load_muted(cfg.state_file):
        state["muted"] = load_muted(cfg.state_file)
    notifier.subscribers = set(state.get("subscribers", {}))
    notifier.muted = set(state.get("muted", {}))
    if baseline:
        state["active_ready"] = True  # the first run fills the active list itself
    elif not state.get("active_ready"):
        backfill_active(cfg, state)
    sent = run_cycle(cfg, state, notifier, baseline=baseline)
    if not baseline:
        maybe_heartbeat(cfg, state, notifier)
    apply_chat_changes(state, notifier)
    save_state(cfg.state_file, state)
    return sent


def cmd_get_chat_id(cfg: Config) -> None:
    res = telegram_api(cfg, "getUpdates")
    if not res.get("ok"):
        sys.exit(f"Telegram error: {res.get('description')}")
    chats = {}
    for upd in res.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
    if not chats:
        print("No chats found. Send any message to your bot in Telegram (or add it to a group and post there), then run this again.")
        return
    print("Chats that have messaged your bot:")
    for cid, name in chats.items():
        print(f"  TELEGRAM_CHAT_ID={cid}    ({name})")


def single_instance_lock(state_file: str):
    """Stop a second loop on the same state file: two copies answer every command twice
    and overwrite each other's Telegram offset, so old commands get answered again."""
    try:
        import fcntl
    except ImportError:  # Windows: no lock
        return None
    fh = open(f"{state_file}.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"Another copy of the bot is already running (lock: {state_file}.lock). Stop it first.")
    return fh


def main() -> None:
    parser = argparse.ArgumentParser(description="Thailand + Indonesia disaster alerts to Telegram")
    parser.add_argument("--once", action="store_true", help="check once and exit (for cron / GitHub Actions)")
    parser.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    parser.add_argument("--test", action="store_true", help="send a test message and exit")
    parser.add_argument("--get-chat-id", action="store_true", help="list chat ids that have messaged the bot")
    parser.add_argument("--env-file", default=".env", help="path to .env file (default .env)")
    parser.add_argument("--command", metavar="TEXT", help='print the reply to a bot command, e.g. "/country_list_province TH"')
    args = parser.parse_args()

    load_dotenv(args.env_file)
    cfg = Config()
    notifier = Notifier(cfg, dry_run=args.dry_run)

    if args.command:
        for reply in handle_command(args.command, cfg, load_state(cfg.state_file)) or ["(not a command this bot answers)"]:
            print(reply, end="\n\n")
        return
    if args.get_chat_id:
        cmd_get_chat_id(cfg)
        return
    if args.test:
        notifier.subscribers = set((load_state(cfg.state_file) or {}).get("subscribers")
                                   or load_subscribers(cfg.state_file))
        notifier.muted = set((load_state(cfg.state_file) or {}).get("muted") or load_muted(cfg.state_file))
        ok = notifier.send("🧪 Test from your disaster alert bot. If you can read this, Telegram delivery works.")
        sys.exit(0 if ok else 1)

    if args.once:
        check_once(cfg, notifier)
        if not args.dry_run:
            try:
                handle_telegram_updates(cfg, notifier)
            except Exception as exc:  # noqa: BLE001
                log(f"Command check failed: {exc}")
        return

    _lock = single_instance_lock(cfg.state_file)  # noqa: F841 — held for the life of the process
    log(f"Starting loop: every {cfg.poll_seconds}s, countries={sorted(cfg.countries)}")
    if not args.dry_run:
        try:
            register_commands(cfg)
        except Exception as exc:  # noqa: BLE001
            log(f"setMyCommands failed: {exc}")
    while True:
        try:
            check_once(cfg, notifier)
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            log(f"Cycle failed: {exc}")
        if args.dry_run:
            time.sleep(cfg.poll_seconds)
            continue
        # Between feed checks, long-poll Telegram so commands get answered within seconds
        deadline = time.time() + cfg.poll_seconds
        while (left := deadline - time.time()) > 0:
            try:
                handle_telegram_updates(cfg, notifier, wait=max(1, min(25, int(left))))
            except Exception as exc:  # noqa: BLE001
                log(f"Command check failed: {exc}")
                time.sleep(min(10, max(0.0, left)))


if __name__ == "__main__":
    main()
