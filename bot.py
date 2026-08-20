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

DATA_FILE = "bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"sent_links": [], "telegram_messages": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ৬ ঘণ্টার পুরোনো টেলিগ্রাম মেসেজ অটো ডিলিট ফাংশন
def cleanup_old_telegram_messages(data):
    current_time = time.time()
    retention_period = 6 * 3600  # ৬ ঘণ্টা
    remaining_messages = []
    
    for item in data.get("telegram_messages", []):
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
            remaining_messages.append(item)
            
    data["telegram_messages"] = remaining_messages
    return data

def extract_fallback_keywords(title):
    english_words = re.findall(r'[a-zA-Z0-9]+', title)
    if english_words and len(english_words) >= 2:
        return " ".join(english_words[:4])
    return "breaking news footage"

# Gemini দিয়ে খবর পটেনশিয়াল কিনা যাচাই এবং ইংরেজি কিওয়ার্ড এক্সট্র্যাক্ট
def evaluate_news_and_get_keywords(news_title, source_count):
    fallback_kw = extract_fallback_keywords(news_title)
    
    # একাধিক সোর্সে থাকলে সরাসরি পটেনশিয়াল
    if source_count >= 2:
        is_potential = True
    else:
        is_potential = False

    if not GEMINI_API_KEY:
        return is_potential, fallback_kw

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        prompt = f"""
        Analyze this news headline: "{news_title}"

        Task 1: Is this headline visually compelling, high-impact, or breaking enough to make a good VIDEO STORY for YouTube/Social Media? Answer strictly YES or NO.
        Task 2: Extract 2-3 essential ENGLISH stock footage search keywords for this news (e.g. "Israel Gaza strike" or "Dhaka protest").

        Output EXACT format:
        POTENTIAL: <YES/NO>
        KEYWORDS: <English Keywords>
        """
        
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        keywords = fallback_kw
        for line in text.split("\n"):
            if line.startswith("POTENTIAL:") and source_count < 2:
                is_potential = "YES" in line.upper()
            elif line.startswith("KEYWORDS:"):
                extracted_kw = line.replace("KEYWORDS:", "").strip()
                clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', extracted_kw).strip()
                if clean_kw:
                    keywords = clean_kw

        return is_potential, keywords

    except Exception as e:
        print(f"⚠️ Gemini Evaluation Error: {e}")
        return is_potential, fallback_kw

def send_telegram_message(title, link, sources_str, search_query):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # ইউআরএল ফিক্সিং (স্পেস ও স্পেশাল ক্যারেক্টার এনকোড করা)
    clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', search_query).strip() or "news footage"
    encoded_path = urllib.parse.quote(clean_kw)
    encoded_param = urllib.parse.quote_plus(clean_kw)
    
    # ChatGPT প্রম্পট লিংক
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
                {"text": f"🖼️ Google Images", "url": f"https://www.google.com/search?tbm=isch&q={encoded_param}"},
                {"text": "🎬 Envato Elements", "url": f"https://elements.envato.com/all-items/{encoded_path}"}
            ],
            [
                {"text": "📰 Reuters Footage", "url": f"https://www.reuters.com/site-search/?query={encoded_param}"},
                {"text": "🎥 Pexels Stock", "url": f"https://www.pexels.com/search/{encoded_path}/"}
            ]
        ]
    }
    
    message = (
        f"🚨 **NEW VIDEO STORY ALERT** ({sources_str})\n\n"
        f"📰 **শিরোনাম:** {title}\n"
        f"🔗 **মূল খবর:** {link}\n\n"
        f"🔑 **ফুটেজ ট্যাগ:** `{clean_kw}`\n"
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

def is_similar(title1, title2):
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() > 0.25

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Bot Token or Chat ID Missing!")
        return

    data = load_data()
    data = cleanup_old_telegram_messages(data)
    sent_links_set = set(data.get("sent_links", []))
    
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

    for i in range(len(all_articles)):
        item_a = all_articles[i]
        
        # পূর্বে পাঠানো খবর হলে বাদ যাবে
        if item_a["link"] in sent_links_set:
            continue

        matched_sources = {item_a["source"]}
        for j in range(i + 1, len(all_articles)):
            item_b = all_articles[j]
            if item_a["source"] != item_b["source"] and is_similar(item_a["title"], item_b["title"]):
                matched_sources.add(item_b["source"])

        source_count = len(matched_sources)
        sources_str = ", ".join(matched_sources)

        # ২ টি শর্ত যাচাই:
        # ১. অন্তত ২টি সোর্সে প্রকাশিত খবর
        # ২. বা ১টি সোর্সে হলেও Gemini দ্বারা ভিডিও স্টোরির জন্য অনুমোদিত খবর
        is_potential, keywords = evaluate_news_and_get_keywords(item_a['title'], source_count)

        if is_potential:
            print(f"🎯 Sending Story ({sources_str}): {item_a['title']} | KW: {keywords}")
            
            msg_id = send_telegram_message(item_a['title'], item_a['link'], sources_str, keywords)
            
            if msg_id:
                # স্থায়ীভাবে লিঙ্ক সেভ রাখা (যাতে ৬ ঘণ্টা পর মেসেজ ডিলিট হলেও খবর পুনরায় না আসে)
                data["sent_links"].append(item_a["link"])
                sent_links_set.add(item_a["link"])
                
                # ৬ ঘণ্টা পর ডিলিট করার জন্য মেসেজ ট্র্যাকিং
                data["telegram_messages"].append({
                    "message_id": msg_id,
                    "timestamp": time.time()
                })

    save_data(data)

if __name__ == "__main__":
    main()
