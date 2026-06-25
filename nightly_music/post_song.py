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
        "اولویت اولِ تو این است: آهنگی انتخاب کنی که پشتش یک «داستان واقعی، مستند و جذاب» "
        "وجود دارد که تو واقعاً از درستی‌اش مطمئنی — مثل ماجرای ساخته‌شدن آهنگ، اتفاقی که "
        "الهام‌بخشش بوده، یا داستان واقعی پشت متن ترانه. اگر چنین آهنگی پیدا کردی که در لیست "
        "«انتخاب نکن» نیست، has_real_story را true بگذار و آن داستانِ واقعی را در فیلد story "
        "بنویس (۴ تا ۶ خط). فقط وقتی true بگذار که مطمئنی داستان واقعی و درست است؛ هرگز داستان "
        "از خودت نساز و به‌عنوان واقعیت جا نزن. "
        "اگر آهنگِ تازه‌ای با داستان واقعی پیدا نکردی یا همه‌شان قبلاً فرستاده شده‌اند، یک آهنگ "
        "خوب دیگر انتخاب کن (ترند، محبوب یا باب میل مخاطب ایرانی)، has_real_story را false و "
        "story را خالی بگذار. "
        "ترکیبی متنوع از آهنگ‌های فارسی و بین‌المللی، و آهنگ‌های شناخته‌شده که در سرویس‌های "
        "موسیقی پیدا می‌شوند انتخاب کن."
    )
    user = (
        "یک آهنگ برای امشب انتخاب کن و خروجی را فقط به صورت JSON بده با این کلیدها:\n"
        "{\n"
        '  "title": "نام آهنگ به زبان اصلی",\n'
        '  "artist": "نام خواننده به زبان اصلی",\n'
        '  "title_en": "نام آهنگ به انگلیسی/لاتین (برای آهنگ فارسی، فینگلیش)",\n'
        '  "artist_en": "نام خواننده به انگلیسی/لاتین",\n'
        '  "language": "fa یا en یا ...",\n'
        '  "search_query": "عبارت جستجوی تمیز فقط نام خواننده و نام آهنگ، '
        'بدون کلماتی مثل official/audio/video/lyrics/HD",\n'
        '  "has_real_story": true یا false,\n'
        '  "story": "اگر has_real_story=true بود، داستان واقعی (۴ تا ۶ خط)؛ وگرنه خالی",\n'
        '  "reason": "در یک جمله کوتاه چرا این آهنگ"\n'
        "}\n\n"
        f"آهنگ‌هایی که نباید انتخاب کنی:\n"
        f"{json.dumps(recent, ensure_ascii=False)}"
    )
    raw = ai_chat(system, user, max_tokens=700, temperature=1.0, json_mode=True)
    song = parse_json(raw)
    for k in ("title", "artist", "search_query"):
        if not song.get(k):
            raise RuntimeError(f"خروجی هوش مصنوعی ناقص بود (کلید «{k}» نبود).")
    # اگر نسخه‌ی انگلیسی نداد، از همان زبان اصلی استفاده می‌کنیم
    song["title_en"] = (song.get("title_en") or song["title"]).strip()
    song["artist_en"] = (song.get("artist_en") or song["artist"]).strip()
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
            return mp3s[0]
        print(f"[download] ❌ شکست با: {name} — تلاش بعدی...", file=sys.stderr)

    print(last_output, file=sys.stderr)
    raise RuntimeError("دانلود آهنگ با همه‌ی منابع (یوتیوب و ساندکلاد) شکست خورد.")


# ------------------------ نوشتن داستان احساسی ------------------------
def write_story(song):
    system = (
        f"تو یک نویسنده‌ی احساسی و حرفه‌ای هستی و به زبان {STORY_LANGUAGE} می‌نویسی. "
        "برای یک کانال موسیقی متنی خیلی کوتاه می‌نویسی (حداکثر ۳ خط). "
        "هدف: کاری کنی که خواننده با خواندنش حس کند این آهنگ دقیقاً برای یک لحظه یا "
        "صحنه‌ی خاصِ زندگی‌اش ساخته شده و همان لحظه دلش بخواهد گوشش کند — "
        "یعنی یک صحنه یا حسِ ملموس و قابل‌لمس بساز، نه توصیف کلی. "
        "لحن شاعرانه، گرم و تأثیرگذار اما بدون اغراق و کلیشه. "
        "نام آهنگ و خواننده را داخل متن نیاور (جداگانه می‌آید). "
        "حداکثر ۱ تا ۲ ایموجی. فقط خودِ متن را بنویس، بدون عنوان یا علامت نقل‌قول."
    )
    user = (
        f"آهنگ: «{song['title']}» از {song['artist']}.\n"
        "یک متن خیلی کوتاه (حداکثر ۳ خط) بنویس که خواننده را به شنیدن آهنگ مشتاق کند."
    )
    return ai_chat(system, user, max_tokens=300, temperature=0.95)


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
    song = None
    tried = []
    for i in range(MAX_SONG_ATTEMPTS):
        song = choose_song(history, avoid=tried)
        label = f"{song['artist']} - {song['title']}"
        print(f"🎯 ({i + 1}/{MAX_SONG_ATTEMPTS}) آهنگ: {label}")
        try:
            mp3 = download_song(song["search_query"])
            print(f"⬇️  دانلود شد: {mp3.name}")
            break
        except RuntimeError as e:
            print(f"⚠️  این آهنگ دانلود نشد: {e}", file=sys.stderr)
            print("🔁 یک آهنگ دیگر انتخاب می‌کنم...", file=sys.stderr)
            tried.append(label.lower())
            mp3 = None

    if not mp3 or not song:
        raise RuntimeError("بعد از چند تلاش، هیچ آهنگی قابل دانلود نبود.")

    # نام انگلیسی برای متادیتای فایل + گذاشتن آیدی کانال کنار نام آهنگ
    title_en = song["title_en"]
    artist_en = song["artist_en"]
    file_title = f"{title_en} | {CHANNEL_ID}"
    set_file_tags(mp3, file_title, artist_en, CHANNEL_TAG)

    # داستان: اگر داستان واقعی موجود بود همان را می‌فرستیم، وگرنه خودمان می‌نویسیم
    if song.get("has_real_story") and song.get("story", "").strip():
        story = song["story"].strip()
        print("📖 داستان واقعی پیدا شد")
    else:
        story = write_story(song)
        print("✍️  داستان احساسی نوشته شد")

    title = html.escape(song["title"])
    artist = html.escape(song["artist"])
    story_safe = html.escape(story)
    tag_safe = html.escape(CHANNEL_TAG)

    # چیدمان: داستانِ بولد → کوتِ خواننده/آهنگ → تگ کانالِ بولد
    caption = (
        f"<b>{story_safe}</b>\n\n"
        f"<blockquote>🎵 {artist} — {title}</blockquote>\n\n"
        f"<b>{tag_safe}</b>"
    )

    if len(caption) <= 1024:
        send_audio(mp3, file_title, artist_en, caption)
    else:
        # حالت نادر (داستان خیلی بلند): آهنگ با کوت+تگ، داستان در پیام جدا
        short_caption = (
            f"<blockquote>🎵 {artist} — {title}</blockquote>\n\n<b>{tag_safe}</b>"
        )
        send_audio(mp3, file_title, artist_en, short_caption)
        send_message(f"<b>{story_safe}</b>")
    print("📨 ارسال شد به کانال")

    key = f"{song['artist']} - {song['title']}".lower()
    history.append(
        {
            "key": key,
            "title": song["title"],
            "artist": song["artist"],
            "date": time.strftime("%Y-%m-%d"),
        }
    )
    save_history(history)
    print("✅ تمام شد")


if __name__ == "__main__":
    main()
