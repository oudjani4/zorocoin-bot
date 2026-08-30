"""
إرسال TON حقيقي من محفظة الخزينة لمحفظة مستخدم - يُستخدم في السحب الفوري.
مبني على البنية الفعلية المؤكدة لـ tonutils==2.2.0 (contracts.wallet).
"""
import asyncio
import os

from ton_core import to_nano, NetworkGlobalID
from tonutils.clients import ToncenterClient
from tonutils.contracts.wallet import WalletV4R2

_wallet = None
_send_lock = asyncio.Lock()


async def _get_wallet():
    global _wallet
    if _wallet is not None:
        return _wallet

    mnemonic_raw = os.getenv("TREASURY_WALLET_MNEMONIC", "")
    if not mnemonic_raw:
        raise RuntimeError("TREASURY_WALLET_MNEMONIC مش موجود في .env")

    api_key = os.getenv("TONCENTER_API_KEY", "") or None
    is_testnet = os.getenv("DISTRIBUTION_TESTNET", "true").lower() == "true"
    network = NetworkGlobalID.TESTNET if is_testnet else NetworkGlobalID.MAINNET

    client = ToncenterClient(network=network, api_key=api_key)
    wallet, _public_key, _private_key, _mnemonic = WalletV4R2.from_mnemonic(
        client, mnemonic_raw
    )
    _wallet = wallet
    return _wallet


async def send_ton(destination: str, amount_ton: float, comment: str = "Zoro Withdrawal") -> str:
    """
    يبعث TON حقيقي لعنوان معين ويرجع الـ tx hash (normalized_hash).
    محمي بـ lock عشان يمنع تنفيذ تحويلين بنفس اللحظة (race condition).
    """
    async with _send_lock:
        wallet = await _get_wallet()
        amount_nano = to_nano(amount_ton)
        external_msg = await wallet.transfer(
            destination=destination,
            amount=amount_nano,
            body=comment,
        )
        return external_msg.normalized_hash
