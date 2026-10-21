import os
import sys
import json
import time
import base64
import urllib.parse
from datetime import datetime
import requests
import feedparser
from slugify import slugify

# উইন্ডোজ টার্মিনালে বাংলা ও স্পেশাল ক্যারেক্টার প্রদর্শনের জন্য
sys.stdout.reconfigure(encoding='utf-8')

# ==================== কনফিগারেশন ====================
GEMINI_API_KEY = "AQ.Ab8RN6JSh7vpD-lNikbDO_f3XElrRlxt0hzgzPwTMQI-bK_gRQ"       # আপনার আসল Gemini API Key দিন

# ওয়ার্ডপ্রেস ক্রেডেনশিয়ালস
WP_SITE_URL = "https://buzz.mamahmud.top"       # আপনার ওয়েবসাইটের URL (শেষে কোনো / থাকবে না)
WP_USERNAME = "mamahmud"                # ওয়ার্ডপ্রেস ইউজারনেম
WP_APP_PASSWORD = "CuZ0 FUcf 99R9 koiK IIbv U5VU"      # Application Password (১৬ অক্ষরের কোড)

POST_STATUS = "publish"                      # সরাসরি লাইভ করার জন্য "publish"
OUTPUT_DIR = "generated_news"
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")
PROCESSED_LOG_FILE = "processed_articles.json"
DAILY_QUOTA_FILE = "daily_category_quota.json"

# প্রতি ক্যাটাগরিতে প্রতিদিন সর্বোচ্চ পোস্ট সংখ্যা (৪ ক্যাটাগরি x ২ = ৮টি দৈনিক)
DAILY_POSTS_PER_CATEGORY = 2

# আপনার ৪টি নির্ধারিত ক্যাটাগরি ও তাদের ডেডিকেটেড গুগল লাইভ ফিড
CATEGORY_FEEDS = {
    "Pop Culture": "https://news.google.com/rss/search?q=pop+culture+celebrity+when:1d&hl=en-US&gl=US&ceid=US:en",
    "Entertainment": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNREpxYW5RU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US%3Aen",
    "Sports": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRFp1ZEdvU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US%3Aen",
    "Tech & Trends": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US%3Aen"
}
# ===================================================


def get_wp_auth_header() -> dict:
    credentials = f"{WP_USERNAME.strip()}:{WP_APP_PASSWORD.strip()}"
    token = base64.b64encode(credentials.encode()).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def get_or_create_wp_category(category_name: str) -> int:
    """ওয়ার্ডপ্রেসে ক্যাটাগরি চেক ও আইডি সংগ্রহ"""
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
    """ট্যাগগুলো যাচাই করে আইডি সংগ্রহ অথবা নতুন ট্যাগ তৈরি"""
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

            print(f"    [✓] মিডিয়া আপলোড সফল: ID {media_id}")
            return {"id": media_id, "url": source_url}
    except Exception as e:
        print(f"    [!] মিডিয়া আপলোড এরর: {e}")

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
                img_tag = f'<figure class="wp-block-image size-large"><img src="{body_images[0]["url"]}" alt="{title}" /><figcaption>Trending updates on {title}</figcaption></figure>'
                html_lines.append(img_tag)
                inserted_first = True
            elif inserted_first and not inserted_second and len(body_images) > 1 and body_images[1].get("url"):
                img_tag = f'<figure class="wp-block-image size-large"><img src="{body_images[1]["url"]}" alt="{title}" /><figcaption>Exclusive coverage</figcaption></figure>'
                html_lines.append(img_tag)
                inserted_second = True
        elif line_clean:
            html_lines.append(f"<p>{line_clean}</p>")

    return "\n".join(html_lines)


def post_to_wordpress(article_data: dict, saved_images: dict, target_category: str) -> bool:
    print(f"\n[*] [{target_category}] ক্যাটাগরিতে পোস্ট ও ছবি আপলোড শুরু হচ্ছে...")
    headline = article_data.get("seo_headline", "")
    slug = article_data.get("slug", "")

    # ১. ফিচার্ড ইমেজ আপলোড
    feat_img_info = {}
    if saved_images.get("featured"):
        print("  [*] ফিচার্ড ইমেজ আপলোড হচ্ছে...")
        feat_img_info = upload_image_to_wordpress(saved_images["featured"], alt_text=headline)

    # ২. ২টি বডি ইমেজ আপলোড
    body_imgs_info = []
    if saved_images.get("body_image_1"):
        info1 = upload_image_to_wordpress(saved_images["body_image_1"], alt_text=f"{headline} detail")
        if info1: body_imgs_info.append(info1)
        
    if saved_images.get("body_image_2"):
        info2 = upload_image_to_wordpress(saved_images["body_image_2"], alt_text=f"{headline} scene")
        if info2: body_imgs_info.append(info2)

    # ৩. বডি টেক্সট ফরম্যাটিং
    raw_body = article_data.get("article_body_markdown", "")
    final_content_html = format_article_body_with_images(raw_body, body_imgs_info, headline)

    # ৪. ক্যাটাগরি ও ট্যাগ প্রসেসিং
    cat_id = get_or_create_wp_category(target_category)
    raw_tags = article_data.get("tags") or article_data.get("focus_keywords", [])
    print(f"  [*] ট্যাগ প্রসেস হচ্ছে ({len(raw_tags[:7])} টি ট্যাগ)...")
    tag_ids = get_or_create_wp_tags(raw_tags[:7])

    # ৫. পোস্ট তৈরি
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
            print(f"\n[★] অসাধারণ! পোস্ট সফলভাবে [{target_category}]-এ লাইভ হয়েছে:")
            print(f"    -> লিংক: {published_url}")
            return True
        else:
            print(f"[!] পোস্ট তৈরিতে সমস্যা ({res.status_code}): {res.text[:120]}")
    except Exception as e:
        print(f"[!] পোস্ট রিকোয়েস্ট এরর: {e}")

    return False


def get_daily_quota_tracker() -> dict:
    today_str = datetime.now().strftime("%Y-%m-%d")
    tracker = {"date": today_str, "counts": {cat: 0 for cat in CATEGORY_FEEDS}}
    
    if os.path.exists(DAILY_QUOTA_FILE):
        try:
            with open(DAILY_QUOTA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("date") == today_str:
                    return data
        except Exception:
            pass
    return tracker


def save_daily_quota_tracker(tracker: dict):
    with open(DAILY_QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2)


def select_next_category() -> str:
    """যে ক্যাটাগরির দিনের কোটা (২টি) এখনো পূর্ণ হয়নি সেটি নির্বাচন করা"""
    tracker = get_daily_quota_tracker()
    for cat in CATEGORY_FEEDS:
        if tracker["counts"].get(cat, 0) < DAILY_POSTS_PER_CATEGORY:
            return cat
    return ""


def get_available_online_models() -> list:
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY.strip()}"
    try:
        res = requests.get(url, timeout=20)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            valid_models = []
            for m in models_data:
                name = m["name"].replace("models/", "")
                if "generateContent" in m.get("supportedGenerationMethods", []) and "tts" not in name:
                    valid_models.append(name)
            valid_models.sort(key=lambda x: ("3.8" not in x, "lite" in x, "flash" not in x))
            return valid_models
    except Exception as e:
        print(f"[!] মডেল তালিকা এরর: {e}")
    return ["gemini-3.8-flash", "gemini-flash-lite-latest"]


def call_gemini_dynamic(prompt: str, active_models: list) -> str:
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY.strip()}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.65}
    }
    for model in active_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=45)
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
    enhanced_prompt = f"{clean_prompt}, authentic editorial press photo, photojournalism style, 8k resolution, cinematic lighting, sharp details"
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

    print(f"[*] [{category_name}] ক্যাটাগরির জন্য ট্রেন্ড স্ক্যান করা হচ্ছে...")
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
        print(f"[!] ফিড এরর: {e}")
    return None


def generate_humanized_news(raw_news: dict, active_models: list) -> dict:
    prompt = f"""
    You are a premier US news journalist writing exclusively for the category: {raw_news['category']}.
    This story is currently trending in the United States. Rewrite it into a completely humanized, engaging, non-AI-sounding news article.

    Source Context: {raw_news['title']}
    Details: {raw_news['summary']}

    EDITORIAL RULES:
    1. Tone: Fast-paced, authentic, journalistic, and natural to US readers.
    2. FORBIDDEN WORDS: Strictly never use 'delve', 'tapestry', 'testament', 'beacon', 'furthermore', 'moreover', 'in conclusion', 'vibrant', 'plethora'.
    3. Headings: Use only ## for subheadings. The SEO headline will be the single H1.
    4. Image Prompts: Generate 3 realistic editorial photojournalistic image prompts matching the story.
    5. Tags: Provide 5 to 7 specific, highly relevant topical tags (celebrity names, events, organizations, keywords).

    Return ONLY a valid JSON object matching this schema:
    {{
        "seo_headline": "Punchy click-worthy headline under 65 chars",
        "slug": "clean-slug",
        "meta_description": "Catchy 150-char meta description with a hook",
        "focus_keywords": ["keyword1", "keyword2", "keyword3"],
        "tags": ["Tag 1", "Tag 2", "Tag 3", "Tag 4", "Tag 5", "Tag 6"],
        "summary_bullets": ["Key highlight 1", "Key highlight 2"],
        "article_body_markdown": "Full breaking story with ## subheadings (350-450 words).",
        "featured_image_prompt": "Editorial press photo wide shot",
        "body_image_1_prompt": "Close-up action or key subject moment",
        "body_image_2_prompt": "Atmospheric background or supporting detail"
    }}
    """
    response_text = call_gemini_dynamic(prompt, active_models)
    return json.loads(response_text)


def run_automation():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)
    processed = load_processed_history()

    target_category = select_next_category()
    if not target_category:
        print("[★] আজকের জন্য সবকটি ক্যাটাগরির কোটা (প্রতিটিতে ২টি করে মোট ৮টি পোস্ট) ইতিমধ্যে পূর্ণ হয়েছে!")
        return

    tracker = get_daily_quota_tracker()
    print(f"\n[+] আজকের নির্বাচিত ক্যাটাগরি: {target_category} (আজ হয়েছে: {tracker['counts'][target_category]}/{DAILY_POSTS_PER_CATEGORY})")

    active_models = get_available_online_models()
    news_item = fetch_news_for_category(target_category)

    if not news_item:
        print(f"[!] {target_category}-এর জন্য কোনো ফ্রেশ সংবাদ পাওয়া যায়নি।")
        return

    print(f"[*] নিউজ প্রসেসিং শুরু: {news_item['title'][:65]}...")

    try:
        result = generate_humanized_news(news_item, active_models)
        slug = result.get("slug") or slugify(result.get("seo_headline", "news"))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        p_feat = result.get("featured_image_prompt") or f"{news_item['title']} event"
        p_body1 = result.get("body_image_1_prompt") or f"{news_item['title']} highlight"
        p_body2 = result.get("body_image_2_prompt") or f"{news_item['title']} atmosphere"

        print("  [*] ৩টি এআই ছবি তৈরি ও ডাউনলোড হচ্ছে...")
        img_feat = download_ai_image_with_retry(p_feat, f"{timestamp}_{slug[:25]}_featured.jpg")
        img_body1 = download_ai_image_with_retry(p_body1, f"{timestamp}_{slug[:25]}_body1.jpg")
        img_body2 = download_ai_image_with_retry(p_body2, f"{timestamp}_{slug[:25]}_body2.jpg")

        saved_images = {
            "featured": img_feat,
            "body_image_1": img_body1,
            "body_image_2": img_body2
        }

        # লোকাল ব্যাকআপ সেভ
        filename = f"{timestamp}_{slug[:35]}.json"
        with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
            json.dump({
                "category": target_category,
                "source_url": news_item["link"],
                "data": result,
                "saved_images": saved_images
            }, f, ensure_ascii=False, indent=2)

        # ওয়ার্ডপ্রেসে নির্ধারিত ক্যাটাগরি ও ৫-৭টি ট্যাগসহ পোস্ট
        is_published = post_to_wordpress(result, saved_images, target_category)

        if is_published:
            processed.add(news_item["link"])
            processed.add(news_item["title"])
            save_processed_history(processed)

            # ক্যাটাগরি কোটা আপডেট
            tracker["counts"][target_category] += 1
            save_daily_quota_tracker(tracker)
            print(f"\n[★] আপডেট: {target_category}-এ আজকের মোট পোস্ট সংখ্যা: {tracker['counts'][target_category]}/{DAILY_POSTS_PER_CATEGORY}")

    except Exception as e:
        print(f"[!] প্রসেসিংয়ে সমস্যা: {e}")


if __name__ == "__main__":
    run_automation()