import requests
import os
import random
import urllib.parse
import time
import logging
import sqlite3
import re
from datetime import datetime, date
from flask import Flask, request

app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ADMIN_ID = 1917675707
MAX_TOPICS = 25

TOPICS_FILE = "topics.txt"
DB_FILE = "bot_data.db"

# ---------- DATABASE ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT, date TEXT)''')
    conn.commit()
    conn.close()
    logging.info("Database ready")

# ---------- TOPIC FUNCTIONS ----------
def load_topics():
    if os.path.exists(TOPICS_FILE):
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return ["📱 ဖုန်းအသစ်ဝယ်မယ်ဆို သိထားသင့်တဲ့အချက် ၅ ချက်"]

def save_topics(topics):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        for topic in topics:
            f.write(topic + "\n")

def add_topic(topic, auto_remove=True):
    topics = load_topics()
    if topic in topics:
        return False, "❌ Topic ရှိပြီးသား"
    topics.append(topic)
    removed_count = 0
    if auto_remove and len(topics) > MAX_TOPICS:
        excess = len(topics) - MAX_TOPICS
        removed_count = excess
        topics = topics[excess:]
    save_topics(topics)
    if removed_count > 0:
        return True, f"✅ Topic ထည့်ပြီး\n{topic}\n\n🗑️ အဟောင်း {removed_count} ခု အလိုအလျောက်ဖျက်ပြီး"
    else:
        return True, f"✅ Topic ထည့်ပြီး\n{topic}"

def remove_topic(index):
    topics = load_topics()
    if 1 <= index <= len(topics):
        removed = topics.pop(index - 1)
        save_topics(topics)
        return True, f"✅ ဖျက်ပြီး\n{removed}"
    return False, "❌ မှားယွင်းသောနံပါတ်"

def get_topics_list():
    topics = load_topics()
    if not topics:
        return "📭 Topic မရှိသေးပါ။"
    text = f"📚 စုစုပေါင်း ({len(topics)} / {MAX_TOPICS}) ခု\n"
    for i, t in enumerate(topics[:50]):
        text += f"{i+1}. {t}\n"
    return text

# ---------- GEMINI TEXT (3.1 Flash Lite) ----------
def gemini_text_request(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY}"
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    for _ in range(2):
        try:
            r = requests.post(url, json=data, timeout=60)
            if r.status_code == 200:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logging.error(f"Gemini text error: {e}")
        time.sleep(2)
    return "AI Error"

# ---------- SHOP INFO ----------
def load_shop_info():
    if os.path.exists("shop_info.txt"):
        with open("shop_info.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def generate_post(topic):
    shop_info = load_shop_info()
    prompt = f"""You are a professional Facebook content writer for a phone shop in Myanmar.
Write a Facebook post about: {topic}

Requirements:
- Language: Burmese (Myanmar)
- Use emojis naturally
- Include 3-5 bullet points (• or -)
- Keep under 800 characters
- Sound friendly and engaging, like a real shop page
- At the end, include this shop info (if exists): {shop_info}
- Do NOT use markdown. Just plain text with line breaks."""
    return gemini_text_request(prompt)

# ---------- IMAGE GENERATION (Leonardo -> Pollinations -> Imagen) ----------
def generate_leonardo_image(prompt):
    LEONARDO_API_KEY = os.environ.get("LEONARDO_API_KEY")
    if not LEONARDO_API_KEY:
        logging.info("Leonardo: No API key")
        return None
    url = "https://cloud.leonardo.ai/api/rest/v1/generations"
    headers = {"Authorization": f"Bearer {LEONARDO_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt[:500],  # Limit prompt length
        "modelId": "b24e16ff-06e3-47eb-8b33-4ed6a5a6c5e9",
        "width": 1024,
        "height": 1024,
        "num_images": 1,
        "presetStyle": "DYNAMIC"
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code == 200:
            gen_id = r.json()["sdGenerationJob"]["generationId"]
            for _ in range(15):
                time.sleep(2)
                res = requests.get(f"https://cloud.leonardo.ai/api/rest/v1/generations/{gen_id}", headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    if data["generations_by_pk"]["status"] == "COMPLETE":
                        img_url = data["generations_by_pk"]["generated_images"][0]["url"]
                        logging.info("Leonardo: Success")
                        return requests.get(img_url, timeout=30).content
                    elif data["generations_by_pk"]["status"] == "FAILED":
                        break
    except Exception as e:
        logging.error(f"Leonardo error: {e}")
    return None

def generate_pollinations_image(prompt):
    # Clean prompt for URL
    safe_prompt = urllib.parse.quote(f"realistic smartphone product photo, {prompt[:200]}, 4k, high quality, natural lighting")
    url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width=1024&height=1024"
    try:
        r = requests.get(url, timeout=90)
        if r.status_code == 200 and len(r.content) > 1000:
            logging.info("Pollinations: Success")
            return r.content
        else:
            logging.warning(f"Pollinations: status {r.status_code}, size {len(r.content) if r.content else 0}")
    except Exception as e:
        logging.error(f"Pollinations error: {e}")
    return None

def generate_imagen_image(prompt):
    # Imagen 3.0 – keep as last resort
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        result = client.models.generate_images(
            model='imagen-3.0-generate-002',
            prompt=prompt[:300],
            config=types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1",
            )
        )
        if result.generated_images:
            logging.info("Imagen 3.0: Success")
            return result.generated_images[0].image.image_bytes
    except Exception as e:
        logging.error(f"Imagen error: {e}")
    return None

def generate_image_from_post(post_text):
    """Priority: Leonardo → Pollinations → Imagen"""
    logging.info("Generating image from post...")
    
    # 1. Leonardo
    img = generate_leonardo_image(post_text)
    if img:
        return img
    
    # 2. Pollinations (most reliable free option)
    img = generate_pollinations_image(post_text)
    if img:
        return img
    
    # 3. Imagen (last resort)
    img = generate_imagen_image(post_text)
    if img:
        return img
    
    logging.error("All image generators failed")
    return None

# ---------- BULK TOPIC GENERATION ----------
def generate_topic_batch():
    prompt = """Generate a list of 10 detailed, specific smartphone-related topics for Facebook posts.
Each topic should be 80-120 characters, Myanmar language, start with an emoji.
Format: numbered list 1 to 10, nothing else.
Example:
1. 📱 ဖုန်းအသစ်ဝယ်မယ်ဆို သိထားသင့်တဲ့အချက် ၅ ချက်
2. 🔋 Battery health ကောင်းအောင်ထိန်းသိမ်းနည်း"""
    return gemini_text_request(prompt)

def parse_topic_list(raw_text):
    topics = []
    for line in raw_text.split('\n'):
        line = line.strip()
        match = re.match(r'^\d+[\.\-]\s*(.+)$', line)
        if match:
            topic = match.group(1).strip()
            if topic:
                topics.append(topic)
    return topics[:10]

# ---------- TELEGRAM HELPERS ----------
def send_telegram(text, chat_id):
    if not chat_id:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": chat_id, "text": text[:4000]}, timeout=30)
    except Exception as e:
        logging.error(f"Telegram send error: {e}")

def send_photo(image_bytes, caption, chat_id):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                      files={"photo": ("image.jpg", image_bytes)},
                      data={"chat_id": chat_id, "caption": caption[:200]}, timeout=60)
    except Exception as e:
        logging.error(f"Photo send error: {e}")

# ---------- WEBHOOK ----------
@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json()
    if "message" in update:
        msg = update["message"]
        chat_id = str(msg["chat"]["id"])
        text = msg.get("text", "")
        
        if chat_id != str(ADMIN_ID):
            return "Unauthorized", 403
        
        if text in ["/start", "/help"]:
            send_telegram(f"""📱 **Commands**
/view_topics - Topic စာရင်း
/add_topic [topic] - Topic အသစ် (Auto FIFO max {MAX_TOPICS})
/remove_topic [num] - Topic ဖျက်
/write [topic] - Post + Image (Leonardo → Pollinations)
/write_topic [num] - Topic ရွေးရေး + Image
/random_post - ကျပန်း + Image
/generate_topic - AI Topic (၁၀ ခု) + Auto Save
/status - Bot အခြေအနေ""", chat_id)
        
        elif text == "/view_topics":
            send_telegram(get_topics_list(), chat_id)
        
        elif text.startswith("/add_topic"):
            param = text.replace("/add_topic", "").strip()
            if not param:
                send_telegram("❌ /add_topic [topic]", chat_id)
            else:
                ok, msg = add_topic(param)
                send_telegram(msg, chat_id)
        
        elif text.startswith("/remove_topic"):
            parts = text.split()
            if len(parts) != 2 or not parts[1].isdigit():
                send_telegram("❌ /remove_topic 2", chat_id)
            else:
                ok, msg = remove_topic(int(parts[1]))
                send_telegram(msg, chat_id)
        
        # ----- WRITE TOPIC (by number) -----
        elif text.startswith("/write_topic"):
            parts = text.split()
            if len(parts) != 2 or not parts[1].isdigit():
                send_telegram("❌ /write_topic 1", chat_id)
            else:
                topics = load_topics()
                idx = int(parts[1])
                if 1 <= idx <= len(topics):
                    topic = topics[idx-1]
                    send_telegram(f"⏳ Generating post for: {topic}", chat_id)
                    try:
                        post = generate_post(topic)
                        send_telegram(post, chat_id)
                        img = generate_image_from_post(post)
                        if img:
                            send_photo(img, post[:200], chat_id)
                        else:
                            send_telegram("⚠️ No image generated. Check logs.", chat_id)
                    except Exception as e:
                        logging.error(f"Write topic error: {e}")
                        send_telegram("❌ Fail", chat_id)
                else:
                    send_telegram("❌ Topic not found", chat_id)
        
        # ----- WRITE CUSTOM -----
        elif text.startswith("/write"):
            topic = text.replace("/write", "").strip()
            if not topic:
                send_telegram("❌ /write iPhone 16", chat_id)
            else:
                send_telegram(f"⏳ Generating post for: {topic}", chat_id)
                try:
                    post = generate_post(topic)
                    send_telegram(post, chat_id)
                    img = generate_image_from_post(post)
                    if img:
                        send_photo(img, post[:200], chat_id)
                    else:
                        send_telegram("⚠️ No image generated. Check logs.", chat_id)
                except Exception as e:
                    logging.error(f"Write custom error: {e}")
                    send_telegram("❌ Fail", chat_id)
        
        # ----- RANDOM -----
        elif text == "/random_post":
            topics = load_topics()
            if not topics:
                send_telegram("❌ Topic list empty", chat_id)
            else:
                topic = random.choice(topics)
                send_telegram(f"🎲 Random topic: {topic}", chat_id)
                try:
                    post = generate_post(topic)
                    send_telegram(post, chat_id)
                    img = generate_image_from_post(post)
                    if img:
                        send_photo(img, post[:200], chat_id)
                    else:
                        send_telegram("⚠️ No image generated. Check logs.", chat_id)
                except Exception as e:
                    logging.error(f"Random post error: {e}")
                    send_telegram("❌ Fail", chat_id)
        
        # ----- GENERATE BATCH TOPICS + AUTO ADD -----
        elif text == "/generate_topic":
            send_telegram("⏳ AI is generating 10 new topics...", chat_id)
            try:
                raw = generate_topic_batch()
                topics_list = parse_topic_list(raw)
                if not topics_list:
                    send_telegram("❌ Could not parse AI topics. Try again.", chat_id)
                    return
                added = 0
                added_topics = []
                for t in topics_list:
                    ok, _ = add_topic(t, auto_remove=True)
                    if ok:
                        added += 1
                        added_topics.append(t)
                msg = f"🤖 **AI Topic Generator**\n✅ Added {added} new topics.\n📚 Total: {len(load_topics())} / {MAX_TOPICS}\n\n**New topics:**\n"
                for i, t in enumerate(added_topics[:5], 1):
                    msg += f"{i}. {t}\n"
                if len(added_topics) > 5:
                    msg += f"... and {len(added_topics)-5} more."
                send_telegram(msg, chat_id)
            except Exception as e:
                logging.error(f"Generate topic batch error: {e}")
                send_telegram("❌ Failed to generate topics.", chat_id)
        
        elif text == "/status":
            topics = load_topics()
            send_telegram(f"🤖 Bot Status\nTopics: {len(topics)} / {MAX_TOPICS}\n✅ Running", chat_id)
    
    return "OK", 200

# ---------- MAIN ----------
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)