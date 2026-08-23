import os
import re
import time
import json
import hashlib
import feedparser
import requests
import urllib.parse
from difflib import SequenceMatcher
from datetime import datetime, timezone
import google.generativeai as genai

# ═══════════════════════════════ কনফিগারেশন ══════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
CHAT_ID        = os.environ.get("CHAT_ID")
GEMINI_API_KEY = os.environ.get("AQ.Ab8RN6KARdiKbw1feh0rQalEL93jK2ygJDT4fPjIxqBG-oBrSw")

# ── পোস্টিং লিমিট ─────────────────────────────────────────────────────────
MAX_POST_PER_RUN     = 7        # প্রতি রানে সর্বোচ্চ পোস্ট
MIN_POST_PER_RUN     = 5        # প্রতি রানে সর্বনিম্ন চেষ্টা
RETENTION_HOURS      = 24       # ঘণ্টা পর মেসেজ অটো-ডিলিট
NEWS_AGE_LIMIT_HOURS = 36       # এর পুরোনো নিউজ বাদ (আগে ছিল ৪৮)
MAX_ENTRIES_PER_FEED = 15       # প্রতি ফিড থেকে সর্বোচ্চ এন্ট্রি
MAX_SAVED_LINKS      = 2000     # JSON-এ সর্বশেষ এত লিংক রাখা হবে
MAX_SAVED_TITLES     = 500      # JSON-এ সর্বশেষ এত টাইটেল রাখা হবে

# ── ডুপ্লিকেট থ্রেশহোল্ড ──────────────────────────────────────────────────
TITLE_SIM_THRESHOLD   = 0.60    # টাইটেল মিলের ন্যূনতম রেশিও
KEYWORD_SIM_THRESHOLD = 0.70    # কীওয়ার্ড মিলের ন্যূনতম রেশিও

# ── ভাইরাল স্কোরিং ──────────────────────────────────────────────────────────
MULTI_SOURCE_BONUS    = 30      # একাধিক সোর্সে থাকলে স্কোর বোনাস
FRESHNESS_BONUS_HOURS = 6       # এর মধ্যে প্রকাশিত হলে ফ্রেশনেস বোনাস
FRESHNESS_BONUS_PTS   = 20      # ফ্রেশনেস বোনাস পয়েন্ট

POST_DELAY_SEC = 2.0            # পোস্টের মাঝে বিরতি

OLD_YEAR_PATTERNS = ["/2021/", "/2022/", "/2023/", "/2024/"]

# ── Gemini কনফিগ ────────────────────────────────────────────────────────────
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"⚠️ Gemini Config Error: {e}")

RSS_FEEDS = {
    "BBC Bangla":  "https://feeds.bbci.co.uk/bengali/rss.xml",
    "Prothom Alo": "https://www.prothomalo.com/feed",
    "Al Jazeera":  "https://www.aljazeera.com/xml/rss/all.xml",
    "CNN":         "http://rss.cnn.com/rss/edition.rss",
    "Reuters":     "https://feeds.reuters.com/reuters/topNews",
    "DW Bangla":   "https://rss.dw.com/rdf/rss-ben-all",
}

DATA_FILE = "bot_data.json"

STOPWORDS = {
    'how','is','are','was','were','the','a','an','in','of','to','for','and',
    'with','on','at','this','that','from','by','what','why','news','breaking',
    'story','update','latest','says','said','new','after','over','as','its',
    'be','has','have','he','she','it','they','their','will','can','may',
    'could','would','should','but','not','no','so','if','then','than','into',
    'about','more','also','just','get','i','we','you','do','did','been','up',
    'two','one','three','amid','hit','back','against','war','dead','kill',
}

# ═══════════════════════════ ডেটা লোড / সেভ ══════════════════════════════════
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("sent_titles", [])
                data.setdefault("sent_links", [])
                data.setdefault("sent_topics", [])
                data.setdefault("telegram_messages", [])
                data.setdefault("title_fingerprints", [])
                return data
        except Exception as e:
            print(f"⚠️ Data load error: {e}")
    return {
        "sent_links":          [],
        "sent_titles":         [],
        "sent_topics":         [],
        "telegram_messages":   [],
        "title_fingerprints":  [],
    }

def save_data(data: dict):
    data["sent_links"]         = data.get("sent_links",         [])[-MAX_SAVED_LINKS:]
    data["sent_titles"]        = data.get("sent_titles",        [])[-MAX_SAVED_TITLES:]
    data["sent_topics"]        = data.get("sent_topics",        [])[-MAX_SAVED_TITLES:]
    data["title_fingerprints"] = data.get("title_fingerprints", [])[-MAX_SAVED_TITLES:]
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Data save error: {e}")

# ═══════════════════════════ URL নরমালাইজেশন ══════════════════════════════════
def normalize_url(url: str) -> str:
    url = url.split('?')[0].split('#')[0].rstrip('/')
    url = re.sub(r'^https?://(www\.)?', '', url, flags=re.IGNORECASE)
    return url.lower()

def url_fingerprint(url: str) -> str:
    return hashlib.md5(normalize_url(url).encode()).hexdigest()

def title_fingerprint(title: str) -> str:
    """টাইটেলের প্রথম ৬০ অক্ষরের MD5 — দ্রুত হার্ড ডুপ্লিকেট ধরতে।"""
    clean = re.sub(r'\W+', '', title.lower())[:60]
    return hashlib.md5(clean.encode()).hexdigest()

# ═══════════════════════════ সময় ফরম্যাট ════════════════════════════════════
def format_pub_time(entry) -> str:
    """
    RSS entry থেকে প্রকাশের সময় বের করে বাংলাদেশ সময়ে (UTC+6) ফরম্যাট করে।
    উদা: "আজ রাত ১১:৩০" বা "গতকাল দুপুর ২:১৫"
    """
    if not (hasattr(entry, 'published_parsed') and entry.published_parsed):
        return ""
    try:
        pub_ts  = time.mktime(entry.published_parsed)
        bd_time = datetime.fromtimestamp(pub_ts + 6 * 3600)   # UTC+6
        now_bd  = datetime.fromtimestamp(time.time() + 6 * 3600)

        hour   = bd_time.hour
        minute = bd_time.strftime("%M")

        # বাংলা AM/PM লেবেল
        if hour < 6:
            period = "রাত"
        elif hour < 12:
            period = "সকাল"
        elif hour == 12:
            period = "দুপুর"
        elif hour < 17:
            period = "বিকাল"
        elif hour < 20:
            period = "সন্ধ্যা"
        else:
            period = "রাত"

        hour12 = hour % 12 or 12
        time_str = f"{period} {hour12}:{minute}"

        # আজকের নাকি গতকালের?
        if bd_time.date() == now_bd.date():
            return f"⏰ আজ {time_str}"
        elif (now_bd.date() - bd_time.date()).days == 1:
            return f"⏰ গতকাল {time_str}"
        else:
            return f"⏰ {bd_time.strftime('%d %b')} {time_str}"
    except Exception:
        return ""

def get_pub_timestamp(entry) -> float:
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            return time.mktime(entry.published_parsed)
        except Exception:
            pass
    return time.time()

# ═══════════════════════════ পুরোনো খবর ফিল্টার ══════════════════════════════
def is_old_story(entry, url: str) -> bool:
    if any(yr in url for yr in OLD_YEAR_PATTERNS):
        return True
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            pub_ts = time.mktime(entry.published_parsed)
            if (time.time() - pub_ts) > (NEWS_AGE_LIMIT_HOURS * 3600):
                return True
        except Exception:
            pass
    return False

def freshness_score(entry) -> int:
    """নিউজ যত তাজা, স্কোর তত বেশি।"""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            age_hours = (time.time() - time.mktime(entry.published_parsed)) / 3600
            if age_hours <= FRESHNESS_BONUS_HOURS:
                return FRESHNESS_BONUS_PTS
            elif age_hours <= 12:
                return 10
        except Exception:
            pass
    return 0

# ══════════════════════════ ডুপ্লিকেট ডিটেকশন ════════════════════════════════
def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r'[^\w\s]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()

def text_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def is_title_duplicate(new_title: str, sent_titles: list):
    norm = normalize_title(new_title)
    for past in sent_titles:
        score = text_sim(norm, normalize_title(past))
        if score >= TITLE_SIM_THRESHOLD:
            return True, round(score, 2), past
    return False, 0.0, ""

def is_keyword_duplicate(new_kw: str, sent_topics: list):
    norm = new_kw.lower().strip()
    for past in sent_topics:
        score = text_sim(norm, past.lower().strip())
        if score >= KEYWORD_SIM_THRESHOLD:
            return True, round(score, 2), past
    return False, 0.0, ""

def escape_md(text: str) -> str:
    return re.sub(r'([_*\[\]`])', r'\\\1', text)

# ═══════════════════════ Gemini কীওয়ার্ড + ভাইরাল স্কোর ══════════════════
def get_gemini_analysis(headline: str) -> dict:
    """
    Gemini দিয়ে একসাথে:
    - ২-৩টি ইংরেজি কীওয়ার্ড (ফুটেজ সার্চের জন্য)
    - ভাইরাল স্কোর ০-১০০ (ইন্টারেস্ট/শেয়ারযোগ্যতা)
    - একলাইনে বাংলায় কেন গুরুত্বপূর্ণ
    রিটার্ন করে।
    """
    default = {
        "keywords":      _fallback_keywords(headline),
        "viral_score":   50,
        "importance_bn": "",
    }
    if not GEMINI_API_KEY:
        return default
    try:
        model  = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            f"Analyze this news headline and respond ONLY as valid JSON, no markdown:\n"
            f"Headline: \"{headline}\"\n\n"
            f"Respond with exactly this structure:\n"
            f"{{\n"
            f'  "keywords": "2-3 English nouns for stock footage search",\n'
            f'  "viral_score": <integer 0-100 based on global impact and shareability>,\n'
            f'  "importance_bn": "এক বাক্যে বাংলায় কেন এই খবর গুরুত্বপূর্ণ"\n'
            f"}}\n\n"
            f"Rules for keywords: only nouns/proper nouns, no stopwords, space-separated.\n"
            f"Rules for viral_score: 80-100=breaking/global crisis, 60-79=major event, "
            f"40-59=important, below 40=routine."
        )
        resp = model.generate_content(prompt)
        raw  = resp.text.strip()
        raw  = re.sub(r'^```json|^```|```$', '', raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)

        keywords = data.get("keywords", "").strip()
        words    = [w for w in keywords.split() if w.lower() not in STOPWORDS and len(w) > 2]
        keywords = " ".join(words[:3]) if words else _fallback_keywords(headline)

        return {
            "keywords":      keywords,
            "viral_score":   int(data.get("viral_score", 50)),
            "importance_bn": data.get("importance_bn", "").strip(),
        }
    except Exception as e:
        print(f"    ⚠️ Gemini analysis error: {e}")
        return default

def _fallback_keywords(headline: str) -> str:
    words = [w for w in re.findall(r'[a-zA-Z]{3,}', headline) if w.lower() not in STOPWORDS]
    return " ".join(words[:3]) if len(words) >= 2 else "world event"

# ══════════════════════════ টেলিগ্রাম মেসেজ ══════════════════════════════════
def send_telegram(item: dict, analysis: dict) -> int | None:
    title       = item["title"]
    link        = item["link"]
    source      = item["source"]
    pub_time    = item.get("pub_time", "")
    sources_str = item.get("sources_str", source)   # "CNN | BBC" etc.
    keywords    = analysis["keywords"]
    viral_score = analysis["viral_score"]
    importance  = analysis.get("importance_bn", "")

    clean_kw  = " ".join(keywords.split())
    enc_kw    = urllib.parse.quote(clean_kw)
    safe_title = escape_md(title)

    # ভাইরাল স্কোর ইমোজি
    if viral_score >= 80:
        score_emoji = "🔥🔥🔥"
    elif viral_score >= 60:
        score_emoji = "🔥🔥"
    elif viral_score >= 40:
        score_emoji = "🔥"
    else:
        score_emoji = "📌"

    # মাল্টি-সোর্স ব্যাজ
    multi_badge = ""
    if "|" in sources_str:
        multi_badge = "\n✅ *একাধিক সোর্সে নিশ্চিত:* " + escape_md(sources_str)

    script_prompt = (
        f"আপনি একজন আন্তর্জাতিক নিউজ চ্যানেলের এক্সিকিউটিভ প্রডিউসার। "
        f"নিচের শিরোনাম থেকে স্টুডিওর জন্য প্রফেশনাল বাংলা নিউজ প্যাকেজ স্ক্রিপ্ট তৈরি করুন:\n\n"
        f"সংবাদ: \"{title}\"\n\n"
        f"ফরম্যাট:\n"
        f"১. [ইউটিউব শিরোনাম]: ক্যাচি ও আকর্ষণীয় টাইটেল।\n"
        f"২. [অ্যাঙ্কর ইনট্রো]: ২০ সেকেন্ডের প্রেজেন্টার ইনট্রো।\n"
        f"৩. [ভয়েসওভার স্ক্রিপ্ট]: ৯০-১১০ শব্দের ভয়েসওভার, ব্র্যাকেটে বি-রোল নির্দেশ দিন।\n"
        f"৪. [আউটরো ও হ্যাশট্যাগ]: সাইন-অফ ও ৩টি হ্যাশট্যাগ।"
    )
    enc_script = urllib.parse.quote(script_prompt)

    keyboard = {
        "inline_keyboard": [
            [{"text": "🎙️ স্টুডিও স্ক্রিপ্ট বানান (ChatGPT)",
              "url": f"https://chatgpt.com/?q={enc_script}"}],
            [
                {"text": "🖼️ Google Images",
                 "url": f"https://www.google.com/search?tbm=isch&q={enc_kw}"},
                {"text": "🎬 Envato Elements",
                 "url": f"https://elements.envato.com/all-items/{enc_kw}"},
            ],
            [
                {"text": "📰 Reuters Footage",
                 "url": f"https://www.reuters.com/site-search/?query={enc_kw}"},
                {"text": "🎥 Pexels Stock",
                 "url": f"https://www.pexels.com/search/{enc_kw}/"},
            ],
            [
                {"text": "📺 পূর্ণ সংবাদ পড়ুন",
                 "url": link},
            ],
        ]
    }

    # ── মেসেজ বডি ───────────────────────────────────────────────────────────
    importance_line = f"\n💡 _{escape_md(importance)}_" if importance else ""

    text = (
        f"{score_emoji} *ভাইরাল স্কোর: {viral_score}/100* | {escape_md(source)}\n"
        f"{pub_time}\n"
        f"{multi_badge}\n\n"
        f"📰 *শিরোনাম:*\n{safe_title}"
        f"{importance_line}\n\n"
        f"🔑 *ফুটেজ ট্যাগ:* `{clean_kw}`\n"
        f"💡 _নিচের বাটন দিয়ে স্ক্রিপ্ট ও ফুটেজ নিন_"
    )

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":                  CHAT_ID,
        "text":                     text,
        "parse_mode":               "Markdown",
        "disable_web_page_preview": False,
        "reply_markup":             keyboard,
    }
    try:
        res = requests.post(api_url, json=payload, timeout=10)
        if res.status_code == 200:
            return res.json().get("result", {}).get("message_id")
        # ── parse_mode error হলে plain text এ retry ─────────────────────────
        if res.status_code == 400 and "parse" in res.text.lower():
            payload["parse_mode"] = ""
            payload["text"] = (
                f"[ভাইরাল স্কোর: {viral_score}/100] {source}\n"
                f"{pub_time}\n\n"
                f"{title}\n\n"
                f"ট্যাগ: {clean_kw}"
            )
            res2 = requests.post(api_url, json=payload, timeout=10)
            if res2.status_code == 200:
                return res2.json().get("result", {}).get("message_id")
        print(f"    ⚠️ Telegram {res.status_code}: {res.text[:200]}")
    except Exception as e:
        print(f"    ⚠️ Telegram exception: {e}")
    return None

# ═══════════════════════ পুরোনো মেসেজ ডিলিট ══════════════════════════════════
def cleanup_old_messages(data: dict) -> dict:
    cutoff    = RETENTION_HOURS * 3600
    remaining = []
    deleted   = 0
    for item in data.get("telegram_messages", []):
        age = time.time() - item.get("timestamp", 0)
        if age > cutoff:
            msg_id = item.get("message_id")
            if msg_id:
                try:
                    res = requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                        json={"chat_id": CHAT_ID, "message_id": msg_id},
                        timeout=5,
                    )
                    if res.status_code == 200:
                        deleted += 1
                        print(f"    🗑️  Deleted 24h+ message (ID: {msg_id})")
                    elif res.status_code == 400:
                        # Telegram এ মেসেজ আগেই নেই — তবু লিস্ট থেকে বাদ দাও
                        deleted += 1
                except Exception as e:
                    print(f"    ⚠️ Delete error (ID: {msg_id}): {e}")
                    remaining.append(item)   # ব্যর্থ হলে পরের রানে আবার চেষ্টা
        else:
            remaining.append(item)
    if deleted:
        print(f"  🗑️  মোট {deleted}টি পুরোনো মেসেজ ডিলিট হয়েছে।")
    data["telegram_messages"] = remaining
    return data

# ════════════════════════════════ মূল লজিক ════════════════════════════════════
def process_news():
    data = load_data()
    data = cleanup_old_messages(data)

    # ── ইন-মেমরি লুকআপ স্ট্রাকচার ────────────────────────────────────────
    sent_fp_set    = {url_fingerprint(u)   for u in data.get("sent_links",          [])}
    sent_tfp_set   = set(data.get("title_fingerprints", []))
    sent_titles    = data.get("sent_titles", [])
    sent_topics    = data.get("sent_topics", [])

    # ── RSS ফিড সংগ্রহ ─────────────────────────────────────────────────────
    # key: normalize_title → list of {source, ...}
    title_map: dict[str, list[dict]] = {}

    for source, feed_url in RSS_FEEDS.items():
        try:
            feed  = feedparser.parse(feed_url, agent="Mozilla/5.0")
            taken = 0
            for entry in feed.entries:
                if taken >= MAX_ENTRIES_PER_FEED:
                    break
                if not getattr(entry, 'link', None) or not getattr(entry, 'title', None):
                    continue
                norm_url = normalize_url(entry.link)
                if is_old_story(entry, norm_url):
                    continue

                article = {
                    "source":    source,
                    "title":     entry.title.strip(),
                    "link":      entry.link,
                    "norm":      norm_url,
                    "pub_time":  format_pub_time(entry),
                    "pub_ts":    get_pub_timestamp(entry),
                    "fresh_pts": freshness_score(entry),
                    "entry":     entry,
                }

                # একই/মিলসই টাইটেল → গ্রুপ করো (মাল্টি-সোর্স ডিটেকশন)
                nt = normalize_title(entry.title)
                matched_key = None
                for existing_key in title_map:
                    if text_sim(nt, existing_key) >= 0.70:
                        matched_key = existing_key
                        break
                if matched_key:
                    title_map[matched_key].append(article)
                else:
                    title_map[nt] = [article]
                taken += 1
        except Exception as e:
            print(f"  ⚠️ Feed error ({source}): {e}")

    # ── প্রতিটি গ্রুপ থেকে সেরা আর্টিকেল বাছো ─────────────────────────────
    candidates = []
    for nt, group in title_map.items():
        # গ্রুপের মধ্যে সবচেয়ে তাজাটা বেছে নাও
        best = max(group, key=lambda x: x["pub_ts"])
        sources_list = list({g["source"] for g in group})
        best["multi_source"]  = len(sources_list) > 1
        best["sources_str"]   = " | ".join(sources_list)
        # ভাইরাল বেস স্কোর = ফ্রেশনেস + মাল্টি-সোর্স বোনাস (Gemini স্কোর পরে যোগ হবে)
        best["base_score"] = best["fresh_pts"] + (MULTI_SOURCE_BONUS if best["multi_source"] else 0)
        candidates.append(best)

    print(f"\n📡 ইউনিক স্টোরি: {len(candidates)} টি\n{'─'*60}")

    # ── ফিল্টার + স্কোরিং ──────────────────────────────────────────────────
    filtered = []
    dup_url = dup_title = dup_kw_pre = 0

    for item in candidates:
        title = item["title"]
        link  = item["link"]
        fp    = url_fingerprint(link)
        tfp   = title_fingerprint(title)

        # ফিল্টার ১: হুবহু URL
        if fp in sent_fp_set:
            dup_url += 1
            continue

        # ফিল্টার ২: হুবহু টাইটেল (দ্রুত MD5 চেক)
        if tfp in sent_tfp_set:
            dup_title += 1
            continue

        # ফিল্টার ৩: টাইটেল সাদৃশ্য (fuzzy)
        is_dup, score, _ = is_title_duplicate(title, sent_titles)
        if is_dup:
            dup_title += 1
            print(f"  ⏭️  [Title {score}]  {title[:55]}")
            continue

        item["fp"]  = fp
        item["tfp"] = tfp
        filtered.append(item)

    print(f"  ✂️  ডুপ্লিকেট বাদ → URL:{dup_url} টাইটেল:{dup_title}")
    print(f"  📋 বাকি ক্যান্ডিডেট: {len(filtered)} টি\n{'─'*60}")

    # ── Gemini বিশ্লেষণ + ভাইরাল স্কোর সাজানো ──────────────────────────────
    analyzed = []
    for item in filtered:
        analysis = get_gemini_analysis(item["title"])

        # কীওয়ার্ড ডুপ্লিকেট চেক
        is_dup, score, _ = is_keyword_duplicate(analysis["keywords"], sent_topics)
        if is_dup:
            dup_kw_pre += 1
            print(f"  ⏭️  [KW {score}] {item['title'][:50]}")
            continue

        total_score = item["base_score"] + analysis["viral_score"]
        analyzed.append((total_score, item, analysis))

    # ভাইরাল স্কোর অনুযায়ী নামানো ক্রমে সাজাও
    analyzed.sort(key=lambda x: x[0], reverse=True)

    print(f"  🎯 পোস্ট করার যোগ্য: {len(analyzed)} টি")
    print(f"  📤 সর্বোচ্চ পোস্ট এই রানে: {MAX_POST_PER_RUN}")
    print(f"{'─'*60}")

    posted = 0
    for total_score, item, analysis in analyzed:
        if posted >= MAX_POST_PER_RUN:
            break

        title = item["title"]
        link  = item["link"]

        print(f"  ✅ [স্কোর:{total_score}] [{item['sources_str']}]")
        print(f"     {title[:70]}")
        print(f"     Tag: {analysis['keywords']} | {item['pub_time']}")

        msg_id = send_telegram(item, analysis)

        if msg_id:
            data["sent_links"].append(link)
            data["sent_titles"].append(title)
            data["sent_topics"].append(analysis["keywords"])
            data["title_fingerprints"].append(item["tfp"])
            sent_fp_set.add(item["fp"])
            sent_tfp_set.add(item["tfp"])
            sent_titles.append(title)
            sent_topics.append(analysis["keywords"])
            data["telegram_messages"].append({
                "message_id": msg_id,
                "timestamp":  time.time(),
            })
            posted += 1
            time.sleep(POST_DELAY_SEC)

    print(f"\n{'═'*60}")
    print(
        f"📊 রিপোর্ট → পোস্ট: {posted} | "
        f"URL ডুপ: {dup_url} | টাইটেল ডুপ: {dup_title} | "
        f"KW ডুপ: {dup_kw_pre}"
    )
    save_data(data)

# ═══════════════════════════════ এন্ট্রি পয়েন্ট ════════════════════════════
def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN বা CHAT_ID সেট করা নেই!")
        return
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY নেই — fallback keyword ব্যবহার হবে।")
    print(f"🚀 News Bot রান শুরু — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    process_news()
    print("✅ রান শেষ।")

if __name__ == "__main__":
    main()
