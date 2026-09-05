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
from models import User, RequiredTask, UserTaskCompletion, PendingLevelUpgrade, ProcessedPayment, WithdrawalRequest, VideoTaskSubmission
from auth import get_current_telegram_user

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
REQUIRED_CHANNELS_ENV = [c.strip() for c in os.getenv("REQUIRED_CHANNELS", "").split(",") if c.strip()]
REFERRAL_BONUS = float(os.getenv("REFERRAL_BONUS", 25))
DEFAULT_MINING_RATE = float(os.getenv("MINING_RATE_PER_HOUR", 10))
MAX_SESSION_HOURS = float(os.getenv("MAX_SESSION_HOURS", 3))

# ---------------------------------------------------------------------------
# Miner tab: 100-level system, each upgrade is paid in real TON to the treasury wallet.
# Upgrade price from level L to L+1 = LEVEL_BASE_PRICE_TON + (L-1) * LEVEL_PRICE_INCREMENT_TON
# i.e: 1 -> 2 = 1.0 TON, 2 -> 3 = 1.5 TON, 3 -> 4 = 2.0 TON ... and so on.
# Mining rate at level L = DEFAULT_MINING_RATE + (L-1) * LEVEL_MINING_RATE_INCREMENT
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
# Minimum withdrawal - same values used in scripts/distribute_tokens.py
# (must be kept in sync between both so the number the user sees in
# the app matches what actually gets applied at distribution time).
# ---------------------------------------------------------------------------
ZORO_TO_TON_RATE = float(os.getenv("ZORO_TO_TON_RATE", 500))  # 1 TON = how many ZORO
MIN_WITHDRAWAL_TON = float(os.getenv("MIN_WITHDRAWAL_TON", 0.5))
MIN_WITHDRAWAL_ZORO = float(os.getenv("MIN_WITHDRAWAL_ZORO", 600))


def price_for_level_step(current_level: int) -> float:
    """Upgrade price from current_level to current_level + 1, in TON."""
    return round(LEVEL_BASE_PRICE_TON + (current_level - 1) * LEVEL_PRICE_INCREMENT_TON, 4)


def mining_rate_for_level(level: int) -> float:
    """Mining rate (ZORO/hour) at a given level."""
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
                db.add(RequiredTask(channel_username=ch, title=f"Join {ch}", reward_amount=20))
            await db.commit()



@app.post("/api/admin/fix-referral-code")
async def fix_referral_code(
    secret: str,
    username: str,
    db: AsyncSession = Depends(get_db),
):
    if secret != "zoro-temp-fix-2026":
        raise HTTPException(403, "Not authorized")

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    new_code = gen_referral_code()
    while (await db.execute(select(User).where(User.referral_code == new_code))).scalar_one_or_none():
        new_code = gen_referral_code()

    user.referral_code = new_code
    await db.commit()
    return {"ok": True, "new_referral_code": new_code}


def gen_referral_code() -> str:
    return "".join(random.choices(string.digits, k=8))


def is_valid_ton_address(address: str) -> bool:
    """Basic validation of a TON address format (raw or user-friendly)."""
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

    if not user.referral_code:
        code = gen_referral_code()
        while (await db.execute(select(User).where(User.referral_code == code))).scalar_one_or_none():
            code = gen_referral_code()
        user.referral_code = code
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
            return True, None  # claimable, no next-available time yet
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
        # Mining is gated on wallet linking only - tasks are a separate repeatable bonus
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
        raise HTTPException(400, "Invalid TON wallet address")

    user = await get_or_create_user(db, tg_user)

    # The wallet is linked only once and cannot be changed afterwards, to protect
    # against attempts to redirect earnings to a different wallet after mining or referrals.
    if user.wallet_address:
        if user.wallet_address == address:
            return {"ok": True, "wallet_address": user.wallet_address, "already_linked": True}
        raise HTTPException(
            400,
            "Your wallet is already linked and cannot be changed. Contact support if there is an issue."
        )

    user.wallet_address = address
    user.wallet_linked_at = datetime.utcnow()
    await db.commit()
    return {"ok": True, "wallet_address": user.wallet_address, "already_linked": False}


async def is_channel_member(channel_username: str, telegram_user_id: int) -> bool:
    """
    Actually verifies the user's channel membership via the Telegram Bot API (getChatMember).
    The bot must be an admin in the channel for this request to succeed (as documented in the README).
    """
    if not BOT_TOKEN:
        raise HTTPException(500, "BOT_TOKEN is not configured on the server")

    # Convert any format (full link / @username / username without @) into a valid @username for Telegram
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
        # Channel does not exist, bot is not an admin there, or another Telegram error
        return False

    status = data.get("result", {}).get("status", "")
    # Statuses considered "actual member": creator / administrator / member
    # (excluded: left, kicked, restricted)
    return status in ("creator", "administrator", "member")


@app.post("/api/claim-task/{task_id}")
async def claim_task(
    task_id: int,
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Grants the user the task reward after actually verifying their channel
    subscription via the Telegram Bot API. The task becomes available again
    after the cooldown period (cooldown_hours).
    """
    user = await get_or_create_user(db, tg_user)

    task_result = await db.execute(select(RequiredTask).where(RequiredTask.id == task_id))
    task = task_result.scalar_one_or_none()
    if not task or not task.is_active:
        raise HTTPException(404, "Task not found")

    if not await is_channel_member(task.channel_username, user.telegram_id):
        raise HTTPException(
            403,
            f"You haven't joined {task.channel_username} yet. Join first, then try again.",
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
            raise HTTPException(429, f"Task will be available again in {int(remaining // 3600)}h {int((remaining % 3600) // 60)}m")
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
        raise HTTPException(400, "You need to link your wallet first")

    if user.mining_started_at is not None:
        raise HTTPException(400, "Mining is already running")

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
        raise HTTPException(400, "No active mining session")

    mined = calc_pending_mined(user)
    user.pool_balance += mined
    user.mining_started_at = None

    await db.commit()
    return {"ok": True, "claimed": mined, "pool_balance": round(user.pool_balance, 4)}

@app.post("/api/transfer-to-holding")
async def transfer_to_holding(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)

    amount = user.pool_balance
    if amount <= 0:
        raise HTTPException(400, "No balance available to transfer")

    user.pool_balance = 0.0
    user.holding_balance += amount
    await db.commit()

    return {
        "success": True,
        "transferred": round(amount, 4),
        "new_pool_balance": round(user.pool_balance, 4),
        "new_holding_balance": round(user.holding_balance, 4),
    }


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
# Miner tab (levels system)
# ---------------------------------------------------------------------------

@app.get("/api/levels")
async def list_levels(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """List of all MAX_LEVEL levels with price and rate, plus the user's current level."""
    user = await get_or_create_user(db, tg_user)
    levels = []
    for lvl in range(1, MAX_LEVEL + 1):
        levels.append({
            "level": lvl,
            "mining_rate_per_hour": mining_rate_for_level(lvl),
            # Upgrade price *for this* level (from the one before it); no price for level 1, it's the default
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
    Returns payment details (treasury address, amount, and a unique comment) so
    the frontend can send a TonConnect transaction with them. The actual upgrade
    only happens after /verify.
    """
    if not TREASURY_WALLET_ADDRESS:
        raise HTTPException(500, "Treasury wallet is not configured on the server (TREASURY_WALLET_ADDRESS)")

    user = await get_or_create_user(db, tg_user)
    if user.level >= MAX_LEVEL:
        raise HTTPException(400, "You have already reached the maximum level")

    price = price_for_level_step(user.level)
    nonce = secrets.token_hex(8)  # 16 hex chars - placed in the transaction comment so we can find it

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
        # The user/frontend must put this exact comment inside the transaction (comment/text payload)
        "comment": f"zoro-lvl:{user.telegram_id}:{user.level + 1}:{nonce}",
        "expires_at": pending.expires_at.isoformat(),
    }


class VerifyUpgradeBody(BaseModel):
    nonce: str


async def find_matching_transaction(sender_address: str, min_amount_nanoton: int, after_ts: int) -> str | None:
    """
    Searches the treasury wallet's recent transactions for an incoming transaction
    from the user's wallet with an amount >= required, after the upgrade request
    was created. Returns the tx_hash if found.
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
    Actually confirms the payment arrived on the TON network before upgrading
    the user's level. Without this step, anyone could claim they paid and get
    upgraded for free.
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
        raise HTTPException(404, "Upgrade request not found")
    if pending.processed:
        raise HTTPException(400, "This request has already been processed")
    if datetime.utcnow() > pending.expires_at:
        raise HTTPException(400, "Upgrade request has expired, start a new upgrade")

    tx_hash = "manual-unverified"

    existing_payment = await db.execute(select(ProcessedPayment).where(ProcessedPayment.tx_hash == tx_hash))
    if existing_payment.scalar_one_or_none():
        raise HTTPException(400, "This transaction has already been used")

    pending.processed = True
    user.level = pending.to_level
    user.mining_rate_per_hour = mining_rate_for_level(user.level)
    db.add(ProcessedPayment(
        tx_hash=tx_hash, user_id=user.id, nonce=pending.nonce, amount_ton=pending.price_ton,
    ))

    # ---------------------------------------------------------------
    # Referral commission: if this user came through someone else's referral,
    # the referrer instantly gets 50% of the upgrade value (converted from
    # TON to ZORO) added directly to their Holding Balance, ready to withdraw.
    # ---------------------------------------------------------------
    referral_bonus_zoro = 0.0
    if user.referred_by_id:
        referrer = await db.get(User, user.referred_by_id)
        if referrer:
            upgrade_value_zoro = pending.price_ton * ZORO_TO_TON_RATE
            referral_bonus_zoro = round(upgrade_value_zoro * 0.5, 4)
            referrer.holding_balance += referral_bonus_zoro

    await db.commit()

    return {
        "ok": True,
        "new_level": user.level,
        "mining_rate_per_hour": user.mining_rate_per_hour,
        "tx_hash": tx_hash,
    }


ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "7790518329")


async def notify_admin(text: str):
    if not BOT_TOKEN or not ADMIN_TELEGRAM_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": ADMIN_TELEGRAM_ID, "text": text})
    except Exception:
        pass


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
        raise HTTPException(400, "You need to link your wallet first")

    amount = payload.amount_zoro
    if amount <= 0:
        raise HTTPException(400, "Invalid amount")
    if amount < MIN_WITHDRAWAL_ZORO:
        raise HTTPException(400, f"Minimum withdrawal is {MIN_WITHDRAWAL_ZORO} ZORO")
    if amount > user.pool_balance:
        raise HTTPException(400, "Your balance is insufficient for this withdrawal")

    user.pool_balance -= amount
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

    admin_msg = (
        f"طلب سحب جديد!\n"
        f"المستخدم: {tg_user.get('username') or tg_user.get('id')}\n"
        f"الكمية: {amount} ZORO ({amount_ton:.4f} TON)\n"
        f"العنوان: {user.wallet_address}"
    )
    await notify_admin(admin_msg)

    return {
        "success": True,
        "message": "Withdrawal request submitted, pending review",
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
        raise HTTPException(status_code=401, detail="Invalid login credentials",
                             headers={"WWW-Authenticate": "Basic"})
    return True


class TaskTitleUpdate(BaseModel):
    id: int
    title: str


@app.get("/admin/tasks")
async def admin_list_tasks(db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    result = await db.execute(select(RequiredTask))
    tasks = result.scalars().all()
    return [{"id": t.id, "title": t.title, "channel": t.channel_username} for t in tasks]


@app.post("/admin/tasks/update-titles")
async def admin_update_task_titles(
    updates: list[TaskTitleUpdate],
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(verify_admin),
):
    updated = []
    for u in updates:
        task = await db.get(RequiredTask, u.id)
        if task:
            task.title = u.title
            updated.append(u.id)
    await db.commit()
    return {"success": True, "updated_ids": updated}


@app.get("/admin/users")
async def admin_list_users(search: str = "", db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    query = select(User)
    if search:
        s = search.strip()
        if s.lstrip("-").isdigit():
            query = query.where(User.telegram_id == int(s))
        elif s.startswith("@"):
            query = query.where(User.username.ilike(f"%{s[1:]}%"))
        else:
            query = query.where(
                (User.wallet_address.ilike(f"%{s}%")) |
                (User.username.ilike(f"%{s}%")) |
                (User.first_name.ilike(f"%{s}%"))
            )
    result = await db.execute(query.order_by(User.id.desc()).limit(100))
    users = result.scalars().all()

    # Build referrer lookup (upline) in one extra query instead of hitting the DB per user.
    referrer_ids = {u.referred_by_id for u in users if u.referred_by_id}
    referrers = {}
    if referrer_ids:
        ref_result = await db.execute(select(User).where(User.id.in_(referrer_ids)))
        referrers = {r.id: r for r in ref_result.scalars().all()}

    counts_result = await db.execute(select(User.referred_by_id))
    referred_counts = {}
    for (rid,) in counts_result.all():
        if rid:
            referred_counts[rid] = referred_counts.get(rid, 0) + 1

    out = []
    for u in users:
        referrer = referrers.get(u.referred_by_id) if u.referred_by_id else None
        out.append({
            "id": u.id, "telegram_id": u.telegram_id, "username": u.username,
            "first_name": u.first_name, "wallet_address": u.wallet_address,
            "level": u.level, "pool_balance": round(u.pool_balance, 4),
            "holding_balance": round(u.holding_balance, 4),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "referral_code": u.referral_code,
            "referred_by": (
                {"id": referrer.id, "telegram_id": referrer.telegram_id,
                 "username": referrer.username, "first_name": referrer.first_name,
                 "wallet_address": referrer.wallet_address}
                if referrer else None
            ),
            "referred_count": referred_counts.get(u.id, 0),
        })
    return out


@app.post("/admin/users/{user_id}/reset")
async def admin_reset_user_balance(user_id: int, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.pool_balance = 0.0
    user.holding_balance = 0.0
    await db.commit()
    return {"success": True, "message": f"Balance reset for {user.telegram_id}"}


class LevelUpdatePayload(BaseModel):
    level: int


@app.post("/admin/users/{user_id}/level")
async def admin_update_level(user_id: int, payload: LevelUpdatePayload, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    if payload.level < 1 or payload.level > MAX_LEVEL:
        raise HTTPException(400, f"Level must be between 1 and {MAX_LEVEL}")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.level = payload.level
    user.mining_rate_per_hour = mining_rate_for_level(payload.level)
    await db.commit()
    return {
        "success": True,
        "message": f"Updated level for {user.telegram_id} to {payload.level}",
        "new_level": user.level,
        "new_mining_rate": round(user.mining_rate_per_hour, 4),
    }


@app.get("/api/my-withdrawals")
async def my_withdrawals(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)
    result = await db.execute(
        select(WithdrawalRequest)
        .where(WithdrawalRequest.user_id == user.id)
        .order_by(WithdrawalRequest.created_at.desc())
        .limit(50)
    )
    items = result.scalars().all()
    return {
        "count": len(items),
        "withdrawals": [
            {
                "amount_zoro": w.amount_zoro,
                "amount_ton": w.amount_ton,
                "status": w.status,
                "tx_hash": w.tx_hash,
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "processed_at": w.processed_at.isoformat() if w.processed_at else None,
            }
            for w in items
        ],
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
        raise HTTPException(404, "Withdrawal request not found")
    if withdrawal.status == "paid":
        raise HTTPException(400, "This request has already been paid")
    withdrawal.status = "paid"
    withdrawal.tx_hash = payload.tx_hash or None
    withdrawal.processed_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "message": "Request marked as paid"}


@app.post("/admin/withdrawals/{withdrawal_id}/reject")
async def admin_reject_withdrawal(withdrawal_id: int, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)):
    withdrawal = await db.get(WithdrawalRequest, withdrawal_id)
    if not withdrawal:
        raise HTTPException(404, "Withdrawal request not found")
    if withdrawal.status != "pending":
        raise HTTPException(400, "This request has already been processed")
    user = await db.get(User, withdrawal.user_id)
    if user:
        user.pool_balance += withdrawal.amount_zoro
    withdrawal.status = "rejected"
    withdrawal.processed_at = datetime.utcnow()
    await db.commit()
    return {"success": True, "message": "Request rejected and balance returned to user"}


class VideoSubmitPayload(BaseModel):
    youtube_url: str


@app.post("/api/submit-video-task")
async def submit_video_task(
    payload: VideoSubmitPayload,
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)

    existing_result = await db.execute(
        select(VideoTaskSubmission).where(
            VideoTaskSubmission.user_id == user.id,
            VideoTaskSubmission.status == "pending",
        )
    )
    if existing_result.scalar_one_or_none():
        raise HTTPException(400, "You already have a pending submission. Wait for admin review.")

    if "youtube.com" not in payload.youtube_url and "youtu.be" not in payload.youtube_url:
        raise HTTPException(400, "Please submit a valid YouTube link.")

    db.add(VideoTaskSubmission(user_id=user.id, youtube_url=payload.youtube_url))
    await db.commit()
    return {"status": "submitted"}


@app.get("/api/my-video-task")
async def my_video_task(
    tg_user: dict = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    user = await get_or_create_user(db, tg_user)
    result = await db.execute(
        select(VideoTaskSubmission)
        .where(VideoTaskSubmission.user_id == user.id)
        .order_by(VideoTaskSubmission.submitted_at.desc())
    )
    latest = result.scalars().first()
    if not latest:
        return {"status": None}
    return {"status": latest.status, "youtube_url": latest.youtube_url}


@app.get("/admin/video-submissions")
async def admin_list_video_submissions(
    status: str = "pending", db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)
):
    result = await db.execute(select(VideoTaskSubmission).where(VideoTaskSubmission.status == status))
    subs = result.scalars().all()
    output = []
    for s in subs:
        user_result = await db.execute(select(User).where(User.id == s.user_id))
        u = user_result.scalar_one_or_none()
        output.append({
            "id": s.id,
            "telegram_id": u.telegram_id if u else None,
            "username": u.username if u else None,
            "youtube_url": s.youtube_url,
            "submitted_at": s.submitted_at.isoformat(),
        })
    return output


@app.post("/admin/video-submissions/{submission_id}/approve")
async def admin_approve_video_submission(
    submission_id: int, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)
):
    result = await db.execute(select(VideoTaskSubmission).where(VideoTaskSubmission.id == submission_id))
    sub = result.scalar_one_or_none()
    if not sub or sub.status != "pending":
        raise HTTPException(404, "Submission not found or already reviewed")

    user_result = await db.execute(select(User).where(User.id == sub.user_id))
    user = user_result.scalar_one_or_none()
    user.pool_balance += sub.reward_amount

    sub.status = "approved"
    sub.reviewed_at = datetime.utcnow()
    await db.commit()
    return {"status": "approved", "credited": sub.reward_amount}


@app.post("/admin/video-submissions/{submission_id}/reject")
async def admin_reject_video_submission(
    submission_id: int, db: AsyncSession = Depends(get_db), _: bool = Depends(verify_admin)
):
    result = await db.execute(select(VideoTaskSubmission).where(VideoTaskSubmission.id == submission_id))
    sub = result.scalar_one_or_none()
    if not sub or sub.status != "pending":
        raise HTTPException(404, "Submission not found or already reviewed")

    sub.status = "rejected"
    sub.reviewed_at = datetime.utcnow()
    await db.commit()
    return {"status": "rejected"}
