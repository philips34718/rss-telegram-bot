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

# নতুন API Key আপডেট করা হয়েছে
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or "AQ.Ab8RN6JRevJ2C7EiSTWkT63s4HjuZ1U_xSXCI797f8Q92xCxgQ"

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

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sent_links": [], "sent_keywords": [], "telegram_messages": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ৬ ঘণ্টার পুরোনো মেসেজ অটো ডিলিট
def cleanup_old_telegram_messages(data):
    current_time = time.time()
    retention_period = 6 * 3600  # 6 Hours
    remaining_messages = []
    
    for item in data.get("telegram_messages", []):
        if current_time - item.get("timestamp", 0) > retention_period:
            msg_id = item.get("message_id")
            if msg_id:
                delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                try:
                    requests.post(delete_url, json={"chat_id": CHAT_ID, "message_id": msg_id}, timeout=5)
                    print(f"🗑️ Deleted 6h old message (ID: {msg_id})")
                except Exception as e:
                    print(f"⚠️ Message delete failed: {e}")
        else:
            remaining_messages.append(item)
            
    data["telegram_messages"] = remaining_messages
    return data

def clean_link(raw_url):
    return raw_url.split('?')[0].rstrip('/')

def is_similar_text(str1, str2, threshold=0.40):
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio() > threshold

# Gemini দিয়ে বাংলা/ইংরেজি যেকোনো শিরোনাম থেকে ২-৩টি নিখুঁত ইংরেজি সার্চ কিওয়ার্ড তৈরি
def get_english_keywords(news_title):
    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = (
                f"Translate the main subject of this news title into 2 to 3 ENGLISH search keywords for video stock footage.\n"
                f"Headline: \"{news_title}\"\n\n"
                f"Strict Rules:\n"
                f"1. Output ONLY 2-3 English words (e.g. 'Gaza strike', 'Dhaka protest', 'Trump speech').\n"
                f"2. Do NOT output Bengali words, explanations, or quotes."
            )
            response = model.generate_content(prompt)
            clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', response.text).strip()
            
            words = clean_kw.split()
            if len(words) >= 1:
                return " ".join(words[:3])
        except Exception as e:
            print(f"❌ Gemini Keyword Error: {e}")

    # ব্যাকআপ পদ্ধতি: শিরোনামে ইংরেজি থাকলে তা ব্যবহার করবে
    eng_words = re.findall(r'[a-zA-Z0-9]+', news_title)
    if len(eng_words) >= 2:
        return " ".join(eng_words[:3])

    return "news story"

def send_telegram_message(title, link, sources_str, search_query):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    encoded_kw = urllib.parse.quote(search_query)
    
    script_prompt = (
        f"আপনি একজন নিউজ চ্যানেলের প্রডিউসার। এই খবরের ওপর একটি ১১০ শব্দের আকর্ষণীয় "
        f"বাংলা প্রেজেন্টার ভয়েসওভার স্ক্রিপ্ট, হাই-সিটিআর ইউটিউব শিরোনাম এবং ৩টি হ্যাশট্যাগসহ "
        f"ডেসক্রিপশন লিখুন:\n\n\"{title}\""
    )
    encoded_script_prompt = urllib.parse.quote(script_prompt)
    
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🎙️ ১-ক্লিকে প্রেজেন্টার স্ক্রিপ্ট বানান (ChatGPT)", "url": f"https://chatgpt.com/?q={encoded_script_prompt}"}
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
        f"🚨 **NEW VIDEO STORY ALERT** ({sources_str})\n\n"
        f"📰 **শিরোনাম:** {title}\n"
        f"🔗 **মূল খবর:** {link}\n\n"
        f"🔑 **ফুটেজ সার্চ ট্যাগ:** `{search_query}`\n"
        f"💡 *টিপস: ভিডিও বানাতে চাইলে নিচের 'প্রেজেন্টার স্ক্রিপ্ট' বাটনে চাপ দিন।*"
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
    else:
        print(f"❌ Telegram Send Error: {res.text}")
        return None

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Bot Token or Chat ID Missing!")
        return

    data = load_data()
    data = cleanup_old_telegram_messages(data)
    
    sent_links_set = set(data.get("sent_links", []))
    sent_keywords_list = data.get("sent_keywords", [])
    
    all_articles = []
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:6]:  # প্রতি ফিড থেকে সেরা ৬টি তাজা খবর
                cleaned_url = clean_link(entry.link)
                all_articles.append({
                    "source": source_name,
                    "title": entry.title.strip(),
                    "link": cleaned_url
                })
        except Exception as e:
            print(f"⚠️ Error fetching {source_name}: {e}")

    print(f"ℹ️ Total fetched articles: {len(all_articles)}")

    for i in range(len(all_articles)):
        item_a = all_articles[i]
        
        # ১. ডিরেক্ট লিঙ্ক চেক (একই লিঙ্ক ডুপ্লিকেট বন্ধ)
        if item_a["link"] in sent_links_set:
            continue

        # ২. কিওয়ার্ড তৈরি করা
        keywords = get_english_keywords(item_a['title'])

        # ৩. কিওয়ার্ডের মাধ্যমে বাংলা-ইংরেজি ডুপ্লিকেট খবর ফিল্টার করা
        already_sent = False
        for past_kw in sent_keywords_list:
            if is_similar_text(keywords, past_kw, threshold=0.50):
                already_sent = True
                break
        
        if already_sent:
            print(f"⏭️ Skipping Duplicate Topic: {item_a['title']} ({keywords})")
            continue

        # সোর্স কাউন্ট দেখা (একই খবর কত জায়গায় আছে)
        matched_sources = {item_a["source"]}
        for j in range(i + 1, len(all_articles)):
            item_b = all_articles[j]
            if item_a["source"] != item_b["source"]:
                kw_b = get_english_keywords(item_b['title'])
                if is_similar_text(keywords, kw_b, threshold=0.40):
                    matched_sources.add(item_b["source"])

        sources_str = ", ".join(matched_sources)

        print(f"🎯 Processing Story ({sources_str}): {item_a['title']} | Keywords: {keywords}")
        
        msg_id = send_telegram_message(item_a['title'], item_a['link'], sources_str, keywords)
        
        if msg_id:
            data["sent_links"].append(item_a["link"])
            data["sent_keywords"].append(keywords)
            sent_links_set.add(item_a["link"])
            sent_keywords_list.append(keywords)
            
            data["telegram_messages"].append({
                "message_id": msg_id,
                "timestamp": time.time()
            })

    save_data(data)

if __name__ == "__main__":
    main()
