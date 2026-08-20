import os
import re
import feedparser
import requests
import urllib.parse
from difflib import SequenceMatcher
import google.generativeai as genai

# Secrets থেকে পরিবেশ ভ্যারিয়েবলের সঠিক নাম দিয়ে ডেটা নেওয়া
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")  # <--- এখানে ফিক্স করা হয়েছে

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

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

# ব্যাকআপ কিওয়ার্ড (শুধুমাত্র ইংরেজি শব্দ রাখবে যাতে লিংক না ভাঙে)
def extract_fallback_keywords(title):
    # ইংরেজি শব্দ বের করা
    english_words = re.findall(r'[a-zA-Z0-9]+', title)
    if english_words and len(english_words) >= 2:
        return " ".join(english_words[:4])
    return "breaking news footage"

# Gemini দিয়ে বাংলা স্ক্রিপ্ট এবং ইংরেজি সার্চ কিওয়ার্ড তৈরি
def generate_script_and_keywords(news_title):
    fallback_kw = extract_fallback_keywords(news_title)
    
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY missing in environment variables.")
        return "⚠️ এআই স্ক্রিপ্ট জেনারেট করা যায়নি (API Key খুঁজে পাওয়া যায়নি)।", fallback_kw
    
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"""
        Analyze this news title (it can be in English or Bengali): "{news_title}"

        Task 1: Write an engaging 80-100 word news script in Bengali for broadcast.
        Task 2: Extract 2-3 essential ENGLISH search keywords for stock video search (e.g., "Israel Gaza war" or "Protest rally").

        Output Format EXACTLY as:
        SCRIPT: <Bengali Script>
        KEYWORDS: <English Keywords>
        """
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        script = "⚠️ স্ক্রিপ্ট তৈরি করতে সমস্যা হয়েছে।"
        keywords = fallback_kw
        
        if "SCRIPT:" in text and "KEYWORDS:" in text:
            parts = text.split("KEYWORDS:")
            script = parts[0].replace("SCRIPT:", "").strip()
            keywords = parts[1].strip()
        elif text:
            script = text

        return script, keywords
    except Exception as e:
        print(f"❌ Gemini API Error: {e}")
        return "⚠️ স্ক্রিপ্ট জেনারেট করতে সমস্যা হয়েছে।", fallback_kw

def send_telegram_message(text, search_query):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # শুধু ইংরেজি ও প্রয়োজনীয় ক্যারেক্টার লিঙ্ক ফিল্টার
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
    if res.status_code != 200:
        print(f"❌ Telegram API Error: {res.text}")
    else:
        print("✅ Telegram message sent successfully!")

def is_similar(title1, title2):
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() > 0.25

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Bot Token or Chat ID is missing!")
        return

    sent_links = get_sent_links()
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
    match_found = False

    for i in range(len(all_articles)):
        item_a = all_articles[i]
        
        if item_a["link"] in sent_links:
            continue

        matched_sources = {item_a["source"]}
        
        for j in range(i + 1, len(all_articles)):
            item_b = all_articles[j]
            if item_a["source"] != item_b["source"] and is_similar(item_a["title"], item_b["title"]):
                matched_sources.add(item_b["source"])

        if len(matched_sources) >= 2:
            match_found = True
            sources_str = ", ".join(matched_sources)
            print(f"🎯 Match Found ({sources_str}): {item_a['title']}")
            
            bangla_script, english_keywords = generate_script_and_keywords(item_a['title'])
            
            message = (
                f"🚨 IMPORTANT NEWS ({sources_str})\n\n"
                f"📰 {item_a['title']}\n"
                f"🔗 {item_a['link']}\n\n"
                f"📝 ড্রাফট বাংলা স্ক্রিপ্ট:\n{bangla_script}"
            )
            
            send_telegram_message(message, english_keywords)
            save_sent_link(item_a["link"])
            sent_links.add(item_a["link"])

    if not match_found:
        print("ℹ️ No new matched news found in this run.")

if __name__ == "__main__":
    main()
