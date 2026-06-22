#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات ۳ — «رادیو بولتن» نسخهٔ موسیقیِ شبانه
هر شب ساعت ۲۱ به‌وقت تهران یک قطعهٔ موسیقی از archive.org برمی‌دارد، متادیتای فایل را
با نام چنل برند می‌کند (آلبوم + عنوان)، کِردیتِ هنرمند و لینکِ لایسنس را حفظ می‌کند،
یک متنِ احساسیِ فارسی می‌نویسد و به‌صورتِ فایلِ صوتی می‌فرستد.
ایمنی: فقط آیتم‌هایی با لایسنسِ آزاد (Creative Commons / مالکیت عمومی) استفاده می‌شوند؛
هر آیتمی که لایسنسِ آزاد نداشته باشد رد می‌شود. استفادهٔ غیرتجاری.
"""

import os
import sys
import json
import html
import random
import requests
from urllib.parse import quote
from datetime import datetime, timezone, timedelta

from mutagen.id3 import ID3, TIT2, TPE1, TALB, ID3NoHeaderError

# ===================== تنظیمات (این بخش را می‌توانی عوض کنی) =====================
BOT_NAME       = "رادیو بولتن (موسیقی)"
CHANNEL_ID     = "@testbotaii"          # ← جایی که پست می‌شود (اگر چنلِ موسیقیِ جدا داری عوضش کن)
CHANNEL_HANDLE = "@RadioBulletin"       # ← هندلِ عمومی که می‌خواهی مردم join کنند؛ در متادیتا و کپشن می‌آید
BACKUP_CHANNEL = "@analyzeAisTrb"       # کانالِ گزارشِ فنی
ALBUM_BRAND    = f"رادیو بولتن | {CHANNEL_HANDLE}"   # در فیلدِ آلبومِ فایل می‌نشیند (برندینگ)

# عبارت‌های جست‌وجو برای تنوع (موسیقیِ آزاد)؛ هر شب چندتا امتحان می‌شود
QUERY_TERMS = ["love", "soul", "jazz", "folk", "piano", "blues",
               "acoustic", "pop", "world", "romantic", "song", "guitar"]

# ===================== ثابت‌ها =====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")

AI_MODEL       = "openai/gpt-4.1"      # بهترین مدلِ رایگان (GPT-5 فقط با پلنِ پولی؛ آن‌وقت اینجا "openai/gpt-5" بگذار)
AI_MODEL_CHAIN = [AI_MODEL, "openai/gpt-4o", "openai/gpt-4o-mini"]
AI_ENDPOINT    = "https://models.github.ai/inference/chat/completions"

ARCHIVE_SEARCH    = "https://archive.org/advancedsearch.php"
ARCHIVE_META      = "https://archive.org/metadata/"
ARCHIVE_DL        = "https://archive.org/download/"
ARCHIVE_MAX_BYTES = 45 * 1024 * 1024   # سقفِ حجمِ فایل برای تلگرام (۵۰ مگ محدودیتِ بات است)
UA                = {"User-Agent": "RadioBulletinBot/1.0 (Telegram music ritual)"}

STATE_FILE  = "seen.json"
TEHRAN      = timezone(timedelta(hours=3, minutes=30))
TG_API      = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MP3_PATH    = "track.mp3"


# ===================== وضعیت (آهنگ‌های قبلاً پخش‌شده) =====================
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("  ⚠️ نتوانستم وضعیت را ذخیره کنم:", e)


# ===================== انتخابِ آهنگ از archive.org =====================
def _first_creator(meta):
    c = meta.get("creator")
    if isinstance(c, list):
        return (c[0] if c else "ناشناس")
    return c or "ناشناس"

# ===================== دانلود و تگ‌گذاری =====================
def download_mp3(url, path):
    with requests.get(url, headers=UA, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
    if total < 20000:   # کمتر از ~۲۰ کیلوبایت یعنی فایل سالم نیست
        raise RuntimeError(f"فایلِ دانلودشده خیلی کوچک است ({total} بایت)")
    print(f"  ⬇️ دانلود شد ({total // 1024} کیلوبایت)")
    return total


def tag_mp3(path, title, artist, album):
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.setall("TIT2", [TIT2(encoding=3, text=title)])    # عنوان (+ هندلِ چنل)
    tags.setall("TPE1", [TPE1(encoding=3, text=artist)])   # هنرمند (کِردیت)
    tags.setall("TALB", [TALB(encoding=3, text=album)])    # آلبوم (برندِ چنل)
    tags.save(path)
    print(f"  🏷 متادیتا ست شد (آلبوم = {album})")


# ===================== نوشتنِ متنِ احساسی با هوش مصنوعی =====================
def ai_caption(track):
    mood = track.get("_mood", "")
    mi = track.get("musicinfo") or {}
    tg = (mi.get("tags") or {})
    flat = []
    for k in ("genres", "instruments", "vartags"):
        v = tg.get(k)
        if isinstance(v, list):
            flat.extend(v)
    tagstr = ", ".join(flat[:8]) if flat else ""

    system = (
        "You write VERY SHORT, emotional captions in COLLOQUIAL PERSIAN (Farsi) for a "
        "nightly music ritual on a Telegram channel called «رادیو بولتن». "
        "You are given a track (title, artist, mood, tags).\n"
        "RULES:\n"
        "1) The caption MUST start with a sentence of the form «این آهنگ برای ...هاییه که ...» "
        "— choose وقت‌هایی / روزهایی / شب‌هایی / موقع‌هایی to best fit the mood, naming a real, "
        "relatable moment that gives the listener a concrete REASON to press play right now.\n"
        "2) The little story/scene you tell MUST tightly match the VIBE of THIS specific song. "
        "Infer the vibe from its mood, tags and title, and paint a scene or feeling that truly "
        "belongs to THIS song's world — never generic filler that could fit any random song.\n"
        "3) You do NOT know real facts about the artist or the song, so NEVER state invented "
        "facts, biography, or events as if true. Stay in mood/feeling/scene.\n"
        "4) SHORT: 2 to 4 short lines, ending with a gentle nudge to listen. Warm and sincere. "
        "No hashtags, no emojis. Keep it under 320 characters.\n"
        "Output ONLY the Persian caption text, nothing else."
    )
    user = (
        f"Track title: {track.get('name')}\n"
        f"Artist: {track.get('artist_name')}\n"
        f"Mood: {mood}\n"
        f"Tags: {tagstr}\n"
        "Write the Persian caption now."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}",
               "Content-Type": "application/json"}
    used_model = None
    for m in AI_MODEL_CHAIN:
        try:
            payload = {"model": m, "messages": messages}
            if not m.startswith("openai/gpt-5"):
                payload["temperature"] = 0.9   # GPT-5 فقط دمای پیش‌فرض را می‌پذیرد
            resp = requests.post(AI_ENDPOINT, headers=headers, timeout=90, json=payload)
            if resp.status_code == 429:
                print(f"  ⏳ سقفِ {m} پر است؛ مدلِ بعدی...")
                continue
            resp.raise_for_status()
            used_model = m
            txt = resp.json()["choices"][0]["message"]["content"].strip()
            if txt:
                if m != AI_MODEL:
                    print(f"  (با مدلِ پشتیبان نوشته شد: {m})")
                return txt, used_model
        except Exception as e:
            print(f"  ⚠️ خطای مدلِ {m}:", e)
            continue
    # فالبکِ ساده اگر هیچ مدلی جواب نداد
    return ("این آهنگ برای شب‌هاییه که دلت یه آرامشِ ساده می‌خواد.\n"
            "بذارش، چند دقیقه فقط گوش بده."), (used_model or "fallback")


def build_caption(body, track):
    artist = html.escape(str(track.get("artist_name", "")))
    name   = html.escape(str(track.get("name", "")))
    lic    = track.get("license_ccurl") or "https://creativecommons.org/licenses/"
    body   = html.escape(body.strip())
    if len(body) > 400:
        body = body[:399].rstrip() + "…"
    details = (
        f"🎙 هنرمند: {artist}\n"
        f"🎵 قطعه: {name}\n"
        f"📜 لایسنس: <a href=\"{html.escape(lic)}\">Creative Commons</a> · از archive.org\n"
        f"🎧 رادیو بولتن — {CHANNEL_HANDLE}"
    )
    # داستان بولد؛ جزئیات داخلِ کوت
    return f"<b>{body}</b>\n\n<blockquote>{details}</blockquote>"


# ===================== تلگرام =====================
def send_audio(path, caption, performer, title):
    with open(path, "rb") as audio:
        files = {"audio": ("track.mp3", audio, "audio/mpeg")}
        data = {
            "chat_id": CHANNEL_ID,
            "caption": caption,
            "parse_mode": "HTML",
            "performer": performer,
            "title": title,
        }
        r = requests.post(f"{TG_API}/sendAudio", data=data, files=files, timeout=180)
    try:
        j = r.json()
    except Exception:
        j = {}
    if not j.get("ok"):
        print("  ❌ ارسالِ تلگرام ناموفق:", r.text[:300])
        return None
    mid = j["result"]["message_id"]
    print(f"  ✅ آهنگ فرستاده شد (message_id={mid})")
    return mid


def post_backup(track, model_label, msg_id):
    try:
        now = datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M")
        chan = CHANNEL_ID.lstrip("@")
        link = f"https://t.me/{chan}/{msg_id}" if msg_id else "—"
        lic = track.get("license_ccurl") or "—"
        text = (
            f"🏷 ربات: {BOT_NAME}\n"
            f"🕘 زمان (تهران): {now}\n"
            f"🎵 قطعه: {track.get('name')}\n"
            f"🎙 هنرمند: {track.get('artist_name')}\n"
            f"🆔 archive.org ID: {track.get('id')}\n"
            f"📜 لایسنس: {lic}\n"
            f"🤖 مدل: {model_label}\n"
            f"📌 پست: {link}"
        )
        requests.post(f"{TG_API}/sendMessage", timeout=30,
                      data={"chat_id": BACKUP_CHANNEL, "text": text,
                            "disable_web_page_preview": "true"})
    except Exception as e:
        print("  ⚠️ گزارشِ پشتیبان ارسال نشد:", e)


# ===================== اجرا =====================
def main():
    print("🎵 شروعِ رادیو بولتن (موسیقی) —",
          datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M"))
    missing = [k for k, v in [("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
                              ("GITHUB_TOKEN", GITHUB_TOKEN)] if not v]
    if missing:
        print("❌ این متغیرها ست نشده‌اند:", ", ".join(missing))
        sys.exit(1)

    state = load_state()
    used = state.get("used_track_ids", [])
    used_set = set(str(x) for x in used)

    track = pick_track(used_set)
    if not track:
        print("⛔ آهنگِ آزادِ تازه‌ای پیدا نشد. فردا دوباره.")
        return

    # دانلود
    try:
        download_mp3(track["audiodownload"], MP3_PATH)
    except Exception as e:
        print("❌ دانلودِ آهنگ ناموفق:", e)
        return

    # عنوانِ متادیتا = اسمِ آهنگ + هندلِ چنل (کنارِ اسمِ آهنگ در پلیر دیده می‌شود)
    title_meta = f"{str(track.get('name', '')).strip()} | {CHANNEL_HANDLE}"
    artist_name = str(track.get("artist_name", ""))

    # متادیتا (برندینگ + کِردیت)
    try:
        tag_mp3(MP3_PATH, title=title_meta, artist=artist_name, album=ALBUM_BRAND)
    except Exception as e:
        print("  ⚠️ تگ‌گذاری ناموفق (با همان فایل ادامه می‌دهیم):", e)

    # متنِ احساسی + کپشن
    body, model_label = ai_caption(track)
    caption = build_caption(body, track)

    # ارسال
    mid = send_audio(MP3_PATH, caption=caption, performer=artist_name, title=title_meta)
    if not mid:
        print("❌ ارسال ناموفق بود؛ آهنگ را به لیستِ پخش‌شده اضافه نمی‌کنم.")
        return

    # گزارشِ پشتیبان + ذخیرهٔ وضعیت
    post_backup(track, model_label, mid)
    used.append(str(track.get("id")))
    state["used_track_ids"] = used[-2000:]
    save_state(state)
    print("🏁 تمام شد.")


if __name__ == "__main__":
    main()
