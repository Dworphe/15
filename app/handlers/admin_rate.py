from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from decimal import Decimal

from app.db.base import async_session
from app.db.models import User, RoleEnum
from app.services.settings import get_settings, set_rub_per_usdt


router = Router(name="admin_rate")


class RateStates(StatesGroup):
    waiting_value = State()


async def _admin_only(message: Message) -> bool:
    async with async_session() as session:
        u = await session.scalar(select(User).where(User.tg_id == message.from_user.id))
        if not u or u.role != RoleEnum.admin:
            await message.answer("Доступно только администратору.")
            return False
        return True


@router.message(Command("rate"))
async def show_rate(message: Message):
    if not await _admin_only(message):
        return
    s = await get_settings()
    await message.answer(f"Текущий курс: <b>{s.rub_per_usdt:.4f} RUB</b> за 1 USDT.")


@router.message(Command("rate_set"))
async def set_rate_start(message: Message, state: FSMContext):
    if not await _admin_only(message):
        return
    await state.set_state(RateStates.waiting_value)
    await message.answer(
        "Введите курс USDT (RUB за 1 USDT), например: <code>102.35</code> или <code>102,35</code>."
    )


@router.message(RateStates.waiting_value)
async def set_rate_value(message: Message, state: FSMContext):
    try:
        s = await set_rub_per_usdt(message.text or "")
    except Exception as e:
        await message.answer(f"Ошибка: {e}\nВведите число, например 101.90")
        return
    await state.clear()
    await message.answer(f"Курс обновлён: <b>{s.rub_per_usdt:.4f} RUB</b> за 1 USDT.")


# ===== ИНЛАЙН-МЕНЮ УПРАВЛЕНИЯ КУРСОМ =====

def _kb_rate_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Показать курс", callback_data="rate:show")
    kb.button(text="✏️ Изменить курс", callback_data="rate:set")
    kb.adjust(2)
    # быстрые корректировки
    kb.button(text="−1", callback_data="rate:adj:-1")
    kb.button(text="−0.1", callback_data="rate:adj:-0.1")
    kb.button(text="+0.1", callback_data="rate:adj:+0.1")
    kb.button(text="+1", callback_data="rate:adj:+1")
    kb.adjust(4)
    return kb.as_markup()


async def _admin_only_cq(cq: CallbackQuery) -> bool:
    async with async_session() as session:
        u = await session.scalar(select(User).where(User.tg_id == cq.from_user.id))
        if not u or u.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору.", show_alert=True)
            return False
        return True


@router.message(Command("rate_menu"))
async def rate_menu(message: Message):
    if not await _admin_only(message):
        return
    s = await get_settings()
    await message.answer(
        f"Текущий курс: <b>{s.rub_per_usdt:.4f} RUB</b> за 1 USDT.\n"
        "Выберите действие:",
        reply_markup=_kb_rate_menu()
    )


@router.callback_query(F.data == "rate:show")
async def cb_rate_show(cq: CallbackQuery):
    if not await _admin_only_cq(cq):
        return
    s = await get_settings()
    await cq.message.edit_text(
        f"Текущий курс: <b>{s.rub_per_usdt:.4f} RUB</b> за 1 USDT.\n"
        "Выберите действие:",
        reply_markup=_kb_rate_menu()
    )
    await cq.answer()


@router.callback_query(F.data == "rate:set")
async def cb_rate_set(cq: CallbackQuery, state: FSMContext):
    if not await _admin_only_cq(cq):
        return
    await state.set_state(RateStates.waiting_value)
    await cq.message.answer(
        "Введите курс USDT (RUB за 1 USDT), например: <code>102.35</code>"
    )
    await cq.answer()


@router.callback_query(F.data.startswith("rate:adj:"))
async def cb_rate_adjust(cq: CallbackQuery):
    if not await _admin_only_cq(cq):
        return
    step_str = cq.data.split("rate:adj:", 1)[1]
    try:
        step = Decimal(step_str)
    except Exception:
        await cq.answer("Некорректный шаг.", show_alert=True)
        return

    s = await get_settings()
    new_rate = Decimal(str(s.rub_per_usdt)) + step
    if new_rate <= 0 or new_rate > Decimal("1000000"):
        await cq.answer("Неверное значение курса.", show_alert=True)
        return

    updated = await set_rub_per_usdt(new_rate)
    await cq.message.edit_text(
        f"Курс обновлён: <b>{updated.rub_per_usdt:.4f} RUB</b> за 1 USDT.\n"
        "Выберите действие:",
        reply_markup=_kb_rate_menu()
    )
    await cq.answer("Сохранено")


@router.callback_query(F.data == "admin:rate_menu")
async def cb_open_rate_from_admin_menu(cq: CallbackQuery):
    if not await _admin_only_cq(cq):
        return
    s = await get_settings()
    await cq.message.answer(
        f"Текущий курс: <b>{s.rub_per_usdt:.4f} RUB</b> за 1 USDT.\n"
        "Выберите действие:",
        reply_markup=_kb_rate_menu()
    )
    await cq.answer()


