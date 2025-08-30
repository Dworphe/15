from __future__ import annotations
from datetime import timedelta
import logging
from typing import Iterable

from aiogram import Bot
from sqlalchemy import select, update

from app.db.base import async_session
from app.db.models import Deal, DealRound, SystemSettings, DealStatus, User, RoleEnum, DealParticipant
from app.scheduler import get_scheduler
from app.utils.dt import now_tz, to_aware_utc
from app.workers.timers import ensure_update_timer

log = logging.getLogger(__name__)


async def _broadcast_stage1(bot: Bot, deal: Deal) -> Iterable[int]:
    """Разошлём «Новая сделка — Принять/Отклонить» и вернём chat_id получателей."""
    from app.handlers.utils import ensure_user
    from app.utils.keyboard import InlineKeyboardBuilder
    
    recipients: list[int] = []
    
    async with async_session() as s:
        # Получаем всех активных трейдеров
        users_result = await s.execute(
            select(User.tg_id).where(
                User.role == RoleEnum.trader,
                User.is_active == True
            )
        )
        trader_tg_ids = [row[0] for row in users_result.fetchall()]
        
        # Создаем клавиатуру для принятия/отклонения
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Принять сделку (+1 REP)", callback_data=f"deal:{deal.id}:accept")
        kb.button(text="❌ Отклонить сделку (-2 REP)", callback_data=f"deal:{deal.id}:decline")
        kb.adjust(1)
        
        # Отправляем сообщения всем трейдерам
        for tg_id in trader_tg_ids:
            try:
                await bot.send_message(
                    tg_id,
                    (
                        "🎯 <b>НОВАЯ СДЕЛКА — АУКЦИОН (ПОКУПКА)</b>\n\n"
                        f"📋 <b>Номер:</b> <code>{deal.deal_no}</code>\n"
                        f"💰 <b>Сумма:</b> {deal.amount_rub:.2f} RUB (~{deal.amount_usdt_snapshot:.2f} USDT)\n"
                        f"🎁 <b>Вознаграждение (база):</b> {deal.base_pct:.2f}%\n"
                        f"💳 <b>Банк:</b> {deal.pay_bank}\n"
                        f"⏰ <b>Время на принятие:</b> 60 сек\n\n"
                        "Выберите действие:"
                    ),
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )
                recipients.append(tg_id)
            except Exception as e:
                log.warning(f"Failed to send message to {tg_id}: {e}")
                continue
    
    return recipients


async def start_stage1_for_deal(deal_id: int) -> None:
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return
        st = (await s.execute(select(SystemSettings))).scalar_one()
        # предполагаем, что в SystemSettings есть длительность Этапа 1 (если её нет — используем round_secs_default)
        stage1_secs = getattr(st, "stage1_secs_default", None) or getattr(st, "round_secs_default", 60)
        d.status = DealStatus.OPEN
        d.stage1_deadline_at = now_tz() + timedelta(seconds=stage1_secs)
        d.stage1_finished = False
        await s.commit()

    # Получаем бота из контекста или создаем новый
    try:
        bot = Bot.get_current()
    except RuntimeError:
        # Если бот не в контексте, создаем временный
        from app.config import settings
        bot = Bot(token=settings.bot_token)
    
    recips = await _broadcast_stage1(bot, d)

    # Планируем окончание Этапа 1
    scheduler = get_scheduler()
    scheduler.add_job(
        end_stage1,
        trigger="date",
        run_date=to_aware_utc(d.stage1_deadline_at),
        id=f"deal:{deal_id}:stage1",
        kwargs={"deal_id": deal_id},
        replace_existing=True,
    )

    # Включим «один таймер» для каждого получателя
    for chat_id in recips:
        ensure_update_timer(scheduler, chat_id, deal_id, kind="buy")


async def end_stage1(deal_id: int) -> None:
    """Завершение Этапа 1: если есть принявшие — запускаем Раунд 1, иначе отменяем сделку."""
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return

        # Получаем участников, принявших участие из таблицы DealParticipant
        participants_result = await s.execute(
            select(DealParticipant.user_id).where(
                DealParticipant.deal_id == deal_id,
                DealParticipant.accepted == True
            )
        )
        participants = [row[0] for row in participants_result.fetchall()]

        d.stage1_finished = True

        if not participants:
            d.status = DealStatus.CANCELLED
            await s.commit()
            log.info("Deal %s cancelled: no participants", deal_id)
            return

        # Старт Раунда 1
        st = (await s.execute(select(SystemSettings))).scalar_one()
        round_secs = getattr(st, "round_secs_default", 60)
        d.status = DealStatus.BIDDING

        r1 = DealRound(deal_id=deal_id, round_number=1, is_active=True,
                       deadline_at=now_tz() + timedelta(seconds=round_secs))
        s.add(r1)
        await s.commit()

    # Планируем окончание Раунда 1
    scheduler = get_scheduler()
    scheduler.add_job(
        end_round,
        trigger="date",
        run_date=to_aware_utc(r1.deadline_at),
        id=f"deal:{deal_id}:round:{r1.round_number}",
        kwargs={"deal_id": deal_id, "round_no": 1},
        replace_existing=True,
    )

    # Включим таймер участникам (если не включен)
    for chat_id in participants:
        ensure_update_timer(scheduler, chat_id, deal_id, kind="buy")


async def end_round(deal_id: int, round_no: int) -> None:
    """Определяем победителя или создаём тай-брейк раунд, либо запускаем этап оплаты."""
    async with async_session() as s:
        # 1) Закроем текущий раунд
        r = (await s.execute(
            select(DealRound).where(DealRound.deal_id == deal_id,
                                    DealRound.round_number == round_no)
        )).scalar_one_or_none()
        if not r:
            return
        r.is_active = False

        d = await s.get(Deal, deal_id)

        # TODO: Определи здесь победителя: winner_user_id, winner_bid_pct
        winner_user_id: int | None = getattr(d, "winner_user_id", None)

        if winner_user_id is None:
            # Ничья → создать следующий раунд (если не превышен лимит)
            st = (await s.execute(select(SystemSettings))).scalar_one()
            next_no = round_no + 1
            max_ties = getattr(st, "max_tie_rounds_default", 1)
            if next_no > (1 + max_ties):
                # форс-победитель по правилу сервиса или отмена
                d.status = DealStatus.CANCELLED
                await s.commit()
                return

            r_next = DealRound(deal_id=deal_id, round_number=next_no, is_active=True,
                               deadline_at=now_tz() + timedelta(seconds=getattr(st, "round_secs_default", 60)))
            s.add(r_next)
            await s.commit()

            # Планируем следующий раунд
            get_scheduler().add_job(
                end_round,
                trigger="date",
                run_date=to_aware_utc(r_next.deadline_at),
                id=f"deal:{deal_id}:round:{next_no}",
                kwargs={"deal_id": deal_id, "round_no": next_no},
                replace_existing=True,
            )
            return

        # Есть победитель → этап оплаты победителем
        st = (await s.execute(select(SystemSettings))).scalar_one()
        pay_mins = getattr(st, "pay_mins_default", 20)
        d.winner_user_id = winner_user_id
        d.pay_deadline_at = now_tz() + timedelta(minutes=pay_mins)
        d.status = DealStatus.WAITING_ADMIN_TRANSFER
        await s.commit()

    # Планируем окончание оплаты
    get_scheduler().add_job(
        end_pay,
        trigger="date",
        run_date=to_aware_utc(d.pay_deadline_at),
        id=f"deal:{deal_id}:pay",
        kwargs={"deal_id": deal_id},
        replace_existing=True,
    )


async def end_pay(deal_id: int) -> None:
    """Проверка оплаты по дедлайну — либо успех, либо спор/отмена."""
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return
        if getattr(d, "is_paid", False):
            d.status = DealStatus.COMPLETED
        else:
            d.status = DealStatus.REVIEW  # или CANCELLED
        await s.commit()
