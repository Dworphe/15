from __future__ import annotations
import logging
from typing import Optional, Tuple

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db.base import async_session
from app.db.models import Deal, DealRound
from app.services.ui_timers import edit_timer_text, ensure_timer_message
from app.utils.dt import now_tz, to_aware_utc, seconds_left, fmt_mmss

log = logging.getLogger(__name__)


async def _get_active_deadline_for_deal(deal_id: int) -> Optional[Tuple[str, object, object]]:
    """Возвращает (phase, obj, until) или None.
       phase: 'stage1' | 'round' | 'pay'
       obj:   объект сделки или раунда
       until: datetime дедлайна (aware)
    """
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return None

        # Этап 1 (принятие участия)
        s1 = getattr(d, "stage1_deadline_at", None)
        s1_done = getattr(d, "stage1_finished", False)
        if s1 and not s1_done:
            return "stage1", d, to_aware_utc(s1)

        # Активный раунд
        res = await s.execute(
            select(DealRound).where(
                DealRound.deal_id == deal_id,
                DealRound.is_active == True,  # noqa: E712
            )
        )
        r = res.scalar_one_or_none()
        if r and getattr(r, "deadline_at", None):
            return "round", r, to_aware_utc(r.deadline_at)

        # Оплата победителем (если предусмотрено)
        pay_until = getattr(d, "pay_deadline_at", None)
        is_paid = getattr(d, "is_paid", False)
        if pay_until and not is_paid:
            return "pay", d, to_aware_utc(pay_until)

        return None


def ensure_update_timer(scheduler: AsyncIOScheduler, chat_id: int, deal_id: int, kind: str = "buy") -> None:
    """Добавляет interval-job с детерминированным id, если его ещё нет."""
    job_id = f"timer:{kind}:{deal_id}:{chat_id}"
    if scheduler.get_job(job_id) is None:
        scheduler.add_job(
            func=update_countdown,
            trigger="interval",
            seconds=5,
            id=job_id,
            max_instances=1,
            coalesce=True,
            kwargs={"chat_id": chat_id, "deal_id": deal_id, "kind": kind},
        )
        log.info("Added timer job %s", job_id)


async def update_countdown(chat_id: int, deal_id: int, kind: str = "buy") -> None:
    bot = Bot.get_current()
    await ensure_timer_message(bot, chat_id, deal_id, kind)

    active = await _get_active_deadline_for_deal(deal_id)
    if not active:
        await edit_timer_text(bot, chat_id, deal_id, "⏳ Этап завершён.", kind)
        return

    phase, obj, until = active
    left = seconds_left(until)
    await edit_timer_text(bot, chat_id, deal_id, f"⏳ Осталось: {fmt_mmss(left)}", kind)
