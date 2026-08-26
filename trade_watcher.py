import os
import time
import requests
import yt_dlp

YOUTUBE_URL = "https://www.youtube.com/live/v4zl0bkeIco"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram credentials are not configured.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=20
        )

        print("Telegram:", response.status_code)

    except Exception as error:
        print("Telegram error:", error)


def check_live():
    options = {
        "quiet": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["tv", "web_safari"]
            }
        }
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                YOUTUBE_URL,
                download=False
            )

        return (
            info.get("is_live", False),
            info.get("title", "Unknown stream")
        )

    except Exception as error:
        print("YouTube check error:", error)
        return False, None


print("================================")
print("       TRADE WATCHER")
print("================================")
print("YouTube:", YOUTUBE_URL)
print("Waiting for stream...\n")

last_status = False

while True:

    is_live, title = check_live()

    if is_live and not last_status:

        print("🟢 STREAM IS LIVE!")
        print("Title:", title)

        send_telegram(
            "🟢 TRADE WATCHER\n\n"
            "YouTube stream is LIVE!\n\n"
            f"{title}\n\n"
            "Watching for trade entries."
        )

    elif not is_live and last_status:

        print("🔴 STREAM ENDED")

        send_telegram(
            "🔴 TRADE WATCHER\n\n"
            "The YouTube stream has ended."
        )

    else:

        if is_live:
            print("🟢 LIVE")
        else:
            print("⚫ Offline")

    last_status = is_live

    time.sleep(60)
