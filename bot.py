import os
import re
import time
import json
import feedparser
import requests
import urllib.parse
from difflib import SequenceMatcher
import google.generativeai as genai

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
# Environment Variable থেকে Key নেওয়া হবে, ব্যাকআপ হিসেবে প্রদত্ত Key রাখা হয়েছে
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AQ.Ab8RN6Ikq7g5wQLWLQEv1gejtj9raWkk7PPgQjSPim08FF3GFw"

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

RSS_FEEDS = {
    "BBC Bangla": "https://feeds.bbci.co.uk/bengali/rss.xml",
    "Prothom Alo": "https://www.prothomalo.com/feed",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "CNN": "http://rss.cnn.com/rss/edition.rss"
}

HISTORY_FILE = "sent_history.json"

def get_sent_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_sent_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def cleanup_old_messages(history):
    current_time = time.time()
    retention_period = 24 * 3600
    updated_history = []
    
    for item in history:
        if current_time - item.get("timestamp", 0) > retention_period:
            msg_id = item.get("message_id")
            if msg_id:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                try:
                    requests.post(url, json={"chat_id": CHAT_ID, "message_id": msg_id}, timeout=5)
                except Exception:
                    pass
        else:
            updated_history.append(item)
            
    return updated_history

def extract_fallback_keywords(title):
    english_words = re.findall(r'[a-zA-Z0-9]+', title)
    if english_words and len(english_words) >= 2:
        return " ".join(english_words[:4])
    return "breaking news footage"

def analyze_and_generate_content(news_title, source_count):
    fallback_kw = extract_fallback_keywords(news_title)
    
    if not GEMINI_API_KEY:
        return False, news_title, "⚠️ API Key অনুপস্থিত।", "⚠️ ডেসক্রিপশন নেই।", fallback_kw

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        Analyze this news headline: "{news_title}"
        Total media sources reporting this: {source_count}

        Tasks:
        1. VIRAL_CHECK: Is this news high-impact, breaking, emotional, or visually compelling enough to make a viral video story? Answer "YES" or "NO". (Note: If source_count >= 2, default to YES).
        2. YT_TITLE: Create an engaging, high-CTR YouTube Headline in Bengali.
        3. SCRIPT: Write a 90-110 word dynamic Presenter Voiceover Script in Bengali suitable for video broadcast.
        4. YT_DESC: Write a concise YouTube Description in Bengali with 3 relevant hashtags.
        5. KEYWORDS: Extract 2-3 essential ENGLISH search keywords for stock footage search.

        Output Format EXACTLY as:
        VIRAL: <YES/NO>
        TITLE: <Bengali YT Title>
        SCRIPT: <Bengali Presenter Script>
        DESC: <Bengali Description>
        KEYWORDS: <English Keywords>
        """
        response = model.generate_content(prompt)
        text = response.text.strip()

        is_viral = False
        yt_title = news_title
        script = "⚠️ স্ক্রিপ্ট জেনারেট করা যায়নি।"
        yt_desc = news_title
        keywords = fallback_kw

        lines = text.split("\n")
        for line in lines:
            if line.startswith("VIRAL:"):
                is_viral = "YES" in line.upper()
            elif line.startswith("TITLE:"):
                yt_title = line.replace("TITLE:", "").strip()
            elif line.startswith("SCRIPT:"):
                script = line.replace("SCRIPT:", "").strip()
            elif line.startswith("DESC:"):
                yt_desc = line.replace("DESC:", "").strip()
            elif line.startswith("KEYWORDS:"):
                keywords = line.replace("KEYWORDS:", "").strip()

        # Multi-line extraction handling
        if "SCRIPT:" in text and "DESC:" in text:
            script_part = text.split("SCRIPT:")[1].split("DESC:")[0].strip()
            desc_part = text.split("DESC:")[1].split("KEYWORDS:")[0].strip()
            if script_part: script = script_part
            if desc_part: yt_desc = desc_part

        return is_viral, yt_title, script, yt_desc, keywords

    except Exception as e:
        print(f"❌ Gemini Error: {e}")
        return (source_count >= 2), news_title, "⚠️ স্ক্রিপ্ট তৈরিতে ত্রুটি হয়েছে।", news_title, fallback_kw

def send_telegram_message(text, search_query):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', search_query).strip()
    if not clean_kw:
        clean_kw = "news footage"
        
    encoded_query = urllib.parse.quote(clean_kw)
    
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🖼️ Google Images", "url": f"https://www.google.com/search?tbm=isch&q={encoded_query}"},
                {"text": "🎬 Envato Elements", "url": f"https://elements.envato.com/all-items/{encoded_query}"}
            ],
            [
                {"text": "📰 Reuters Footage", "url": f"https://www.reuters.com/site-search/?query={encoded_query}"},
                {"text": "🎥 Pexels Stock", "url": f"https://www.pexels.com/search/{encoded_query}/"}
            ]
        ]
    }
    
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": False,
        "reply_markup": keyboard
    }
    
    res = requests.post(url, json=payload, timeout=10)
    if res.status_code == 200:
        return res.json().get("result", {}).get("message_id")
    return None

def is_similar(title1, title2):
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() > 0.25

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Bot Token or Chat ID Missing!")
        return

    history = get_sent_history()
    history = cleanup_old_messages(history)
    sent_links = {item["link"] for item in history}
    
    all_articles = []
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:10]:
                all_articles.append({
                    "source": source_name,
                    "title": entry.title,
                    "link": entry.link
                })
        except Exception as e:
            print(f"⚠️ Error fetching {source_name}: {e}")

    for i in range(len(all_articles)):
        item_a = all_articles[i]
        if item_a["link"] in sent_links:
            continue

        matched_sources = {item_a["source"]}
        for j in range(i + 1, len(all_articles)):
            item_b = all_articles[j]
            if item_a["source"] != item_b["source"] and is_similar(item_a["title"], item_b["title"]):
                matched_sources.add(item_b["source"])

        source_count = len(matched_sources)
        sources_str = ", ".join(matched_sources)

        # AI-driven Validation (১টি সাইটে থাকলেও ভাইরাল পটেনশিয়াল থাকলে প্রসেস করবে)
        is_viral, yt_title, script, yt_desc, keywords = analyze_and_generate_content(item_a['title'], source_count)

        if is_viral or source_count >= 2:
            print(f"🎯 Processing Story ({sources_str}): {item_a['title']}")
            
            message = (
                f"🚨 **POTENTIAL VIRAL STORY** ({sources_str})\n\n"
                f"📰 **মূল খবর:** {item_a['title']}\n"
                f"🔗 **লিংক:** {item_a['link']}\n\n"
                f"📌 **YouTube Headline:**\n{yt_title}\n\n"
                f"🎙️ **প্রেজেন্টার/ভয়েসওভার স্ক্রিপ্ট:**\n{script}\n\n"
                f"📝 **YouTube Description:**\n{yt_desc}"
            )
            
            msg_id = send_telegram_message(message, keywords)
            if msg_id:
                history.append({
                    "link": item_a["link"],
                    "message_id": msg_id,
                    "timestamp": time.time()
                })
                sent_links.add(item_a["link"])

    save_sent_history(history)

if __name__ == "__main__":
    main()
