import os
import sys
import json
import time
import random
import base64
import urllib.parse
from datetime import datetime
import requests
import feedparser
from slugify import slugify

# টার্মিনালে বাংলা ও স্পেশাল ক্যারেক্টার প্রদর্শনের জন্য
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8')

# ==================== কনফিগারেশন ====================
# গিটহাব সিক্রেটস সাপোর্ট (যদি এনভায়রনমেন্টে থাকে তবে সেখান থেকে নেবে, অন্যথায় ডিফল্ট মান ব্যবহার করবে)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6JSh7vpD-lNikbDO_f3XElrRlxt0hzgzPwTMQI-bK_gRQ").strip()
WP_SITE_URL = os.getenv("WP_SITE_URL", "https://buzz.mamahmud.top").strip()
WP_USERNAME = os.getenv("WP_USERNAME", "mamahmud").strip()
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "CuZ0 FUcf 99R9 koiK IIbv U5VU").strip()

POST_STATUS = "publish"
OUTPUT_DIR = "generated_news"
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")
PROCESSED_LOG_FILE = "processed_articles.json"
DAILY_QUOTA_FILE = "daily_category_quota.json"

# ৪টি নির্ধারিত ক্যাটাগরি ও তাদের ডেডিকেটেড গুগল লাইভ ফিড
CATEGORY_FEEDS = {
    "Pop Culture": "https://news.google.com/rss/search?q=pop+culture+celebrity+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Entertainment": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNREpxYW5RU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US%3Aen",
    "Sports": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRFp1ZEdvU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US%3Aen",
    "Tech & Trends": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US%3Aen"
}
# ===================================================


def get_wp_auth_header() -> dict:
    credentials = f"{WP_USERNAME}:{WP_APP_PASSWORD}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def get_or_create_wp_category(category_name: str) -> int:
    url = f"{WP_SITE_URL.rstrip('/')}/wp-json/wp/v2/categories"
    headers = get_wp_auth_header()
    
    try:
        res = requests.get(f"{url}?search={urllib.parse.quote(category_name)}", headers=headers, timeout=20)
        if res.status_code == 200:
            for cat in res.json():
                if cat.get("name", "").lower() == category_name.lower():
                    return cat.get("id")
        
        headers["Content-Type"] = "application/json"
        create_res = requests.post(url, headers=headers, json={"name": category_name}, timeout=20)
        if create_res.status_code in (200, 201):
            return create_res.json()["id"]
    except Exception as e:
        print(f"    [!] ক্যাটাগরি হ্যান্ডলিং এরর: {e}")
        
    return 1


def get_or_create_wp_tags(tag_names: list) -> list:
    tag_ids = []
    url = f"{WP_SITE_URL.rstrip('/')}/wp-json/wp/v2/tags"
    headers = get_wp_auth_header()

    for name in tag_names:
        clean_name = name.strip()
        if not clean_name:
            continue
        try:
            res = requests.get(f"{url}?search={urllib.parse.quote(clean_name)}", headers=headers, timeout=15)
            found_id = None
            if res.status_code == 200:
                for t in res.json():
                    if t.get("name", "").lower() == clean_name.lower():
                        found_id = t.get("id")
                        break

            if not found_id:
                headers_create = get_wp_auth_header()
                headers_create["Content-Type"] = "application/json"
                create_res = requests.post(url, headers=headers_create, json={"name": clean_name}, timeout=15)
                if create_res.status_code in (200, 201):
                    found_id = create_res.json().get("id")

            if found_id:
                tag_ids.append(found_id)
        except Exception:
            continue

    return tag_ids


def upload_image_to_wordpress(image_path: str, alt_text: str = "") -> dict:
    if not image_path or not os.path.exists(image_path):
        return {}

    url = f"{WP_SITE_URL.rstrip('/')}/wp-json/wp/v2/media"
    headers = get_wp_auth_header()
    filename = os.path.basename(image_path)
    
    headers.update({
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "image/jpeg"
    })

    try:
        with open(image_path, "rb") as img_file:
            media_data = img_file.read()

        response = requests.post(url, headers=headers, data=media_data, timeout=60)
        if response.status_code in (200, 201):
            res_json = response.json()
            media_id = res_json.get("id")
            source_url = res_json.get("source_url")
            
            if alt_text and media_id:
                patch_headers = get_wp_auth_header()
                patch_headers["Content-Type"] = "application/json"
                requests.post(f"{url}/{media_id}", headers=patch_headers, json={"alt_text": alt_text}, timeout=20)

            print(f"    [✓] মিডিয়া আপলোড সফল: ID {media_id}")
            return {"id": media_id, "url": source_url}
    except Exception as e:
        print(f"    [!] মিডিয়া আপলোড এরর: {e}")

    return {}


def format_article_body_with_images(markdown_text: str, body_images: list, title: str) -> str:
    html_lines = []
    lines = markdown_text.split("\n")
    inserted_first = False
    inserted_second = False

    for line in lines:
        line_clean = line.strip()
        if line_clean.startswith("## "):
            heading_text = line_clean.replace("## ", "")
            html_lines.append(f"<h2>{heading_text}</h2>")
            
            if not inserted_first and len(body_images) > 0 and body_images[0].get("url"):
                img_tag = f'<figure class="wp-block-image size-large"><img src="{body_images[0]["url"]}" alt="{title}" /><figcaption>In-depth look: {title}</figcaption></figure>'
                html_lines.append(img_tag)
                inserted_first = True
            elif inserted_first and not inserted_second and len(body_images) > 1 and body_images[1].get("url"):
                img_tag = f'<figure class="wp-block-image size-large"><img src="{body_images[1]["url"]}" alt="{title}" /><figcaption>Developing coverage</figcaption></figure>'
                html_lines.append(img_tag)
                inserted_second = True
        elif line_clean:
            html_lines.append(f"<p>{line_clean}</p>")

    return "\n".join(html_lines)


def post_to_wordpress(article_data: dict, saved_images: dict, target_category: str) -> bool:
    print(f"\n[*] [{target_category}] ক্যাটাগরিতে পোস্ট ও ছবি আপলোড শুরু হচ্ছে...")
    headline = article_data.get("seo_headline", "")
    slug = article_data.get("slug", "")

    # ১. ফিচার্ড ইমেজ
    feat_img_info = {}
    if saved_images.get("featured"):
        print("  [*] ফিচার্ড ইমেজ আপলোড হচ্ছে...")
        feat_img_info = upload_image_to_wordpress(saved_images["featured"], alt_text=headline)

    # ২. বডি ইমেজ
    body_imgs_info = []
    if saved_images.get("body_image_1"):
        info1 = upload_image_to_wordpress(saved_images["body_image_1"], alt_text=f"{headline} detail")
        if info1: body_imgs_info.append(info1)
        
    if saved_images.get("body_image_2"):
        info2 = upload_image_to_wordpress(saved_images["body_image_2"], alt_text=f"{headline} scene")
        if info2: body_imgs_info.append(info2)

    # ৩. আর্টিকেল ফরম্যাটিং
    raw_body = article_data.get("article_body_markdown", "")
    final_content_html = format_article_body_with_images(raw_body, body_imgs_info, headline)

    # ৪. ক্যাটাগরি ও ট্যাগ
    cat_id = get_or_create_wp_category(target_category)
    raw_tags = article_data.get("tags") or article_data.get("focus_keywords", [])
    print(f"  [*] ট্যাগ প্রসেস হচ্ছে ({len(raw_tags[:7])} টি ট্যাগ)...")
    tag_ids = get_or_create_wp_tags(raw_tags[:7])

    # ৫. পোস্ট পেলোড
    url = f"{WP_SITE_URL.rstrip('/')}/wp-json/wp/v2/posts"
    headers = get_wp_auth_header()
    headers["Content-Type"] = "application/json"

    post_payload = {
        "title": headline,
        "content": final_content_html,
        "slug": slug,
        "status": POST_STATUS,
        "categories": [cat_id],
        "tags": tag_ids,
        "excerpt": article_data.get("meta_description", "")
    }

    if feat_img_info.get("id"):
        post_payload["featured_media"] = feat_img_info["id"]

    try:
        res = requests.post(url, headers=headers, json=post_payload, timeout=40)
        if res.status_code in (200, 201):
            published_url = res.json().get("link")
            print(f"\n[★] পোস্ট সফলভাবে লাইভ হয়েছে:")
            print(f"    -> লিংক: {published_url}")
            return True
        else:
            print(f"[!] পোস্ট তৈরিতে সমস্যা ({res.status_code}): {res.text[:120]}")
    except Exception as e:
        print(f"[!] পোস্ট রিকোয়েস্ট এরর: {e}")

    return False


def get_daily_quota_tracker() -> dict:
    today_str = datetime.now().strftime("%Y-%m-%d")
    # দৈনিক টার্গেট ৩ থেকে ৫ এর মধ্যে রেন্ডমভাবে নির্ধারিত হবে
    random_target = random.randint(3, 5)
    default_tracker = {
        "date": today_str,
        "daily_target": random_target,
        "counts": {cat: 0 for cat in CATEGORY_FEEDS}
    }
    
    if os.path.exists(DAILY_QUOTA_FILE):
        try:
            with open(DAILY_QUOTA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    if "daily_target" not in data:
                        data["daily_target"] = random_target
                    return data
        except Exception:
            pass
    return default_tracker


def save_daily_quota_tracker(tracker: dict):
    with open(DAILY_QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2)


def select_balanced_category() -> str:
    """ক্যাটাগরি ভারসাম্য বজায় রেখে পরবর্তী উপযুক্ত ক্যাটাগরি নির্বাচন করা"""
    tracker = get_daily_quota_tracker()
    total_posted_today = sum(tracker["counts"].values())
    target_today = tracker.get("daily_target", 4)

    # দিনের মোট টার্গেট পূর্ণ হলে আর পোস্ট হবে না
    if total_posted_today >= target_today:
        return ""

    # যে ক্যাটাগরিগুলোতে এখনো ২টির বেশি পোস্ট হয়নি তাদের মধ্যে সবচেয়ে কম পোস্ট হওয়া ক্যাটাগরি নির্বাচন
    available_cats = [cat for cat in CATEGORY_FEEDS if tracker["counts"].get(cat, 0) < 2]
    if not available_cats:
        return ""

    # সবচেয়ে কম কাউন্ট থাকা ক্যাটাগরিগুলোকে আগে প্রাধান্য দেওয়া
    min_count = min(tracker["counts"].get(cat, 0) for cat in available_cats)
    least_posted_cats = [cat for cat in available_cats if tracker["counts"].get(cat, 0) == min_count]
    
    return random.choice(least_posted_cats)


def get_available_online_models() -> list:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
    try:
        res = requests.get(url, timeout=20)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            valid_models = []
            for m in models_data:
                name = m["name"].replace("models/", "")
                if "generateContent" in m.get("supportedGenerationMethods", []) and "tts" not in name:
                    valid_models.append(name)
            valid_models.sort(key=lambda x: ("flash" not in x, "pro" in x))
            return valid_models
    except Exception as e:
        print(f"[!] মডেল তালিকা এরর: {e}")
    return ["gemini-2.5-flash", "gemini-1.5-flash"]


def call_gemini_dynamic(prompt: str, active_models: list) -> str:
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7}
    }
    for model in active_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=50)
            if res.status_code == 200:
                return res.json()["candidates"][0]["content"]["parts"][0]["text"]
            elif res.status_code in (429, 503):
                continue
        except Exception:
            continue
    raise Exception("কোনো সক্রিয় এআই মডেল রেসপন্স করেনি।")


def download_ai_image_with_retry(prompt: str, filename: str, max_retries: int = 3) -> str:
    os.makedirs(IMAGE_DIR, exist_ok=True)
    clean_prompt = prompt.replace("\n", " ").strip()
    enhanced_prompt = f"{clean_prompt}, authentic editorial photojournalism, realistic lighting, sharp focus, 8k resolution, unedited news press photo style"
    encoded_prompt = urllib.parse.quote(enhanced_prompt)
    
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1200&height=675&nologo=true"
    filepath = os.path.join(IMAGE_DIR, filename)

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(image_url, timeout=40)
            if response.status_code == 200 and len(response.content) > 5000:
                with open(filepath, "wb") as f:
                    f.write(response.content)
                print(f"    [✓] লোকাল ছবি প্রস্তুত: {filename}")
                return filepath
            else:
                time.sleep(2)
        except Exception:
            time.sleep(2)
    return ""


def load_processed_history() -> set:
    if os.path.exists(PROCESSED_LOG_FILE):
        try:
            with open(PROCESSED_LOG_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_processed_history(history: set):
    with open(PROCESSED_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history), f, indent=2)


def fetch_news_for_category(category_name: str):
    feed_url = CATEGORY_FEEDS.get(category_name)
    processed = load_processed_history()

    print(f"[*] [{category_name}] ক্যাটাগরির লাইভ ফিড স্ক্যান করা হচ্ছে...")
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "") or entry.get("description", "")

            if link not in processed and title not in processed:
                return {
                    "category": category_name,
                    "title": title,
                    "link": link,
                    "summary": summary
                }
    except Exception as e:
        print(f"[!] ফিড রিডিং এরর: {e}")
    return None


def generate_humanized_news(raw_news: dict, active_models: list) -> dict:
    prompt = f"""
    You are an award-winning investigative American journalist writing exclusively for the category: '{raw_news['category']}'.
    Transform this developing US story into a comprehensive, deeply engaging, and authentic news report.

    Source Headline: {raw_news['title']}
    Source Context: {raw_news['summary']}

    EDITORIAL & QUALITY GUIDELINES:
    1. Humanized Voice: Write with natural cadence, nuance, and genuine journalistic flair. Avoid robotic summaries. Start directly with an arresting lede (hook) that places the reader right in the action.
    2. Absolute Forbidden AI Clichés: NEVER use phrases like 'delve into', 'testament to', 'tapestry', 'beacon', 'furthermore', 'moreover', 'in conclusion', 'in summary', 'vibrant', 'plethora', 'bustling', 'it is important to note', 'serves as a reminder'.
    3. Structural Depth: Provide comprehensive context, background details, public reactions, and future outlook. Use only ## for descriptive subheadings.
    4. Word Count: 550 to 750 words of rich, original reporting.
    5. Visual Direction: Create 3 realistic, press-wire editorial photojournalism descriptions (no CGI, no sci-fi surrealism).
    6. Tags: 5 to 7 highly targeted tags including real person names, organizations, places, and core themes.

    Return ONLY a valid JSON object matching this schema:
    {{
        "seo_headline": "Compelling, journalistic headline under 65 chars",
        "slug": "clean-url-slug",
        "meta_description": "Sharp 150-char meta summary ending with high reader intrigue",
        "focus_keywords": ["keyword1", "keyword2", "keyword3"],
        "tags": ["Tag 1", "Tag 2", "Tag 3", "Tag 4", "Tag 5", "Tag 6"],
        "summary_bullets": ["Key critical development 1", "Key critical development 2"],
        "article_body_markdown": "Full breaking story with ## subheadings formatted in clear paragraphs (550-750 words).",
        "featured_image_prompt": "Editorial press photo establishing wide shot",
        "body_image_1_prompt": "Editorial news photo candid action moment",
        "body_image_2_prompt": "Editorial news photo detailed situational context"
    }}
    """
    response_text = call_gemini_dynamic(prompt, active_models)
    return json.loads(response_text)


def run_automation():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)

    tracker = get_daily_quota_tracker()
    total_posted = sum(tracker["counts"].values())
    target_today = tracker.get("daily_target", 4)

    print(f"==================================================")
    print(f"[*] আজকের তারিখ: {tracker['date']}")
    print(f"[*] আজকের মোট টার্গেট: {target_today} টি পোস্ট (ইতোমধ্যে সম্পন্ন: {total_posted} টি)")
    print(f"[*] ক্যাটাগরি স্ট্যাটাস: {tracker['counts']}")
    print(f"==================================================")

    if total_posted >= target_today:
        print("[★] আজকের জন্য নির্ধারিত দৈনিক টার্গেট ইতিমধ্যে পূর্ণ হয়েছে! পরবর্তী রানের অপেক্ষা করা হচ্ছে।")
        return

    target_category = select_balanced_category()
    if not target_category:
        print("[!] কোনো উপযুক্ত ক্যাটাগরি পাওয়া যায়নি বা কোটা সাময়িকভাবে ব্যালেন্সড রয়েছে।")
        return

    # হিউম্যান-লাইক রেন্ডম স্লিপ (১০ থেকে ৪০ মিনিট অনিয়মিত বিরতি)
    delay_seconds = random.randint(600, 2400)
    delay_minutes = round(delay_seconds / 60, 1)
    print(f"[~] হিউম্যানাইজিং রেন্ডম ডিলে: বট {delay_minutes} মিনিট অপেক্ষা করছে...")
    time.sleep(delay_seconds)

    print(f"\n[+] নির্বাচিত ক্যাটাগরি: {target_category} (আজ পাবলিশ হয়েছে: {tracker['counts'][target_category]} টি)")

    active_models = get_available_online_models()
    news_item = fetch_news_for_category(target_category)

    if not news_item:
        print(f"[!] {target_category}-এর জন্য কোনো ফ্রেশ সংবাদ পাওয়া যায়নি।")
        return

    print(f"[*] সংবাদ প্রক্রিয়াকরণ শুরু: {news_item['title'][:65]}...")

    try:
        result = generate_humanized_news(news_item, active_models)
        slug = result.get("slug") or slugify(result.get("seo_headline", "news"))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        p_feat = result.get("featured_image_prompt") or f"{news_item['title']} event"
        p_body1 = result.get("body_image_1_prompt") or f"{news_item['title']} highlight"
        p_body2 = result.get("body_image_2_prompt") or f"{news_item['title']} atmosphere"

        print("  [*] ৩টি এআই প্রেস ছবি তৈরি ও ডাউনলোড হচ্ছে...")
        img_feat = download_ai_image_with_retry(p_feat, f"{timestamp}_{slug[:25]}_featured.jpg")
        img_body1 = download_ai_image_with_retry(p_body1, f"{timestamp}_{slug[:25]}_body1.jpg")
        img_body2 = download_ai_image_with_retry(p_body2, f"{timestamp}_{slug[:25]}_body2.jpg")

        saved_images = {
            "featured": img_feat,
            "body_image_1": img_body1,
            "body_image_2": img_body2
        }

        # ব্যাকআপ সেভ
        filename = f"{timestamp}_{slug[:35]}.json"
        with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
            json.dump({
                "category": target_category,
                "source_url": news_item["link"],
                "data": result,
                "saved_images": saved_images
            }, f, ensure_ascii=False, indent=2)

        # ওয়ার্ডপ্রেসে পাবলিশ
        is_published = post_to_wordpress(result, saved_images, target_category)

        if is_published:
            processed = load_processed_history()
            processed.add(news_item["link"])
            processed.add(news_item["title"])
            save_processed_history(processed)

            # কোটা বৃদ্ধি ও সংরক্ষণ
            tracker["counts"][target_category] += 1
            save_daily_quota_tracker(tracker)
            print(f"\n[★] সফল! {target_category}-এ আজকের পোস্ট সংখ্যা: {tracker['counts'][target_category]}")

    except Exception as e:
        print(f"[!] প্রক্রিয়াকরণে এরর: {e}")


if __name__ == "__main__":
    run_automation()
