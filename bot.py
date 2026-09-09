import os
import sys
import re
import time
import json
import hashlib
import feedparser
import requests
import urllib.parse
from difflib import SequenceMatcher
from datetime import datetime, timezone

# ── Gemini SDK ইমপোর্ট (আধুনিক google-genai অফিসিয়াল প্যাকেজ + ব্যাকওয়ার্ড কম্প্যাটিবিলিটি) ──
USE_MODERN_GENAI = False
_CLIENT = None

try:
    from google import genai
    from google.genai import types
    USE_MODERN_GENAI = True
except ImportError:
    try:
        import warnings
        # পুরোনো প্যাকেজ থাকলে অপ্রয়োজনীয় FutureWarning লুকানো
        warnings.filterwarnings("ignore", category=FutureWarning)
        import google.generativeai as legacy_genai
        USE_MODERN_GENAI = False
    except ImportError:
        pass

# ═══════════════════════════════ কনফিগারেশন ══════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN")
CHAT_ID        = os.environ.get("CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ── পোস্টিং ও শিডিউলিং লিমিট ──────────────────────────────────────────────
MAX_POST_PER_RUN       = 7     # প্রতি সাইকেলে মোট সর্বোচ্চ পোস্ট
MIN_POST_PER_RUN       = 4     # প্রতি সাইকেলে ন্যূনতম পোস্ট
MAX_POSTS_PER_SOURCE   = 2     # কোনো একক সোর্স (যেমন আল জাজিরা) একা সব পোস্ট দখল করতে পারবে না!
RETENTION_HOURS        = 24    # ২৪ ঘণ্টা পর টেলিগ্রাম পোস্ট অটো ডিলিট হবে
CRAWL_INTERVAL_MINUTES = 90    # এক্সাক্ট ১.৫ ঘণ্টা (৯০ মিনিট) পর পর ক্রল হবে
NEWS_AGE_LIMIT_HOURS   = 10    # ১০ ঘণ্টার বেশি পুরোনো সংবাদ বাদ
MAX_ENTRIES_PER_FEED   = 25
MAX_SAVED_LINKS        = 2000
MAX_SAVED_TITLES       = 500

# ── ডুপ্লিকেট থ্রেশহোল্ড ──────────────────────────────────────────────────
TITLE_SIM_THRESHOLD   = 0.60
KEYWORD_SIM_THRESHOLD = 0.70

# ── ভাইরাল স্কোরিং ──────────────────────────────────────────────────────────
MULTI_SOURCE_BONUS    = 30
FRESHNESS_BONUS_HOURS = 2
FRESHNESS_BONUS_PTS   = 60
POST_DELAY_SEC        = 2.0

OLD_YEAR_PATTERNS = ["/2021/", "/2022/", "/2023/", "/2024/"]

# ── রিয়েল ব্রাউজার হেডার (প্রথম আলো বা বিবিসি যেন রিকোয়েস্ট ব্লক না করে) ───
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml; q=0.9, */*; q=0.8"
}

# ── Gemini কনফিগ ────────────────────────────────────────────────────────────
if GEMINI_API_KEY:
    if USE_MODERN_GENAI:
        try:
            _CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        except Exception as e:
            print(f"⚠️ Modern Google-GenAI init error: {e}")
    else:
        try:
            legacy_genai.configure(api_key=GEMINI_API_KEY)
            _CLIENT = legacy_genai
        except Exception as e:
            print(f"⚠️ Legacy Gemini Config Error: {e}")

# ── মাল্টি-সোর্স আরএসএস ফিডস (বাংলা ও আন্তর্জাতিক সংবাদ সোর্স) ─────────────
RSS_FEEDS = {
    "Prothom Alo": "https://www.prothomalo.com/feed",
    "BBC Bangla":  "https://feeds.bbci.co.uk/bengali/rss.xml",
    "DW Bangla":   "https://rss.dw.com/rdf/rss-ben-all",
    "Al Jazeera":  "https://www.aljazeera.com/xml/rss/all.xml",
    "CNN":         "http://rss.cnn.com/rss/edition.rss",
    "BBC World":   "https://feeds.bbci.co.uk/news/world/rss.xml",
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

# ══════════════════════════════════════════════════════════════════════════════
# বাংলা সংবাদের জন্য স্মার্ট কীওয়ার্ড ও স্টক ফুটেজ ট্যাগ ডিকশনারি
# (বাংলা হেডলাইন হলেও কখনো "world event" আসবে না)
# ══════════════════════════════════════════════════════════════════════════════
BANGLA_KEYWORD_MAP = {
    "গাজা": "Gaza war ruins",
    "হামাস": "Hamas conflict soldiers",
    "ইসরায়েল": "Israel military strike",
    "ইসরায়েলি": "Israel military missile",
    "লেবানন": "Lebanon Beirut strike",
    "হিজবুল্লাহ": "Hezbollah rocket strike",
    "ইউক্রেন": "Ukraine war front",
    "রাশিয়া": "Russia military missile",
    "পুতিন": "Vladimir Putin Kremlin",
    "যুক্তরাষ্ট্র": "US White House",
    "চীন": "China military Beijing",
    "ভারত": "India Delhi politics",
    "পাকিস্তান": "Pakistan politics Islamabad",
    "ঢাকা": "Dhaka Bangladesh city",
    "চট্টগ্রাম": "Chittagong port Bangladesh",
    "সিলেট": "Sylhet flood rain",
    "নির্বাচন": "election vote ballot",
    "বিক্ষোভ": "street protest crowd",
    "আন্দোলন": "crowd protest rally",
    "সংঘর্ষ": "police clash teargas",
    "গুলি": "shooting police clash",
    "নিহত": "killed casualty crime",
    "মৃত্যু": "ambulance hospital emergency",
    "আহত": "injured hospital medical",
    "গ্রেপ্তার": "police arrest handcuffs",
    "আদালত": "courtroom judge gavel",
    "সরকার": "parliament cabinet government",
    "প্রধানমন্ত্রী": "prime minister speech",
    "ভূমিকম্প": "earthquake rubble destruction",
    "বন্যা": "flood rescue water",
    "ঘূর্ণিঝড়": "cyclone storm devastation",
    "ঝড়": "storm heavy rain",
    "আগুন": "firefighters building flame",
    "দুর্ঘটনা": "highway road crash",
    "অর্থনীতি": "economy stock market",
    "মুদ্রাস্ফীতি": "price hike grocery market",
    "ডলার": "dollar currency bank",
    "তেল": "fuel oil refinery",
    "বিমান": "airplane airport flight",
    "ক্রিকেট": "cricket stadium match",
    "ফুটবল": "football soccer stadium",
    "বিশ্বকাপ": "world cup trophy",
    "হাসপাতাল": "hospital medical emergency",
    "ডেঙ্গু": "dengue mosquito hospital",
}

# ══════════════════════════════════════════════════════════════════════════════
# হিউম্যান-রাইটিং টিভি স্ক্রিপ্ট ফরম্যাট (কোনো স্পনসর বা Walton AC থাকবে না)
# ══════════════════════════════════════════════════════════════════════════════
SCRIPT_FORMAT_TEMPLATE = """আপনি একজন বাস্তব অভিজ্ঞ বাংলা টিভি সাংবাদিক এবং ইউটিউব নিউজ প্রেজেন্টার।
নিচের সংবাদটি নিয়ে মানুষের মুখে বলা সহজ, প্রাঞ্জল ও আকর্ষণীয় একটি টিভি স্ক্রিপ্ট লিখুন।
কোনো স্পনসর (যেমন Walton AC বা বিজ্ঞাপন) থাকবে না। কোনো কৃত্রিম রোবোটিক বা কঠিন বিশ্লেষণ করবেন না।

সংবাদ: "{headline}"

━━━━━━━━━━━━━ হিউম্যান আউটপুট ফরম্যাট ━━━━━━━━━━━━━

থাম: [ইউটিউব থাম্বনেইলের জন্য ৫-৭ শব্দের তীব্র কৌতূহলী হুক]

হেড: [আকর্ষণীয় ও নাটকীয় প্রশ্নবোধক/কৌতূহলী শিরোনাম]

[One crisp, factual English summary line for international context]

ডেস
[উপস্থাপকের ১৫-২০ সেকেন্ডের সাবলীল সূচনা। সহজ ও কথ্য ভাষায় দর্শককে সরাসরি বলবেন, যেমন: "দর্শক, এইমাত্র পাওয়া জরুরি খবরে জানা যাচ্ছে..."]

ভয়েসওভার
[৬০-৮০ শব্দের বাস্তবসম্মত, প্রাঞ্জল ও কথ্য ভয়েসওভার। কোনো অপ্রয়োজনীয় জটিল বাক্য নয়। প্রতিটি প্যারার পর লিখুন "আপস…" এবং ছোট বি-রোল নির্দেশ যেমন: [বি-রোল: সংশ্লিষ্ট ঘটনার ফুটেজ]]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

নিয়মাবলী:
- স্পনসর বা বিজ্ঞাপনী কোনো শব্দ লিখবেন না।
- ভয়েসওভারের প্যারাশেষে "আপস…" ও ব্র্যাকেটে [বি-রোল] নির্দেশ রাখুন।
- লেখাটি যেন শুনে মনে হয় রক্ত-মাংসের একজন পেশাদার সাংবাদিক নিজের ভাষায় উপস্থাপন করছেন।"""

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
                data.setdefault("sent_content_fps", [])
                return data
        except Exception as e:
            print(f"⚠️ Data load error: {e}")
    return {
        "sent_links":         [],
        "sent_titles":        [],
        "sent_topics":        [],
        "telegram_messages":  [],
        "title_fingerprints": [],
        "sent_content_fps":   [],
    }

def save_data(data: dict):
    data["sent_links"]         = data.get("sent_links",         [])[-MAX_SAVED_LINKS:]
    data["sent_titles"]        = data.get("sent_titles",        [])[-MAX_SAVED_TITLES:]
    data["sent_topics"]        = data.get("sent_topics",        [])[-MAX_SAVED_TITLES:]
    data["title_fingerprints"] = data.get("title_fingerprints", [])[-MAX_SAVED_TITLES:]
    data["sent_content_fps"]   = data.get("sent_content_fps",   [])[-MAX_SAVED_TITLES:]
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
    clean = re.sub(r'\W+', '', title.lower())[:60]
    return hashlib.md5(clean.encode()).hexdigest()

def content_fingerprint(title: str) -> str:
    words = re.findall(r'[a-zA-Z\u0980-\u09FF]+', title.lower())
    key   = " ".join(words[:8])
    return hashlib.md5(key.encode()).hexdigest()

# ═══════════════════════════ সময় ও ফিল্টারিং ═════════════════════════════════
def format_pub_time(entry) -> str:
    if not (hasattr(entry, 'published_parsed') and entry.published_parsed):
        return ""
    try:
        pub_ts  = time.mktime(entry.published_parsed)
        bd_time = datetime.fromtimestamp(pub_ts + 6 * 3600)
        now_bd  = datetime.fromtimestamp(time.time() + 6 * 3600)

        hour   = bd_time.hour
        minute = bd_time.strftime("%M")

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
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            age_hours = (time.time() - time.mktime(entry.published_parsed)) / 3600
            if age_hours <= 2:
                return 60
            elif age_hours <= 4:
                return 40
            elif age_hours <= 6:
                return 25
            elif age_hours <= 10:
                return 10
        except Exception:
            pass
    return 0

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

# ══════════════════════════════════════════════════════════════════════════════
# স্মার্ট ফলব্যাক ফাংশন (Never returns "world event")
# ══════════════════════════════════════════════════════════════════════════════
def _fallback_keywords(headline: str) -> str:
    # ১. বাংলা টপিক ম্যাপিং চেক করো
    for bn_key, en_tag in BANGLA_KEYWORD_MAP.items():
        if bn_key in headline:
            return en_tag

    # ২. হেডলাইনে যদি ইংরেজি শব্দ থাকে
    words = [w for w in re.findall(r'[a-zA-Z]{3,}', headline) if w.lower() not in STOPWORDS]
    if len(words) >= 2:
        return " ".join(words[:3])
    elif len(words) == 1:
        return f"{words[0]} news event"

    # ৩. প্রসঙ্গভিত্তিক ট্যাগ
    if any(term in headline for term in ["হত্যা", "খুন", "লাশ", "মরদেহ", "গুলি"]):
        return "crime police investigation"
    if any(term in headline for term in ["বৃষ্টি", "বৃষ্টিপাত", "বন্যা", "ভারী"]):
        return "heavy monsoon rain flood"
    if any(term in headline for term in ["বিশ্ববিদ্যালয়", "কলেজ", "শিক্ষার্থী", "পরীক্ষা"]):
        return "student university protest"

    return "breaking international news"

# ═══════════════════════════ Gemini কীওয়ার্ড + ভাইরাল স্কোর ════════════════════
def get_gemini_analysis(headline: str) -> dict:
    default = {
        "keywords":      _fallback_keywords(headline),
        "viral_score":   50,
        "importance_bn": "দর্শকদের জন্য এই সংবাদের প্রেক্ষাপট তাৎপর্যপূর্ণ।",
    }
    if not GEMINI_API_KEY:
        return default

    # ৫-৩ হাই ডিমান্ড বা সাময়িক ট্রাফিক সামলাতে একাধিক মডেল ট্রাই করা হবে
    CANDIDATE_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    prompt = (
        f"You are a senior news editor for a Bangladeshi YouTube channel.\n"
        f"Analyze this news headline: \"{headline}\"\n\n"
        f"IMPORTANT RULES:\n"
        f"1. 'keywords' MUST ALWAYS be 2-3 specific English nouns/proper nouns suitable for searching stock footage on Getty, Reuters, Envato, Pexels (e.g. 'Gaza protest crowd', 'Dhaka traffic rain', 'military tank strike').\n"
        f"   EVEN IF THE HEADLINE IS IN BENGALI, TRANSLATE CORE CONCEPTS INTO CONCISE ENGLISH STOCK FOOTAGE KEYWORDS. NEVER output 'world event'.\n"
        f"2. 'viral_score': integer 0-100:\n"
        f"   - 90-100 = war, assassination, natural disaster, global emergency\n"
        f"   - 70-89  = major political event, economic crisis, viral protest\n"
        f"   - 50-69  = important national/international news\n"
        f"   - 30-49  = routine updates, sports, culture\n"
        f"   - 0-29   = minor local news\n"
        f"3. 'importance_bn': exactly 1 sentence in Bengali explaining why it matters to the viewers.\n\n"
        f"Respond ONLY as valid JSON (no markdown, no backticks, no explanation):\n"
        f"{{\n"
        f'  "keywords": "2-3 English nouns",\n'
        f'  "viral_score": 75,\n'
        f'  "importance_bn": "দর্শকের জন্য কেন গুরুত্বপূর্ণ"\n'
        f"}}"
    )

    generation_config = {
        "response_mime_type": "application/json",
        "temperature": 0.2
    }

    for model_name in CANDIDATE_MODELS:
        try:
            raw = ""
            if USE_MODERN_GENAI and _CLIENT:
                resp = _CLIENT.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                raw = resp.text.strip() if resp and resp.text else ""
            elif _CLIENT:
                model = _CLIENT.GenerativeModel(model_name)
                resp  = model.generate_content(prompt, generation_config=generation_config)
                raw   = resp.text.strip() if resp and resp.text else ""
            else:
                break

            match = re.search(r'\{.*\}', raw, re.DOTALL)
            data = json.loads(match.group(0)) if match else json.loads(raw)

            keywords = data.get("keywords", "").strip()

            clean_words = []
            for w in re.findall(r'[a-zA-Z]+', keywords):
                if w.lower() not in STOPWORDS and len(w) > 1:
                    clean_words.append(w)

            final_keywords = " ".join(clean_words[:3]) if clean_words else _fallback_keywords(headline)

            return {
                "keywords":      final_keywords,
                "viral_score":   int(data.get("viral_score", 50)),
                "importance_bn": data.get("importance_bn", "").strip() or default["importance_bn"],
            }
        except Exception as e:
            err_str = str(e)
            if "503" in err_str or "high demand" in err_str.lower() or "unavailable" in err_str.lower():
                print(f"    ⚠️ Gemini {model_name} সাময়িক হাই ডিমান্ডে (503), পরবর্তী ব্যাকআপ মডেলে ট্রাই করা হচ্ছে...")
                time.sleep(1)
                continue
            else:
                print(f"    ⚠️ Gemini {model_name} রেসপন্স দেয়নি ({err_str[:60]})...")
                continue

    # সব মডেল ব্যর্থ হলেও কোনো ক্র্যাশ হবে না — স্মার্ট মাল্টিলিঙ্গুয়াল ডিকশনারি কাজ করবে
    print("    💡 Gemini হাই ডিমান্ডে থাকায় স্মার্ট মাল্টিলিঙ্গুয়াল ফলব্যাক ইঞ্জিন দিয়ে ফুটেজ ট্যাগ তৈরি হয়েছে।")
    return default

# ══════════════════════════ টেলিগ্রাম মেসেজ ══════════════════════════════════
def send_telegram(item: dict, analysis: dict) -> tuple[int | None, int | None]:
    """
    টেলিগ্রামে প্রিভিউ ও টুলস বাটন মেসেজ পাঠায়।
    দুটো মেসেজের আইডি-ই রিটার্ন করে যাতে ২৪ ঘণ্টা পর দুটিই অটো ডিলিট হতে পারে!
    """
    title       = item["title"]
    link        = item["link"]
    source      = item["source"]
    pub_time    = item.get("pub_time", "")
    sources_str = item.get("sources_str", source)
    keywords    = analysis["keywords"]
    viral_score = analysis["viral_score"]
    importance  = analysis.get("importance_bn", "")

    clean_kw   = " ".join(keywords.split())
    enc_kw     = urllib.parse.quote(clean_kw)
    safe_title = escape_md(title)

    if viral_score >= 80:
        score_emoji = "🔥🔥🔥"
    elif viral_score >= 60:
        score_emoji = "🔥🔥"
    elif viral_score >= 40:
        score_emoji = "🔥"
    else:
        score_emoji = "📌"

    multi_badge = ""
    if "|" in sources_str:
        multi_badge = "\n✅ *একাধিক সোর্সে নিশ্চিত:* " + escape_md(sources_str)

    script_prompt = SCRIPT_FORMAT_TEMPLATE.format(headline=title)
    enc_script    = urllib.parse.quote(script_prompt)

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

    importance_line = f"\n💡 _{escape_md(importance)}_" if importance else ""

    text_preview = (
        f"{score_emoji} *ভাইরাল স্কোর: {viral_score}/100* | {escape_md(source)}\n"
        f"{pub_time}"
        f"{multi_badge}\n\n"
        f"📰 *শিরোনাম:*\n{safe_title}"
        f"{importance_line}\n\n"
        f"🔗 {link}\n\n"
        f"🔑 *ফুটেজ ট্যাগ:* `{clean_kw}`"
    )

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    def _send(text, parse_mode="Markdown", reply_id=None, markup=None):
        p = {
            "chat_id":                  CHAT_ID,
            "text":                     text,
            "parse_mode":               parse_mode,
            "disable_web_page_preview": False,
        }
        if reply_id:
            p["reply_to_message_id"] = reply_id
        if markup:
            p["reply_markup"]             = markup
            p["disable_web_page_preview"] = True
        try:
            res = requests.post(api_url, json=p, timeout=10)
            if res.status_code == 200:
                return res.json().get("result", {}).get("message_id")
            if res.status_code == 400 and "parse" in res.text.lower() and parse_mode:
                p["parse_mode"] = ""
                res2 = requests.post(api_url, json=p, timeout=10)
                if res2.status_code == 200:
                    return res2.json().get("result", {}).get("message_id")
            print(f"    ⚠️ Telegram {res.status_code}: {res.text[:150]}")
        except Exception as e:
            print(f"    ⚠️ Telegram exception: {e}")
        return None

    preview_msg_id = _send(text_preview)
    if not preview_msg_id:
        return (None, None)

    time.sleep(0.5)
    tools_msg_id = _send("🔽 *স্ক্রিপ্ট ও ফুটেজ টুলস:*", reply_id=preview_msg_id, markup=keyboard)

    return (preview_msg_id, tools_msg_id)

# ═══════════════════════ ২৪ ঘণ্টার পুরোনো মেসেজ অটো ডিলিট ════════════════════
def cleanup_old_messages(data: dict) -> dict:
    """
    ২৪ ঘণ্টার বেশি পুরোনো সমস্ত টেলিগ্রাম পোস্ট ও বাটন মেসেজ স্বয়ংক্রিয়ভাবে মুছে ফেলে।
    """
    cutoff    = RETENTION_HOURS * 3600  # 24 hours in seconds
    now       = time.time()
    remaining = []
    deleted   = 0

    messages_list = data.get("telegram_messages", [])
    if not messages_list:
        return data

    for item in messages_list:
        age_sec = now - item.get("timestamp", 0)

        # সাপোর্ট: নতুন 'message_ids' লিস্ট এবং পুরোনো 'message_id'
        msg_ids = item.get("message_ids") or ([item.get("message_id")] if item.get("message_id") else [])

        if age_sec >= cutoff:
            for mid in msg_ids:
                if not mid:
                    continue
                try:
                    res = requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                        json={"chat_id": CHAT_ID, "message_id": mid},
                        timeout=7,
                    )
                    if res.status_code == 200:
                        deleted += 1
                        print(f"    🗑️ ২৪ ঘণ্টা পার — টেলিগ্রাম মেসেজ ডিলিট (ID: {mid})")
                    elif res.status_code == 400:
                        # মেসেজ হয়তো আগেই ব্যবহারকারী ডিলিট করেছেন
                        deleted += 1
                except Exception as e:
                    print(f"    ⚠️ ডিলিট ব্যর্থ (ID: {mid}): {e}")
        else:
            remaining.append(item)

    if deleted > 0:
        print(f"  🗑️ ২৪ ঘণ্টার পুরোনো মোট {deleted}টি মেসেজ টেলিগ্রাম চ্যানেল থেকে অটো-ডিলিট করা হয়েছে।")

    data["telegram_messages"] = remaining
    return data

# ════════════════════════════════ মূল লজিক ════════════════════════════════════
def process_news():
    data = load_data()

    # ১. সাইকেলের শুরুতেই ২৪ ঘণ্টার পুরোনো মেসেজ ডিলিট চালানো
    data = cleanup_old_messages(data)

    sent_fp_set      = {url_fingerprint(u) for u in data.get("sent_links", [])}
    sent_tfp_set     = set(data.get("title_fingerprints", []))
    sent_cfp_set     = set(data.get("sent_content_fps", []))
    sent_titles      = data.get("sent_titles", [])
    sent_topics      = data.get("sent_topics", [])

    title_map: dict[str, list[dict]] = {}

    # ২. সমস্ত ফিড থেকে লাইভ সংবাদ সংগ্রহ (User-Agent সহ যাতে কোনো ফিড ব্লক না হয়)
    print("\n📡 সমস্ত আরএসএস সোর্স ক্রল করা হচ্ছে (প্রথম আলো, বিবিসি বাংলা, DW, সিএনএন, আল জাজিরা)...")
    for source, feed_url in RSS_FEEDS.items():
        try:
            # requests দিয়ে ফেচ করা নিশ্চিত করে রিয়েল ব্রাউজার হেডার
            res = requests.get(feed_url, headers=REQUEST_HEADERS, timeout=12)
            feed = feedparser.parse(res.content)
            taken = 0
            for entry in feed.entries:
                if taken >= MAX_ENTRIES_PER_FEED:
                    break
                if not getattr(entry, 'link', None) or not getattr(entry, 'title', None):
                    continue
                norm_url = normalize_url(entry.link)
                if is_old_story(entry, norm_url):
                    continue

                cfp = content_fingerprint(entry.title)
                if cfp in sent_cfp_set:
                    continue

                article = {
                    "source":    source,
                    "title":     entry.title.strip(),
                    "link":      entry.link,
                    "norm":      norm_url,
                    "pub_time":  format_pub_time(entry),
                    "pub_ts":    get_pub_timestamp(entry),
                    "fresh_pts": freshness_score(entry),
                    "cfp":       cfp,
                    "entry":     entry,
                }

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
            print(f"  ✓ {source}: {taken}টি সাম্প্রতিক সংবাদ পাওয়া গেছে")
        except Exception as e:
            print(f"  ⚠️ ফিড ত্রুটি ({source}): {e}")

    candidates = []
    for nt, group in title_map.items():
        best = max(group, key=lambda x: x["pub_ts"])
        sources_list = list({g["source"] for g in group})
        best["multi_source"]  = len(sources_list) > 1
        best["sources_str"]   = " | ".join(sources_list)
        best["base_score"]    = best["fresh_pts"] + (MULTI_SOURCE_BONUS if best["multi_source"] else 0)
        candidates.append(best)

    print(f"\n🔍 ফিল্টারিং ও ডুপ্লিকেট যাচাই: {len(candidates)} টি ক্যান্ডিডেট স্টোরি...")

    filtered = []
    dup_url = dup_title = dup_kw_pre = 0

    for item in candidates:
        title = item["title"]
        link  = item["link"]
        fp    = url_fingerprint(link)
        tfp   = title_fingerprint(title)

        if fp in sent_fp_set:
            dup_url += 1
            continue

        if tfp in sent_tfp_set:
            dup_title += 1
            continue

        is_dup, score, _ = is_title_duplicate(title, sent_titles)
        if is_dup:
            dup_title += 1
            continue

        item["fp"]  = fp
        item["tfp"] = tfp
        filtered.append(item)

    # Gemini দ্বারা ভাইরাল স্কোরিং ও কীওয়ার্ড অ্যানালাইসিস
    analyzed = []
    for item in filtered:
        analysis = get_gemini_analysis(item["title"])

        is_dup, score, _ = is_keyword_duplicate(analysis["keywords"], sent_topics)
        if is_dup:
            dup_kw_pre += 1
            continue

        total_score = item["base_score"] + analysis["viral_score"]
        analyzed.append((total_score, item, analysis))

    analyzed.sort(key=lambda x: x[0], reverse=True)

    # ══════════════════════════════════════════════════════════════════════════
    # মাল্টি-সোর্স ফেয়ার ব্যালেন্সিং অ্যালগরিদম (Multi-Source Diversity Fair Share)
    # যাতে একক কোনো সোর্স (যেমন শুধু আল জাজিরা) সব স্লট দখল না করে!
    # প্রথম আলো, বিবিসি বাংলা, সিএনএন, DW এবং আল জাজিরা সবই সুযোগ পাবে।
    # ══════════════════════════════════════════════════════════════════════════
    source_buckets: dict[str, list] = {}
    for score, item, analysis in analyzed:
        src = item["source"]
        source_buckets.setdefault(src, []).append((score, item, analysis))

    selected_posts = []
    source_counts = {s: 0 for s in source_buckets}

    # ধাপ ১: প্রতিটি সক্রিয় সোর্স থেকে অন্তত ১টি করে সেরা খবর বাছাই
    for src, items in source_buckets.items():
        if items and len(selected_posts) < MAX_POST_PER_RUN:
            selected_posts.append(items[0])
            source_counts[src] += 1

    # ধাপ ২: বাকি স্লটগুলো অন্যান্য হাই-স্কোরিং খবর দিয়ে পূরণ (সর্বোচ্চ MAX_POSTS_PER_SOURCE)
    remaining_items = []
    for src, items in source_buckets.items():
        for itm in items[1:]:
            remaining_items.append(itm)
    remaining_items.sort(key=lambda x: x[0], reverse=True)

    for score, item, analysis in remaining_items:
        if len(selected_posts) >= MAX_POST_PER_RUN:
            break
        src = item["source"]
        if source_counts.get(src, 0) < MAX_POSTS_PER_SOURCE:
            selected_posts.append((score, item, analysis))
            source_counts[src] = source_counts.get(src, 0) + 1

    print(f"\n🎯 মাল্টি-সোর্স ব্যালেন্সড পোস্ট বাছাই: {len(selected_posts)} টি")
    for s, c in source_counts.items():
        if c > 0:
            print(f"   • {s}: {c}টি সংবাদ")
    print(f"{'─'*60}")

    posted = 0
    for total_score, item, analysis in selected_posts:
        title = item["title"]
        link  = item["link"]

        print(f"  🚀 সেন্ড করা হচ্ছে [{item['source']}] (স্কোর: {total_score})")
        print(f"     {title[:65]}")
        print(f"     ট্যাগ: {analysis['keywords']} | {item['pub_time']}")

        preview_id, tools_id = send_telegram(item, analysis)

        if preview_id:
            data["sent_links"].append(link)
            data["sent_titles"].append(title)
            data["sent_topics"].append(analysis["keywords"])
            data["title_fingerprints"].append(item["tfp"])
            data["sent_content_fps"].append(item["cfp"])

            sent_fp_set.add(item["fp"])
            sent_tfp_set.add(item["tfp"])
            sent_cfp_set.add(item["cfp"])
            sent_titles.append(title)
            sent_topics.append(analysis["keywords"])

            # দুটি মেসেজের আইডি-ই সংরক্ষণ করুন যাতে ২৪ ঘণ্টা পর দুটিই ডিলিট হয়
            msg_ids_to_track = [preview_id]
            if tools_id:
                msg_ids_to_track.append(tools_id)

            data["telegram_messages"].append({
                "message_ids": msg_ids_to_track,
                "timestamp":   time.time(),
                "title":       title[:50],
                "source":      item["source"],
            })
            posted += 1
            time.sleep(POST_DELAY_SEC)

    print(f"\n{'═'*60}")
    print(f"📊 সাইকেল রিপোর্ট → মোট টেলিগ্রাম পোস্ট: {posted} টি")
    print(f"{'═'*60}")
    save_data(data)

# ══════════════════════════ এক্সিকিউশন ও ১.৫ ঘণ্টা লুপ ═════════════════════════
def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN বা CHAT_ID এনভায়রনমেন্ট ভেরিয়েবল সেট করা নেই!")
        print("   টার্মিনালে সেট করুন: export BOT_TOKEN='...' ও export CHAT_ID='...'")
        return

    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY পাওয়া যায়নি — স্মার্ট অফলাইন ফলব্যাক ডিকশনারি সক্রিয় থাকবে।")

    # GitHub Actions বা CI রানার স্বয়ংক্রিয়ভাবে সনাক্তকরণ
    # (GitHub Actions-এ workflow ক্রন শিডিউলে চলে, তাই ইনফিনিট লুপে রাখলে ৯০ মিনিট স্লিপে 'Operation was canceled' হয়ে যায়)
    is_github_actions = os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true"
    run_once = "--once" in sys.argv or is_github_actions or os.environ.get("RUN_ONCE") == "true"

    if run_once:
        if is_github_actions:
            print(f"\n🤖 [GitHub Actions রানার সনাক্ত হয়েছে]")
            print(f"⚡ ১টি পূর্ণ সাইকেল ক্রল ও টেলিগ্রাম ব্রডকাস্ট শুরু — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"💡 GitHub Actions-এর ক্রন বা ডিসপ্যাচে ইনফিনিট স্লিপ এড়াতে ১ সাইকেল শেষেই এটি সফলভাবে Exit (0) করবে।")
        else:
            print(f"\n🚀 এককালীন টেস্ট রান শুরু — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        process_news()
        print("✅ সাইকেল সফলভাবে সম্পন্ন হয়েছে। এক্সিট কোড: 0")
        return

    # নিয়মিত ১.৫ ঘণ্টা পর পর স্বয়ংক্রিয় ক্রল শিডিউলার
    print("="*70)
    print("🤖 বাংলা নিউজ টেলিগ্রাম বট — সক্রিয়")
    print(f"⏰ ক্রল শিডিউল: প্রতি ১.৫ ঘণ্টা (৯০ মিনিট) পর পর স্বয়ংক্রিয়ভাবে চলবে")
    print(f"🗑️ অটো ডিলিট: ২৪ ঘণ্টা পর পর পুরোনো টেলিগ্রাম মেসেজ অটো ডিলিট হবে")
    print(f"📰 মাল্টি-সোর্স: প্রথম আলো, বিবিসি বাংলা, DW, সিএনএন, আল জাজিরা")
    print(f"🎙️ স্ক্রিপ্ট: হিউম্যান-রিটেন পেশাদার ব্রডকাস্ট স্টাইল (নো স্পনসর)")
    print("="*70)

    cycle = 1
    while True:
        try:
            print(f"\n⚡ [সাইকেল #{cycle}] ক্রল শুরু: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
            process_news()
        except Exception as e:
            print(f"❌ সাইকেল ত্রুটি: {e}")

        cycle += 1
        next_run = datetime.fromtimestamp(time.time() + CRAWL_INTERVAL_MINUTES * 60)
        print(f"\n⏳ পরবর্তী ক্রল হবে ১.৫ ঘণ্টা (৯০ মিনিট) পর → {next_run.strftime('%I:%M %p')}")
        print(f"💤 স্লিপিং {CRAWL_INTERVAL_MINUTES} মিনিট... (থামাতে Ctrl+C চাপুন)")
        time.sleep(CRAWL_INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    main()
