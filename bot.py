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
    return {"sent_links": [], "sent_titles": [], "telegram_messages": []}

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
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                delete_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                try:
                    requests.post(delete_url, json={"chat_id": CHAT_ID, "message_id": msg_id}, timeout=5)
                    print(f"🗑️ Deleted 6h old message (ID: {msg_id})")
                except Exception as e:
                    print(f"⚠️ Failed to delete message {msg_id}: {e}")
        else:
            remaining_messages.append(item)
            
    data["telegram_messages"] = remaining_messages
    return data

# লিংকের ট্র্যাকিং প্যারামিটার মুছে লিংক ক্লিন করা (যাতে এক খবর বারবার না আসে)
def clean_link(raw_url):
    return raw_url.split('?')[0].rstrip('/')

def is_similar(title1, title2, threshold=0.25):
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio() > threshold

# বাংলা বা ইংরেজি শিরোনামকে ভিডিও ফুটেজের জন্য সঠিক ২-৩টি ইংরেজি কিওয়ার্ডে রূপান্তর
def get_english_keywords(news_title):
    if not GEMINI_API_KEY:
        return "news footage"

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            f"Translate the core topic of this news title into 2 to 3 simple ENGLISH search keywords for stock videos.\n"
            f"News Title: \"{news_title}\"\n\n"
            f"Rules:\n"
            f"1. Output ONLY 2-3 English words separated by space (e.g. 'Israel Gaza attack' or 'Dhaka protest').\n"
            f"2. Do NOT write any Bengali, punctuation, or explanations."
        )
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # ইংরেজি অক্ষর ও স্পেস ছাড়া বাকি সব মুছে নেওয়া
        clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', text).strip()
        if clean_kw and len(clean_kw) >= 3:
            return clean_kw
    except Exception as e:
        print(f"⚠️ Keyword Extraction Error: {e}")

    return "news event"

# ১টি সোর্সে আসা খবরটি ভিডিও তৈরি করার মতো গুরুত্বপূর্ণ কি না তা পরীক্ষা করা
def is_potential_video_story(news_title):
    if not GEMINI_API_KEY:
        return True

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            f"Is this news headline important, breaking, or visually exciting enough to make a short VIDEO story?\n"
            f"Headline: \"{news_title}\"\n\n"
            f"Answer strictly with 'YES' or 'NO'."
        )
        response = model.generate_content(prompt)
        return "YES" in response.text.strip().upper()
    except Exception as e:
        print(f"⚠️ Potential Check Error: {e}")
        return True

def send_telegram_message(title, link, sources_str, search_query):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # ইউআরএল এনকোডিং (Envato ও Google-এ স্পেসজনিত ভুল লিংক ফিক্সিং)
    encoded_kw = urllib.parse.quote(search_query)
    
    # ChatGPT প্রেজেন্টার স্ক্রিপ্ট লিংক
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
        f"🔑 **ফুটেজ ট্যাগ:** `{search_query}`\n"
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
    sent_titles_list = data.get("sent_titles", [])
    
    all_articles = []
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:8]:
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
        
        # ১. লিংক মিলিয়ে পুরোনো খবর বাদ দেওয়া
        if item_a["link"] in sent_links_set:
            continue

        # ২. শিরোনামের মিল দেখে আগের পাঠানো খবর ফিল্টার করা
        already_sent = False
        for past_title in sent_titles_list:
            if is_similar(item_a["title"], past_title, threshold=0.50):
                already_sent = True
                break
        if already_sent:
            continue

        # সোর্স মিলিয়ে দেখা (কয়টি সাইটে এসেছে)
        matched_sources = {item_a["source"]}
        for j in range(i + 1, len(all_articles)):
            item_b = all_articles[j]
            if item_a["source"] != item_b["source"] and is_similar(item_a["title"], item_b["title"], threshold=0.25):
                matched_sources.add(item_b["source"])

        source_count = len(matched_sources)
        sources_str = ", ".join(matched_sources)

        # কন্ডিশন ১: অন্তত ২টি সোর্সে প্রকাশিত
        # কন্ডিশন ২: ১টি সোর্সে আসলেও ভিডিওর জন্য গুরুত্বপূর্ণ
        should_send = False
        if source_count >= 2:
            should_send = True
        else:
            should_send = is_potential_video_story(item_a['title'])

        if should_send:
            # সঠিক ইংরেজি কিওয়ার্ড বের করা
            keywords = get_english_keywords(item_a['title'])
            print(f"🎯 Processing Story ({sources_str}): {item_a['title']} | KW: {keywords}")
            
            msg_id = send_telegram_message(item_a['title'], item_a['link'], sources_str, keywords)
            
            if msg_id:
                # স্থায়ীভাবে ডাটাবেজে জমা রাখা (যাতে ভবিষ্যতে কখনোই ২বার না আসে)
                data["sent_links"].append(item_a["link"])
                data["sent_titles"].append(item_a["title"])
                sent_links_set.add(item_a["link"])
                sent_titles_list.append(item_a["title"])
                
                # ৬ ঘণ্টা পর টেলিগ্রাম মেসেজ মুছে দেওয়ার জন্য ট্র্যাকিং
                data["telegram_messages"].append({
                    "message_id": msg_id,
                    "timestamp": time.time()
                })

    save_data(data)

if __name__ == "__main__":
    main()
