#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات ۳ — «رادیو بولتن» نسخهٔ موسیقیِ شبانه
هر شب ساعت ۲۱ به‌وقت تهران یک قطعهٔ موسیقیِ با لایسنسِ آزاد می‌گیرد، متادیتای فایل را
با نام چنل برند می‌کند (عنوان + آلبوم)، کِردیتِ هنرمند و لینکِ لایسنس را حفظ می‌کند،
یک متنِ احساسیِ فارسی می‌نویسد و به‌صورتِ فایلِ صوتی می‌فرستد. استفادهٔ غیرتجاری.

★ منبعِ موسیقی «ماژولار» است: فقط بخشِ بینِ «شروعِ منبع» و «پایانِ منبع» را عوض کن.
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

# ===================== تنظیماتِ عمومی (مستقل از منبع) =====================
BOT_NAME       = "رادیو بولتن (موسیقی)"
CHANNEL_ID     = "@testbotaii"          # ← جایی که پست می‌شود
CHANNEL_HANDLE = "@RadioBulletin"       # ← هندلِ عمومی که می‌خواهی مردم join کنند (در متادیتا و کپشن می‌آید)
BACKUP_CHANNEL = "@analyzeAisTrb"       # کانالِ گزارشِ فنی
ALBUM_BRAND    = f"رادیو بولتن | {CHANNEL_HANDLE}"   # در فیلدِ آلبومِ فایل می‌نشیند (برندینگ)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")   # اگر خالی باشد، خودکار از GitHub Models استفاده می‌شود

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GITHUB_ENDPOINT = "https://models.github.ai/inference/chat/completions"
# زنجیره: اول بهترین، بعد فالبک‌ها — (provider, model)
AI_CHAIN = [
    ("gemini", "gemini-2.5-pro"),
    ("gemini", "gemini-2.5-flash"),
    ("github", "openai/gpt-4.1"),
    ("github", "openai/gpt-4o"),
    ("github", "openai/gpt-4o-mini"),
]

UA          = {"User-Agent": "RadioBulletinBot/1.0 (Telegram music ritual)"}
MAX_BYTES   = 45 * 1024 * 1024         # سقفِ حجمِ فایل (محدودیتِ بات تلگرام ~۵۰ مگ)
STATE_FILE  = "seen.json"
TEHRAN      = timezone(timedelta(hours=3, minutes=30))
TG_API      = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MP3_PATH    = "track.mp3"


# ███████████████████████ شروعِ منبع (SOURCE ADAPTER) ███████████████████████
#
#  برای عوض‌کردنِ منبع، فقط همین بلوک (تا «پایانِ منبع») را جایگزین کن.
#  تنها چیزی که بقیهٔ ربات از منبع می‌خواهد، یک تابع است:
#
#       fetch_track(used_ids)  ->  dict  یا  None
#
#  این تابع باید یک «دیکشنریِ نرمال‌شده» با همین کلیدها برگرداند (یا None اگر چیزی نیافت):
#       {
#         "id":          str,        # شناسهٔ یکتا برای جلوگیری از تکرار   (الزامی)
#         "name":        str,        # اسمِ آهنگ                          (الزامی)
#         "artist":      str,        # نامِ هنرمند برای کِردیت             (الزامی)
#         "audio_url":   str,        # لینکِ مستقیمِ دانلودِ MP3           (الزامی)
#         "license_url": str,        # لینکِ لایسنس (CC/آزاد)             (الزامی)
#         "source":      str,        # نامِ منبع، مثل "archive.org"        (الزامی)
#         "mood":        str,        # حال‌وهوا — کمک به نوشتنِ متن        (اختیاری)
#         "tags":        list[str],  # تگ‌ها — کمک به نوشتنِ متن           (اختیاری)
#       }
#
#  used_ids یک set از idهای قبلاً پخش‌شده است؛ آن‌ها را رد کن.
#  شرطِ مهم: فقط آهنگِ با لایسنسِ آزاد (CC/مالکیت عمومی) و دانلودِ قانونی برگردان.
#
SOURCE_NAME = "archive.org"

QUERY_TERMS = ["love", "soul", "jazz", "folk", "piano", "blues",
               "acoustic", "pop", "world", "romantic", "song", "guitar"]

ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_META   = "https://archive.org/metadata/"
ARCHIVE_DL     = "https://archive.org/download/"

# ── فهرستِ دستیِ موسیقیِ فارسیِ کلاسیک/مالکیت‌عمومی (اولویتِ اول) ──
# شناسه را از انتهای لینکِ archive.org/details/<شناسه> بردار.
# ⚠️ ربات فقط آیتم‌هایی را پخش می‌کند که تگِ لایسنسِ آزاد (CC/مالکیت عمومی) داشته باشند،
#    پس قبل از افزودن مطمئن شو صفحه‌ی آیتم «Public Domain» یا «Creative Commons» نشان می‌دهد
#    و موسیقیِ کلاسیکِ قدیمی است (نه پاپِ مدرنِ کپی‌رایتی). هر چه بیشتر اضافه کنی، فارسیِ بیشتر.
PERSIAN_ARCHIVE_IDS = [
    "iranian-classical-music",   # نمونه — قبل از اعتماد، خودت تأییدش کن (یا با لینک‌های مطمئنِ خودت جایگزین کن)
]


def _is_free_license(url):
    u = (url or "").lower()
    return ("creativecommons.org" in u) or ("publicdomain" in u) or ("/cc0" in u)


def _first_creator(meta):
    c = meta.get("creator")
    if isinstance(c, list):
        return (c[0] if c else "ناشناس")
    return c or "ناشناس"


def _archive_search(term, rows=50):
    params = [
        ("q", f'mediatype:(audio) AND licenseurl:[* TO *] AND ({term})'),
        ("fl[]", "identifier"), ("fl[]", "title"),
        ("fl[]", "creator"), ("fl[]", "licenseurl"),
        ("rows", str(rows)),
        ("page", str(random.randint(1, 15))),
        ("output", "json"),
        ("sort[]", "downloads desc"),
    ]
    r = requests.get(ARCHIVE_SEARCH, params=params, headers=UA, timeout=30)
    r.raise_for_status()
    return (r.json().get("response", {}) or {}).get("docs", []) or []


def _archive_resolve(identifier):
    """متادیتای آیتم؛ فقط اگر لایسنسش آزاد بود یک فایلِ mp3 برمی‌گرداند، وگرنه None."""
    r = requests.get(ARCHIVE_META + identifier, headers=UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    meta = j.get("metadata", {}) or {}
    lic = meta.get("licenseurl", "")
    if not _is_free_license(lic):
        return None
    best = None
    for f in (j.get("files", []) or []):
        name = f.get("name", "")
        if not name.lower().endswith(".mp3"):
            continue
        try:
            size = int(f.get("size", "0"))
        except Exception:
            size = 0
        if size and size > MAX_BYTES:
            continue
        best = f
        break
    if not best:
        return None
    fname = best["name"]
    title = best.get("title") or meta.get("title") or fname
    return {
        "id": identifier,
        "name": str(title),
        "artist": str(_first_creator(meta)),
        "audio_url": ARCHIVE_DL + identifier + "/" + quote(fname),
        "license_url": lic,
        "source": SOURCE_NAME,
        "mood": "",
        "tags": [],
    }


def _archive_item_tracks(identifier):
    """همه‌ی قطعاتِ mp3 یک آیتمِ فارسی را برمی‌گرداند — فقط اگر لایسنسش آزاد باشد."""
    r = requests.get(ARCHIVE_META + identifier, headers=UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    meta = j.get("metadata", {}) or {}
    lic = meta.get("licenseurl", "")
    if not _is_free_license(lic):          # ایمنی: بدونِ لایسنسِ آزاد، رد
        print(f"  ⏭ آیتمِ «{identifier}» لایسنسِ آزاد ندارد؛ رد شد.")
        return []
    artist = _first_creator(meta)
    out = []
    for f in (j.get("files", []) or []):
        name = f.get("name", "")
        if not name.lower().endswith(".mp3"):
            continue
        try:
            size = int(f.get("size", "0"))
        except Exception:
            size = 0
        if size and size > MAX_BYTES:
            continue
        title = f.get("title") or name.rsplit(".", 1)[0]
        out.append({
            "id": f"{identifier}/{name}",
            "name": str(title),
            "artist": str(artist),
            "audio_url": ARCHIVE_DL + identifier + "/" + quote(name),
            "license_url": lic,
            "source": SOURCE_NAME,
            "mood": "کلاسیک و نوستالژیکِ ایرانی",
            "tags": ["persian", "classical", "nostalgic"],
        })
    return out


def fetch_persian(used_ids):
    """از فهرستِ دستیِ فارسی یک قطعه‌ی تکراری‌نشده برمی‌گرداند (یا None)."""
    ids = PERSIAN_ARCHIVE_IDS[:]
    random.shuffle(ids)
    pool = []
    for ident in ids[:8]:
        try:
            tracks = _archive_item_tracks(ident)
        except Exception as e:
            print(f"  ⚠️ خطا در آیتمِ فارسیِ «{ident}»:", e)
            continue
        pool += [t for t in tracks if t["id"] not in used_ids]
    if not pool:
        return None
    t = random.choice(pool)
    print(f"  🇮🇷 فارسی انتخاب شد: «{t['name']}» از «{t['artist']}»")
    return t


def fetch_track(used_ids):
    """نقطهٔ ورودِ منبع: یک آهنگِ آزاد و تکراری‌نشده برمی‌گرداند (یا None)."""
    # ۱) اولویتِ اول: فارسی (فهرستِ دستیِ کلاسیکِ تأییدشده)
    t = fetch_persian(used_ids)
    if t:
        return t
    # ۲) فالبک: جست‌وجوی عمومیِ آهنگِ آزاد (غیرفارسیِ احساسی)
    terms = QUERY_TERMS[:]
    random.shuffle(terms)
    for term in terms[:6]:
        try:
            docs = _archive_search(term)
        except Exception as e:
            print(f"  ⚠️ خطا در جست‌وجوی «{term}»:", e)
            continue
        random.shuffle(docs)
        for d in docs[:8]:
            ident = d.get("identifier", "")
            if not ident or ident in used_ids:
                continue
            if d.get("licenseurl") and not _is_free_license(d.get("licenseurl")):
                continue
            try:
                tr = _archive_resolve(ident)
            except Exception as e:
                print(f"  ⚠️ خطا در آیتمِ {ident}:", e)
                continue
            if not tr:
                continue
            tr["mood"] = term
            print(f"  🎯 انتخاب شد: «{tr['name']}» از «{tr['artist']}» "
                  f"(جست‌وجو: {term} | لایسنس: {tr['license_url']})")
            return tr
    return None
# ███████████████████████ پایانِ منبع (SOURCE ADAPTER) ███████████████████████


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
    if total < 20000:
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
    mood = track.get("mood", "")
    tagstr = ", ".join((track.get("tags") or [])[:8])

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
        f"Artist: {track.get('artist')}\n"
        f"Mood: {mood}\n"
        f"Tags: {tagstr}\n"
        "Write the Persian caption now."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    for provider, model in AI_CHAIN:
        endpoint = GEMINI_ENDPOINT if provider == "gemini" else GITHUB_ENDPOINT
        key = GEMINI_API_KEY if provider == "gemini" else GITHUB_TOKEN
        if not key:
            continue
        try:
            resp = requests.post(
                endpoint, timeout=120,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "temperature": 0.9})
            if resp.status_code == 429:
                print(f"  ⏳ سقفِ {provider}:{model} پر است؛ مدلِ بعدی...")
                continue
            resp.raise_for_status()
            txt = resp.json()["choices"][0]["message"]["content"].strip()
            if txt:
                print(f"  🤖 مدل: {provider}:{model}")
                return txt, f"{provider}:{model}"
        except Exception as e:
            print(f"  ⚠️ خطای {provider}:{model}:", e)
            continue
    return ("این آهنگ برای شب‌هاییه که دلت یه آرامشِ ساده می‌خواد.\n"
            "بذارش، چند دقیقه فقط گوش بده."), "fallback"


def build_caption(body, track):
    artist = html.escape(str(track.get("artist", "")))
    name   = html.escape(str(track.get("name", "")))
    src    = html.escape(str(track.get("source", "")))
    lic    = str(track.get("license_url") or "")
    if "creativecommons" in lic.lower():
        lic_label, lic_href = "Creative Commons", lic
    elif "publicdomain" in lic.lower() or "cc0" in lic.lower():
        lic_label, lic_href = "مالکیت عمومی", lic
    elif lic:
        lic_label, lic_href = "لایسنسِ آزاد", lic
    else:
        lic_label, lic_href = "اثرِ آزاد", "https://creativecommons.org/"
    body = html.escape(body.strip())
    if len(body) > 400:
        body = body[:399].rstrip() + "…"
    details = (
        f"🎙 هنرمند: {artist}\n"
        f"🎵 قطعه: {name}\n"
        f"📜 لایسنس: <a href=\"{html.escape(lic_href)}\">{lic_label}</a> · از {src}\n"
        f"🎧 رادیو بولتن — {CHANNEL_HANDLE}"
    )
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
        lic = track.get("license_url") or "—"
        text = (
            f"🏷 ربات: {BOT_NAME}\n"
            f"🕘 زمان (تهران): {now}\n"
            f"🎵 قطعه: {track.get('name')}\n"
            f"🎙 هنرمند: {track.get('artist')}\n"
            f"🆔 {track.get('source')} ID: {track.get('id')}\n"
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

    track = fetch_track(used_set)          # ← تنها نقطهٔ تماس با منبع
    if not track:
        print("⛔ آهنگِ آزادِ تازه‌ای پیدا نشد. فردا دوباره.")
        return

    try:
        download_mp3(track["audio_url"], MP3_PATH)
    except Exception as e:
        print("❌ دانلودِ آهنگ ناموفق:", e)
        return

    title_meta = f"{str(track.get('name', '')).strip()} | {CHANNEL_HANDLE}"
    artist_name = str(track.get("artist", ""))

    try:
        tag_mp3(MP3_PATH, title=title_meta, artist=artist_name, album=ALBUM_BRAND)
    except Exception as e:
        print("  ⚠️ تگ‌گذاری ناموفق (با همان فایل ادامه می‌دهیم):", e)

    body, model_label = ai_caption(track)
    caption = build_caption(body, track)

    mid = send_audio(MP3_PATH, caption=caption, performer=artist_name, title=title_meta)
    if not mid:
        print("❌ ارسال ناموفق بود؛ آهنگ را به لیستِ پخش‌شده اضافه نمی‌کنم.")
        return

    post_backup(track, model_label, mid)
    used.append(str(track.get("id")))
    state["used_track_ids"] = used[-2000:]
    save_state(state)
    print("🏁 تمام شد.")


if __name__ == "__main__":
    main()
