import os
import feedparser
import requests
from difflib import SequenceMatcher

# Secrets থেকে তথ্য নেওয়া
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# আপনার কাঙ্ক্ষিত ৪টি নিউজ সোর্স
RSS_FEEDS = {
    "BBC Bangla": "https://feeds.bbci.co.uk/bengali/rss.xml",
    "Prothom Alo": "https://www.prothomalo.com/feed",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "CNN": "http://rss.cnn.com/rss/edition.rss"
}

SENT_LOG_FILE = "sent_links.txt"

def get_sent_links():
    if os.path.exists(SENT_LOG_FILE):
        with open(SENT_LOG_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()

def save_sent_link(link):
    with open(SENT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{link}\n")

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")

# শিরোনামের মধ্যে মিল (Similarity) মাপার ফাংশন
def is_similar(title1, title2):
    # ৩০% এর বেশি মিল থাকলে একই গুরুত্বপূর্ণ খবর ধরা হবে
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() > 0.30

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Bot token or Chat ID is missing!")
        return

    sent_links = get_sent_links()
    all_articles = []

    # ৪টি সাইট থেকেই খবর সংগ্রহ
    for source_name, feed_url in RSS_FEEDS.items():
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:10]:
            all_articles.append({
                "source": source_name,
                "title": entry.title,
                "link": entry.link
            })

    # অন্তত ২টি সাইটে একই খবর আছে কিনা ফিল্টার করা
    for i in range(len(all_articles)):
        item_a = all_articles[i]
        
        if item_a["link"] in sent_links:
            continue

        matched_sources = {item_a["source"]}
        
        for j in range(i + 1, len(all_articles)):
            item_b = all_articles[j]
            # সাইট দুটি আলাদা কিন্তু খবরে মিল থাকলে
            if item_a["source"] != item_b["source"] and is_similar(item_a["title"], item_b["title"]):
                matched_sources.add(item_b["source"])

        # লজিক: যদি অন্তত ২টি আলাদা সাইটে (যেমন BBC + Prothom Alo) একই খবর থাকে
        if len(matched_sources) >= 2:
            sources_str = ", ".join(matched_sources)
            message = f"🚨 *IMPORTANT NEWS* ({sources_str})\n\n📰 *{item_a['title']}*\n\n🔗 {item_a['link']}"
            
            send_telegram_message(message)
            save_sent_link(item_a["link"])
            sent_links.add(item_a["link"])

if __name__ == "__main__":
    main()
