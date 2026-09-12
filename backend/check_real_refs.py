import asyncio
from database import AsyncSessionLocal
from models import User
from sqlalchemy import select, func

async def check():
    async with AsyncSessionLocal() as db:
        total = await db.execute(select(func.count()).select_from(User).where(User.referred_by_id.is_not(None)))
        fallback = await db.execute(select(func.count()).select_from(User).where(User.referred_by_id == 2))
        real = await db.execute(select(func.count()).select_from(User).where(User.referred_by_id.is_not(None), User.referred_by_id != 2))
        print("total referred:", total.scalar())
        print("fallback (id=2):", fallback.scalar())
        print("real referrals (not id=2):", real.scalar())

        result = await db.execute(select(User).where(User.referred_by_id.is_not(None), User.referred_by_id != 2).limit(5))
        for u in result.scalars():
            print("  -> user id:", u.id, "telegram_id:", u.telegram_id, "level:", u.level, "referred_by_id:", u.referred_by_id)

asyncio.run(check())
