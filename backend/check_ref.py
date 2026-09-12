import asyncio
from database import AsyncSessionLocal
from models import User
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.referred_by_id.is_not(None)).limit(1))
        u = result.scalar_one_or_none()
        if not u:
            print("no referred users found at all")
            return
        print("user id:", u.id, "telegram_id:", u.telegram_id, "level:", u.level, "referred_by_id:", u.referred_by_id)
        r = await db.get(User, u.referred_by_id)
        print("referrer id:", r.id, "referrer telegram_id:", r.telegram_id, "referrer holding_balance:", r.holding_balance)

asyncio.run(check())
