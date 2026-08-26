"""
سكريبت توزيع Jetton (توكن TON) الفعلي على المستخدمين حسب رصيدهم في Pool Wallet.

الاستخدام:
   1. Dry run (مراجعة فقط، من غير أي تحويل):
      python scripts/distribute_tokens.py --label "2026-08-26"

      ده هيطلعلك:
        - ملف distribution_list_<label>.csv فيه كل المستحقين ومبالغهم
        - دفعة (DistributionBatch) جديدة في قاعدة البيانات بنفس المبالغ دي بالظبط،
          عشان أي تنفيذ فعلي بعد كده يوزع نفس الأرقام اللي راجعتها، مش أرقام
          جديدة اتغيرت لو الناس كملت تعدين بعد المراجعة.

   2. راجع الـ CSV كويس (بالعين!) قبل ما تكمل.

   3. تنفيذ فعلي (--execute):
      python scripts/distribute_tokens.py --label "2026-08-26" --execute

      - السكريبت بيقرأ نفس الدفعة اللي اتعملت وقت الـ dry-run (مش بيعمل query
        جديد على أرصدة المستخدمين).
      - بيحول لكل مستخدم على حدة، وبعد كل تحويل ناجح بيسجل الحالة "success"
        فورًا في قاعدة البيانات قبل ما يكمل للي بعده.
      - لو السكريبت وقع أو اتقفل في النص، شغّله تاني بنفس --label: هيتخطى
        اللي خلص (status=success) ويكمل من اللي فاضل بس. آمن للتكرار.

⚠️ تحذيرات مهمة قبل التنفيذ الفعلي:
   - جرّب على testnet الأول (--testnet) وبمبالغ تافهة، اتأكد إن كل حاجة شغالة
     زي ما تتوقع (المحفظة، الـ jetton wallet، صلاحية الاتصال بالـ API) قبل
     ما تلمس mainnet.
   - التحويل الفعلي عملية لا رجعة فيها. راجع الـ CSV بالكامل، وابدأ بدفعة
     صغيرة تجريبية على mainnet (كام مستخدم بس) قبل الدفعة الكاملة.
   - محفظة التوزيع لازم يكون فيها:
       (أ) رصيد كافي من توكن Zoro (Jetton) نفسه لتغطية كل المبالغ.
       (ب) رصيد TON كافي لدفع رسوم الغاز لكل تحويل (كل تحويل Jetton محتاج
           ~0.05-0.1 TON تقريبًا كغاز + forward fee، تأكد من الرقم الفعلي
           حسب شبكة TON وقت التنفيذ).
   - الـ mnemonic بتاع محفظة التوزيع أخطر سر في المشروع كله. متحطوش في .env
     على سيرفر مشترك أو ترفعه لأي مكان. فكّر تستخدم متغير بيئة مؤقت وقت
     التشغيل بس، أو hardware wallet / multisig لو المبالغ كبيرة.

المكتبات المطلوبة (ضيفها لـ requirements.txt):
   pip install tonutils

   tonutils بتغلف التعامل مع محافظ TON وتحويلات الـ Jetton بشكل مبسط.
   ⚠️ الـ API بتاعها ممكن يتغير بين النسخ - تأكد من التوثيق الرسمي على
   PyPI/GitHub (nessshon/tonutils) بيتوافق مع الكود تحت قبل ما تشغله فعليًا،
   خصوصًا توقيع (signature) دالة transfer_jetton في النسخة اللي مثبتة عندك.
"""
import argparse
import asyncio
import csv
import logging
import os
import sys
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User, DistributionBatch, DistributionRecord

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "distribution.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("distribute_tokens")

# ---------------------------------------------------------------------------
# الحد الأدنى للسحب: مبني على رسوم الغاز الحقيقية على TON + سعر الصرف بين
# ZORO والتوكن الفعلي، مش رقم عشوائي. المشروع (مش المستخدم) هو اللي بيدفع
# الغاز لكل تحويل في السكريبت ده، فلازم رصيد المستخدم يستاهل أكتر من الغاز
# بهامش أمان كويس، وإلا هتخسر فلوس فعلية على تحويلات تافهة.
#
#   ZORO_TO_TON_RATE   = كام ZORO = 1 TON عند التحويل الفعلي (قرارك انت، هتحدده
#                         وقت الإطلاق حسب إجمالي المعروض والقيمة المستهدفة)
#   MIN_WITHDRAWAL_TON  = أقل قيمة (بالـ TON) يستاهلها السحب - افتراضيًا 0.5 TON،
#                         يعني هامش أمان ~5-10x فوق رسوم الغاز (~0.05-0.1 TON)
#
# الاتنين قابلين للتعديل من .env من غير ما تلمس الكود.
# ---------------------------------------------------------------------------
ZORO_TO_TON_RATE = float(os.getenv("ZORO_TO_TON_RATE", 500))  # 1 TON = كام ZORO
MIN_WITHDRAWAL_TON = float(os.getenv("MIN_WITHDRAWAL_TON", 0.5))
MIN_BALANCE_TO_DISTRIBUTE = round(MIN_WITHDRAWAL_TON * ZORO_TO_TON_RATE, 4)  # بالـ ZORO

TX_DELAY_SECONDS = float(os.getenv("DISTRIBUTION_TX_DELAY_SECONDS", 4))  # فاصل بين كل تحويل وتاني
MAX_RETRIES_PER_USER = int(os.getenv("DISTRIBUTION_MAX_RETRIES", 3))


# ---------------------------------------------------------------------------
# مرحلة 1: بناء الدفعة (Dry run) - قراءة الأرصدة الحالية وتجميدها في CSV + DB
# ---------------------------------------------------------------------------

async def fetch_eligible_users():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.wallet_address.is_not(None), User.pool_balance >= MIN_BALANCE_TO_DISTRIBUTE)
        )
        return result.scalars().all()


def csv_path_for_label(label: str) -> str:
    return os.path.join(os.path.dirname(__file__), f"distribution_list_{label}.csv")


async def dry_run(label: str, jetton_master_address: str, is_testnet: bool):
    log.info(
        f"الحد الأدنى للسحب: {MIN_BALANCE_TO_DISTRIBUTE} ZORO "
        f"(= {MIN_WITHDRAWAL_TON} TON عند سعر {ZORO_TO_TON_RATE} ZORO = 1 TON)"
    )
    users = await fetch_eligible_users()
    if not users:
        log.warning("مفيش مستخدمين مستحقين للتوزيع دلوقتي.")
        return

    total = sum(u.pool_balance for u in users)
    path = csv_path_for_label(label)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["telegram_id", "username", "wallet_address", "amount_zoro"])
        for u in users:
            writer.writerow([u.telegram_id, u.username, u.wallet_address, round(u.pool_balance, 4)])

    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(DistributionBatch).where(DistributionBatch.label == label))
        if existing.scalar_one_or_none():
            log.error(
                f"في دفعة موجودة بالفعل بالاسم '{label}'. استخدم --label تاني عشان "
                f"متخلطش بين مراجعة قديمة وجديدة."
            )
            return

        batch = DistributionBatch(
            label=label,
            csv_path=path,
            is_testnet=is_testnet,
            jetton_master_address=jetton_master_address,
            total_amount=total,
            total_users=len(users),
        )
        db.add(batch)
        await db.flush()

        for u in users:
            db.add(DistributionRecord(
                batch_id=batch.id,
                user_id=u.id,
                wallet_address=u.wallet_address,
                amount=round(u.pool_balance, 4),
                status="pending",
            ))
        await db.commit()

    log.info(f"✅ دفعة '{label}': {len(users)} مستخدم، إجمالي {total:.4f} ZORO")
    log.info(f"✅ القايمة اتصدرت لـ {path}")
    log.info(f"⚠️ راجع الملف كويس، وبعدين شغّل بنفس --label مع --execute.")
    log.info(f"{'🧪 وضع testnet' if is_testnet else '🔴 وضع mainnet - فلوس حقيقية'}")


# ---------------------------------------------------------------------------
# مرحلة 2: التنفيذ الفعلي - تحويل Jetton حقيقي لكل مستخدم في الدفعة
# ---------------------------------------------------------------------------

async def get_wallet():
    """
    يجهز محفظة التوزيع من الـ mnemonic. مفصولة في دالة لوحدها عشان لو غيرت
    الـ SDK يوم من الأيام، ده المكان الوحيد اللي محتاج تعدله.
    """
    try:
        from tonutils.client import ToncenterClient
        from tonutils.wallet import WalletV4R2
    except ImportError:
        raise RuntimeError(
            "مكتبة tonutils مش متثبتة. شغّل: pip install tonutils"
        )

    mnemonic_raw = os.getenv("DISTRIBUTION_WALLET_MNEMONIC", "")
    if not mnemonic_raw:
        raise RuntimeError("DISTRIBUTION_WALLET_MNEMONIC مش موجود في .env")
    mnemonic = mnemonic_raw.split()

    api_key = os.getenv("TONCENTER_API_KEY", "")
    is_testnet = os.getenv("DISTRIBUTION_TESTNET", "true").lower() == "true"

    client = ToncenterClient(api_key=api_key, is_testnet=is_testnet)
    wallet, public_key, private_key, _ = WalletV4R2.from_mnemonic(client, mnemonic)
    return wallet, is_testnet


async def send_one_transfer(wallet, record: DistributionRecord, jetton_master_address: str, jetton_decimals: int) -> str:
    """
    ينفذ تحويل Jetton واحد ويرجع الـ tx hash. أي استثناء هنا معناه فشل التحويل
    (شبكة، رصيد غاز، عنوان غلط..) وبيتعامل معاه الكولر بإعادة المحاولة.
    """
    tx_hash = await wallet.transfer_jetton(
        destination=record.wallet_address,
        jetton_master_address=jetton_master_address,
        jetton_amount=record.amount,
        jetton_decimals=jetton_decimals,
        forward_payload="Zoro Airdrop",
    )
    return tx_hash


async def execute_distribution(label: str, jetton_decimals: int):
    async with AsyncSessionLocal() as db:
        batch_result = await db.execute(select(DistributionBatch).where(DistributionBatch.label == label))
        batch = batch_result.scalar_one_or_none()
        if not batch:
            log.error(f"مفيش دفعة بالاسم '{label}'. شغّل بدون --execute الأول عشان تعمل dry-run.")
            return

        records_result = await db.execute(
            select(DistributionRecord).where(DistributionRecord.batch_id == batch.id)
        )
        records = records_result.scalars().all()

    pending = [r for r in records if r.status != "success"]
    already_done = len(records) - len(pending)
    log.info(f"دفعة '{label}': {len(records)} سجل، {already_done} خلصوا قبل كده، {len(pending)} باقيين.")

    if not pending:
        log.info("✅ كل الدفعة اتوزعت خلاص.")
        return

    wallet, is_testnet = await get_wallet()
    if is_testnet != batch.is_testnet:
        log.error(
            f"⚠️ الدفعة دي اتعملت في وضع {'testnet' if batch.is_testnet else 'mainnet'} "
            f"بس المحفظة الحالية شغالة على {'testnet' if is_testnet else 'mainnet'}. "
            f"وقفت عشان متبعتش على الشبكة الغلط."
        )
        return

    success_count = 0
    fail_count = 0

    for record in pending:
        async with AsyncSessionLocal() as db:
            db_record = await db.get(DistributionRecord, record.id)
            db_record.attempts += 1

            try:
                tx_hash = await send_one_transfer(wallet, db_record, batch.jetton_master_address, jetton_decimals)

                db_record.status = "success"
                db_record.tx_hash = tx_hash
                db_record.error = None
                db_record.completed_at = datetime.utcnow()

                user = await db.get(User, db_record.user_id)
                if user:
                    # بنشيل بالظبط المبلغ اللي اتحول (مش كل الرصيد الحالي)، عشان لو
                    # المستخدم كمّل تعدين بعد المراجعة، الفرق يفضل في pool_balance
                    # للدفعة الجاية، ومايضاعش.
                    user.pool_balance = max(0.0, user.pool_balance - db_record.amount)
                    user.holding_balance += db_record.amount

                await db.commit()
                success_count += 1
                log.info(f"✅ user_id={db_record.user_id} amount={db_record.amount} tx={tx_hash}")

            except Exception as e:
                db_record.error = str(e)[:500]
                if db_record.attempts >= MAX_RETRIES_PER_USER:
                    db_record.status = "failed"
                    fail_count += 1
                    log.error(
                        f"❌ user_id={db_record.user_id} فشل نهائيًا بعد {db_record.attempts} محاولات: {e}"
                    )
                else:
                    log.warning(
                        f"⚠️ user_id={db_record.user_id} فشل (محاولة {db_record.attempts}/{MAX_RETRIES_PER_USER}): {e}"
                    )
                await db.commit()

        await asyncio.sleep(TX_DELAY_SECONDS)

    async with AsyncSessionLocal() as db:
        db_batch = await db.get(DistributionBatch, batch.id)
        still_pending = await db.execute(
            select(DistributionRecord).where(
                DistributionRecord.batch_id == batch.id, DistributionRecord.status == "pending"
            )
        )
        if not still_pending.scalars().first():
            db_batch.completed_at = datetime.utcnow()
            await db.commit()

    log.info(f"انتهت الجولة: {success_count} نجح، {fail_count} فشل نهائيًا.")
    if fail_count:
        log.info(f"شغّل نفس الأمر تاني بنفس --label عشان تعيد محاولة اللي فشل (لو المشكلة مؤقتة).")


async def main():
    parser = argparse.ArgumentParser(description="توزيع توكن Zoro على المستحقين")
    parser.add_argument("--label", required=True, help="اسم/تاريخ الدفعة، مثال: 2026-08-26")
    parser.add_argument("--jetton-master", default=os.getenv("JETTON_MASTER_ADDRESS", ""), help="عنوان الـ Jetton Master")
    parser.add_argument("--jetton-decimals", type=int, default=int(os.getenv("JETTON_DECIMALS", 9)))
    parser.add_argument("--testnet", action="store_true", default=os.getenv("DISTRIBUTION_TESTNET", "true").lower() == "true")
    parser.add_argument("--execute", action="store_true", help="نفّذ التحويلات فعليًا (خطر: لا يمكن التراجع)")
    args = parser.parse_args()

    if args.execute:
        if not args.jetton_master:
            log.error("محتاج --jetton-master أو JETTON_MASTER_ADDRESS في .env")
            return
        confirm = input(
            f"⚠️ هتنفذ توزيع فعلي حقيقي لدفعة '{args.label}' على "
            f"{'testnet' if args.testnet else 'mainnet (فلوس حقيقية)'}. اكتب 'YES' للتأكيد: "
        )
        if confirm != "YES":
            log.info("تم الإلغاء.")
            return
        await execute_distribution(args.label, args.jetton_decimals)
    else:
        if not args.jetton_master:
            log.error("محتاج --jetton-master أو JETTON_MASTER_ADDRESS في .env حتى وقت الـ dry-run (بيتسجل في الدفعة).")
            return
        await dry_run(args.label, args.jetton_master, args.testnet)


if __name__ == "__main__":
    asyncio.run(main())
