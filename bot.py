import os
import feedparser
import requests
import urllib.parse
from difflib import SequenceMatcher
import google.generativeai as genai

# Secrets থেকে তথ্য নেওয়া
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GEMINI_API_KEY = os.environ.get("AQ.Ab8RN6Ikq7g5wQLWLQEv1gejtj9raWkk7PPgQjSPim08FF3GFw")

# Gemini AI কনফিগারেশন
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# কাঙ্ক্ষিত ৪টি নিউজ সোর্স
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

# Gemini দিয়ে অটোমেটিক বাংলা স্ক্রিপ্ট তৈরি
def generate_bangla_script(news_title):
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API Key সেট করা নেই।"
    
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        তুমি একজন পেশাদার নিউজ স্ক্রিপ্ট রাইটার। নিচের খবরের শিরোনামটি বিশ্লেষণ করে বাংলা নিউজ ভিডিওর জন্য ৮০-১০০ শব্দের মধ্যে একটি আকর্ষণীয় ড্রাফট স্ক্রিপ্ট তৈরি করো।
        
        খবরের শিরোনাম: {news_title}
        
        শুধু বাংলা স্ক্রিপ্টটি আউটপুট হিসেবে দেবে, বাড়তি কোনো ভূমিকা লেখার দরকার নেই।
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating script: {e}")
        return "⚠️ স্ক্রিপ্ট জেনারেট করতে সমস্যা হয়েছে।"

# ইনলাইন বাটনসহ টেলিগ্রাম মেসেজ পাঠানো
def send_telegram_message(text, search_query):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # ইউআরএল-বান্ধব কিওয়ার্ড তৈরি
    encoded_query = urllib.parse.quote_plus(search_query)
    
    # ফুটেজ ও থাম্বনেইল সার্চের জন্য direct URL buttons
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🖼️ Google Images", "url": f"https://www.google.com/search?tbm=isch&q={encoded_query}"},
                {"text": "🎬 Envato", "url": f"https://elements.envato.com/all-items/{encoded_query}"}
            ],
            [
                {"text": "📰 Reuters", "url": f"https://www.reuters.com/site-search/?query={encoded_query}"},
                {"text": "🎥 Pexels", "url": f"https://www.pexels.com/search/{encoded_query}/"}
            ]
        ]
    }
    
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": keyboard
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending message: {e}")

# শিরোনামের মিল মাপা
def is_similar(title1, title2):
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

    # ফিল্টার ও ম্যাচিং
    for i in range(len(all_articles)):
        item_a = all_articles[i]
        
        if item_a["link"] in sent_links:
            continue

        matched_sources = {item_a["source"]}
        
        for j in range(i + 1, len(all_articles)):
            item_b = all_articles[j]
            if item_a["source"] != item_b["source"] and is_similar(item_a["title"], item_b["title"]):
                matched_sources.add(item_b["source"])

        # অন্তত ২টি সাইটে একই খবর থাকলে
        if len(matched_sources) >= 2:
            sources_str = ", ".join(matched_sources)
            
            # বাংলা স্ক্রিপ্ট তৈরি
            bangla_script = generate_bangla_script(item_a['title'])
            
            # মেসেজের ফরম্যাট
            message = (
                f"🚨 *IMPORTANT NEWS* ({sources_str})\n\n"
                f"📰 *{item_a['title']}*\n"
                f"🔗 {item_a['link']}\n\n"
                f"📝 *ড্রাফট বাংলা স্ক্রিপ্ট:*\n{bangla_script}"
            )
            
            # মেসেজ পাঠানো (সাথে সার্চ বাটন যুক্ত থাকবে)
            send_telegram_message(message, item_a['title'])
            
            save_sent_link(item_a["link"])
            sent_links.add(item_a["link"])

if __name__ == "__main__":
    main()
