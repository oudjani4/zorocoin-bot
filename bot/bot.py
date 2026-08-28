import asyncio
import logging
import os

from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
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



async def check_subscription(bot, user_id: int) -> list:
    """يرجع لائحة القنوات اللي المستخدم ماشي مشترك فيها"""
    not_joined = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(channel)
        except Exception as e:
            print(f"Error checking {channel}: {e}")
            not_joined.append(channel)
    return not_joined


def build_join_keyboard(missing_channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in missing_channels:
        username = ch.lstrip("@")
        buttons.append([InlineKeyboardButton(text=f"➕ انضم لـ {ch}", url=f"https://t.me/{username}")])
    buttons.append([InlineKeyboardButton(text="✅ تحققت، كمّل", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    parts = message.text.split(maxsplit=1)
    referral_code = parts[1] if len(parts) > 1 else None

    webapp_url = WEBAPP_URL
    if referral_code:
        sep = "&" if "?" in webapp_url else "?"
        webapp_url = f"{webapp_url}{sep}ref={referral_code}"

    if REQUIRED_CHANNELS:
        missing = await check_subscription(message.bot, message.from_user.id)
        if missing:
            await message.answer(
                "قبل ما تقدر تستخدم البوت، خاصك تنضم لهاد القنوات:",
                reply_markup=build_join_keyboard(missing),
            )
            return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 افتح تطبيق Zoro Airdrop", web_app=WebAppInfo(url=webapp_url))]
        ]
    )
    await message.answer(
        f"أهلاً بيك في Zoro Airdrop! 🎉\n"
        f"اربط محفظتك وابدأ تجمع نقاط دلوقتي، ولما التوكن يتطلق هيتوزع عليك حسب رصيدك.\n\n"
        f"اضغط الزرار تحت عشان تفتح التطبيق:",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    missing = await check_subscription(callback.bot, callback.from_user.id)
    if missing:
        await callback.answer("مازال ناقصك تنضم لبعض القنوات ⚠️", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 افتح تطبيق Zoro Airdrop", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )
    await callback.message.edit_text(
        "تمام! ✅ دابا تقدر تفتح التطبيق:",
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
