# app/db/models.py
from __future__ import annotations
import enum
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text,
    text, Index, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TZ = ZoneInfo("Europe/Amsterdam")
def now_tz() -> datetime: return datetime.now(TZ)

class Base(DeclarativeBase): pass

# ---- Роли ----
class RoleEnum(str, enum.Enum):
    admin = "admin"
    trader = "trader"
    operator = "operator"

# ---- Системные настройки (как ранее) ----
class SystemSettings(Base):
    __tablename__ = "system_settings"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rub_per_usdt: Mapped[float] = mapped_column(Numeric(18, 6), server_default=text("100.00"))
    service_fee_default_pct: Mapped[float] = mapped_column(Numeric(6, 2), server_default=text("1.00"))
    service_fee_min_pct: Mapped[float] = mapped_column(Numeric(6, 2), server_default=text("0.00"))
    service_fee_max_pct: Mapped[float] = mapped_column(Numeric(6, 2), server_default=text("3.00"))
    service_fee_overridable: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    stage1_secs_default: Mapped[int] = mapped_column(Integer, server_default=text("60"))
    round_secs_default: Mapped[int] = mapped_column(Integer, server_default=text("20"))
    max_tie_rounds_default: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    pay_mins_default: Mapped[int] = mapped_column(Integer, server_default=text("20"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, onupdate=now_tz, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

# ---- Аудит ----
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_tg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    before: Mapped[str | None] = mapped_column(Text, nullable=True)
    after: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)

# ---- Пользователи ----
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tg_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True, nullable=True)
    role: Mapped[RoleEnum] = mapped_column(SAEnum(RoleEnum), default=RoleEnum.trader, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("0"), nullable=False)
    is_online: Mapped[bool] = mapped_column(Boolean, server_default=text("0"), nullable=False)
    rep: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    balance_rub: Mapped[float] = mapped_column(Numeric(18, 2), server_default=text("0.00"), nullable=False)
    balance_usdt: Mapped[float] = mapped_column(Numeric(18, 2), server_default=text("0.00"), nullable=False)
    reserved_usdt: Mapped[float] = mapped_column(Numeric(18, 2), server_default=text("0.00"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)

# ---- Профиль трейдера / карты (как ранее) ----
class TraderProfile(Base):
    __tablename__ = "trader_profiles"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str] = mapped_column(String(32))
    referral_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)
    user: Mapped["User"] = relationship()
    cards: Mapped[list["TraderCard"]] = relationship(back_populates="profile", cascade="all, delete-orphan")

class BankEnum(str, enum.Enum):
    TBANK = "ТБАНК"
    ALFABANK = "АЛЬФАБАНК"
    SBERBANK = "СБЕРБАНК"
    VTB = "ВТБ"
    YANDEX = "ЯНДЕКС БАНК"
    OZON = "ОЗОН БАНК"
    OTHER = "ДРУГОЙ БАНК"

class TraderCard(Base):
    __tablename__ = "trader_cards"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("trader_profiles.id"), index=True)
    bank: Mapped[BankEnum] = mapped_column(SAEnum(BankEnum), default=BankEnum.TBANK)
    bank_other_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    card_number: Mapped[str] = mapped_column(String(32))  # хранить в маскируемом виде на UI
    holder_name: Mapped[str] = mapped_column(String(128))
    has_sbp: Mapped[bool] = mapped_column(Boolean, server_default=text("0"), nullable=False)
    sbp_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sbp_fullname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)
    profile: Mapped["TraderProfile"] = relationship(back_populates="cards")

# ---- Токены доступа ----
class AccessToken(Base):
    __tablename__ = "access_tokens"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("trader_profiles.id"), index=True)
    token: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

# ---- Сделки / Аукцион ----
class DealType(str, enum.Enum):
    BUY = "BUY"     # админ покупает RUB (старый сценарий)
    SELL = "SELL"   # ПРОДАЖА USDT с карты трейдера (этот сценарий)

class DealStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"                    # Этап 1 рассылается
    BIDDING = "BIDDING"              # Этап 2/3 идут
    WINNER_ASSIGNED = "WINNER_ASSIGNED"
    WAITING_ADMIN_TRANSFER = "WAITING_ADMIN_TRANSFER"   # админ должен отправить RUB на карту
    WAITING_TRADER_CONFIRM = "WAITING_TRADER_CONFIRM"   # админ подтвердил, ждём подтверждение трейдера
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    CANCELLED_BY_TRADER = "CANCELLED_BY_TRADER"
    CANCELLED_TIMEOUT = "CANCELLED_TIMEOUT"
    REVIEW = "REVIEW"

class Deal(Base):
    __tablename__ = "deals"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deal_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    deal_type: Mapped[DealType] = mapped_column(SAEnum(DealType), default=DealType.SELL, index=True)

    amount_rub: Mapped[float] = mapped_column(Numeric(18, 2))
    amount_usdt_snapshot: Mapped[float] = mapped_column(Numeric(18, 2))

    base_pct: Mapped[float] = mapped_column(Numeric(6, 2))           # верхняя граница ставок
    service_fee_pct: Mapped[float] = mapped_column(Numeric(6, 2))    # снэпшот комиссии сервиса

    # Выбранная победителем карта (снэпшоты)
    winner_card_id: Mapped[int | None] = mapped_column(ForeignKey("trader_cards.id"), nullable=True)
    winner_card_bank: Mapped[str | None] = mapped_column(String(64), nullable=True)
    winner_card_mask: Mapped[str | None] = mapped_column(String(32), nullable=True)
    winner_card_holder: Mapped[str | None] = mapped_column(String(128), nullable=True)

    audience_type: Mapped[str] = mapped_column(String(32))  # all / rep_range / personal
    audience_filter: Mapped[str | None] = mapped_column(String(256), nullable=True)  # JSON

    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    warning_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))

    round_secs: Mapped[int] = mapped_column(Integer, server_default=text("20"))
    max_tie_rounds: Mapped[int] = mapped_column(Integer, server_default=text("5"))

    # Этап 1 — принятие участия
    stage1_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage1_finished: Mapped[bool] = mapped_column(Boolean, server_default=text("0"), nullable=False)
    
    # Этап A — оплата админом
    pay_mins: Mapped[int] = mapped_column(Integer, server_default=text("20"))
    pay_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, server_default=text("0"), nullable=False)
    
    # Этап B — подтверждение трейдером
    confirm_mins: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    confirm_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[DealStatus] = mapped_column(SAEnum(DealStatus), index=True, default=DealStatus.DRAFT)
    winner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    winner_bid_pct: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)

Index("ix_deals_type_status", Deal.deal_type, Deal.status)

# Участники/ставки/раунды
class DealParticipant(Base):
    __tablename__ = "deal_participants"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    accepted: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)
    UniqueConstraint("deal_id", "user_id", name="uq_participant")

class DealRound(Base):
    __tablename__ = "deal_rounds"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer, index=True)  # 1=Этап2, 2..=тай-брейки
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("0"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class Bid(Base):
    __tablename__ = "bids"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer, index=True)
    pct: Mapped[float] = mapped_column(Numeric(6, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_tz, nullable=False)

Index("ix_bids_deal_round_user", Bid.deal_id, Bid.round_number, Bid.user_id, unique=True)
Index("ix_bids_deal_round_pct", Bid.deal_id, Bid.round_number, Bid.pct)

# --- UI таймеры ---
class UiTimerMessage(Base):
    __tablename__ = "ui_timer_messages"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="buy")  # buy|sell
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("chat_id", "deal_id", "kind", name="uix_ui_timer_chat_deal_kind"),
    )
