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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AQ.Ab8RN6Le-AI9X8PTHinSOlMD9OrSDfdxdjWb6FpzISRPDSM6oQ"

# GitHub Actions বা Cron-এর জন্য এটি False থাকবে (প্রতি রানে একবার চেক করবে)
RUN_CONTINUOUSLY = False  

# ২৪ ঘণ্টা পর টেলিগ্রাম মেসেজ অটো ডিলিট সময়সীমা
RETENTION_PERIOD_SECONDS = 24 * 3600  

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"⚠️ Gemini Config Error: {e}")

RSS_FEEDS = {
    "BBC Bangla": "https://feeds.bbci.co.uk/bengali/rss.xml",
    "Prothom Alo": "https://www.prothomalo.com/feed",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "CNN": "http://rss.cnn.com/rss/edition.rss"
}

DATA_FILE = "bot_data.json"

STOPWORDS = {
    'how', 'is', 'are', 'was', 'were', 'the', 'a', 'an', 'in', 'of', 'to', 
    'for', 'and', 'with', 'on', 'at', 'this', 'that', 'from', 'by', 'what', 
    'why', 'news', 'breaking', 'story', 'update', 'latest', 'says', 'said'
}

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sent_links": [], "sent_topics": [], "telegram_messages": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def cleanup_old_telegram_messages(data):
    current_time = time.time()
    remaining_messages = []
    
    for item in data.get("telegram_messages", []):
        if current_time - item.get("timestamp", 0) > RETENTION_PERIOD_SECONDS:
            msg_id = item.get("message_id")
            if msg_id:
                delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                try:
                    requests.post(delete_url, json={"chat_id": CHAT_ID, "message_id": msg_id}, timeout=5)
                    print(f"🗑️ Deleted 24h old message (ID: {msg_id})")
                except Exception as e:
                    print(f"⚠️ Message delete error: {e}")
        else:
            remaining_messages.append(item)
            
    data["telegram_messages"] = remaining_messages
    return data

def clean_url(url):
    return url.split('?')[0].rstrip('/')

def is_similar_text(str1, str2, threshold=0.40):
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio() > threshold

def get_stock_keywords(headline):
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = (
                f"Extract ONLY 2 to 3 high-value ENGLISH search nouns for downloading video footage based on this title.\n"
                f"Headline: \"{headline}\"\n\n"
                f"Rules:\n"
                f"1. Strictly NO filler words ('how', 'is', 'news', 'breaking').\n"
                f"2. Output ONLY 2-3 English words (e.g. 'Trump speech', 'Gaza strike', 'Dhaka flood')."
            )
            response = model.generate_content(prompt)
            clean_text = re.sub(r'[^a-zA-Z0-9\s]', '', response.text).strip()
            words = [w for w in clean_text.split() if w.lower() not in STOPWORDS]
            if words:
                return " ".join(words[:3])
        except Exception as e:
            print(f"⚠️ Gemini Keyword Error: {e}")

    eng_words = [w for w in re.findall(r'[a-zA-Z0-9]+', headline) if w.lower() not in STOPWORDS]
    if len(eng_words) >= 2:
        return " ".join(eng_words[:3])
        
    return "world event"

def send_telegram_message(title, link, source_name, keywords):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    clean_kw = " ".join(keywords.split())
    encoded_kw = urllib.parse.quote(clean_kw)
    
    # স্টুডিও মানের প্রফেশনাল টিভি প্রডিউসার প্রম্পট
    script_prompt = (
        f"আপনি একজন আন্তর্জাতিক নিউজ চ্যানেলের এক্সিকিউটিভ প্রডিউসার। "
        f"নিচের শিরোনাম থেকে নিউজ স্টুডিওর জন্য একটি সচল ও আকর্ষণীয় বাংলা নিউজ প্যাকেজ স্ক্রিপ্ট তৈরি করুন:\n\n"
        f"সংবাদ: \"{title}\"\n\n"
        f"ফরম্যাট নিয়মাবলী:\n"
        f"১. [ইউটিউব শিরোনাম]: ১টি হাই-সিটিআর আকর্ষণীয় শিরোনাম।\n"
        f"২. [অ্যাঙ্কর ভূমিকা]: স্টুডিও অ্যাঙ্করের পড়ার জন্য ২০ সেকেন্ডের প্রফেশনাল সূচনা।\n"
        f"৩. [ভয়েসওভার স্ক্রিপ্ট]: ফুটেজের ব্যাকগ্রাউন্ডে চালানোর জন্য ৯০-১১০ শব্দের তথ্যবহুল ভয়েসওভার। মাঝখানে ব্র্যাকেটে ভিডিও ফুটেজের দিকনির্দেশনা দিন (যেমন: [ফুটেজ: বক্তৃতার দৃশ্য], [গ্রাফিক্স: মূল পয়েন্ট])।\n"
        f"৪. [আউটরো ও হ্যাশট্যাগ]: চ্যানেল সাবস্ক্রাইব করার সাইন-অফ এবং ৩টি হ্যাশট্যাগ।"
    )
    encoded_script_prompt = urllib.parse.quote(script_prompt)
    
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🎙️ ১-ক্লিকে স্টুডিও ভয়েসওভার স্ক্রিপ্ট (ChatGPT)", "url": f"https://chatgpt.com/?q={encoded_script_prompt}"}
            ],
            [
                {"text": "🖼️ Google Images", "url": f"https://www.google.com/search?tbm=isch&q={encoded_kw}"},
                {"text": "🎬 Envato Elements", "url": f"https://elements.envato.com/all-items/{encoded_kw}"}
            ],
            [
                {"text": "📰 Reuters Footage", "url": f"https://www.reuters.com/site-search/?query={encoded_kw}"},
                {"text": "🎥 Pexels Stock", "url": f"https://www.pexels.com/search/{encoded_kw}/"}
            ]
        ]
    }
    
    message = (
        f"🚨 **NEW VIDEO STORY ALERT** ({source_name})\n\n"
        f"📰 **শিরোনাম:** {title}\n"
        f"🔗 **মূল খবর:** {link}\n\n"
        f"🔑 **ফুটেজ সার্চ ট্যাগ:** `{clean_kw}`\n"
        f"💡 *টিপস: স্টুডিও স্ক্রিপ্ট পেতে নিচের বাটন ব্যবহার করুন।*"
    )
    
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
        "reply_markup": keyboard
    }
    
    res = requests.post(url, json=payload, timeout=10)
    if res.status_code == 200:
        return res.json().get("result", {}).get("message_id")
    return None

def process_news():
    data = load_data()
    data = cleanup_old_telegram_messages(data)
    
    sent_links_set = set(data.get("sent_links", []))
    sent_topics_list = data.get("sent_topics", [])
    
    all_articles = []
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                all_articles.append({
                    "source": source_name,
                    "title": entry.title.strip(),
                    "link": clean_url(entry.link)
                })
        except Exception as e:
            print(f"⚠️ Feed error ({source_name}): {e}")

    for item_a in all_articles:
        # ১. ইউআরএল চেক
        if item_a["link"] in sent_links_set:
            continue

        keywords = get_stock_keywords(item_a['title'])

        # ২. ডুপ্লিকেট কিওয়ার্ড/টপিক ফিল্টার
        already_posted = False
        for past_topic in sent_topics_list:
            if is_similar_text(keywords, past_topic, threshold=0.40):
                already_posted = True
                break
        
        if already_posted:
            print(f"⏭️ Duplicate Skipped: {item_a['title']} ({keywords})")
            continue

        print(f"🎯 New Story ({item_a['source']}): {item_a['title']} | Tag: {keywords}")
        
        msg_id = send_telegram_message(item_a['title'], item_a['link'], item_a['source'], keywords)
        
        if msg_id:
            data["sent_links"].append(item_a["link"])
            data["sent_topics"].append(keywords)
            sent_links_set.add(item_a["link"])
            sent_topics_list.append(keywords)
            
            data["telegram_messages"].append({
                "message_id": msg_id,
                "timestamp": time.time()
            })

    save_data(data)

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Bot Token or Chat ID is missing!")
        return

    print("🚀 Running News Processing...")
    process_news()

if __name__ == "__main__":
    main()
