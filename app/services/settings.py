from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from app.db.base import async_session
from app.db.models import SystemSettings, AuditLog
from app.logging_setup import get_logger

logger = get_logger(__name__)

@dataclass
class SettingsDTO:
    rub_per_usdt: float
    service_fee_default_pct: float
    service_fee_min_pct: float
    service_fee_max_pct: float
    service_fee_overridable: bool
    stage1_secs_default: int
    round_secs_default: int
    max_tie_rounds_default: int
    pay_mins_default: int

_cache: SettingsDTO | None = None

async def get_settings() -> SettingsDTO:
    global _cache
    if _cache: return _cache
    async with async_session() as session:
        row = await session.scalar(select(SystemSettings).limit(1))
        _cache = SettingsDTO(
            rub_per_usdt=float(row.rub_per_usdt),
            service_fee_default_pct=float(row.service_fee_default_pct),
            service_fee_min_pct=float(row.service_fee_min_pct),
            service_fee_max_pct=float(row.service_fee_max_pct),
            service_fee_overridable=bool(row.service_fee_overridable),
            stage1_secs_default=int(row.stage1_secs_default),
            round_secs_default=int(row.round_secs_default),
            max_tie_rounds_default=int(row.max_tie_rounds_default),
            pay_mins_default=int(row.pay_mins_default),
        )
        return _cache

async def update_settings(actor_tg_id: int, **kwargs) -> SettingsDTO:
    """
    kwargs: любые поля SystemSettings из списка выше
    """
    global _cache
    async with async_session() as session:
        row = await session.scalar(select(SystemSettings).limit(1))
        before = {
            "rub_per_usdt": float(row.rub_per_usdt),
            "service_fee_default_pct": float(row.service_fee_default_pct),
            "service_fee_min_pct": float(row.service_fee_min_pct),
            "service_fee_max_pct": float(row.service_fee_max_pct),
            "service_fee_overridable": bool(row.service_fee_overridable),
            "stage1_secs_default": int(row.stage1_secs_default),
            "round_secs_default": int(row.round_secs_default),
            "max_tie_rounds_default": int(row.max_tie_rounds_default),
            "pay_mins_default": int(row.pay_mins_default),
        }
        for k, v in kwargs.items():
            if hasattr(row, k): setattr(row, k, v)
        row.updated_by = actor_tg_id
        await session.commit()

        after = {**before}
        for k in kwargs:
            after[k] = getattr(row, k)

        session.add(AuditLog(
            actor_tg_id=actor_tg_id,
            actor_role="admin",
            action="UPDATE",
            entity="system_settings",
            before=str(before),
            after=str(after)
        ))
        await session.commit()
    _cache = None
    return await get_settings()


# --- New: admin setter for RUB/USDT rate (compat with PATCH spec) ---
async def set_rub_per_usdt(new_rate: float | str | Decimal) -> SettingsDTO:
    """Update only rub_per_usdt with validation. Returns fresh SettingsDTO."""
    # normalize
    if isinstance(new_rate, str):
        new_rate = new_rate.strip().replace(" ", "").replace(",", ".")
    try:
        d = Decimal(str(new_rate))
    except (InvalidOperation, ValueError):
        raise ValueError("Некорректное значение курса.")
    if d <= 0 or d > Decimal("1000000"):
        raise ValueError("Курс должен быть > 0 и разумным.")

    async with async_session() as session:
        row = await session.scalar(select(SystemSettings).limit(1))
        row.rub_per_usdt = float(d.quantize(Decimal("0.0001")))
        await session.commit()

    # invalidate cache and return
    global _cache
    _cache = None
    return await get_settings()
