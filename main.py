"""
Playwright - intercept X.com SearchTimeline API response.
"""

import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from playwright.async_api import async_playwright

CHROME_EXECUTABLE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_FILE      = "chrome_profile.json"
KEYWORD           = "Biểu tình"


# ── Lưu profile (chạy 1 lần nếu chưa có) ─────────────────────────────────────
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


# ── Chạy bot + intercept API ──────────────────────────────────────────────────
async def run():
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

        # ── Intercept SearchTimeline API ──────────────────────────────────────
        api_responses = []

        async def handle_response(response):
            if "SearchTimeline" in response.url:
                try:
                    data = await response.json()
                    api_responses.append(data)
                    print(f"[API] Nhận response từ: {response.url[:80]}...")

                    # Lấy danh sách tweet từ response
                    tweets = extract_tweets(data)
                    for tweet in tweets:
                        print(f"  → [{tweet['created_at']}] @{tweet['user']}: {tweet['text'][:80]}")
                except Exception as e:
                    print(f"[WARN] Không parse được response: {e}")

        page.on("response", handle_response)
        # ──────────────────────────────────────────────────────────────────────

        # Vào trang search
        from urllib.parse import quote
        url = f"https://x.com/search?q={quote(KEYWORD)}&src=typed_query&f=live"
        print(f"[1] Vào: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        # Chờ tweet đầu tiên xuất hiện thay vì networkidle
        await page.wait_for_selector('[data-testid="tweet"]', timeout=15000)

        print(f"\n[OK] Tổng số API response nhận được: {len(api_responses)}")

        # Lấy tất cả tweets từ các response
        all_tweets = []
        for data in api_responses:
            all_tweets.extend(extract_tweets(data))

        # Lưu ra data/data.json
        output_dir = Path("data")
        output_dir.mkdir(exist_ok=True)
        output_file = output_dir / "data.json"

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_tweets, f, ensure_ascii=False, indent=2)

        print(f"[OK] Đã lưu {len(all_tweets)} tweets ra: {output_file}")

        input("\nNhấn Enter để đóng...")
        await browser.close()


def to_unix(twitter_date: str) -> int:
    """Đổi Twitter date string sang Unix timestamp (seconds).
    Ví dụ: 'Wed Jun 26 06:53:32 +0000 2024' → 1719384812
    """
    if not twitter_date:
        return 0
    dt = datetime.strptime(twitter_date, "%a %b %d %H:%M:%S %z %Y")
    return int(dt.timestamp())


def extract_tweets(data: dict) -> list:
    """Trích xuất tweet từ GraphQL response của SearchTimeline."""
    tweets = []
    try:
        instructions = (
            data["data"]["search_by_raw_query"]["search_timeline"]["timeline"]["instructions"]
        )
        for instruction in instructions:
            entries = instruction.get("entries", [])
            for entry in entries:
                content = entry.get("content", {})
                item_content = content.get("itemContent", {})
                tweet_result = item_content.get("tweet_results", {}).get("result", {})

                legacy = tweet_result.get("legacy", {})
                if not legacy:
                    continue

                user_result = tweet_result.get("core", {}).get("user_results", {}).get("result", {})
                user_legacy = user_result.get("legacy", {})
                user_core   = user_result.get("core", {})
                avatar      = user_result.get("avatar", {})

                tweets.append({
                    # Tweet
                    "id_str":        legacy.get("id_str", ""),
                    "user_id_str":   legacy.get("user_id_str", ""),
                    "full_text":     legacy.get("full_text", ""),
                    "created_at":    legacy.get("created_at", ""),
                    "created_at_unix": to_unix(legacy.get("created_at", "")),
                    "likes":         legacy.get("favorite_count", 0),
                    "retweets":      legacy.get("retweet_count", 0),
                    "replies":       legacy.get("reply_count", 0),
                    "views_count":   tweet_result.get("views", {}).get("count", "0"),
                    # User
                    "user_id":           user_result.get("rest_id", ""),
                    "screen_name":       user_core.get("screen_name", ""),
                    "name":              user_core.get("name", ""),
                    "account_created_at":      user_core.get("created_at", ""),
                    "account_created_at_unix": to_unix(user_core.get("created_at", "")),
                    "image_url":         avatar.get("image_url", ""),
                    "description":       user_legacy.get("description", ""),
                    "followers_count":   user_legacy.get("followers_count", 0),
                    "friends_count":     user_legacy.get("friends_count", 0),
                    "fast_followers_count": user_legacy.get("fast_followers_count", 0),
                    "normal_followers_count": user_legacy.get("normal_followers_count", 0),
                    "media_count":       user_legacy.get("media_count", 0),
                    "has_graduated_access": user_result.get("has_graduated_access", False),
                })
    except (KeyError, TypeError):
        pass
    return tweets


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    if not os.path.exists(PROFILE_FILE):
        print(f"[INFO] Chưa có {PROFILE_FILE} → chạy save_profile()...")
        asyncio.run(save_profile())
    else:
        asyncio.run(run())
