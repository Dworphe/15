# app/handlers/sell_auction.py
from __future__ import annotations
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, StateFilter

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, and_, desc
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json

from app.db.base import async_session
from app.db.models import (Deal, DealType, DealStatus, DealParticipant, User, Bid, DealRound, RoleEnum)
from app.services.settings import get_settings
from app.services.balance import ensure_usdt_margin
from app.handlers.utils import ensure_user
from app.utils.money import bank_round_2
from app.scheduler import schedule_countdown_message
from app.utils.dt import now_tz, to_aware_utc

router = Router(name="sell_auction")
TZ = ZoneInfo("Europe/Amsterdam")

class BidStates(StatesGroup):
    waiting_bid = State()  # пользователь вводит ставку для конкретной сделки

# ===== Этап 1: Рассылка =====
async def eligible_traders(deal: Deal) -> list[int]:
    async with async_session() as s:
        q = select(User.tg_id, User.id, User.is_online, User.balance_usdt).where(
            User.role=="trader", User.is_active==True, User.is_online==True
        )
        rows = (await s.execute(q)).all()
        # фильтры аудитории
        filt = json.loads(deal.audience_filter or "{}")
        out = []
        for tg_id, uid, online, busdt in rows:
            if not online: continue
            if float(busdt) < float(deal.amount_usdt_snapshot): continue
            # карта: допустим наличие карты проверим позже при выборе (или здесь — опционально)
            # репутация
            if deal.audience_type == "rep":
                rep_q = await s.execute(select(User.rep).where(User.id==uid))
                rep = int(rep_q.scalar() or 0)
                if "rep_min" in filt and rep < int(filt["rep_min"]): continue
                if "rep_max" in filt and rep > int(filt["rep_max"]): continue
            if deal.audience_type == "personal":
                if int(filt.get("telegram_id", 0)) != int(tg_id): continue
            out.append(int(tg_id))
        return out

def stage1_kb(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="Принять сделку (+1 REP)", callback_data=f"sell1:accept:{deal_id}")
    b.button(text="Отклонить сделку (-2 REP)", callback_data=f"sell1:decline:{deal_id}")
    b.adjust(1,1)
    return b.as_markup()

async def broadcast_stage1(deal_id: int):
    from app.utils.money import bank_round_2
    from app.bot import create_bot_and_dp
    from app.config import settings
    bot, _ = create_bot_and_dp(settings.bot_token)
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        sset = await get_settings()
        # готовим текст
        max_reward = 0.0
        if float(d.base_pct) > 0:
            max_reward = bank_round_2(float(d.amount_rub) * float(d.base_pct)/100.0)
        txt = (
            "<b>ВЫ УЧАСТВУЕТЕ В АУКЦИОНЕ!</b>\n"
            "Вид сделки: <b>ПРОДАЖА USDT</b>\n"
            f"Номер: <code>{d.deal_no}</code>\n"
            f"Вознаграждение (база): {float(d.base_pct):.2f}%\n"
            f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
            f"Макс. вознаграждение (RUB): {max_reward:.2f}\n"
            f"Комиссия сервиса: {float(d.service_fee_pct):.2f}%\n"
            f"Время сделки: {int(sset.round_secs_default)} сек для Этапа 1; оплата: {int( (d.pay_deadline_at is not None) or 0)}\n"
            + (f"\n<b>Комментарий:</b>\n{d.comment}\n" if d.comment else "") +
            ("\n<b>!ПРЕДУПРЕЖДЕНИЕ!</b>\n!Перевод только на карту трейдера!\n!Перевод с другого банка — риск задержек/комиссий!\n" if d.warning_enabled else "") +
            "\nДИСКЛЕЙМЕР"
        )
        # отправка
        tg_ids = await eligible_traders(d)
        for tg_id in tg_ids:
            try:
                # отправляем карточку сделки
                m = await bot.send_message(tg_id, txt, reply_markup=stage1_kb(d.id))
                # Добавляем единый таймер для пользователя и сделки
                from app.workers.timers import ensure_update_timer
                from app.scheduler import get_scheduler
                from app.config import settings
                scheduler = get_scheduler(settings.database_url)
                ensure_update_timer(scheduler, tg_id, d.id, kind="sell")
            except Exception:
                continue

@router.callback_query(F.data.startswith("sell1:"))
async def stage1_click(cq: CallbackQuery):
    _, action, deal_id_s = cq.data.split(":",2)
    deal_id = int(deal_id_s)
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        d = await s.get(Deal, deal_id)
        if not d or d.status != DealStatus.OPEN:
            await cq.answer("Сделка недоступна.", show_alert=True); return
        # маржинальное требование по USDT
        if not await ensure_usdt_margin(u.id, float(d.amount_usdt_snapshot)):
            await cq.answer("Недостаточно USDT для участия.", show_alert=True); return
        # записываем участие
        exist = await s.scalar(select(DealParticipant).where(DealParticipant.deal_id==deal_id, DealParticipant.user_id==u.id))
        if not exist:
            p = DealParticipant(deal_id=deal_id, user_id=u.id, accepted=(action=="accept"))
            s.add(p)
        else:
            exist.accepted = (action=="accept")
        # REP
        if action == "accept": u.rep += 1
        else: u.rep -= 2
        await s.commit()
    if action == "accept":
        await cq.message.answer("Отлично! Вы получили +1 REP. Ждите второй этап… ⏳")
    else:
        await cq.message.answer("Очень жаль: −2 REP. Ожидайте другие сделки.")
    await cq.answer()

# ===== Этап 2: приём ставок (FSM) =====
def bid_range_hint(base_pct: float) -> str:
    lo, hi = -5.00, base_pct
    return f"Введите вашу ставку в диапазоне [{lo:.2f}; {hi:.2f}] с шагом 0.01"

@router.message(Command("bid"))
async def bid_help(message: Message):
    await message.answer("Команда для ставки активируется автоматически в Этапе 2.")

# Воркфлоу Этапа 2: по старту раунда воркер создаёт DealRound; мы шлём допущенным инструкцию «введите ставку»
async def notify_round_start(deal_id: int, round_number: int):
    from app.bot import create_bot_and_dp
    from app.config import settings
    bot, _ = create_bot_and_dp(settings.bot_token)
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d: return
        # список допущенных (accepted)
        rows = await s.execute(select(DealParticipant, User.tg_id).join(User, DealParticipant.user_id==User.id)
                               .where(DealParticipant.deal_id==deal_id, DealParticipant.accepted==True))
        for p, tg_id in rows.all():
            try:
                text = (f"<b>АУКЦИОН НАЧАЛСЯ — РАУНД {round_number}</b>\n"
                        f"{bid_range_hint(float(d.base_pct))}\n"
                        "Отправьте число (например 0.25 или -1.50).")
                msg = await bot.send_message(int(tg_id), text)
                # найдём текущий DealRound по deal_id и round_number и возьмём его deadline_at
                deadline = (await s.scalar(select(DealRound.deadline_at).where(DealRound.deal_id==deal_id, DealRound.round_number==round_number)))
                if deadline:
                    tm = await bot.send_message(int(tg_id), "⏳ Осталось: 00:00")
                    from app.config import settings
                    await schedule_countdown_message(tm.chat.id, tm.message_id, deadline.isoformat(), job_id=f"sell:r{round_number}:{tm.chat.id}:{tm.message_id}", db_url=settings.database_url)
            except Exception:
                continue

@router.message(BidStates.waiting_bid)
async def accept_bid(message: Message, state: FSMContext):
    data = await state.get_data()
    deal_id = int(data.get("deal_id"))
    try:
        v = float((message.text or "").replace(",", "."))
    except:
        await message.answer("Нужно число. Пример: 0.25 или -2.00"); return
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d or d.status != DealStatus.BIDDING:
            await message.answer("Приём ставок закрыт."); return
        lo, hi = -5.00, float(d.base_pct)
        # валидация диапазона и шага 0.01
        if v < lo or v > hi or abs(round(v*100) - v*100) > 1e-9:
            await message.answer(bid_range_hint(float(d.base_pct))); return
        # текущий раунд
        r = await s.scalar(select(DealRound).where(DealRound.deal_id==deal_id).order_by(DealRound.round_number.desc()).limit(1))
        if not r:
            await message.answer("Техническая пауза. Попробуйте позже."); return
        # сохранить ставку (последняя перезаписывает)
        exist = await s.scalar(select(Bid).where(Bid.deal_id==deal_id, Bid.round_number==r.round_number, Bid.user_id== (await s.scalar(select(User.id).where(User.tg_id==message.from_user.id)))))
        if exist:
            exist.pct = round(v,2)
        else:
            uid = await s.scalar(select(User.id).where(User.tg_id==message.from_user.id))
            s.add(Bid(deal_id=deal_id, round_number=r.round_number, user_id=uid, pct=round(v,2)))
        await s.commit()
    await message.answer("Ставка принята.")

@router.message(StateFilter(None), F.text.regexp(r"^[+-]?\d+(?:[.,]\d{1,2})?$"))
async def bid_from_plain_number(message: Message):
    async with async_session() as s:
        u = await s.scalar(select(User).where(User.tg_id == message.from_user.id))
        if not u or u.role != RoleEnum.trader:
            return
        # далее — код идентичен BUY-версии
        d = await s.scalar(
            select(Deal)
            .join(DealParticipant, DealParticipant.deal_id == Deal.id)
            .where(
                Deal.status == DealStatus.BIDDING,
                DealParticipant.user_id == u.id,
                DealParticipant.accepted == True,
            )
            .order_by(desc(Deal.id))
            .limit(1)
        )
        if not d:
            return

        r = await s.scalar(
            select(DealRound)
            .where(DealRound.deal_id == d.id)
            .order_by(DealRound.round_number.desc())
            .limit(1)
        )
        _now = now_tz()
        deadline = to_aware_utc(r.deadline_at) if r else None
        if not r or (deadline and deadline <= _now):
            return

        v = float((message.text or "").replace(",", "."))
        lo, hi = -5.00, float(d.base_pct)
        if v < lo or v > hi:
            await message.answer(f"Ставка вне диапазона. Допустимо от {lo:.2f} до {hi:.2f}.")
            return

        v = round(v, 2)
        b = await s.scalar(select(Bid).where(
            Bid.deal_id == d.id, Bid.round_number == r.round_number, Bid.user_id == u.id))
        if b:
            b.pct = v
        else:
            s.add(Bid(deal_id=d.id, round_number=r.round_number, user_id=u.id, pct=v))
        await s.commit()

    from app.utils.dt import seconds_left
    left = seconds_left(r.deadline_at)
    await message.answer(f"Ставка принята. До конца раунда: {max(0, left)} сек.")
