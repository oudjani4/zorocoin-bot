import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

# مثال DATABASE_URL: postgresql://user:pass@localhost:5432/dbname
# لازم نحول الـ driver لنسخة async
RAW_URL = os.getenv("DATABASE_URL", "postgresql://zoro_user:zoro_pass@localhost:5432/zoro_airdrop")
ASYNC_URL = RAW_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(ASYNC_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """ينشئ الجداول لو مش موجودة. يُستدعى مرة عند تشغيل السيرفر."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
