"""
Playwright - intercept X.com SearchTimeline API response.
"""

import asyncio
import json
import os
import re
import random
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import quote
import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from playwright.async_api import async_playwright

load_dotenv()

CHROME_EXECUTABLE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_FILE      = "chrome_profile.json"

# ── MongoDB config ────────────────────────────────────────────────────────────
MONGO_URI   = os.environ.get("MONGO_URI", "mongodb://root:@103.97.125.64:5525/")
MONGO_DB    = "abp_warehouse"
MONGO_COL   = "keyword"
# ORG_IDS từ env, dạng: ORG_IDS=123,456,789
ORG_IDS     = [int(x.strip()) for x in os.environ.get("ORG_IDS", "").split(",") if x.strip()]

async def get_keywords_from_mongo(org_ids: list[int]) -> list[str]:
    """Truy vấn keywords từ MongoDB theo danh sách org_id, platform=twitter."""
    client = AsyncIOMotorClient(MONGO_URI)
    try:
        col = client[MONGO_DB][MONGO_COL]
        cursor = col.find(
            {"org_id": {"$in": org_ids}},
            {"keyword": 1, "_id": 0},
        )
        docs = await cursor.to_list(length=None)
        keywords = [d["keyword"] for d in docs if d.get("keyword")]
        print(f"[MONGO] Tìm thấy {len(keywords)} keywords cho org_ids={org_ids}")
        return keywords
    finally:
        client.close()


# ── API config ────────────────────────────────────────────────────────────────
HTTP_TIMEOUT = 30.0
ES_INDEX     = "not_classify_org_posts"


def _get_api_master() -> str:
    return os.environ.get("API_MASTER_URL", "http://localhost:8000")


def _build_urls() -> tuple[str, str]:
    base = _get_api_master()
    return (
        f"{base}/api/v1/posts/insert-unclassified-org-posts",
        f"{base}/api/v1/posts/insert-posts",
    )


async def post_to_es_unclassified(content: list) -> dict:
    url_unclassified, _ = _build_urls()
    total = len(content)
    data  = {
        "index":  ES_INDEX,
        "data":   content,
        "upsert": True,
    }

    # Lưu ra data/data.json
    output_dir = Path("data")
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "data.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] Đã lưu {total} posts ra: {output_file}")

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            print(f"[API] Đang push {total} posts lên: {url_unclassified}")
            response = await client.post(url_unclassified, json=data)
            if response.status_code >= 400:
                return {
                    "success":  False,
                    "total":    total,
                    "status":   response.status_code,
                    "error":    response.text,
                    "response": None,
                }
            return {
                "success":  True,
                "total":    total,
                "status":   response.status_code,
                "error":    None,
                "response": response.json(),
            }
    except Exception as e:
        return {
            "success":  False,
            "total":    total,
            "status":   None,
            "error":    str(e),
            "response": None,
        }


# ── XPost class ───────────────────────────────────────────────────────────────
class XPost:
    BASE_URL          = "https://x.com"
    crawl_source      = 17
    crawl_source_code = "x"
    auth_type         = 1
    source_type       = 17
    crawl_bot         = "x-1"

    def _build_post_url(self, screen_name: str, post_id: Optional[str]) -> str:
        if not post_id:
            return ""
        return f"{self.BASE_URL}/{screen_name}/status/{post_id}"

    def _build_author_url(self, screen_name: str) -> str:
        return f"{self.BASE_URL}/{screen_name}"

    def new(self, data: dict) -> dict:
        screen_name = data.get("screen_name", "")
        post_id     = data.get("id_str", None)
        media_urls  = data.get("media_urls", [])
        return {
            "doc_type":          1,
            "crawl_source":      self.crawl_source,
            "crawl_source_code": self.crawl_source_code,
            "pub_time":          data.get("created_at_unix", 0),
            "crawl_time":        int(datetime.now().timestamp()),
            "subject_id":        post_id,
            "title":             None,
            "description":       data.get("full_text", None),
            "content":           data.get("full_text", None),
            "url":               self._build_post_url(screen_name, post_id),
            "media_urls":        json.dumps(media_urls, ensure_ascii=False),
            "comments":          data.get("replies", 0),
            "shares":            data.get("retweets", 0),
            "reactions":         data.get("likes", 0),
            "favors":            0,
            "views":             int(data.get("views_count", 0) or 0),
            "web_tags":          "[]",
            "web_keywords":      "[]",
            "auth_id":           data.get("user_id", None),
            "auth_name":         data.get("screen_name", None),
            "auth_type":         self.auth_type,
            "auth_url":          self._build_author_url(screen_name),
            "source_id":         post_id,
            "source_type":       self.source_type,
            "source_name":       data.get("name", None),
            "source_url":        self._build_post_url(screen_name, post_id),
            "reply_to":          None,
            "level":             None,
            "sentiment":         0,
            "isPriority":        False,
            "crawl_bot":         self.crawl_bot,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────
def clean_text(text: str) -> str:
    """Thay thế các ký tự xuống dòng bằng dấu cách."""
    if not text:
        return text
    return re.sub(r'\n+', ' ', text).strip()


def to_unix(twitter_date: str) -> int:
    """'Wed Jun 26 06:53:32 +0000 2024' → unix timestamp"""
    if not twitter_date:
        return 0
    dt = datetime.strptime(twitter_date, "%a %b %d %H:%M:%S %z %Y")
    return int(dt.timestamp())


def extract_tweets(data: dict) -> list:
    """Trích xuất tweet từ GraphQL SearchTimeline response."""
    tweets = []
    try:
        instructions = (
            data["data"]["search_by_raw_query"]["search_timeline"]["timeline"]["instructions"]
        )
        for instruction in instructions:
            for entry in instruction.get("entries", []):
                tweet_result = (
                    entry.get("content", {})
                         .get("itemContent", {})
                         .get("tweet_results", {})
                         .get("result", {})
                )
                legacy = tweet_result.get("legacy", {})
                if not legacy:
                    continue

                user_result = (
                    tweet_result.get("core", {})
                                .get("user_results", {})
                                .get("result", {})
                )
                user_legacy = user_result.get("legacy", {})
                user_core   = user_result.get("core", {})
                avatar      = user_result.get("avatar", {})

                # Media URLs đính kèm trong tweet
                media_list = (
                    legacy.get("extended_entities", {}).get("media", [])
                    or legacy.get("entities", {}).get("media", [])
                )
                media_urls = [
                    m.get("media_url_https", "")
                    for m in media_list
                    if m.get("media_url_https")
                ]

                tweets.append({
                    "id_str":                  legacy.get("id_str", ""),
                    "user_id_str":             legacy.get("user_id_str", ""),
                    "full_text":               clean_text(legacy.get("full_text", "")),
                    "created_at":              legacy.get("created_at", ""),
                    "created_at_unix":         to_unix(legacy.get("created_at", "")),
                    "likes":                   legacy.get("favorite_count", 0),
                    "retweets":                legacy.get("retweet_count", 0),
                    "replies":                 legacy.get("reply_count", 0),
                    "views_count":             tweet_result.get("views", {}).get("count", "0"),
                    "user_id":                 user_result.get("rest_id", ""),
                    "screen_name":             user_core.get("screen_name", ""),
                    "name":                    user_core.get("name", ""),
                    "account_created_at":      user_core.get("created_at", ""),
                    "account_created_at_unix": to_unix(user_core.get("created_at", "")),
                    "image_url":               avatar.get("image_url", ""),
                    "description":             user_legacy.get("description", ""),
                    "followers_count":         user_legacy.get("followers_count", 0),
                    "friends_count":           user_legacy.get("friends_count", 0),
                    "fast_followers_count":    user_legacy.get("fast_followers_count", 0),
                    "normal_followers_count":  user_legacy.get("normal_followers_count", 0),
                    "media_count":             user_legacy.get("media_count", 0),
                    "has_graduated_access":    user_result.get("has_graduated_access", False),
                    "media_urls":              media_urls,
                })
    except (KeyError, TypeError):
        pass
    return tweets


# ── Profile ───────────────────────────────────────────────────────────────────
async def save_profile():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path=CHROME_EXECUTABLE,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()
        await page.goto("https://x.com/login")
        print(">>> Login xong nhấn Enter...")
        input()
        await context.storage_state(path=PROFILE_FILE)
        print(f"[OK] Đã lưu: {PROFILE_FILE}")
        await browser.close()


# ── Run ───────────────────────────────────────────────────────────────────────
async def run():
    if not ORG_IDS:
        print("[ERROR] ORG_IDS chưa được set trong .env")
        return

    keywords = await get_keywords_from_mongo(ORG_IDS)
    if not keywords:
        print("[ERROR] Không tìm thấy keyword nào trong MongoDB")
        return

    x_post = XPost()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path=CHROME_EXECUTABLE,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        context = await browser.new_context(
            storage_state=PROFILE_FILE,
            no_viewport=True,
        )
        page = await context.new_page()

        all_tweets = []

        for keyword in keywords:
            print(f"\n[KW] Tìm kiếm: {keyword}")
            api_responses = []

            async def handle_response(response):
                if "SearchTimeline" in response.url:
                    try:
                        data = await response.json()
                        api_responses.append(data)
                        print(f"  [API] {response.url[:80]}...")
                    except Exception as e:
                        print(f"  [WARN] {e}")

            page.on("response", handle_response)

            url = f"https://x.com/search?q={quote(keyword)}&src=typed_query&f=live"
            await page.goto(url, wait_until="domcontentloaded")
            try:
                await page.wait_for_selector('[data-testid="tweet"]', timeout=15000)
            except Exception:
                print(f"  [WARN] Không tìm thấy tweet cho: {keyword}")

            page.remove_listener("response", handle_response)

            # Extract tweets của keyword này
            kw_tweets = []
            for data in api_responses:
                for tweet in extract_tweets(data):
                    kw_tweets.append(x_post.new(tweet))

            # Push ngay lên API trước khi sang keyword tiếp theo
            if kw_tweets:
                result = await post_to_es_unclassified(kw_tweets)
                print(f"  [PUSH] success={result['success']} | total={result['total']} | status={result['status']}")
                if not result['success']:
                    print(f"  [PUSH] error: {result['error']}")
                all_tweets.extend(kw_tweets)
            else:
                print(f"  [WARN] Không có tweet nào cho: {keyword}")

            # Delay ngẫu nhiên 30-60s trước keyword tiếp theo
            if keyword != keywords[-1]:
                delay = random.randint(30, 60)
                print(f"  [WAIT] Chờ {delay}s trước keyword tiếp theo...")
                await asyncio.sleep(delay)

        print(f"\n[OK] Tổng: {len(all_tweets)} tweets từ {len(keywords)} keywords")

        input("\nNhấn Enter để đóng...")
        await browser.close()


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(PROFILE_FILE):
        print(f"[INFO] Chưa có {PROFILE_FILE} → chạy save_profile()...")
        asyncio.run(save_profile())
    else:
        asyncio.run(run())
