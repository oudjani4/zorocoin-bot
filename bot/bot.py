import asyncio
import logging
import os

import requests
from aiohttp import web
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")
REQUIRED_CHANNELS = [c.strip() for c in os.getenv("REQUIRED_CHANNELS", "").split(",") if c.strip()]

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("zoro-bot")


def api_call(method: str, params: dict | None = None) -> dict:
    """Generic wrapper around a Telegram Bot API call."""
    try:
        resp = requests.post(f"{API_URL}/{method}", json=params or {}, timeout=15)
        return resp.json()
    except Exception as e:
        log.error(f"API call {method} failed: {e}")
        return {"ok": False, "error": str(e)}


def check_subscription(user_id: int) -> list:
    """يرجع لائحة القنوات اللي المستخدم ماشي مشترك فيها"""
    not_joined = []
    for channel in REQUIRED_CHANNELS:
        r = api_call("getChatMember", {"chat_id": channel, "user_id": user_id})
        if not r.get("ok"):
            log.warning(f"Error checking {channel}: {r}")
            not_joined.append(channel)
            continue
        status = r["result"]["status"]
        if status in ("left", "kicked"):
            not_joined.append(channel)
    return not_joined


def build_join_keyboard(missing_channels: list) -> dict:
    buttons = []
    for ch in missing_channels:
        username = ch.lstrip("@")
        buttons.append([{"text": f"➕ انضم لـ {ch}", "url": f"https://t.me/{username}"}])
    buttons.append([{"text": "✅ تحققت، كمّل", "callback_data": "check_sub"}])
    return {"inline_keyboard": buttons}


def build_webapp_keyboard(url: str) -> dict:
    import time
    sep = "&" if "?" in url else "?"
    busted_url = f"{url}{sep}cb={int(time.time())}"
    return {
        "inline_keyboard": [
            [{"text": "🚀 افتح تطبيق Zoro Airdrop", "web_app": {"url": busted_url}}]
        ]
    }


def handle_start(message: dict):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "")
    parts = text.split(maxsplit=1)
    referral_code = parts[1] if len(parts) > 1 else None

    webapp_url = WEBAPP_URL
    if referral_code:
        sep = "&" if "?" in webapp_url else "?"
        webapp_url = f"{webapp_url}{sep}ref={referral_code}"

    if REQUIRED_CHANNELS:
        missing = check_subscription(user_id)
        if missing:
            api_call("sendMessage", {
                "chat_id": chat_id,
                "text": "قبل ما تقدر تستخدم البوت، خاصك تنضم لهاد القنوات:",
                "reply_markup": build_join_keyboard(missing),
            })
            return

    api_call("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "أهلاً بيك في Zoro Airdrop! 🎉\n"
            "اربط محفظتك وابدأ تجمع نقاط دلوقتي، ولما التوكن يتطلق هيتوزع عليك حسب رصيدك.\n\n"
            "اضغط الزرار تحت عشان تفتح التطبيق:"
        ),
        "reply_markup": build_webapp_keyboard(webapp_url),
    })


def handle_check_sub_callback(callback: dict):
    callback_id = callback["id"]
    user_id = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]

    missing = check_subscription(user_id)
    if missing:
        api_call("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "مازال ناقصك تنضم لبعض القنوات ⚠️",
            "show_alert": True,
        })
        return

    api_call("answerCallbackQuery", {"callback_query_id": callback_id})
    api_call("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": "تمام! ✅ دابا تقدر تفتح التطبيق:",
        "reply_markup": build_webapp_keyboard(WEBAPP_URL),
    })


def handle_fallback(message: dict):
    chat_id = message["chat"]["id"]
    api_call("sendMessage", {
        "chat_id": chat_id,
        "text": "استخدم الزرار تحت عشان تفتح التطبيق وتبدأ التجميع:",
        "reply_markup": build_webapp_keyboard(WEBAPP_URL),
    })


def process_update(update: dict):
    if "message" in update:
        message = update["message"]
        text = message.get("text", "")
        if text.startswith("/start"):
            handle_start(message)
        else:
            handle_fallback(message)
    elif "callback_query" in update:
        callback = update["callback_query"]
        if callback.get("data") == "check_sub":
            handle_check_sub_callback(callback)


def polling_loop():
    offset = 0
    log.info("Bot polling started")
    while True:
        try:
            r = requests.get(f"{API_URL}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=40)
            data = r.json()
            if not data.get("ok"):
                log.error(f"getUpdates failed: {data}")
                continue
            for update in data["result"]:
                offset = update["update_id"] + 1
                try:
                    process_update(update)
                except Exception as e:
                    log.error(f"Error processing update: {e}")
        except Exception as e:
            log.error(f"Polling error: {e}")


async def health_check(request):
    return web.Response(text="OK")


async def start_web_server():
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Health check server running on port {port}")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN غير موجود في ملف .env")
    if not WEBAPP_URL or not WEBAPP_URL.startswith("https://"):
        raise RuntimeError("WEBAPP_URL لازم يكون رابط HTTPS صحيح")

    me = api_call("getMe")
    if not me.get("ok"):
        raise RuntimeError(f"BOT_TOKEN غير صالح: {me}")
    log.info(f"Bot started: @{me['result']['username']}")

    await start_web_server()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, polling_loop)


if __name__ == "__main__":
    asyncio.run(main())
