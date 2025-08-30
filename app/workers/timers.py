from __future__ import annotations
import asyncio
import logging
from typing import Optional, Tuple
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.bot_provider import get_bot
from app.db.base import async_session
from app.db.models import Deal, DealRound
from app.trading.models_trade import Trade
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


async def update_countdown(*args, **kwargs) -> None:
    """Обновляет countdown для всех активных сделок с привязанными UI-сообщениями"""
    # Игнорируем всё, что пришло от старых джобов (chat_id, message_id и т.п.)
    try:
        bot = get_bot()
        now = datetime.now(timezone.utc)
        
        async with async_session() as s:
            q = (
                select(Trade.id, Trade.admin_chat_id, Trade.admin_message_id, Trade.countdown_until)
                .where(Trade.admin_chat_id.is_not(None))
                .where(Trade.admin_message_id.is_not(None))
                .where(Trade.countdown_until.is_not(None))
            )
            rows = (await s.execute(q)).all()

        for trade_id, chat_id, msg_id, iso in rows:
            try:
                dl = datetime.fromisoformat(iso)
            except Exception:
                # некорректный формат — снимаем
                from app.trading.service import set_countdown
                await set_countdown(trade_id, None)
                continue

            left = (dl - now).total_seconds()
            if left <= 0:
                # таймер истёк — один раз зафиксировать 00:00 и снять
                from app.trading.service import render_trade_card
                text = await render_trade_card(trade_id, 0)
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=text
                    )
                except TelegramBadRequest as e:
                    if "chat not found" in str(e).lower() or "message to edit not found" in str(e).lower():
                        log.info("countdown: orphan view (trade=%s): %s", trade_id, e)
                        from app.trading.service import clear_admin_view
                        await clear_admin_view(trade_id)
                    else:
                        log.warning("countdown: edit failed (trade=%s): %r", trade_id, e)
                finally:
                    from app.trading.service import set_countdown
                    await set_countdown(trade_id, None)
                continue

            try:
                from app.trading.service import render_trade_card
                text = await render_trade_card(trade_id, int(left))
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text
                )
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower() or "message to edit not found" in str(e).lower():
                    log.info("countdown: orphan view (trade=%s): %s", trade_id, e)
                    from app.trading.service import clear_admin_view
                    await clear_admin_view(trade_id)
                else:
                    log.warning("countdown: edit failed (trade=%s): %r", trade_id, e)
            
            await asyncio.sleep(0)  # не душим event loop
            
    except Exception as e:
        log.error(f"update_countdown: critical error: {e}")
