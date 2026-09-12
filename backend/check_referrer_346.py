import asyncio
from database import AsyncSessionLocal
from models import User
from sqlalchemy import select

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == 346))
        r = result.scalar_one_or_none()
        print("referrer 346 telegram_id:", r.telegram_id, "holding_balance:", r.holding_balance)

asyncio.run(check())
