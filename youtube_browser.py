import asyncio
import os
import requests
from playwright.async_api import async_playwright

YOUTUBE_URL = "https://www.youtube.com/live/v4zl0bkeIco"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram credentials missing")
        return

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )


async def main():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--autoplay-policy=no-user-gesture-required"
            ]
        )

        page = await browser.new_page(
            viewport={"width": 1280, "height": 720}
        )

        print("Opening YouTube...")
        await page.goto(
            YOUTUBE_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(10000)

        print("Page title:", await page.title())
        print("URL:", page.url)

        telegram(
            "🟢 TRADE WATCHER\n\n"
            "Browser watcher started.\n"
            "YouTube page opened successfully."
        )

        while True:

            title = await page.title()

            print(
                "Watching:",
                title
            )

            await asyncio.sleep(60)


asyncio.run(main())
