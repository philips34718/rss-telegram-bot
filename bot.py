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

# ৬ ঘণ্টার পুরোনো মেসেজ টেলিগ্রাম থেকে মুছে ফেলার ফাংশন
def cleanup_old_messages(history):
    current_time = time.time()
    retention_period = 6 * 3600  # ৬ ঘণ্টা (সেকেন্ডে)
    updated_history = []
    
    for item in history:
        if current_time - item.get("timestamp", 0) > retention_period:
            msg_id = item.get("message_id")
            if msg_id:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                try:
                    requests.post(url, json={"chat_id": CHAT_ID, "message_id": msg_id}, timeout=5)
                    print(f"🗑️ Deleted 6h old message (ID: {msg_id})")
                except Exception as e:
                    print(f"⚠️ Failed to delete message {msg_id}: {e}")
        else:
            updated_history.append(item)
            
    return updated_history

def extract_fallback_keywords(title):
    english_words = re.findall(r'[a-zA-Z0-9]+', title)
    if english_words and len(english_words) >= 2:
        return " ".join(english_words[:4])
    return "breaking news footage"

# Gemini AI বিশ্লেষণের মাধ্যমে কেবল ভিডিও উপযোগী খবর ফিল্টার ও পারফেক্ট স্ক্রিপ্ট তৈরি
def analyze_and_generate_content(news_title):
    fallback_kw = extract_fallback_keywords(news_title)
    
    if not GEMINI_API_KEY:
        return False, news_title, "⚠️ API Key অনুপস্থিত।", news_title, fallback_kw

    try:
        model = genai.GenerativeModel(
            "gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
        
        prompt = f"""
        You are an expert Executive Producer for a Video News Channel.
        Analyze this headline: "{news_title}"

        Task:
        1. Evaluate if this news is suitable to make an ENGAGING VIDEO STORY (YouTube Shorts/Video Broadcast). It must have strong visual appeal, emotional impact, high audience interest, or breaking nature. Do NOT approve boring text/policy news that only suits Facebook/Text posts.
        2. If YES, generate a full Bengali presenter script (100-140 words), YouTube headline, description, and 2-3 English search keywords for video footage.

        Return JSON strictly matching this schema:
        {{
            "is_video_story": true/false,
            "youtube_title": "High CTR Bengali Headline",
            "presenter_script": "Detailed multi-sentence Bengali voiceover script for presenter",
            "youtube_description": "Bengali description with 3 hashtags",
            "english_keywords": "stock footage search keywords in English"
        }}
        """
        
        response = model.generate_content(prompt)
        data = json.loads(response.text.strip())

        is_video_story = data.get("is_video_story", False)
        yt_title = data.get("youtube_title", news_title)
        script = data.get("presenter_script", "⚠️ স্ক্রিপ্ট জেনারেট করা যায়নি।")
        yt_desc = data.get("youtube_description", news_title)
        keywords = data.get("english_keywords", fallback_kw)

        return is_video_story, yt_title, script, yt_desc, keywords

    except Exception as e:
        print(f"❌ Gemini JSON Parsing Error: {e}")
        return False, news_title, "⚠️ স্ক্রিপ্ট তৈরিতে সমস্যা হয়েছে।", news_title, fallback_kw

def send_telegram_message(text, search_query, news_title):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', search_query).strip()
    if not clean_kw:
        clean_kw = "news footage"
        
    encoded_query = urllib.parse.quote(clean_kw)
    encoded_title = urllib.parse.quote(f"Write a 2 minute detailed video script for news: {news_title}")
    
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🖼️ Google Images", "url": f"https://www.google.com/search?tbm=isch&q={encoded_query}"},
                {"text": "🎬 Envato Elements", "url": f"https://elements.envato.com/all-items/{encoded_query}"}
            ],
            [
                {"text": "📰 Reuters Footage", "url": f"https://www.reuters.com/site-search/?query={encoded_query}"},
                {"text": "🎥 Pexels Stock", "url": f"https://www.pexels.com/search/{encoded_query}/"}
            ],
            [
                {"text": "🤖 AI স্ক্রিপ্ট পুনর্নির্মাণ (ChatGPT)", "url": f"https://chatgpt.com/?q={encoded_title}"}
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

    # ১. পুরোনো মেসেজ ক্লিন করা (৬ ঘণ্টার পুরোনো)
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

        # AI দ্বারা ভিডিও স্টোরির উপযোগী কিনা যাচাই ও পারফেক্ট স্ক্রিপ্ট জেনারেশন
        is_video_story, yt_title, script, yt_desc, keywords = analyze_and_generate_content(item['title'])

        # কেবল ভিডিও উপযোগী খবরই প্রসেস করা হবে
        if is_video_story:
            print(f"🎯 Video Story Approved ({item['source']}): {item['title']}")
            
            message = (
                f"🚨 **NEW VIDEO STORY** ({item['source']})\n\n"
                f"📰 **মূল খবর:** {item['title']}\n"
                f"🔗 **লিংক:** {item['link']}\n\n"
                f"📌 **YouTube Headline:**\n{yt_title}\n\n"
                f"🎙️ **প্রেজেন্টার/ভয়েসওভার স্ক্রিপ্ট:**\n{script}\n\n"
                f"📝 **YouTube Description:**\n{yt_desc}"
            )
            
            msg_id = send_telegram_message(message, keywords, item['title'])
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
