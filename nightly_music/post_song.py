#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
هر شب یک آهنگ هوشمند انتخاب می‌کند، فایل کامل آن را دانلود می‌کند،
یک داستان احساسی پشت موسیقی می‌نویسد و همه را در کانال تلگرام می‌فرستد.

موتور هوش مصنوعی: GitHub Models (رایگان، با همان توکن گیت‌هاب)
"""

import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from openai import OpenAI
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp3 import MP3

# ---------------------------- تنظیمات ----------------------------
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-4o")
STORY_LANGUAGE = os.environ.get("STORY_LANGUAGE", "Persian (Farsi)")
MODELS_ENDPOINT = "https://models.github.ai/inference"

# تگ کانال که هم در فیلد آلبومِ فایل و هم در آخر پیام می‌آید
CHANNEL_TAG = "@RadioBulletin |  رادیو بولتن"
CHANNEL_ID = "@RadioBulletin"  # کنار نام آهنگ در فایل می‌آید

HISTORY_FILE = Path(__file__).parent / "sent_history.json"
DOWNLOAD_DIR = Path("/tmp/song")
MAX_HISTORY = 200  # چند آهنگ آخر را به هوش مصنوعی بدهیم تا تکرار نشود

client = OpenAI(base_url=MODELS_ENDPOINT, api_key=GITHUB_TOKEN)


# ------------------------ ابزار هوش مصنوعی ------------------------
def ai_chat(system, user, max_tokens=900, temperature=0.9, json_mode=False, retries=4):
    """یک درخواست به GitHub Models با backoff برای محدودیت نرخ (rate limit)."""
    for attempt in range(retries):
        try:
            kwargs = dict(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            wait = (2 ** attempt) * 5
            print(f"[AI] خطا (تلاش {attempt + 1}): {e} — {wait}s صبر...", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("درخواست هوش مصنوعی بعد از چند تلاش شکست خورد.")


def parse_json(raw):
    """JSON را حتی اگر داخل ```fence``` باشد می‌خواند."""
    raw = raw.strip()
    raw = re.sub(r"^```(json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
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
        "اولویت اولت: آهنگی پیشنهاد بده که پشتش یک «داستان واقعی و مستندِ جذاب» وجود دارد "
        "(ماجرای ساختش، اتفاقی که الهام‌بخشش بوده و...). اگر آهنگِ تازه‌ای با داستان واقعی "
        "پیدا نکردی یا همه قبلاً فرستاده شده‌اند، یک آهنگ ترند/محبوب/باب میل مخاطب ایرانی بده. "
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
    آهنگ را به mp3 دانلود می‌کند (اول ساندکلاد، بعد یوتیوب) و کاور + متادیتا
    را داخل فایل جاسازی می‌کند.
    اگر خواستی بعداً از پایپلاین خودِ DezAlty (Deezer) استفاده کنی، فقط همین
    تابع را عوض کن (کافی است مسیر یک فایل mp3 معتبر را برگردانی).
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for f in DOWNLOAD_DIR.glob("*"):
        f.unlink()

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

    # ساندکلاد را اول امتحان می‌کنیم چون مشکل بات/جاوااسکریپت ندارد و سریع است.
    # یوتیوب فقط به‌عنوان پشتیبان (روی سرور گیت‌هاب اغلب توسط یوتیوب محدود می‌شود).
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
            print(f"[download] ✅ موفق با: {name}")
            return mp3s[0], _read_info()
        print(f"[download] ❌ شکست با: {name} — تلاش بعدی...", file=sys.stderr)

    print(last_output, file=sys.stderr)
    raise RuntimeError("دانلود آهنگ با همه‌ی منابع (یوتیوب و ساندکلاد) شکست خورد.")


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


# --------------- شناسایی آهنگ واقعی + داستان (بر اساس فایل دانلودشده) ---------------
def describe_track(real_title, real_artist_hint, seed):
    """
    بر اساس عنوانِ واقعیِ فایلِ دانلودشده، اسم تمیز آهنگ/خواننده و داستان را می‌سازد.
    چون ورودی، عنوانِ واقعیِ همان فایل است، اسم نمایش‌داده‌شده دقیقاً با خودِ آهنگ می‌خواند.
    """
    system = (
        "تو یک متخصص موسیقی هستی. عنوان خامِ یک فایل صوتیِ دانلودشده به تو داده می‌شود "
        "(که از یوتیوب یا ساندکلاد آمده و ممکن است شلوغ باشد). وظیفه‌ات: "
        "۱) اسم تمیزِ آهنگ و خواننده‌ی واقعی را از روی همین عنوان استخراج کنی (نه چیز دیگر). "
        "۲) اگر داستانِ واقعی، مستند و درستی از همین آهنگ می‌دانی، آن را روایت کنی. "
        "بسیار مهم: هرگز داستان از خودت نساز و هیچ اطلاعات نادرستی نده. فقط وقتی has_real_story "
        "را true بگذار که کاملاً مطمئنی داستان واقعی و درست است. در غیر این صورت false بگذار "
        "و در story یک متن کوتاهِ احساسی (حداکثر ۳ خط) بنویس که شنونده را مشتاق کند. "
        "اگر از روی عنوان نمی‌توانی آهنگ را تشخیص دهی، اسم را همان‌طور که هست تمیز کن."
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
        '  "artist_en": "اسم خواننده به انگلیسی/لاتین",\n'
        '  "has_real_story": true یا false,\n'
        '  "story": "داستان واقعی (۴ تا ۶ خط) اگر مطمئنی؛ وگرنه متن احساسی کوتاه (۳ خط)"\n'
        "}"
    )
    raw = ai_chat(system, user, max_tokens=700, temperature=0.85, json_mode=True)
    data = parse_json(raw)
    # پشتیبان: اگر چیزی خالی ماند، از عنوان واقعی/seed استفاده کن
    data["title"] = (data.get("title") or real_title or seed).strip()
    data["artist"] = (data.get("artist") or real_artist_hint).strip()
    data["title_en"] = (data.get("title_en") or data["title"]).strip()
    data["artist_en"] = (data.get("artist_en") or data["artist"]).strip()
    data["story"] = (data.get("story") or "").strip()
    return data


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

    # شناساییِ آهنگِ واقعی + داستان، بر اساس عنوانِ همان فایلی که دانلود شد
    track = describe_track(
        info.get("title", ""),
        info.get("artist", ""),
        seed["search_query"],
    )
    if track.get("has_real_story"):
        print(f"📖 داستان واقعی: {track['artist']} — {track['title']}")
    else:
        print(f"✍️  داستان احساسی: {track['artist']} — {track['title']}")

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
