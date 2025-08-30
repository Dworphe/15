from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from sqlalchemy import select

from app.db.base import async_session
from app.db.models import Deal, DealStatus, User
from app.utils.money import bank_round_2
from app.scheduler import schedule_buy_timeout

router = Router(name="buy_winner")
TZ = ZoneInfo("Europe/Amsterdam")


def winner_kb(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="Я ВЫПОЛНИЛ ПЕРЕВОД", callback_data=f"buywin:paid:{deal_id}")
    b.button(text="ПЛОХИЕ РЕКВИЗИТЫ", callback_data=f"buywin:bad:{deal_id}")
    b.button(text="ОТМЕНА СДЕЛКИ −200 RUB", callback_data=f"buywin:cancel:{deal_id}")
    b.button(text="ОПЕРАТОР", callback_data=f"buywin:op:{deal_id}")
    b.adjust(1, 2, 1)
    return b.as_markup()


@router.callback_query(F.data.startswith("buyadm:confirm:"))
async def admin_confirm(cq: CallbackQuery):
    deal_id = int(cq.data.split(":", 2)[2])
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return await cq.answer()
        u = await s.get(User, int(d.winner_user_id))
        amount = float(d.amount_rub)
        reward_rub = bank_round_2(amount * float(d.winner_bid_pct) / 100.0)
        fee_rub = bank_round_2(reward_rub * float(d.service_fee_pct) / 100.0) if reward_rub > 0 else 0.0
        u.balance_rub = float(u.balance_rub) + amount + (reward_rub - fee_rub if reward_rub >= 0 else -abs(reward_rub))
        d.status = DealStatus.COMPLETED
        await s.commit()
    await cq.message.answer("Сделка завершена ✅")
    await cq.answer()


@router.callback_query(F.data.startswith("buyadm:reject:"))
async def admin_reject(cq: CallbackQuery):
    await cq.message.answer("Отклонено. Свяжитесь с трейдером/оператором.")
    await cq.answer()


@router.callback_query(F.data.startswith("buywin:paid:"))
async def winner_paid(cq: CallbackQuery):
    deal_id = int(cq.data.split(":", 2)[2])
    from app.bot import create_bot_and_dp
    from app.config import settings
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return await cq.answer()
        text_admin = (
            "<b>ПОКУПКА — отметка оплаты от победителя</b>\n"
            f"Сделка № <code>{d.deal_no}</code>\n"
            f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
            f"Реквизиты для оплаты: {d.winner_card_mask}\n"
            f"E-mail: {d.winner_card_holder or '-'}\n\n"
            "Подтвердить получение перевода?"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="Подтвердить", callback_data=f"buyadm:confirm:{deal_id}")
        kb.button(text="Отклонить", callback_data=f"buyadm:reject:{deal_id}")
        kb.adjust(2)
        for admin in settings.admins:
            try:
                await bot.send_message(admin, text_admin, reply_markup=kb.as_markup())
            except:  # noqa: E722
                pass
    await cq.message.answer("Сообщение администратору отправлено. Ожидайте подтверждения.")
    await cq.answer()


@router.callback_query(F.data.startswith("buywin:cancel:"))
async def winner_cancel(cq: CallbackQuery):
    deal_id = int(cq.data.split(":", 2)[2])
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return await cq.answer()
        u = await s.get(User, int(d.winner_user_id))
        u.balance_rub = float(u.balance_rub) - 200.0
        d.status = DealStatus.CANCELLED_BY_TRADER
        await s.commit()
    await cq.message.answer("Сделка отменена. Штраф −200 RUB применён.")
    await cq.answer()


@router.callback_query(F.data.startswith("buywin:bad:"))
async def winner_bad_reqs(cq: CallbackQuery):
    await cq.message.answer("Отмечено. Реквизиты будут проверены администратором.")
    await cq.answer()


