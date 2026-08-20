import os
import re
import time
import json
import feedparser
import requests
import urllib.parse
from difflib import SequenceMatcher

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

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

# ৬ ঘণ্টার পুরোনো মেসেজ টেলিগ্রাম থেকে মুছে ফেলার ফাংশন
def cleanup_old_messages(history):
    current_time = time.time()
    retention_period = 6 * 3600  # 6 Hours
    updated_history = []
    
    for item in history:
        if current_time - item.get("timestamp", 0) > retention_period:
            msg_id = item.get("message_id")
            if msg_id:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                try:
                    requests.post(url, json={"chat_id": CHAT_ID, "message_id": msg_id}, timeout=5)
                    print(f"🗑️ Deleted 6h old message (ID: {msg_id})")
                except Exception:
                    pass
        else:
            updated_history.append(item)
            
    return updated_history

def extract_keywords(title):
    english_words = re.findall(r'[a-zA-Z0-9]+', title)
    if english_words and len(english_words) >= 2:
        return " ".join(english_words[:4])
    return "breaking news footage"

def send_telegram_message(title, link, source, search_query):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', search_query).strip() or "news footage"
    encoded_query = urllib.parse.quote(clean_kw)
    
    # AI প্রম্পট যা বাটনে ক্লিক করলেই প্রেজেন্টার স্ক্রিপ্ট জেনারেট করবে
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
                {"text": "🖼️ Google Images", "url": f"https://www.google.com/search?tbm=isch&q={encoded_query}"},
                {"text": "🎬 Envato Elements", "url": f"https://elements.envato.com/all-items/{encoded_query}"}
            ],
            [
                {"text": "📰 Reuters Footage", "url": f"https://www.reuters.com/site-search/?query={encoded_query}"},
                {"text": "🎥 Pexels Stock", "url": f"https://www.pexels.com/search/{encoded_query}/"}
            ]
        ]
    }
    
    message = (
        f"🚨 **NEW VIDEO STORY ALERT** ({source})\n\n"
        f"📰 **শিরোনাম:** {title}\n"
        f"🔗 **মূল খবর:** {link}\n\n"
        f"💡 *টিপস: ভিডিও বানাতে চাইলে নিচের 'প্রেজেন্টার স্ক্রিপ্ট' বাটনে চাপ দিন।*"
    )
    
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
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

    print(f"ℹ️ Total fetched articles: {len(all_articles)}")

    for item in all_articles:
        if item["link"] in sent_links:
            continue

        keywords = extract_keywords(item['title'])
        print(f"🎯 Sending News ({item['source']}): {item['title']}")
        
        msg_id = send_telegram_message(item['title'], item['link'], item['source'], keywords)
        
        if msg_id:
            history.append({
                "link": item["link"],
                "message_id": msg_id,
                "timestamp": time.time()
            })
            sent_links.add(item["link"])

    save_sent_history(history)

if __name__ == "__main__":
    main()
