import os
import feedparser
import requests

# Secrets থেকে তথ্য সংগ্রহ
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# নিউজ সাইটের RSS লিংকসমূহ
RSS_FEEDS = [
    "https://www.prothomalo.com/feed",
    "https://feeds.bbci.co.uk/bengali/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml"
]

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
        print(f"Message sending failed: {e}")

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("Bot token or Chat ID is missing!")
        return

    sent_links = get_sent_links()

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        # সর্বশেষ ৫টি পোস্ট চেক করবে
        for entry in feed.entries[:5]:
            link = entry.link
            if link not in sent_links:
                title = entry.title
                message = f"📰 *{title}*\n\n🔗 {link}"
                
                send_telegram_message(message)
                save_sent_link(link)
                sent_links.add(link)

if __name__ == "__main__":
    main()
