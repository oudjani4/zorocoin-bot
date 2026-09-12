import asyncio
from sqlalchemy import select
from database import engine, AsyncSession
from models import User

REFERRAL_CODE = "60240719"

async def main():
    async with AsyncSession(engine) as db:
        ref_result = await db.execute(select(User).where(User.referral_code == REFERRAL_CODE))
        referrer = ref_result.scalar_one_or_none()
        if not referrer:
            print(f"❌ لم يتم العثور على مستخدم بكود الإحالة {REFERRAL_CODE}")
            return

        print(f"✅ صاحب الرابط: id={referrer.id}, telegram_id={referrer.telegram_id}, username={referrer.username}")

        result = await db.execute(
            select(User).where(User.referred_by_id.is_(None), User.id != referrer.id)
        )
        users = result.scalars().all()

        print(f"عدد المستخدمين بدون محيل: {len(users)}")

        count = 0
        for u in users:
            u.referred_by_id = referrer.id
            count += 1

        await db.commit()
        print(f"✅ تم ربط {count} مستخدم بصاحب الرابط {REFERRAL_CODE}")

if __name__ == "__main__":
    asyncio.run(main())
