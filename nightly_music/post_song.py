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

# ---------------------------- تنظیمات ----------------------------
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-4o")
STORY_LANGUAGE = os.environ.get("STORY_LANGUAGE", "Persian (Farsi)")
MODELS_ENDPOINT = "https://models.github.ai/inference"

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
def choose_song(history):
    recent = [h["key"] for h in history[-MAX_HISTORY:]]
    system = (
        "تو یک کیوریتور موسیقی حرفه‌ای برای یک کانال تلگرامی با مخاطب ایرانی هستی. "
        "هر شب یک آهنگ انتخاب می‌کنی که حداقل یکی از این ویژگی‌ها را داشته باشد: "
        "۱) پشتش یک داستان واقعی و احساسی جذاب باشد، "
        "۲) این روزها ترند و محبوب باشد، "
        "۳) باب میل و سلیقه‌ی مخاطب ایرانی باشد. "
        "ترکیبی متنوع از آهنگ‌های فارسی/ایرانی و بین‌المللی انتخاب کن و خودت را به یک سبک محدود نکن. "
        "حتماً آهنگ‌های واقعی و موجود که روی یوتیوب پیدا می‌شوند را انتخاب کن. "
        "آهنگ‌هایی که در لیست «قبلاً فرستاده‌شده» آمده‌اند را دوباره انتخاب نکن."
    )
    user = (
        "یک آهنگ برای امشب انتخاب کن و خروجی را فقط به صورت JSON بده با این کلیدها:\n"
        "{\n"
        '  "title": "نام دقیق آهنگ",\n'
        '  "artist": "نام خواننده",\n'
        '  "language": "fa یا en یا ...",\n'
        '  "youtube_query": "بهترین عبارت جستجو در یوتیوب برای پیدا کردن نسخه رسمی/باکیفیت، '
        'معمولاً: نام‌خواننده نام‌آهنگ official audio",\n'
        '  "reason": "در یک جمله کوتاه چرا این آهنگ"\n'
        "}\n\n"
        f"آهنگ‌هایی که قبلاً فرستاده شده‌اند (هیچ‌کدام را انتخاب نکن):\n"
        f"{json.dumps(recent, ensure_ascii=False)}"
    )
    raw = ai_chat(system, user, max_tokens=400, temperature=1.0, json_mode=True)
    song = parse_json(raw)
    # اعتبارسنجی حداقلی
    for k in ("title", "artist", "youtube_query"):
        if not song.get(k):
            raise RuntimeError(f"خروجی هوش مصنوعی ناقص بود (کلید «{k}» نبود).")
    return song


# ------------------------ دانلود آهنگ (ماژولار) ------------------------
def download_song(query):
    """
    آهنگ را از یوتیوب به mp3 دانلود می‌کند و کاور + متادیتا را داخل فایل جاسازی می‌کند.
    اگر خواستی بعداً از پایپلاین خودِ DezAlty استفاده کنی، فقط همین تابع را عوض کن
    (کافی است مسیر یک فایل mp3 معتبر را برگردانی).
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

    # چند منبع/کلاینت را به ترتیب امتحان می‌کنیم. اگر یوتیوب گیر داد،
    # خودکار سراغ ساندکلاد می‌رویم که مشکل بات/جاوااسکریپت ندارد.
    strategies = [
        ("یوتیوب default", yt_common + ["--extractor-args", "youtube:player_client=default,-tv", f"ytsearch1:{query}"]),
        ("یوتیوب ios", yt_common + ["--extractor-args", "youtube:player_client=ios", f"ytsearch1:{query}"]),
        ("یوتیوب android", yt_common + ["--extractor-args", "youtube:player_client=android", f"ytsearch1:{query}"]),
        ("ساندکلاد", common + [f"scsearch1:{query}"]),
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
        f"تو یک نویسنده‌ی احساسی و قصه‌گو هستی و به زبان {STORY_LANGUAGE} می‌نویسی. "
        "برای یک کانال موسیقی، داستان یا حال‌وهوای احساسی پشت یک آهنگ را روایت می‌کنی. "
        "لحن گرم، شاعرانه و تأثیرگذار باشد اما اغراق‌نشده و باورپذیر. "
        "اگر داستان واقعی پشت آهنگ را می‌دانی روایتش کن؛ وگرنه فضا و حسّ آهنگ را زیبا توصیف کن. "
        "هرگز اطلاعات نادرست یا ساختگی به عنوان واقعیت جا نزن. "
        "طول متن حداکثر حدود ۸۰۰ کاراکتر باشد تا در کپشن تلگرام جا شود. "
        "می‌توانی از چند ایموجی مناسب استفاده کنی. متن خام بنویس، بدون عنوان یا فرمت اضافه."
    )
    user = (
        f"آهنگ: «{song['title']}» از {song['artist']}.\n"
        "یک متن کوتاه و احساسی درباره‌ی این آهنگ بنویس."
    )
    return ai_chat(system, user, max_tokens=700, temperature=0.9)


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
def main():
    history = load_history()

    song = choose_song(history)
    print(f"🎯 آهنگ انتخاب‌شده: {song['artist']} — {song['title']}")

    mp3 = download_song(song["youtube_query"])
    print(f"⬇️  دانلود شد: {mp3.name}")

    story = write_story(song)
    print("✍️  داستان نوشته شد")

    title = html.escape(song["title"])
    artist = html.escape(song["artist"])
    story_safe = html.escape(story)

    header = f"🎵 <b>{title}</b>\n🎤 {artist}\n\n"
    full_caption = header + story_safe

    if len(full_caption) <= 1024:
        send_audio(mp3, song["title"], song["artist"], full_caption)
    else:
        # اگر داستان طولانی شد: اول آهنگ، بعد داستان در پیام جدا
        send_audio(mp3, song["title"], song["artist"], header.strip())
        send_message(story_safe)
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
