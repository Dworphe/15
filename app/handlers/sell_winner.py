# app/handlers/sell_winner.py
from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db.base import async_session
from app.db.models import Deal, DealStatus, TraderCard, User, RoleEnum
from app.handlers.utils import ensure_user
from app.services.balance import release_usdt, consume_reserved_usdt
from app.utils.money import bank_round_2
from app.utils.validation import mask_card

router = Router(name="sell_winner")
TZ = ZoneInfo("Europe/Amsterdam")

class PickCardStates(StatesGroup):
    waiting_card = State()

def kb_winner_actions(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Сменить карту", callback_data=f"winner:change_card:{deal_id}")
    b.button(text="❌ Отмена сделки (-200 RUB)", callback_data=f"winner:cancel:{deal_id}")
    b.adjust(1)
    return b.as_markup()

def kb_admin_paid(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Перевод выполнен", callback_data=f"admin:paid:{deal_id}")
    b.adjust(1)
    return b.as_markup()

@router.message(Command("pick_card"))
async def pick_card_start(message: Message, state: FSMContext):
    try:
        deal_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        await message.answer("Использование: /pick_card <ID сделки>")
        return
    
    async with async_session() as s:
        u = await ensure_user(s, message.from_user)
        deal = await s.get(Deal, deal_id)
        
        if not deal:
            await message.answer("Сделка не найдена.")
            return
        
        if not deal.winner_user_id or int(deal.winner_user_id) != u.id:
            await message.answer("Вы не являетесь победителем этой сделки.")
            return
        
        if deal.status != DealStatus.WAITING_ADMIN_TRANSFER:
            await message.answer("Сделка не в статусе ожидания оплаты.")
            return
        
        # Получаем карты пользователя
        profile = await s.execute(
            select(TraderCard).where(TraderCard.profile_id == u.id)
        )
        cards = profile.scalars().all()
        
        if not cards:
            await message.answer("У вас нет добавленных карт. Обратитесь к администратору.")
            return
        
        await state.set_state(PickCardStates.waiting_card)
        await state.update_data(deal_id=deal_id)
        
        # Показываем карты
        b = InlineKeyboardBuilder()
        for card in cards:
            bank_name = card.bank.value if card.bank != "OTHER" else (card.bank_other_name or "Другой банк")
            card_text = f"{bank_name} • {mask_card(card.card_number)} • {card.holder_name}"
            b.button(text=card_text, callback_data=f"pick:card:{deal_id}:{card.id}")
        b.adjust(1)
        
        await message.answer(
            f"<b>Выберите карту для получения перевода</b>\n"
            f"Сделка № <code>{deal.deal_no}</code>\n"
            f"Сумма: {float(deal.amount_rub):.2f} RUB\n\n"
            "Выберите карту:",
            reply_markup=b.as_markup()
        )

@router.callback_query(F.data.startswith("pick:card:"))
async def card_selected(cq: CallbackQuery, state: FSMContext):
    _, _, deal_id_s, card_id_s = cq.data.split(":")
    deal_id = int(deal_id_s)
    card_id = int(card_id_s)
    
    async with async_session() as s:
        deal = await s.get(Deal, deal_id)
        card = await s.get(TraderCard, card_id)
        
        if not deal or not card:
            await cq.answer("Ошибка: сделка или карта не найдена", show_alert=True)
            return
        
        # Сохраняем снэпшоты карты
        deal.winner_card_id = card.id
        deal.winner_card_bank = card.bank.value if card.bank != "OTHER" else (card.bank_other_name or "Другой банк")
        deal.winner_card_mask = mask_card(card.card_number)
        deal.winner_card_holder = card.holder_name
        
        await s.commit()
        
        await cq.message.edit_text(
            f"<b>✅ Карта выбрана!</b>\n"
            f"Сделка № <code>{deal.deal_no}</code>\n"
            f"Банк: {deal.winner_card_bank}\n"
            f"Карта: {deal.winner_card_mask}\n"
            f"Держатель: {deal.winner_card_holder}\n\n"
            f"Ожидайте перевода от администратора.\n"
            f"Дедлайн: до {deal.pay_deadline_at.astimezone(TZ).strftime('%H:%M')}",
            reply_markup=kb_winner_actions(deal_id)
        )
        
        # Уведомляем админов
        await notify_admins_card_selected(deal, card)
        
        await cq.answer("Карта выбрана успешно!")

async def notify_admins_card_selected(deal: Deal, card: TraderCard):
    from app.bot import create_bot_and_dp
    bot, _ = create_bot_and_dp("")
    
    async with async_session() as s:
        admins = await s.execute(
            select(User.tg_id).where(User.role == RoleEnum.admin)
        )
        admin_tg_ids = [int(r[0]) for r in admins.all() if r[0]]
    
    card_info = (
        f"<b>🎯 ПОБЕДИТЕЛЬ ВЫБРАЛ КАРТУ</b>\n"
        f"Сделка № <code>{deal.deal_no}</code>\n"
        f"Победитель: {deal.winner_user_id}\n"
        f"Банк: {deal.winner_card_bank}\n"
        f"Карта: {deal.winner_card_mask}\n"
        f"Держатель: {deal.winner_card_holder}\n"
        f"Сумма: {float(deal.amount_rub):.2f} RUB\n\n"
        f"Отправьте перевод и нажмите «Перевод выполнен»"
    )
    
    for admin_id in admin_tg_ids:
        try:
            await bot.send_message(
                admin_id,
                card_info,
                reply_markup=kb_admin_paid(deal.id)
            )
        except Exception:
            continue

@router.callback_query(F.data.startswith("admin:paid:"))
async def admin_paid(cq: CallbackQuery):
    deal_id = int(cq.data.split(":")[2])
    
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору", show_alert=True)
            return
        
        deal = await s.get(Deal, deal_id)
        if not deal or deal.status != DealStatus.WAITING_ADMIN_TRANSFER:
            await cq.answer("Сделка недоступна", show_alert=True)
            return
        
        # Переводим в статус ожидания подтверждения трейдером
        deal.status = DealStatus.WAITING_TRADER_CONFIRM
        from app.utils.dt import now_tz
        deal.confirm_deadline_at = now_tz() + timedelta(minutes=int(deal.confirm_mins or 10))
        await s.commit()
        
        # Планируем окно подтверждения
        from app.scheduler import schedule_trader_confirm_window
        from app.config import settings
        await schedule_trader_confirm_window(deal.id, after_minutes=int(deal.confirm_mins or 10), db_url=settings.database_url)
        
        # Уведомляем победителя
        await notify_winner_payment_received(deal)
        
        await cq.message.edit_text(
            f"<b>✅ Перевод подтвержден!</b>\n"
            f"Сделка № <code>{deal.deal_no}</code>\n"
            f"Ожидаем подтверждения от трейдера.\n"
            f"Дедлайн: до {deal.confirm_deadline_at.astimezone(TZ).strftime('%H:%M')}"
        )
        
        await cq.answer("Статус обновлен!")

async def notify_winner_payment_received(deal: Deal):
    from app.bot import create_bot_and_dp
    bot, _ = create_bot_and_dp("")
    
    confirm_deadline = deal.confirm_deadline_at.astimezone(TZ).strftime('%H:%M')
    
    b = InlineKeyboardBuilder()
    b.button(text="✅ Я ПОЛУЧИЛ ПЕРЕВОД", callback_data=f"winner:got_money:{deal.id}")
    b.adjust(1)
    
    await bot.send_message(
        int(await _winner_tg_id(deal.winner_user_id)),
        f"<b>💰 ПЕРЕВОД ПОЛУЧЕН!</b>\n"
        f"Сделка № <code>{deal.deal_no}</code>\n"
        f"Администратор подтвердил перевод на карту:\n"
        f"{deal.winner_card_bank} • {deal.winner_card_mask}\n\n"
        f"<b>Подтвердите получение перевода:</b>\n"
        f"Дедлайн: до {confirm_deadline}",
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("winner:got_money:"))
async def winner_got_money(cq: CallbackQuery):
    deal_id = int(cq.data.split(":")[2])
    
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        deal = await s.get(Deal, deal_id)
        
        if not deal or not deal.winner_user_id or int(deal.winner_user_id) != u.id:
            await cq.answer("Ошибка: сделка недоступна", show_alert=True)
            return
        
        if deal.status != DealStatus.WAITING_TRADER_CONFIRM:
            await cq.answer("Сделка не в статусе ожидания подтверждения", show_alert=True)
            return
        
        # Списываем резерв USDT
        ok = await consume_reserved_usdt(u.id, float(deal.amount_usdt_snapshot))
        if not ok:
            await cq.answer("Ошибка: не удалось списать резерв", show_alert=True)
            return
        
        # Применяем вознаграждение и комиссию
        reward_rub = float(deal.amount_rub) * float(deal.winner_bid_pct) / 100.0
        reward_rub = bank_round_2(reward_rub)
        
        if reward_rub > 0:
            # Удерживаем комиссию только с положительного вознаграждения
            fee_rub = bank_round_2(reward_rub * float(deal.service_fee_pct) / 100.0)
            final_reward = bank_round_2(reward_rub - fee_rub)
            
            u.balance_rub = float(u.balance_rub) + final_reward
        else:
            final_reward = reward_rub
            fee_rub = 0.0
            u.balance_rub = float(u.balance_rub) + final_reward
        
        await s.commit()
        
        # Завершаем сделку
        deal.status = DealStatus.COMPLETED
        await s.commit()
        
        await cq.message.edit_text(
            f"<b>🎉 СДЕЛКА ЗАВЕРШЕНА!</b>\n"
            f"Сделка № <code>{deal.deal_no}</code>\n"
            f"Вознаграждение: {reward_rub:.2f} RUB\n"
            f"Комиссия сервиса: {fee_rub:.2f} RUB\n"
            f"Итого к зачислению: {final_reward:.2f} RUB\n\n"
            f"Спасибо за участие в аукционе!"
        )
        
        await cq.answer("Сделка завершена успешно!")

@router.callback_query(F.data.startswith("winner:change_card:"))
async def change_card(cq: CallbackQuery, state: FSMContext):
    deal_id = int(cq.data.split(":")[2])
    
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        deal = await s.get(Deal, deal_id)
        
        if not deal or not deal.winner_user_id or int(deal.winner_user_id) != u.id:
            await cq.answer("Ошибка: сделка недоступна", show_alert=True)
            return
        
        if deal.status != DealStatus.WAITING_ADMIN_TRANSFER:
            await cq.answer("Смена карты возможна только до оплаты", show_alert=True)
            return
        
        # Сбрасываем выбор карты
        deal.winner_card_id = None
        deal.winner_card_bank = None
        deal.winner_card_mask = None
        deal.winner_card_holder = None
        await s.commit()
        
        # Запускаем выбор карты заново
        await pick_card_start(cq.message, state)
        await cq.answer("Выберите новую карту")

@router.callback_query(F.data.startswith("winner:cancel:"))
async def cancel_deal(cq: CallbackQuery):
    deal_id = int(cq.data.split(":")[2])
    
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        deal = await s.get(Deal, deal_id)
        
        if not deal or not deal.winner_user_id or int(deal.winner_user_id) != u.id:
            await cq.answer("Ошибка: сделка недоступна", show_alert=True)
            return
        
        if deal.status not in (DealStatus.WAITING_ADMIN_TRANSFER, DealStatus.WAITING_TRADER_CONFIRM):
            await cq.answer("Отмена невозможна в текущем статусе", show_alert=True)
            return
        
        # Возвращаем резерв USDT
        await release_usdt(u.id, float(deal.amount_usdt_snapshot))
        
        # Штраф 200 RUB
        u.balance_rub = max(0.0, float(u.balance_rub) - 200.0)
        
        # Отменяем сделку
        deal.status = DealStatus.CANCELLED_BY_TRADER
        await s.commit()
        
        await cq.message.edit_text(
            f"<b>❌ СДЕЛКА ОТМЕНЕНА</b>\n"
            f"Сделка № <code>{deal.deal_no}</code>\n"
            f"Причина: отмена победителем\n"
            f"Штраф: -200 RUB\n"
            f"Резерв USDT возвращен"
        )
        
        await cq.answer("Сделка отменена!")

async def _winner_tg_id(user_id: int) -> int:
    async with async_session() as s:
        u = await s.get(User, user_id)
        return int(u.tg_id or 0)
