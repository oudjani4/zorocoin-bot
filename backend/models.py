from datetime import datetime, date
from sqlalchemy import String, BigInteger, Integer, DateTime, Date, ForeignKey, Boolean, UniqueConstraint, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # عنوان محفظة TON (يبدأ عادة بـ EQ / UQ)
    wallet_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wallet_linked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # رصيد داخل التطبيق (Pool Wallet) - لسه معلق، هيتحول فعليًا وقت التوزيع
    pool_balance: Mapped[float] = mapped_column(Float, default=0.0)

    # رصيد اتحول فعليًا للمحفظة على الشبكة (Holding Wallet) - بيتحدث بعد التوزيع الحقيقي بس
    holding_balance: Mapped[float] = mapped_column(Float, default=0.0)

    level: Mapped[int] = mapped_column(Integer, default=1)

    # معدل التعدين (زورو في الساعة)
    mining_rate_per_hour: Mapped[float] = mapped_column(Float, default=10.0)
    # وقت بداية جلسة التعدين الحالية (None لو مش شغال)
    mining_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # أقصى مدة تعدين متواصل بالساعات قبل ما يحتاج يفتح التطبيق تاني ويكمل
    max_session_hours: Mapped[float] = mapped_column(Float, default=3.0)

    # نظام الإحالة (Referral)
    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    referred_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    task_completions: Mapped[list["UserTaskCompletion"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="UserTaskCompletion.user_id"
    )


class RequiredTask(Base):
    """قناة أو مهمة لازم المستخدم يعملها قبل ما يقدر يجمع نقاط."""
    __tablename__ = "required_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_username: Mapped[str] = mapped_column(String(255))  # مثال: @ZoroOfficialChannel
    title: Mapped[str] = mapped_column(String(255))
    reward_amount: Mapped[float] = mapped_column(Float, default=10.0)  # +10 Zoro زي الصورة
    cooldown_hours: Mapped[float] = mapped_column(Float, default=12.0)  # المهمة تتاح تاني كل كام ساعة
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserTaskCompletion(Base):
    """سجل آخر مرة استلم فيها المستخدم مكافأة المهمة دي (المهام قابلة للتكرار كل فترة تهدئة)."""
    __tablename__ = "user_task_completions"
    __table_args__ = (UniqueConstraint("user_id", "task_id", name="uq_user_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    task_id: Mapped[int] = mapped_column(ForeignKey("required_tasks.id"))
    last_claimed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="task_completions", foreign_keys=[user_id])


class DistributionBatch(Base):
    """
    دفعة توزيع واحدة (run) - بتتربط بالـ CSV اللي اتراجع وقت الـ dry-run.
    بنستخدمها عشان نضمن إن التنفيذ الفعلي يوزع بالظبط نفس المبالغ اللي اتراجعت،
    مش أي رصيد جديد اتجمع بعد المراجعة.
    """
    __tablename__ = "distribution_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(64))  # مثال: "2026-08-26"
    csv_path: Mapped[str] = mapped_column(String(500))
    is_testnet: Mapped[bool] = mapped_column(Boolean, default=True)
    jetton_master_address: Mapped[str] = mapped_column(String(255))
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    records: Mapped[list["DistributionRecord"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class DistributionRecord(Base):
    """
    سطر واحد لكل مستخدم جوه دفعة توزيع. الحالة (status) هي اللي بتخلي السكريبت
    "resumable": لو السكريبت وقع في نص التنفيذ، إعادة تشغيله بترجع تتحقق من
    الـ records اللي خلصت (status='success') وتتخطاها، وتكمل من اللي بعدها بس.
    """
    __tablename__ = "distribution_records"
    __table_args__ = (UniqueConstraint("batch_id", "user_id", name="uq_batch_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("distribution_batches.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    wallet_address: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / success / failed
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    batch: Mapped["DistributionBatch"] = relationship(back_populates="records")


class PendingLevelUpgrade(Base):
    """
    طلب ترقية مستوى لسه مانتظرش تأكيد الدفع. بيتعمل لما المستخدم يفتح تبويب
    Miner ويدوس "ترقية"، وبيتحول لـ processed=True بعد ما نتأكد إن الدفع
    وصل فعليًا على الشبكة (شوف /api/levels/verify في main.py).
    الـ nonce هو اللي بيتحط جوه تعليق (comment) معاملة الـ TON، عشان نلاقي
    المعاملة الصح وسط كل معاملات محفظة الخزينة.
    """
    __tablename__ = "pending_level_upgrades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    from_level: Mapped[int] = mapped_column(Integer)
    to_level: Mapped[int] = mapped_column(Integer)
    price_ton: Mapped[float] = mapped_column(Float)
    nonce: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)


class ProcessedPayment(Base):
    """
    سجل بكل معاملة TON اتقبلت فعليًا لترقية مستوى - عشان نمنع إعادة استخدام
    نفس الـ tx_hash مرتين (لو حد حاول يبعت نفس المعاملة للتحقق أكتر من مرة).
    """
    __tablename__ = "processed_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    tx_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    nonce: Mapped[str] = mapped_column(String(32))
    amount_ton: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WithdrawalRequest(Base):
    """طلب سحب رصيد ZORO الى محفظة TON - يحتاج موافقة يدوية من الأدمن."""
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    wallet_address: Mapped[str] = mapped_column(String(255))
    amount_zoro: Mapped[float] = mapped_column(Float)
    amount_ton: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending / approved / rejected / paid
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
