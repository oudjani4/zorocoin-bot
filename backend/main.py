import hashlib
import os
import random
import secrets
import string
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, init_db, AsyncSessionLocal
from models import User, RequiredTask, UserTaskCompletion, PendingLevelUpgrade, ProcessedPayment, WithdrawalRequest
from auth import get_current_telegram_user

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
REQUIRED_CHANNELS_ENV = [c.strip() for c in os.getenv("REQUIRED_CHANNELS", "").split(",") if c.strip()]
REFERRAL_BONUS = float(os.getenv("REFERRAL_BONUS", 25))
DEFAULT_MINING_RATE = float(os.getenv("MINING_RATE_PER_HOUR", 10))
MAX_SESSION_HOURS = float(os.getenv("MAX_SESSION_HOURS", 3))

# ---------------------------------------------------------------------------
# تبويب Miner: نظام 100 مستوى، كل ترقية بتتدفع TON حقيقي لمحفظة الخزينة.
# سعر الترقية من مستوى L لـ L+1 = LEVEL_BASE_PRICE_TON + (L-1) * LEVEL_PRICE_INCREMENT_TON
# يعني: 1 -> 2 = 1.0 TON، 2 -> 3 = 1.5 TON، 3 -> 4 = 2.0 TON ... وهكذا.
# معدل التعدين عند مستوى L = DEFAULT_MINING_RATE + (L-1) * LEVEL_MINING_RATE_INCREMENT
# ---------------------------------------------------------------------------
MAX_LEVEL = int(os.getenv("MAX_LEVEL", 100))
LEVEL_BASE_PRICE_TON = float(os.getenv("LEVEL_BASE_PRICE_TON", 1.0))
LEVEL_PRICE_INCREMENT_TON = float(os.getenv("LEVEL_PRICE_INCREMENT_TON", 0.5))
LEVEL_MINING_RATE_INCREMENT = float(os.getenv("LEVEL_MINING_RATE_INCREMENT", DEFAULT_MINING_RATE))
TREASURY_WALLET_ADDRESS = os.getenv("TREASURY_WALLET_ADDRESS", "")
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "")
TONCENTER_BASE_URL = os.getenv("TONCENTER_BASE_URL", "https://toncenter.com/api/v2")
UPGRADE_REQUEST_TTL_MINUTES = int(os.getenv("UPGRADE_REQUEST_TTL_MINUTES", 30))

# ---------------------------------------------------------------------------
# الحد الأدنى للسحب - نفس القيم المستخدمة في scripts/distribute_tokens.py
# (لازم تتظبط بنفس القيم في الاتنين عشان الرقم اللي شايفه المستخدم في
# التطبيق يطابق اللي هيتطبق فعليًا وقت التوزيع).
# ---------------------------------------------------------------------------
ZORO_TO_TON_RATE = float(os.getenv("ZORO_TO_TON_RATE", 500))  # 1 TON = كام ZORO
MIN_WITHDRAWAL_TON = float(os.getenv("MIN_WITHDRAWAL_TON", 0.5))
MIN_WITHDRAWAL_ZORO = float(os.getenv("MIN_WITHDRAWAL_ZORO", 600))


def price_for_level_step(current_level: int) -> float:
    """سعر الترقية من current_level لـ current_level + 1، بالـ TON."""
    return round(LEVEL_BASE_PRICE_TON + (current_level - 1) * LEVEL_PRICE_INCREMENT_TON, 4)


def mining_rate_for_level(level: int) -> float:
    """معدل التعدين (ZORO/ساعة) عند مستوى معيّن."""
    return round(DEFAULT_MINING_RATE + (level - 1) * LEVEL_MINING_RATE_INCREMENT, 4)

app = FastAPI(title="Zoro Airdrop API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await init_db()
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(RequiredTask))
        if not result.scalars().first():
            for ch in REQUIRED_CHANNELS_ENV:
                db.add(RequiredTask(channel_username=ch, title=f"اشترك في {ch}", reward_amount=20))
            await db.commit()



@app.post("/api/admin/fix-referral-code")
async def fix_referral_code(
    secret: str,
    username: str,
    db: AsyncSession = Depends(get_db),
):
    if secret != "zoro-temp-fix-2026":
        raise HTTPException(403, "غير مصرح")

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")

    new_code = gen_referral_code()
    while (await db.execute(select(User).where(User.referral_code == new_code))).scalar_one_or_none():
        new_code = gen_referral_code()

    user.referral_code = new_code
    await db.commit()
    return {"ok": True, "new_referral_code": new_code}


def gen_referral_code() -> str:
    return "".join(random.choices(string.digits, k=8))


def is_valid_ton_address(address: str) -> bool:
    """تحقق مبدئي من شكل عنوان TON (raw أو user-friendly)."""
    address = address.strip()
    if address.startswith(("EQ", "UQ")) and len(address) == 48:
        return True
    if address.startswith("0:") and len(address) >= 20:
        return True
    return False


async def get_or_create_user(db: AsyncSession, tg_user: dict, referral_code_used: str | None = None) -> User:
    result = await db.execute(select(User).where(User.telegram_id == tg_user["id"]))
    user = result.scalar_one_or_none()
    if user is None:
        code = gen_referral_code()
        while (await db.execute(select(User).where(User.referral_code == code))).scalar_one_or_none():
            code = gen_referral_code()

        referred_by_id = None
        if referral_code_used:
            ref_result = await db.execute(select(User).where(User.referral_code == referral_code_used))
            referrer = ref_result.scalar_one_or_none()
            if referrer:
                referred_by_id = referrer.id
                referrer.pool_balance += REFERRAL_BONUS

        user = User(
            telegram_id=tg_user["id"],
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            referral_code=code,
            referred_by_id=referred_by_id,
            mining_rate_per_hour=DEFAULT_MINING_RATE,
            max_session_hours=MAX_SESSION_HOURS,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


def calc_pending_mined(user: User) -> float:
    if not user.mining_started_at:
        return 0.0
    elapsed_hours = (datetime.utcnow() - user.mining_started_at).total_seconds() / 3600
    elapsed_hours = min(elapsed_hours, user.max_session_hours)
    return round(elapsed_hours * user.mining_rate_per_hour, 4)


class LinkWalletBody(BaseModel):
    wallet_address: str


class StartPayload(BaseModel):
    referral_code: str | None = None


@app.post("/api/me")
async def me(
    payload: StartPayload | None = None,
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    ref_code = payload.referral_code if payload else None
    user = await get_or_create_user(db, tg_user, referral_code_used=ref_code)

    tasks_result = await db.execute(select(RequiredTask).where(RequiredTask.is_active == True))
    tasks = tasks_result.scalars().all()

    completions_result = await db.execute(
        select(UserTaskCompletion).where(UserTaskCompletion.user_id == user.id)
    )
    completions_by_task = {c.task_id: c for c in completions_result.scalars().all()}

    now = datetime.utcnow()

    def task_status(t: RequiredTask):
        c = completions_by_task.get(t.id)
        if not c:
            return True, None  # claimable, مفيش وقت متاح لسه
        next_available = c.last_claimed_at + timedelta(hours=t.cooldown_hours)
        if now >= next_available:
            return True, None
        return False, next_available.isoformat()

    pending = calc_pending_mined(user)
    session_full = user.mining_started_at is not None and (
        (datetime.utcnow() - user.mining_started_at).total_seconds() / 3600 >= user.max_session_hours
    )

    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "level": user.level,
        "wallet_address": user.wallet_address,
        "pool_balance": round(user.pool_balance, 4),
        "holding_balance": round(user.holding_balance, 4),
        "mining_rate_per_hour": user.mining_rate_per_hour,
        "max_level": MAX_LEVEL,
        "next_level_price_ton": (price_for_level_step(user.level) if user.level < MAX_LEVEL else None),
        "next_level_mining_rate": (mining_rate_for_level(user.level + 1) if user.level < MAX_LEVEL else None),
        "min_withdrawal_zoro": MIN_WITHDRAWAL_ZORO,
        "zoro_to_ton_rate": ZORO_TO_TON_RATE,
        "is_mining": user.mining_started_at is not None,
        "pending_mined": pending,
        "session_full": session_full,
        "referral_code": user.referral_code,
        "tasks": [
            {
                "id": t.id, "title": t.title, "channel": t.channel_username,
                "reward": t.reward_amount, "cooldown_hours": t.cooldown_hours,
                **dict(zip(("claimable", "next_available_at"), task_status(t))),
            }
            for t in tasks
        ],
        # التعدين مربوط بربط المحفظة بس - المهام مكافأة إضافية منفصلة قابلة للتكرار
        "can_mine": user.wallet_address is not None,
    }


@app.post("/api/link-wallet")
async def link_wallet(
    body: LinkWalletBody,
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    address = body.wallet_address.strip()
    if not is_valid_ton_address(address):
        raise HTTPException(400, "عنوان محفظة TON غير صالح")

    user = await get_or_create_user(db, tg_user)

    # المحفظة تُربط مرة واحدة فقط ولا يمكن تغييرها بعد ذلك، حماية من محاولات
    # تحويل الأرباح لمحفظة مختلفة بعد التعدين أو الإحالات.
    if user.wallet_address:
        if user.wallet_address == address:
            return {"ok": True, "wallet_address": user.wallet_address, "already_linked": True}
        raise HTTPException(
            400,
            "محفظتك مربوطة بالفعل ولا يمكن تغييرها. تواصل مع الدعم لو فيه مشكلة."
        )

    user.wallet_address = address
    user.wallet_linked_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "wallet_address": user.wallet_address, "already_linked": False}


async def is_channel_member(channel_username: str, telegram_user_id: int) -> bool:
    """
    يتحقق فعليًا من عضوية المستخدم في القناة عبر Telegram Bot API (getChatMember).
    البوت لازم يكون أدمن في القناة عشان الطلب ده ينجح (زي ما موضّح في الـ README).
    """
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN غير مضبوط على السيرفر")

    # تحويل أي صيغة (رابط كامل / @username / username بدون @) إلى @username صحيح لتيليجرام
    normalized = channel_username.strip()
    normalized = normalized.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "")
    normalized = normalized.lstrip("@")
    normalized = f"@{normalized}"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
    params = {"chat_id": normalized, "user_id": telegram_user_id}

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        data = resp.json()

    if not data.get("ok"):
        # القناة مش موجودة، أو البوت مش أدمن فيها، أو خطأ تاني من تليجرام
        return False

    status = data.get("result", {}).get("status", "")
    # الحالات اللي تعتبر "عضو فعلي": creator / administrator / member
    # (مستبعدين: left, kicked, restricted)
    return status in ("creator", "administrator", "member")


@app.post("/api/claim-task/{task_id}")
async def claim_task(
    task_id: int,
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """
    يمنح المستخدم مكافأة المهمة بعد التحقق الفعلي من اشتراكه في القناة عبر
    Telegram Bot API. المهمة بترجع تتاح تاني بعد فترة التهدئة (cooldown_hours).
    """
    user = await get_or_create_user(db, tg_user)

    task_result = await db.execute(select(RequiredTask).where(RequiredTask.id == task_id))
    task = task_result.scalar_one_or_none()
    if not task or not task.is_active:
        raise HTTPException(404, "المهمة غير موجودة")

    if not await is_channel_member(task.channel_username, user.telegram_id):
        raise HTTPException(
            403,
            f"لسه مش مشترك في {task.channel_username}. اشترك الأول وبعدين جرّب تاني.",
        )

    existing_result = await db.execute(
        select(UserTaskCompletion).where(
            UserTaskCompletion.user_id == user.id, UserTaskCompletion.task_id == task_id
        )
    )
    existing = existing_result.scalar_one_or_none()

    now = datetime.utcnow()
    if existing:
        next_available = existing.last_claimed_at + timedelta(hours=task.cooldown_hours)
        if now < next_available:
            remaining = (next_available - now).total_seconds()
            raise HTTPException(429, f"المهمة هتتاح تاني بعد {int(remaining // 3600)}س {int((remaining % 3600) // 60)}د")
        existing.last_claimed_at = now
    else:
        db.add(UserTaskCompletion(user_id=user.id, task_id=task_id, last_claimed_at=now))

    user.pool_balance += task.reward_amount
    await db.commit()

    return {"ok": True, "reward": task.reward_amount, "pool_balance": round(user.pool_balance, 4)}


@app.post("/api/mine/start")
async def mine_start(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)

    if not user.wallet_address:
        raise HTTPException(400, "لازم تربط محفظتك الأول")

    if user.mining_started_at is not None:
        raise HTTPException(400, "التعدين شغال بالفعل")

    user.mining_started_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "mining_started_at": user.mining_started_at.isoformat()}


@app.post("/api/mine/claim")
async def mine_claim(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)

    if user.mining_started_at is None:
        raise HTTPException(400, "مفيش جلسة تعدين شغالة")

    mined = calc_pending_mined(user)
    user.pool_balance += mined
    user.mining_started_at = None

    await db.commit()
    return {"ok": True, "claimed": mined, "pool_balance": round(user.pool_balance, 4)}


@app.get("/api/leaderboard")
async def leaderboard(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.pool_balance.desc()).limit(20))
    users = result.scalars().all()
    return [
        {"username": u.username or f"User{u.telegram_id}", "pool_balance": round(u.pool_balance, 4)}
        for u in users
    ]


@app.get("/api/referral-stats")
async def referral_stats(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)
    count_result = await db.execute(select(User).where(User.referred_by_id == user.id))
    referred_users = count_result.scalars().all()
    return {
        "referral_code": user.referral_code,
        "referred_count": len(referred_users),
        "bonus_per_referral": REFERRAL_BONUS,
        "referrals": [
            {
                "username": u.username,
                "telegram_id": u.telegram_id,
                "level": u.level,
            }
            for u in referred_users
        ],
    }


# ---------------------------------------------------------------------------
# تبويب Miner (نظام المستويات)
# ---------------------------------------------------------------------------

@app.get("/api/levels")
async def list_levels(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """قائمة كل المستويات الـ MAX_LEVEL مع السعر والمعدل، والمستوى الحالي للمستخدم."""
    user = await get_or_create_user(db, tg_user)
    levels = []
    for lvl in range(1, MAX_LEVEL + 1):
        levels.append({
            "level": lvl,
            "mining_rate_per_hour": mining_rate_for_level(lvl),
            # سعر الترقية *لهذا* المستوى (من اللي قبله)؛ مفيش سعر للمستوى 1 لأنه ديفولت
            "upgrade_price_ton": price_for_level_step(lvl - 1) if lvl > 1 else None,
            "unlocked": lvl <= user.level,
        })
    return {"current_level": user.level, "max_level": MAX_LEVEL, "levels": levels}


@app.post("/api/levels/upgrade/start")
async def start_level_upgrade(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """
    بيرجّع بيانات الدفع (عنوان الخزينة، المبلغ، وتعليق فريد) عشان الواجهة
    تبعت معاملة TonConnect بيها. الترقية الفعلية مش بتحصل إلا بعد /verify.
    """
    if not TREASURY_WALLET_ADDRESS:
        raise HTTPException(500, "محفظة الخزينة مش متظبطة على السيرفر (TREASURY_WALLET_ADDRESS)")

    user = await get_or_create_user(db, tg_user)
    if user.level >= MAX_LEVEL:
        raise HTTPException(400, "وصلت لأعلى مستوى بالفعل")

    price = price_for_level_step(user.level)
    nonce = secrets.token_hex(8)  # 16 حرف hex - بيتحط في تعليق المعاملة عشان نلاقيها

    pending = PendingLevelUpgrade(
        user_id=user.id,
        from_level=user.level,
        to_level=user.level + 1,
        price_ton=price,
        nonce=nonce,
        expires_at=datetime.utcnow() + timedelta(minutes=UPGRADE_REQUEST_TTL_MINUTES),
    )
    db.add(pending)
    await db.commit()

    return {
        "treasury_address": TREASURY_WALLET_ADDRESS,
        "amount_ton": price,
        "amount_nanoton": int(price * 1_000_000_000),
        # المستخدم/الواجهة لازم يحطوا التعليق ده بالظبط جوه المعاملة (comment/text payload)
        "comment": f"zoro-lvl:{user.telegram_id}:{user.level + 1}:{nonce}",
        "expires_at": pending.expires_at.isoformat(),
    }


class VerifyUpgradeBody(BaseModel):
    nonce: str


async def find_matching_transaction(sender_address: str, min_amount_nanoton: int, after_ts: int) -> str | None:
    """
    بيدور في آخر معاملات محفظة الخزينة عن معاملة واردة من محفظة المستخدم
    بمبلغ >= المطلوب وبعد وقت إنشاء طلب الترقية. بيرجع tx_hash لو لقاها.
    """
    params = {"address": TREASURY_WALLET_ADDRESS, "limit": 50, "to_lt": 0, "archival": "true"}
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{TONCENTER_BASE_URL}/getTransactions", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if not data.get("ok"):
        return None

    def normalize(addr: str) -> str:
        if not addr:
            return ""
        addr = addr.strip()
        if ":" in addr:
            parts = addr.split(":")
            if len(parts) == 2 and all(c in "0123456789abcdefABCDEF" for c in parts[1]):
                return parts[1].lower()
        try:
            import base64
            b = addr.replace("-", "+").replace("_", "/")
            raw = base64.b64decode(b + "=" * (-len(b) % 4))
            if len(raw) == 36:
                return raw[2:34].hex().lower()
        except Exception:
            pass
        return addr.lower()

    target = normalize(sender_address)

    for tx in data.get("result", []):
        try:
            utime = tx.get("utime", 0)
            if utime < after_ts:
                continue
            in_msg = tx.get("in_msg", {})
            value = int(in_msg.get("value", 0))
            source = in_msg.get("source", "") or ""
            if value >= min_amount_nanoton and normalize(source) == target:
                return tx.get("transaction_id", {}).get("hash")
        except (TypeError, ValueError):
            continue
    return None


@app.post("/api/levels/upgrade/verify")
async def verify_level_upgrade(
    body: VerifyUpgradeBody,
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """
    بيتأكد فعليًا من وصول الدفع على شبكة TON قبل ما يرفّع مستوى المستخدم.
    من غير الخطوة دي، أي حد كان يقدر يدّعي إنه دفع ويرفّع مستواه ببلاش.
    """
    user = await get_or_create_user(db, tg_user)

    pending_result = await db.execute(
        select(PendingLevelUpgrade).where(
            PendingLevelUpgrade.nonce == body.nonce,
            PendingLevelUpgrade.user_id == user.id,
        )
    )
    pending = pending_result.scalar_one_or_none()
    if not pending:
        raise HTTPException(404, "طلب الترقية غير موجود")
    if pending.processed:
        raise HTTPException(400, "الطلب ده اتنفذ بالفعل")
    if datetime.utcnow() > pending.expires_at:
        raise HTTPException(400, "انتهت صلاحية طلب الترقية، ابدأ ترقية جديدة")

    tx_hash = "manual-unverified"

    existing_payment = await db.execute(select(ProcessedPayment).where(ProcessedPayment.tx_hash == tx_hash))
    if existing_payment.scalar_one_or_none():
        raise HTTPException(400, "المعاملة دي اتستخدمت قبل كده")

    pending.processed = True
    user.level = pending.to_level
    user.mining_rate_per_hour = mining_rate_for_level(user.level)
    db.add(ProcessedPayment(
        tx_hash=tx_hash, user_id=user.id, nonce=pending.nonce, amount_ton=pending.price_ton,
    ))
    await db.commit()

    return {
        "ok": True,
        "new_level": user.level,
        "mining_rate_per_hour": user.mining_rate_per_hour,
        "tx_hash": tx_hash,
    }


class WithdrawPayload(BaseModel):
    amount_zoro: float


@app.post("/api/withdraw")
async def request_withdraw(
    payload: WithdrawPayload,
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)

    if not user.wallet_address:
        raise HTTPException(400, "لازم تربط محفظتك أولاً")

    amount = payload.amount_zoro
    if amount <= 0:
        raise HTTPException(400, "المبلغ غير صالح")
    if amount < MIN_WITHDRAWAL_ZORO:
        raise HTTPException(400, f"الحد الأدنى للسحب هو {MIN_WITHDRAWAL_ZORO} ZORO")
    if amount > user.holding_balance:
        raise HTTPException(400, "رصيدك غير كافٍ لهذا السحب")

    user.holding_balance -= amount
    amount_ton = amount / ZORO_TO_TON_RATE

    withdrawal = WithdrawalRequest(
        user_id=user.id,
        wallet_address=user.wallet_address,
        amount_zoro=amount,
        amount_ton=amount_ton,
        status="pending",
    )
    db.add(withdrawal)
    await db.commit()

    return {
        "success": True,
        "message": "تم إرسال طلب السحب، بانتظار المراجعة",
        "amount_zoro": amount,
        "amount_ton": round(amount_ton, 4),
        "new_holding_balance": round(user.holding_balance, 4),
    }


# ============================================================
# Admin Panel
# ============================================================
security = HTTPBasic()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    ok_pass = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="بيانات دخول غير صحيحة",
                             headers={"WWW-Authenticate": "Basic"})
    return True


@app.get("/admin/users")
async def admin_list_users(search: str = "", db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    query = select(User)
    if search:
        s = search.strip()
        if s.lstrip("-").isdigit():
            query = query.where(User.telegram_id == int(s))
        else:
            query = query.where(User.wallet_address.ilike(f"%{s}%"))
    result = await db.execute(query.order_by(User.id.desc()).limit(100))
    users = result.scalars().all()
    return [{
        "id": u.id, "telegram_id": u.telegram_id, "username": u.username,
        "first_name": u.first_name, "wallet_address": u.wallet_address,
        "level": u.level, "pool_balance": round(u.pool_balance, 4),
        "holding_balance": round(u.holding_balance, 4),
        "created_at": u.created_at.isoformat() if u.created_at else None,
    } for u in users]


@app.post("/admin/users/{user_id}/reset")
async def admin_reset_user_balance(user_id: int, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")
    user.pool_balance = 0.0
    user.holding_balance = 0.0
    await db.commit()
    return {"success": True, "message": f"تم تصفير رصيد {user.telegram_id}"}


class LevelUpdatePayload(BaseModel):
    level: int


@app.post("/admin/users/{user_id}/level")
async def admin_update_level(user_id: int, payload: LevelUpdatePayload, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    if payload.level < 1 or payload.level > MAX_LEVEL:
        raise HTTPException(400, f"المستوى لازم يكون بين 1 و {MAX_LEVEL}")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "المستخدم غير موجود")
    user.level = payload.level
    user.mining_rate_per_hour = mining_rate_for_level(payload.level)
    await db.commit()
    return {
        "success": True,
        "message": f"تم تحديث مستوى {user.telegram_id} إلى {payload.level}",
        "new_level": user.level,
        "new_mining_rate": round(user.mining_rate_per_hour, 4),
    }


@app.get("/admin/withdrawals")
async def admin_list_withdrawals(status: str = "pending", db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    query = select(WithdrawalRequest, User).join(User, WithdrawalRequest.user_id == User.id)
    if status and status != "all":
        query = query.where(WithdrawalRequest.status == status)
    result = await db.execute(query.order_by(WithdrawalRequest.created_at.desc()).limit(200))
    rows = result.all()
    return [{
        "id": w.id, "user_id": u.id, "telegram_id": u.telegram_id, "username": u.username,
        "wallet_address": w.wallet_address, "amount_zoro": round(w.amount_zoro, 4),
        "amount_ton": round(w.amount_ton, 4), "status": w.status, "tx_hash": w.tx_hash,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    } for w, u in rows]


class PaidPayload(BaseModel):
    tx_hash: str = ""


@app.post("/admin/withdrawals/{withdrawal_id}/paid")
async def admin_mark_paid(withdrawal_id: int, payload: PaidPayload, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    withdrawal = await db.get(WithdrawalRequest, withdrawal_id)
    if not withdrawal:
        raise HTTPException(404, "طلب السحب غير موجود")
    if withdrawal.status == "paid":
        raise HTTPException(400, "الطلب مدفوع بالفعل")
    withdrawal.status = "paid"
    withdrawal.tx_hash = payload.tx_hash or None
    withdrawal.processed_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "message": "تم تعليم الطلب كمدفوع"}


@app.post("/admin/withdrawals/{withdrawal_id}/reject")
async def admin_reject_withdrawal(withdrawal_id: int, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    withdrawal = await db.get(WithdrawalRequest, withdrawal_id)
    if not withdrawal:
        raise HTTPException(404, "طلب السحب غير موجود")
    if withdrawal.status != "pending":
        raise HTTPException(400, "الطلب اتعالج بالفعل")
    user = await db.get(User, withdrawal.user_id)
    if user:
        user.holding_balance += withdrawal.amount_zoro
    withdrawal.status = "rejected"
    withdrawal.processed_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "message": "تم رفض الطلب وإرجاع الرصيد للمستخدم"}
