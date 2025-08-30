from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.db.base import async_session
from app.db.models import RoleEnum, User
from app.handlers.utils import ensure_user
from app.services.settings import get_settings, update_settings

router = Router(name="admin_settings")

class FinStates(StatesGroup):
    editing = State()
    field = State()

def admin_only(func):
    async def wrapper(message: Message, *a, **kw):
        async with async_session() as session:
            u = await ensure_user(session, message.from_user)
            if u.role != RoleEnum.admin:
                await message.answer("Доступно только администратору.")
                return
        return await func(message, *a, **kw)
    return wrapper

@router.message(Command("settings"))
@admin_only
async def show_finance_menu(message: Message, state: FSMContext):
    s = await get_settings()
    text = (
        "<b>Настройки → Финансы</b>\n"
        f"Курс RUB/USDT: <code>{s.rub_per_usdt:.4f}</code>\n"
        f"Service Fee (дефолт): <code>{s.service_fee_default_pct:.2f}%</code>\n"
        f"Service Fee диапазон: <code>{s.service_fee_min_pct:.2f}% … {s.service_fee_max_pct:.2f}%</code>\n"
        f"Переопределение в сделке: <code>{'Да' if s.service_fee_overridable else 'Нет'}</code>\n"
        f"Раунд (сек): <code>{s.round_secs_default}</code>, Tie-rounds: <code>{s.max_tie_rounds_default}</code>"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Курс RUB/USDT", callback_data="fin:rubperusdt")
    kb.button(text="Fee (дефолт)", callback_data="fin:fee_default")
    kb.button(text="Fee (мин/макс)", callback_data="fin:fee_range")
    kb.button(text="Fee переопред. Да/Нет", callback_data="fin:fee_over")
    kb.button(text="Раунд/добавочные", callback_data="fin:rounds")
    kb.adjust(2,2,1)
    await message.answer(text, reply_markup=kb.as_markup())

@router.callback_query(F.data.startswith("fin:"))
async def fin_edit(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    key = cq.data.split(":")[1]
    await state.set_state(FinStates.editing)
    await state.update_data(key=key)
    prompts = {
        "rubperusdt": "Введите курс RUB_PER_USDT (например 98.55)",
        "fee_default": "Введите Service Fee по умолчанию, % (например 1.25)",
        "fee_range": "Введите мин и макс Fee через пробел, % (например 0.00 3.00)",
        "fee_over": "Включить переопределение в сделке? (да/нет)",
        "rounds": "Введите длительность раунда и число tie-rounds (например 20 5)",
    }
    await cq.message.answer(prompts[key])

@router.message(FinStates.editing)
async def fin_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key")
    txt = (message.text or "").strip().lower()
    actor = message.from_user.id

    try:
        if key == "rubperusdt":
            val = float(txt.replace(",", "."))
            if val < 0.01: raise ValueError
            await update_settings(actor, rub_per_usdt=val)
        elif key == "fee_default":
            val = float(txt.replace(",", "."))
            s = await get_settings()
            if not (s.service_fee_min_pct <= val <= s.service_fee_max_pct): raise ValueError
            await update_settings(actor, service_fee_default_pct=val)
        elif key == "fee_range":
            lo_s, hi_s = txt.split()
            lo, hi = float(lo_s.replace(",", ".")), float(hi_s.replace(",", "."))
            if not (0 <= lo <= hi <= 100): raise ValueError
            s = await update_settings(actor, service_fee_min_pct=lo, service_fee_max_pct=hi)
            # скорректируем дефолт, если вышел за границы
            if not (lo <= s.service_fee_default_pct <= hi):
                mid = max(lo, min(hi, s.service_fee_default_pct))
                await update_settings(actor, service_fee_default_pct=mid)
        elif key == "fee_over":
            flag = True if txt in ("да","yes","y","true","1") else False
            await update_settings(actor, service_fee_overridable=flag)
        elif key == "rounds":
            a, b = txt.split()
            rs, mr = int(a), int(b)
            if not (5 <= rs <= 120): raise ValueError
            if not (1 <= mr <= 10): raise ValueError
            await update_settings(actor, round_secs_default=rs, max_tie_rounds_default=mr)
        else:
            raise ValueError
        await state.clear()
        await show_finance_menu(message, state)
    except Exception:
        await message.answer("Неверный ввод. Повторите.")
