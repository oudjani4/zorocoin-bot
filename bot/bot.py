import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL")
REQUIRED_CHANNELS = [c.strip() for c in os.getenv("REQUIRED_CHANNELS", "").split(",") if c.strip()]

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    parts = message.text.split(maxsplit=1)
    referral_code = parts[1] if len(parts) > 1 else None

    webapp_url = WEBAPP_URL
    if referral_code:
        sep = "&" if "?" in webapp_url else "?"
        webapp_url = f"{webapp_url}{sep}ref={referral_code}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 افتح تطبيق Zoro Airdrop", web_app=WebAppInfo(url=webapp_url))]
        ]
    )

    channels_text = ""
    if REQUIRED_CHANNELS:
        channels_list = "\n".join(f"• {c}" for c in REQUIRED_CHANNELS)
        channels_text = f"\n\nقبل ما تقدر تجمع نقاط، لازم تكون مشترك في:\n{channels_list}"

    await message.answer(
        f"أهلاً بيك في Zoro Airdrop! 🎉\n"
        f"اربط محفظتك وابدأ تجمع نقاط دلوقتي، ولما التوكن يتطلق هيتوزع عليك حسب رصيدك."
        f"{channels_text}\n\n"
        f"اضغط الزرار تحت عشان تفتح التطبيق:",
        reply_markup=keyboard,
    )


@dp.message()
async def fallback_handler(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 افتح تطبيق Zoro Airdrop", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )
    await message.answer("استخدم الزرار تحت عشان تفتح التطبيق وتبدأ التجميع:", reply_markup=keyboard)


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

    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
