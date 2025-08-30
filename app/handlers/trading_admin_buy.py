"""
Мастер создания покупки USDT - скелет (патч №21)
"""

from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from decimal import Decimal
import logging

from app.db.base import async_session
from app.db.models import User, RoleEnum
from app.handlers.utils import ensure_user
from app.trading.service import TradingService
from app.trading.enums import TradeType

router = Router(name="trading_admin_buy")
log = logging.getLogger(__name__)

class BuyTradeStates(StatesGroup):
    """Состояния мастера создания покупки"""
    amount_rub = State()           # Сумма в RUB
    account_details = State()      # Счет/реквизиты
    base_pct = State()            # Базовый процент
    service_fee_pct = State()     # Комиссия сервиса

@router.callback_query(F.data == "admin:trading_buy_new")
async def start_buy_master(cq: CallbackQuery, state: FSMContext):
    """Начало мастера создания покупки"""
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору.", show_alert=True)
            return
    
    await state.clear()
    await state.set_state(BuyTradeStates.amount_rub)
    
    await cq.message.answer(
        "🔄 <b>Мастер создания ПОКУПКИ USDT</b>\n\n"
        "1️⃣ <b>Сумма сделки в RUB</b>\n"
        "Введите сумму в рублях (например: 10000):"
    )
    await cq.answer()

@router.message(BuyTradeStates.amount_rub)
async def process_amount_rub(message: Message, state: FSMContext):
    """Обработка суммы в RUB"""
    try:
        amount = Decimal(message.text.replace(",", "."))
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0. Повторите ввод:")
            return
        
        await state.update_data(amount_rub=amount)
        await state.set_state(BuyTradeStates.account_details)
        
        await message.answer(
            "✅ <b>Сумма принята:</b> {:.2f} RUB\n\n"
            "2️⃣ <b>Счет/реквизиты для получения RUB</b>\n"
            "Введите номер счета или реквизиты:".format(amount)
        )
        
    except (ValueError, TypeError):
        await message.answer("❌ Неверный формат суммы. Введите число (например: 10000):")

@router.message(BuyTradeStates.account_details)
async def process_account_details(message: Message, state: FSMContext):
    """Обработка счета/реквизитов"""
    account_details = message.text.strip()
    if not account_details:
        await message.answer("❌ Реквизиты не могут быть пустыми. Повторите ввод:")
        return
    
    await state.update_data(account_details=account_details)
    await state.set_state(BuyTradeStates.base_pct)
    
    await message.answer(
        "✅ <b>Реквизиты приняты:</b> {}\n\n"
        "3️⃣ <b>Базовый процент</b>\n"
        "Введите базовый процент (-100 до +100, например: 2.5):".format(account_details)
    )

@router.message(BuyTradeStates.base_pct)
async def process_base_pct(message: Message, state: FSMContext):
    """Обработка базового процента"""
    try:
        base_pct = Decimal(message.text.replace(",", "."))
        if not (-100 <= base_pct <= 100):
            await message.answer("❌ Процент должен быть от -100 до +100. Повторите ввод:")
            return
        
        await state.update_data(base_pct=base_pct)
        await state.set_state(BuyTradeStates.service_fee_pct)
        
        await message.answer(
            "✅ <b>Базовый процент принят:</b> {:.2f}%\n\n"
            "4️⃣ <b>Комиссия сервиса</b>\n"
            "Введите комиссию в процентах (0-100, например: 1.0):".format(base_pct)
        )
        
    except (ValueError, TypeError):
        await message.answer("❌ Неверный формат процента. Введите число (например: 2.5):")

@router.message(BuyTradeStates.service_fee_pct)
async def process_service_fee_pct(message: Message, state: FSMContext):
    """Обработка комиссии сервиса и создание сделки"""
    try:
        service_fee_pct = Decimal(message.text.replace(",", "."))
        if not (0 <= service_fee_pct <= 100):
            await message.answer("❌ Комиссия должна быть от 0 до 100. Повторите ввод:")
            return
        
        # Получаем все данные из FSM
        data = await state.get_data()
        amount_rub = data["amount_rub"]
        account_details = data["account_details"]
        base_pct = data["base_pct"]
        
        # Рассчитываем примерную сумму USDT (пока заглушка)
        # В следующем патче добавим реальный расчет по курсу
        amount_usdt = amount_rub / Decimal("100")  # Заглушка
        
        # Создаем торговую операцию
        trade = await TradingService.create_trade(
            trade_type=TradeType.BUY,
            amount_rub=amount_rub,
            amount_usdt=amount_usdt,
            base_pct=base_pct,
            service_fee_pct=service_fee_pct,
            deadlines={
                "account_details": account_details,
                "created_at": str(message.date)
            }
        )
        
        if trade:
            # Отправляем служебное сообщение-карточку сделки админу
            from app.trading.service import render_trade_card, register_admin_view, set_countdown
            text = await render_trade_card(trade.id, None)
            msg = await message.answer(text)
            
            # Регистрируем UI-сообщение и запускаем демо-таймер
            await register_admin_view(trade.id, msg.chat.id, msg.message_id)
            # Для проверки механики — запускаем демо-таймер на 120 секунд
            await set_countdown(trade.id, 120)
            
            await message.answer(
                "✅ <b>Черновик сделки создан (скелет)</b>\n\n"
                f"📋 <b>ID сделки:</b> {trade.id}\n"
                f"💰 <b>Сумма:</b> {amount_rub:.2f} RUB\n"
                f"💱 <b>USDT:</b> {amount_usdt:.2f}\n"
                f"📊 <b>Базовый %:</b> {base_pct:.2f}%\n"
                f"💸 <b>Комиссия:</b> {service_fee_pct:.2f}%\n"
                f"🏦 <b>Реквизиты:</b> {account_details}\n\n"
                "🔧 <i>Функционал в разработке (патч №21)</i>\n"
                "⏰ <i>Демо-таймер запущен на 120 секунд</i>"
            )
            
            log.info(f"Created BUY trade: id={trade.id}, amount_rub={amount_rub}")
        else:
            await message.answer(
                "❌ <b>Ошибка создания сделки</b>\n"
                "Попробуйте еще раз или обратитесь к администратору."
            )
        
        await state.clear()
        
    except (ValueError, TypeError):
        await message.answer("❌ Неверный формат комиссии. Введите число (например: 1.0):")
