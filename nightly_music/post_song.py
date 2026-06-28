#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
هر شب یک آهنگ هوشمند انتخاب می‌کند، فایل کامل آن را دانلود می‌کند،
داستان آهنگ را (با جستجوی گوگلِ گراندشده) می‌نویسد و همه را در کانال تلگرام می‌فرستد.

موتور هوش مصنوعی: Google Gemini (تیر رایگان) با جستجوی گوگلِ داخلی برای داستان واقعی.
"""

import html
import json
import os
import re
import subprocess
import sys
import time
import hashlib
from pathlib import Path

import requests
from google import genai
from google.genai import types
from Crypto.Cipher import Blowfish
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3

# ---------------------------- تنظیمات ----------------------------
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
DEEZER_ARL = os.environ.get("DEEZER_ARL", "").strip()

AI_MODEL = os.environ.get("AI_MODEL", "gemini-2.5-flash")
STORY_LANGUAGE = os.environ.get("STORY_LANGUAGE", "Persian (Farsi)")

# تگ کانال که هم در فیلد آلبومِ فایل و هم در آخر پیام می‌آید
CHANNEL_TAG = "@RadioBulletin |  رادیو بولتن"
CHANNEL_ID = "@RadioBulletin"  # کنار نام آهنگ در فایل می‌آید

HISTORY_FILE = Path(__file__).parent / "sent_history.json"
DOWNLOAD_DIR = Path("/tmp/song")
MAX_HISTORY = 200  # چند آهنگ آخر را به هوش مصنوعی بدهیم تا تکرار نشود
MIN_DURATION = 90  # ثانیه؛ فایل کوتاه‌تر = پیش‌نمایش، نه آهنگ کامل

client = genai.Client(api_key=GEMINI_API_KEY)


# ------------------------ ابزار هوش مصنوعی ------------------------
def ai_chat(system, user, max_tokens=900, temperature=0.9,
            json_mode=False, grounded=False, retries=4):
    """
    یک درخواست به Gemini با backoff.
    grounded=True → از جستجوی گوگلِ داخلیِ Gemini استفاده می‌کند (برای داستان واقعی).
    json_mode=True → خروجی را به‌صورت JSON می‌خواهد (با grounded ترکیب نمی‌شود).
    """
    cfg = dict(
        system_instruction=system,
        temperature=temperature,
        max_output_tokens=max_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    if grounded:
        cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    elif json_mode:
        cfg["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**cfg)

    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=AI_MODEL, contents=user, config=config,
            )
            text = (resp.text or "").strip()
            if not text:
                raise RuntimeError("پاسخ خالی از مدل")
            return text
        except Exception as e:
            wait = (2 ** attempt) * 5
            print(f"[AI] خطا (تلاش {attempt + 1}): {e} — {wait}s صبر...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("درخواست هوش مصنوعی بعد از چند تلاش شکست خورد.")


def parse_json(raw):
    """JSON را حتی اگر داخل ```fence``` یا لای متنِ اضافه باشد می‌خواند."""
    raw = raw.strip()
    raw = re.sub(r"^```(json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    if not raw.startswith("{"):
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group(0)
    return json.loads(raw)


# ------------------------ تاریخچه‌ی آهنگ‌ها ------------------------
def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_history(history):
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ------------------------ انتخاب هوشمند آهنگ ------------------------
def choose_song(history, avoid=None):
    recent = [h["key"] for h in history[-MAX_HISTORY:]]
    if avoid:
        recent = recent + list(avoid)
    system = (
        "تو یک کیوریتور موسیقی حرفه‌ای برای یک کانال تلگرامی با مخاطب ایرانی هستی. "
        "آهنگ را با این اولویت انتخاب کن: "
        "اولویت اول — آهنگی که پشتش یک «داستان واقعی و مستندِ جذاب» وجود دارد "
        "(ماجرای ساختش، اتفاقی که الهام‌بخشش بوده، معنی واقعی ترانه و...). "
        "اولویت دوم — اگر آهنگِ تازه‌ای با داستان واقعی پیدا نکردی، آهنگی انتخاب کن که به‌شدت "
        "یک «حال یا موقعیتِ خاصِ زندگی» را تداعی می‌کند و وابستگیِ حسی می‌آورد — یعنی از آن "
        "آهنگ‌هایی که آدم‌ها در لحظه‌های خاص (دلتنگی، دل‌شکستگی، نوستالژی، سرخوشی و...) گوش می‌دهند "
        "و حس می‌کنند «انگار از حال من نوشته شده». "
        "آهنگ‌های واقعی و شناخته‌شده انتخاب کن که در سرویس‌های موسیقی پیدا می‌شوند. "
        "ترکیبی متنوع از فارسی و بین‌المللی. آهنگ‌های لیست «انتخاب نکن» را تکرار نکن."
    )
    user = (
        "یک آهنگ پیشنهاد بده و خروجی را فقط JSON بده:\n"
        "{\n"
        '  "title": "نام آهنگ",\n'
        '  "artist": "نام خواننده",\n'
        '  "search_query": "عبارت جستجوی تمیز: فقط نام خواننده و نام آهنگ، '
        'بدون کلماتی مثل official/audio/video/lyrics/HD"\n'
        "}\n\n"
        f"آهنگ‌هایی که نباید انتخاب کنی:\n"
        f"{json.dumps(recent, ensure_ascii=False)}"
    )
    raw = ai_chat(system, user, max_tokens=300, temperature=1.0, json_mode=True)
    song = parse_json(raw)
    for k in ("title", "artist", "search_query"):
        if not song.get(k):
            raise RuntimeError(f"خروجی هوش مصنوعی ناقص بود (کلید «{k}» نبود).")
    return song


# ------------------------ دانلود آهنگ (ماژولار) ------------------------
def download_song(query):
    """
    آهنگ کامل را به mp3 دانلود می‌کند.
    اولویت: ۱) Deezer (پایدار، آهنگ کامل، بدون بلاکِ بات)  ۲) ساندکلاد  ۳) یوتیوب.
    خروجی: (مسیر فایل mp3، dict متادیتای واقعی).
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in DOWNLOAD_DIR.glob("*"):
        f.unlink()

    # ۱) Deezer — منبع اصلی
    if DEEZER_ARL:
        try:
            mp3, info = deezer_download(query)
            dur = _duration(mp3)
            if dur >= MIN_DURATION:
                print(f"[download] ✅ موفق با: Deezer ({int(dur)} ثانیه)")
                return mp3, info
            print(f"[download] ⚠️ Deezer فایل ناقص داد ({int(dur)}s) — منبع بعدی", file=sys.stderr)
        except Exception as e:
            print(f"[download] ❌ Deezer: {e} — منبع بعدی...", file=sys.stderr)

    out_template = str(DOWNLOAD_DIR / "track.%(ext)s")

    # کوکی برای عبور از محدودیت «ربات نیستی» یوتیوب (روی سرور گیت‌هاب لازم است).
    cookies_args = []
    cookies_data = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if cookies_data:
        cookies_file = DOWNLOAD_DIR / "cookies.txt"
        cookies_file.write_text(cookies_data + "\n", encoding="utf-8")
        cookies_args = ["--cookies", str(cookies_file)]

    common = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "--embed-thumbnail", "--embed-metadata",
        "--write-info-json",
        "-o", out_template,
    ]
    yt_common = common + cookies_args

    # ۲) ساندکلاد و ۳) یوتیوب — پشتیبان
    strategies = [
        ("ساندکلاد", common + [f"scsearch1:{query}"]),
        ("یوتیوب default", yt_common + ["--extractor-args", "youtube:player_client=default,-tv", f"ytsearch1:{query}"]),
        ("یوتیوب ios", yt_common + ["--extractor-args", "youtube:player_client=ios", f"ytsearch1:{query}"]),
    ]

    last_output = ""
    for name, cmd in strategies:
        for old in DOWNLOAD_DIR.glob("*.mp3"):
            old.unlink()
        result = subprocess.run(cmd, capture_output=True, text=True)
        last_output = result.stdout + "\n" + result.stderr
        mp3s = list(DOWNLOAD_DIR.glob("*.mp3"))
        if result.returncode == 0 and mp3s:
            dur = _duration(mp3s[0])
            if dur >= MIN_DURATION:
                print(f"[download] ✅ موفق با: {name} ({int(dur)} ثانیه)")
                return mp3s[0], _read_info()
            # فایل کوتاه = پیش‌نمایش؛ ردش کن و سراغ منبع بعدی برو
            print(f"[download] ⚠️ فقط پیش‌نمایش {int(dur)}s از «{name}» — رد شد", file=sys.stderr)
        else:
            print(f"[download] ❌ شکست با: {name} — تلاش بعدی...", file=sys.stderr)

    print(last_output, file=sys.stderr)
    raise RuntimeError("نسخه‌ی کاملِ آهنگ از هیچ منبعی پیدا نشد (فقط پیش‌نمایش/خطا).")


# ------------------------ دانلود از Deezer ------------------------
DEEZER_SECRET = b"g4el58wc0zvf9na1"
DEEZER_GW = "https://www.deezer.com/ajax/gw-light.php"


def _deezer_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "en-US,en;q=0.9",
    })
    s.cookies.set("arl", DEEZER_ARL, domain=".deezer.com")
    r = s.get(DEEZER_GW, params={
        "method": "deezer.getUserData", "input": "3",
        "api_version": "1.0", "api_token": "",
    }, timeout=30).json()
    res = r.get("results", {}) or {}
    user = res.get("USER", {}) or {}
    if not user.get("USER_ID"):
        raise RuntimeError("ARL نامعتبر/منقضی است (لاگین Deezer نشد)")
    api_token = res.get("checkForm")
    license_token = (user.get("OPTIONS", {}) or {}).get("license_token")
    return s, api_token, license_token


def _deezer_key(sng_id):
    md5 = hashlib.md5(str(sng_id).encode()).hexdigest()
    return bytes(ord(md5[i]) ^ ord(md5[i + 16]) ^ DEEZER_SECRET[i] for i in range(16))


def _deezer_decrypt(resp, out_path, sng_id):
    key = _deezer_key(sng_id)
    iv = bytes(range(8))  # 0,1,2,...,7
    with open(out_path, "wb") as f:
        for i, chunk in enumerate(resp.iter_content(2048)):
            if not chunk:
                break
            if i % 3 == 0 and len(chunk) == 2048:
                chunk = Blowfish.new(key, Blowfish.MODE_CBC, iv).decrypt(chunk)
            f.write(chunk)


def _deezer_embed(mp3_path, title, artist, cover_url):
    from mutagen.id3 import ID3, APIC, TIT2, TPE1
    try:
        tags = ID3(str(mp3_path))
    except Exception:
        tags = ID3()
    if cover_url:
        try:
            img = requests.get(cover_url, timeout=30).content
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img))
        except Exception:
            pass
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.save(str(mp3_path))


def deezer_download(query):
    """آهنگ کامل را از Deezer می‌گیرد، رمزگشایی و کاور+تگ را جاسازی می‌کند."""
    if not DEEZER_ARL:
        raise RuntimeError("DEEZER_ARL تنظیم نشده")

    # جستجوی عمومی برای پیدا کردن track id و متادیتای تمیز
    sr = requests.get("https://api.deezer.com/search",
                      params={"q": query, "limit": 1}, timeout=30).json()
    data = sr.get("data") or []
    if not data:
        raise RuntimeError("آهنگ در Deezer پیدا نشد")
    track = data[0]
    sng_id = track["id"]
    title = track.get("title") or ""
    artist = (track.get("artist") or {}).get("name", "")
    album = track.get("album") or {}
    cover_url = album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium")

    s, api_token, license_token = _deezer_session()

    # دریافت TRACK_TOKEN
    pr = s.post(DEEZER_GW, params={
        "method": "deezer.pageTrack", "input": "3",
        "api_version": "1.0", "api_token": api_token,
    }, json={"sng_id": sng_id}, timeout=30).json()
    tdata = (pr.get("results") or {}).get("DATA") or {}
    track_token = tdata.get("TRACK_TOKEN")
    if not track_token:
        raise RuntimeError("TRACK_TOKEN دریافت نشد")

    # دریافت لینک مدیا (MP3 128)
    mr = s.post("https://media.deezer.com/v1/get_url", json={
        "license_token": license_token,
        "media": [{"type": "FULL", "formats": [
            {"cipher": "BF_CBC_STRIPE", "format": "MP3_128"}]}],
        "track_tokens": [track_token],
    }, timeout=30).json()
    try:
        url = mr["data"][0]["media"][0]["sources"][0]["url"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("لینک دانلود Deezer در دسترس نیست (شاید در کشورِ اکانت موجود نیست)")

    out_path = DOWNLOAD_DIR / "track.mp3"
    with s.get(url, stream=True, timeout=180) as resp:
        resp.raise_for_status()
        _deezer_decrypt(resp, out_path, sng_id)

    try:
        _deezer_embed(out_path, title, artist, cover_url)
    except Exception as e:
        print(f"[deezer] هشدار: جاسازی کاور/تگ نشد: {e}", file=sys.stderr)

    return out_path, {"title": title, "artist": artist}


def _duration(mp3_path):
    """مدت زمان فایل mp3 را به ثانیه برمی‌گرداند (برای تشخیص پیش‌نمایش)."""
    try:
        return float(MP3(str(mp3_path)).info.length)
    except Exception:
        return 0.0


def _read_info():
    """متادیتای واقعیِ فایلِ دانلودشده را از فایل info.json می‌خواند."""
    info = {}
    for jf in DOWNLOAD_DIR.glob("*.info.json"):
        try:
            info = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            pass
        break
    # عنوان و خواننده‌ی واقعیِ منبع
    real_title = info.get("track") or info.get("title") or ""
    real_artist = (
        info.get("artist")
        or info.get("uploader")
        or info.get("channel")
        or ""
    )
    return {"title": real_title.strip(), "artist": real_artist.strip()}


# --------------- شناسایی آهنگ واقعی (بر اساس فایل دانلودشده) ---------------
def describe_track(real_title, real_artist_hint, seed):
    """
    فقط اسم تمیزِ آهنگ و خواننده را از روی عنوانِ واقعیِ فایلِ دانلودشده استخراج می‌کند.
    چون ورودی، عنوانِ واقعیِ همان فایل است، اسم نمایش‌داده‌شده دقیقاً با خودِ آهنگ می‌خواند.
    """
    system = (
        "تو یک متخصص موسیقی هستی. عنوان خامِ یک فایل صوتیِ دانلودشده به تو داده می‌شود "
        "(از یوتیوب یا ساندکلاد، ممکن است شلوغ باشد). فقط اسم تمیزِ آهنگ و خواننده‌ی "
        "واقعی را از روی همین عنوان استخراج کن (نه چیز دیگر و نه از خودت). "
        "اگر از روی عنوان نمی‌توانی تشخیص دهی، همان را تمیز کن."
    )
    user = (
        f"عنوان خام فایل دانلودشده: «{real_title}»\n"
        f"نام آپلودکننده/خواننده‌ی احتمالی: «{real_artist_hint}»\n"
        f"(برای کمک، جستجوی اولیه این بود: «{seed}»)\n\n"
        "خروجی را فقط JSON بده:\n"
        "{\n"
        '  "title": "اسم تمیز آهنگ به زبان اصلی",\n'
        '  "artist": "اسم تمیز خواننده به زبان اصلی",\n'
        '  "title_en": "اسم آهنگ به انگلیسی/لاتین",\n'
        '  "artist_en": "اسم خواننده به انگلیسی/لاتین"\n'
        "}"
    )
    raw = ai_chat(system, user, max_tokens=300, temperature=0.4, json_mode=True)
    data = parse_json(raw)
    data["title"] = (data.get("title") or real_title or seed).strip()
    data["artist"] = (data.get("artist") or real_artist_hint).strip()
    data["title_en"] = (data.get("title_en") or data["title"]).strip()
    data["artist_en"] = (data.get("artist_en") or data["artist"]).strip()
    return data


# --------------- داستان، گراندد با جستجوی گوگلِ داخلیِ Gemini ---------------
def _emotional_fallback(title, artist):
    """متن احساسیِ خودمانی، بدون نیاز به جستجو (پشتیبان)."""
    system = (
        f"تو به زبان {STORY_LANGUAGE} و خیلی خودمانی می‌نویسی. برای یک آهنگ، یک متن کوتاه "
        "(۲ تا ۳ خط) بنویس که حسِ آهنگ را به یک «موقعیت یا حالِ ملموسِ زندگی» وصل کند، طوری "
        "که مخاطب بگوید «دقیقاً همینه، انگار از حال من نوشته». مثل وقتی یک دوست صمیمی آهنگ را "
        "معرفی می‌کند. نمونه‌ی لحن: «این آهنگ برای وقتاییه که هیچ‌کس درکت نمی‌کنه و از همه‌چی "
        "زده شدی». فارسیِ محاوره‌ای و طبیعی، نه شاعرانه‌ی مصنوعی و نه رباتی. ۱ تا ۲ ایموجی. "
        "نام آهنگ/خواننده را تکرار نکن. فقط خودِ متن را بنویس."
    )
    user = f"آهنگ: «{title}» از «{artist}». یک متن کوتاه و خودمانی بنویس."
    try:
        txt = ai_chat(system, user, max_tokens=200, temperature=0.95)
        if txt.strip():
            return txt.strip()
    except Exception:
        pass
    return "همین حالا گوشش کن؛ بعضی آهنگ‌ها رو باید شنید، نه توضیح داد. 🎧"


def research_story(title, artist):
    """
    با جستجوی گوگلِ داخلیِ Gemini درباره‌ی آهنگ تحقیق می‌کند و داستان را فقط بر اساس
    یافته‌های واقعی می‌نویسد. اگر داستان واقعی پیدا نشد، متن احساسیِ خودمانی برمی‌گرداند.
    """
    system = (
        f"تو یک نویسنده‌ی موسیقی هستی و به زبان {STORY_LANGUAGE} می‌نویسی. "
        "ابتدا با جستجوی گوگل درباره‌ی این آهنگ تحقیق کن. "
        "اگر داستانِ واقعی و مستندی پشتش پیدا کردی (ماجرای ساخت، الهام، معنی واقعی ترانه و...)، "
        "آن را در ۴ تا ۶ خط با لحنی گرم، طبیعی و انسانی (نه رباتی و نه خشک) روایت کن و "
        "has_real_story=true بگذار. داستان را طوری شروع کن که روان بعد از عبارتِ «داستان این "
        "آهنگ از این قراره که» بیاید (ادامه‌ی جمله باشد و با تکرارِ نام آهنگ شروع نشود). "
        "اگر با جستجو داستانِ واقعیِ روشنی پیدا نکردی، has_real_story=false بگذار و یک متن کوتاه "
        "و خودمانی (۲ تا ۳ خط) بنویس که حسِ آهنگ را به یک موقعیتِ ملموسِ زندگی وصل کند، طوری که "
        "مخاطب بگوید «دقیقاً همینه». نمونه: «این آهنگ برای وقتاییه که هیچ‌کس درکت نمی‌کنه». "
        "فارسیِ محاوره‌ای و طبیعی، نه کلیشه‌ای. هرگز چیزی از خودت به‌عنوان واقعیت جا نزن. "
        "نام آهنگ و خواننده را داخل متن تکرار نکن. حداکثر ۱ تا ۲ ایموجی. "
        'فقط و فقط یک JSON خروجی بده: {"has_real_story": true/false, "story": "..."}'
    )
    user = (
        f"آهنگ: «{title}» از «{artist}».\n"
        "درباره‌ی داستان و معنی واقعیِ این آهنگ در گوگل جستجو کن و طبق دستور JSON بده."
    )
    try:
        raw = ai_chat(system, user, max_tokens=1200, temperature=0.7, grounded=True)
        data = parse_json(raw)
        data["has_real_story"] = bool(data.get("has_real_story"))
        data["story"] = (data.get("story") or "").strip()
        if data["story"]:
            return data
    except Exception as e:
        print(f"[story] گراندینگ ناموفق: {e} — متن احساسی جایگزین می‌شود", file=sys.stderr)

    return {"has_real_story": False, "story": _emotional_fallback(title, artist)}


# ------------------------ ست‌کردن متادیتای فایل ------------------------
def set_file_tags(mp3_path, title, artist, album):
    """عنوان/خواننده/آلبومِ فایل mp3 را تنظیم می‌کند (در پلیر تلگرام دیده می‌شود)."""
    try:
        try:
            tags = EasyID3(str(mp3_path))
        except ID3NoHeaderError:
            audio = MP3(str(mp3_path))
            audio.add_tags()
            audio.save()
            tags = EasyID3(str(mp3_path))
        tags["title"] = title
        tags["artist"] = artist
        tags["album"] = album
        tags.save()
    except Exception as e:
        print(f"[tags] هشدار: ست‌کردن متادیتا نشد: {e}", file=sys.stderr)


# ------------------------ ارسال به تلگرام ------------------------
def send_audio(mp3_path, title, performer, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
    with open(mp3_path, "rb") as f:
        files = {"audio": f}
        data = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "title": title,
            "performer": performer,
            "caption": caption[:1024],
            "parse_mode": "HTML",
        }
        r = requests.post(url, data=data, files=files, timeout=180)
    if not r.ok:
        raise RuntimeError(f"ارسال صوت به تلگرام شکست خورد: {r.text}")
    return r.json()


def send_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHANNEL_ID, "text": text[:4096], "parse_mode": "HTML"},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"ارسال پیام به تلگرام شکست خورد: {r.text}")


# ------------------------ اجرای اصلی ------------------------
MAX_SONG_ATTEMPTS = 4  # اگر آهنگی دانلود نشد، چند آهنگ دیگر امتحان کن


def main():
    history = load_history()

    mp3 = None
    info = None
    seed = None
    tried = []
    for i in range(MAX_SONG_ATTEMPTS):
        seed = choose_song(history, avoid=tried)
        label = f"{seed['artist']} - {seed['title']}"
        print(f"🎯 ({i + 1}/{MAX_SONG_ATTEMPTS}) جستجو برای: {label}")
        try:
            mp3, info = download_song(seed["search_query"])
            print(f"⬇️  دانلود شد: {mp3.name} | عنوان واقعی: {info.get('title')}")
            break
        except RuntimeError as e:
            print(f"⚠️  این آهنگ دانلود نشد: {e}", file=sys.stderr)
            print("🔁 یک آهنگ دیگر انتخاب می‌کنم...", file=sys.stderr)
            tried.append(label.lower())
            mp3 = None

    if not mp3 or not info:
        raise RuntimeError("بعد از چند تلاش، هیچ آهنگی قابل دانلود نبود.")

    # شناساییِ آهنگِ واقعی بر اساس عنوانِ همان فایلی که دانلود شد
    track = describe_track(
        info.get("title", ""),
        info.get("artist", ""),
        seed["search_query"],
    )
    print(f"🎼 شناسایی شد: {track['artist']} — {track['title']}")

    # داستان: اول وب را می‌گردیم، بعد فقط بر اساس نتایجِ واقعی می‌نویسیم
    research = research_story(track["title"], track["artist"])
    if research["has_real_story"]:
        track["story"] = f"📖 داستان این آهنگ از این قراره که {research['story']}"
        print("📖 داستان واقعی (از روی نتایج وب) نوشته شد")
    else:
        track["story"] = research["story"]
        print("✍️  داستان واقعی پیدا نشد؛ متن احساسی نوشته شد")

    # نام انگلیسی برای متادیتای فایل + گذاشتن آیدی کانال کنار نام آهنگ
    title_en = track["title_en"]
    artist_en = track["artist_en"]
    file_title = f"{title_en} | {CHANNEL_ID}"
    set_file_tags(mp3, file_title, artist_en, CHANNEL_TAG)

    title = html.escape(track["title"])
    artist = html.escape(track["artist"])
    story_safe = html.escape(track["story"])
    tag_safe = html.escape(CHANNEL_TAG)

    # چیدمان جدید: اسم خواننده/آهنگِ بولد بالا → داستان در کوتِ کلپس‌شده → تگ کانالِ بولد
    caption = (
        f"<b>🎵 {artist} — {title}</b>\n\n"
        f"<blockquote expandable>{story_safe}</blockquote>\n\n"
        f"<b>{tag_safe}</b>"
    )

    if len(caption) <= 1024:
        send_audio(mp3, file_title, artist_en, caption)
    else:
        # حالت نادر (داستان خیلی بلند): آهنگ با سرتیتر+تگ، داستان در پیام جدا
        short_caption = f"<b>🎵 {artist} — {title}</b>\n\n<b>{tag_safe}</b>"
        send_audio(mp3, file_title, artist_en, short_caption)
        send_message(f"<blockquote expandable>{story_safe}</blockquote>")
    print("📨 ارسال شد به کانال")

    key = f"{track['artist']} - {track['title']}".lower()
    history.append(
        {
            "key": key,
            "title": track["title"],
            "artist": track["artist"],
            "date": time.strftime("%Y-%m-%d"),
        }
    )
    save_history(history)
    print("✅ تمام شد")


if __name__ == "__main__":
    main()
