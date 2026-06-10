"""
Playwright - intercept X.com SearchTimeline API response.
"""

import asyncio
import json
import logging
import os
import re
import random
from datetime import datetime
from typing import Optional
from urllib.parse import quote
import httpx
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

import builtins as _builtins
_orig_print = _builtins.print
def print(*args, **kwargs):
    _orig_print(f"[{datetime.now().strftime('%H:%M:%S')}]", *args, **kwargs)

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
        random.shuffle(keywords)
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
    # output_dir = Path("data")
    # output_dir.mkdir(exist_ok=True)
    # output_file = output_dir / "data.json"
    # with open(output_file, "w", encoding="utf-8") as f:
    #     json.dump(data, f, ensure_ascii=False, indent=2)
    # print(f"[OK] Đã lưu {total} posts ra: {output_file}")

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
            "source_id":         data.get("user_id", None),
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


# ── Run with GPM ─────────────────────────────────────────────────────────────
async def _start_profile(profile_id: str, gpm_api: str) -> Optional[str]:
    """Mở GPM profile và trả về debug_addr, hoặc None nếu thất bại."""
    try:
        active_resp = requests.get(f"{gpm_api}/profiles/active/{profile_id}")
        active_resp.raise_for_status()
        active_json = active_resp.json()
        print(f"[GPM:{profile_id}] Active check: {active_json}")

        if active_json.get("data") and active_json["data"].get("remote_debugging_address"):
            debug_addr = active_json["data"]["remote_debugging_address"]
            if str(debug_addr).isdigit():
                debug_addr = f"127.0.0.1:{debug_addr}"
            print(f"[GPM:{profile_id}] Profile đã mở sẵn, debug_addr={debug_addr}")
            return debug_addr

        print(f"[GPM:{profile_id}] Profile chưa mở, đang start...")
        resp = requests.get(f"{gpm_api}/profiles/start/{profile_id}")
        resp.raise_for_status()
        resp_json = resp.json()
        print(f"[GPM:{profile_id}] Start response: {resp_json}")

        data = resp_json.get("data")
        if not data:
            logger.error(f"[GPM:{profile_id}] Không lấy được 'data': {resp_json}")
            return None

        debug_addr = data.get("remote_debugging_address") or data.get("remote_debugging_port")
        if not debug_addr:
            logger.error(f"[GPM:{profile_id}] Không tìm thấy remote_debugging_address: {data}")
            return None

        if str(debug_addr).isdigit():
            debug_addr = f"127.0.0.1:{debug_addr}"

        print(f"[GPM:{profile_id}] debug_addr={debug_addr}")
        print(f"[GPM:{profile_id}] Chờ 8s để browser khởi động...")
        await asyncio.sleep(8)
        return debug_addr

    except Exception as e:
        logger.error(f"[GPM:{profile_id}] Lỗi khi kiểm tra/start profile: {e}")
        return None


async def _close_profile(profile_id: str, gpm_api: str, browser=None):
    """Đóng browser playwright và GPM profile."""
    try:
        if browser:
            await browser.close()
    except Exception:
        pass
    try:
        requests.get(f"{gpm_api}/profiles/close/{profile_id}")
        print(f"[GPM:{profile_id}] Profile đã đóng")
    except Exception as e:
        logger.error(f"[GPM:{profile_id}] Failed to stop profile: {e}")


async def _run_single_profile(profile_id: str, keywords: list[str], gpm_api: str):
    """Crawl toàn bộ keywords với 1 GPM profile.
    Mỗi keyword: mở profile → crawl → đóng profile → chờ.
    """
    x_post     = XPost()
    all_tweets = []

    for idx, keyword in enumerate(keywords):
        # ── Mở profile ────────────────────────────────────────────────────────
        debug_addr = await _start_profile(profile_id, gpm_api)
        if not debug_addr:
            print(f"[GPM:{profile_id}] Bỏ qua keyword '{keyword}' do không start được profile")
            continue

        browser = None
        try:
            async with async_playwright() as p:
                last_error = None
                for attempt in range(1, 11):
                    try:
                        print(f"[GPM:{profile_id}] Attempt {attempt}/10: Đang connect tới http://{debug_addr}...")
                        browser = await p.chromium.connect_over_cdp(f"http://{debug_addr}")
                        print(f"[GPM:{profile_id}] ✓ Kết nối thành công!")
                        break
                    except Exception as e:
                        last_error = e
                        error_msg = str(e)
                        if "ECONNREFUSED" in error_msg:
                            print(f"[GPM:{profile_id}] ✗ Attempt {attempt}: Port chưa mở")
                        elif "timeout" in error_msg.lower():
                            print(f"[GPM:{profile_id}] ✗ Attempt {attempt}: Timeout")
                        else:
                            print(f"[GPM:{profile_id}] ✗ Attempt {attempt}: {error_msg[:150]}")

                        if attempt < 10:
                            print(f"[GPM:{profile_id}] Chờ 3s rồi thử lại...")
                            await asyncio.sleep(3)

                if not browser:
                    print(f"[GPM:{profile_id}] ⚠ Không thể connect qua CDP sau 10 lần thử")
                    print(f"[GPM:{profile_id}] ℹ GPM có thể chưa bật remote debugging")
                    print(f"[GPM:{profile_id}] ℹ Hướng dẫn: Vào GPM Settings → bật Remote Debugging")
                    print(f"[GPM:{profile_id}] ℹ Hoặc mở profile thủ công trong GPM trước khi chạy script")
                    raise Exception(f"Không thể kết nối sau 10 lần thử. Lỗi cuối: {last_error}")

                if not browser.contexts:
                    raise Exception("No browser context found from GPM")
                context = browser.contexts[0]

                pages = context.pages
                if pages:
                    page = pages[0]
                    print(f"[GPM:{profile_id}] Dùng lại tab: {page.url}")
                else:
                    page = await context.new_page()
                    print(f"[GPM:{profile_id}] Không có tab nào, tạo tab mới")

                # ── Crawl keyword ─────────────────────────────────────────────
                print(f"\n[GPM:{profile_id}][KW] Tìm kiếm: {keyword}")
                api_responses = []

                async def handle_response(response):
                    if "SearchTimeline" in response.url:
                        try:
                            body = await response.json()
                            api_responses.append(body)
                            print(f"  [GPM:{profile_id}][API] {response.url[:80]}...")
                        except Exception as e:
                            print(f"  [GPM:{profile_id}][WARN] {e}")

                page.on("response", handle_response)

                url = f"https://x.com/search?q={quote(keyword)}&src=typed_query&f=live"
                await page.goto(url, wait_until="domcontentloaded")

                pre_wait = random.uniform(1, 3)
                print(f"  [GPM:{profile_id}][WAIT] Chờ {pre_wait:.1f}s sau khi vào trang...")
                await asyncio.sleep(pre_wait)

                try:
                    await page.wait_for_selector('[data-testid="tweet"]', timeout=15000)
                except Exception:
                    print(f"  [GPM:{profile_id}][WARN] Không tìm thấy tweet cho: {keyword}")

                pre_scroll_wait = random.uniform(1, 3)
                print(f"  [GPM:{profile_id}][WAIT] Chờ {pre_scroll_wait:.1f}s trước khi scroll...")
                await asyncio.sleep(pre_scroll_wait)

                scroll_times = random.randint(10, 20)
                print(f"  [GPM:{profile_id}][SCROLL] Scroll {scroll_times} lần...")
                for _ in range(scroll_times):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await asyncio.sleep(random.uniform(2, 10))

                page.remove_listener("response", handle_response)

                kw_tweets = []
                for body in api_responses:
                    for tweet in extract_tweets(body):
                        kw_tweets.append(x_post.new(tweet))

                if kw_tweets:
                    result = await post_to_es_unclassified(kw_tweets)
                    print(f"  [GPM:{profile_id}][PUSH] success={result['success']} | total={result['total']} | status={result['status']}")
                    if not result['success']:
                        print(f"  [GPM:{profile_id}][PUSH] error: {result['error']}")
                    all_tweets.extend(kw_tweets)
                else:
                    print(f"  [GPM:{profile_id}][WARN] Không có tweet nào cho: {keyword}")

        except Exception as e:
            logger.exception(f"[GPM:{profile_id}] Error crawling keyword '{keyword}': {e}")

        # ── Đóng profile sau mỗi keyword ──────────────────────────────────────
        await _close_profile(profile_id, gpm_api, browser)

        # ── Chờ trước keyword tiếp theo ───────────────────────────────────────
        if idx < len(keywords) - 1:
            delay = random.randint(120, 240)
            print(f"  [GPM:{profile_id}][WAIT] Chờ {delay}s ({delay//60}p{delay%60}s) trước keyword tiếp theo...")
            await asyncio.sleep(delay)

    print(f"\n[GPM:{profile_id}][OK] Tổng: {len(all_tweets)} tweets từ {len(keywords)} keywords")


async def run_gpm():
    GPM_API     = os.environ.get("GPM_API", "")
    # Nhiều profile IDs, dạng: GPM_PROFILE_IDS=id1,id2,id3
    PROFILE_IDS = [x.strip() for x in os.environ.get("GPM_PROFILE_IDS", "").split(",") if x.strip()]

    if not GPM_API or not PROFILE_IDS:
        logger.error("[GPM] GPM_API hoặc GPM_PROFILE_IDS chưa được set trong .env")
        return

    if not ORG_IDS:
        logger.error("[ERROR] ORG_IDS chưa được set trong .env")
        return

    keywords = await get_keywords_from_mongo(ORG_IDS)
    if not keywords:
        logger.error("[ERROR] Không tìm thấy keyword nào trong MongoDB")
        return

    # Chia đều keywords cho từng profile
    random.shuffle(keywords)
    n = len(PROFILE_IDS)
    chunks = [keywords[i::n] for i in range(n)]
    print(f"[GPM] {n} profiles | {len(keywords)} keywords → mỗi profile ~{len(chunks[0])} keywords")

    # Chạy song song tất cả profiles
    await asyncio.gather(*[
        _run_single_profile(pid, chunk, GPM_API)
        for pid, chunk in zip(PROFILE_IDS, chunks)
    ])


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # if not os.path.exists(PROFILE_FILE):
    #     print(f"[INFO] Chưa có {PROFILE_FILE} → chạy save_profile()...")
    #     asyncio.run(save_profile())
    # else:
    #     asyncio.run(run())
    
    async def main():
        interval_minutes = int(os.environ.get("RUN_INTERVAL_MINUTES", "30"))
        interval_seconds = interval_minutes * 60
        print(f"[MAIN] Interval: {interval_minutes} phút ({interval_seconds}s)")

        while True:
            start = datetime.now()
            print(f"\n[MAIN] Bắt đầu lúc {start.strftime('%Y-%m-%d %H:%M:%S')}")
            try:
                await run_gpm()
            except Exception as e:
                logger.exception(f"[MAIN] Lỗi không mong muốn: {e}")
            elapsed = (datetime.now() - start).total_seconds()
            wait = max(0, interval_seconds - elapsed)
            print(f"[MAIN] Xong. Chờ {int(wait)}s ({int(wait/60)}p) trước lần chạy tiếp theo...")
            await asyncio.sleep(wait)

    asyncio.run(main())
