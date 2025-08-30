from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from decimal import Decimal

from app.db.base import async_session
from app.db.models import User, RoleEnum
# аудит есть в проекте — логируем операции (если модель присутствует)
try:
    from app.db.models import AuditLog
except Exception:
    AuditLog = None

from app.handlers.utils import ensure_user

router = Router(name="admin_balance")

class BalopStates(StatesGroup):
    op = State()
    user_id = State()
    amount = State()

def _round2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01")))

async def _admin_only(message: Message) -> bool:
    async with async_session() as session:
        u = await ensure_user(session, message.from_user)
        if u.role != RoleEnum.admin:
            await message.answer("Доступно только администратору.")
            return False
        return True

async def _find_user_by_any(session, raw_text: str, reply_from=None) -> User | None:
    """ХЕЛПЕР: найти пользователя по разным идентификаторам"""
    # 1) если админ ответил реплаем на сообщение пользователя
    if reply_from:
        u = await session.scalar(select(User).where(User.tg_id == reply_from.id))
        if u:
            return u

    if not raw_text:
        return None
    t = raw_text.strip()

    # 2) tg://user?id=123456 или просто число
    # выцепим цифры
    for prefix in ("tg://user?id=", "user?id="):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break

    # если число — пробуем как users.id, затем как tg_id
    digits = t.replace(" ", "")
    if digits.isdigit():
        num = int(digits)
        # сначала пробуем как PK users.id
        u = await session.get(User, num)
        if u:
            return u
        # затем как tg_id
        return await session.scalar(select(User).where(User.tg_id == num))

    return None

@router.message(Command("balance_add"))
async def balance_add_start(message: Message, state: FSMContext):
    if not await _admin_only(message): return
    await state.set_state(BalopStates.op)
    await state.update_data(op="ADD")
    await state.set_state(BalopStates.user_id)
    await message.answer("Введите <b>users.id</b> или <b>tg_id</b>, или ответьте реплаем на сообщение пользователя.")

@router.message(Command("balance_sub"))
async def balance_sub_start(message: Message, state: FSMContext):
    if not await _admin_only(message): return
    await state.set_state(BalopStates.op)
    await state.update_data(op="SUB")
    await state.set_state(BalopStates.user_id)
    await message.answer("Введите <b>users.id</b> или <b>tg_id</b>, или ответьте реплаем на сообщение пользователя.")

@router.message(BalopStates.user_id)
async def bal_user_id(message: Message, state: FSMContext):
    async with async_session() as session:
        reply_from = message.reply_to_message.from_user if message.reply_to_message else None
        user = await _find_user_by_any(session, message.text or "", reply_from=reply_from)
        if not user:
            await message.answer(
                "Пользователь не найден.\n"
                "Укажи <b>users.id</b> или <b>tg_id</b>, или ответь реплаем на его сообщение."
            )
            return
        await state.update_data(user_id=user.id)
        label = f"id={user.id}, tg_id={user.tg_id}"
        await state.set_state(BalopStates.amount)
        await message.answer(
            f"Найден пользователь: <code>{label}</code>\n"
            "Введите сумму (RUB), например 1500.00"
        )

@router.message(BalopStates.amount)
async def bal_amount(message: Message, state: FSMContext):
    txt = (message.text or "").replace(" ", "").replace(",", ".")
    try:
        amount = float(txt)
        if amount <= 0: raise ValueError
    except Exception:
        await message.answer("Сумма некорректна. Введите положительное число, например 1500.00")
        return

    data = await state.get_data()
    op = data["op"]
    uid = data["user_id"]

    async with async_session() as session:
        user = await session.get(User, uid)
        if not user:
            await message.answer("Пользователь не найден.")
            await state.clear()
            return

        old = float(user.balance_rub or 0.0)
        if op == "ADD":
            new_bal = _round2(old + amount)
            action = "BALANCE_ADD"
            note = f"Зачисление {amount:.2f} RUB админом @{message.from_user.username or message.from_user.id}"
        else:
            if old < amount:
                await message.answer(f"Недостаточно средств. Текущий баланс: {old:.2f} RUB")
                await state.clear()
                return
            new_bal = _round2(old - amount)
            action = "BALANCE_SUB"
            note = f"Списание {amount:.2f} RUB админом @{message.from_user.username or message.from_user.id}"

        user.balance_rub = new_bal
        await session.commit()

        # аудит (если модель есть)
        if AuditLog:
            try:
                session.add(AuditLog(
                    actor_tg_id=message.from_user.id,
                    actor_role="admin",
                    action=action,
                    entity="user_balance",
                    before=str({"user_id": uid, "balance_rub": old}),
                    after=str({"user_id": uid, "balance_rub": new_bal}),
                    note=note,
                ))
                await session.commit()
            except Exception:
                pass

    await message.answer(
        f"Готово. Баланс пользователя id={uid}: {new_bal:.2f} RUB (был {old:.2f} RUB)."
    )

    # (опционально) уведомим пользователя
    try:
        await message.bot.send_message(
            chat_id=user.tg_id,
            text=("💰 На ваш баланс зачислено "
                  if op == "ADD" else
                  "💳 С вашего баланса списано ")
                 + f"{amount:.2f} RUB. Текущий баланс: {new_bal:.2f} RUB."
        )
    except Exception:
        pass

    await state.clear()

# ===== ИНЛАЙН-МЕНЮ ДЛЯ АДМИНА =====

def admin_balance_menu_kb():
    """Клавиатура для меню управления балансом"""
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Зачислить на баланс", callback_data="admin_balance:add")
    kb.button(text="➖ Снять с баланса", callback_data="admin_balance:sub")
    kb.adjust(1)
    return kb.as_markup()

@router.message(Command("balance_menu"))
async def admin_balance_menu(message: Message):
    """Команда для открытия меню управления балансом"""
    # защита: доступ только админу
    if not await _admin_only(message):
        return
    await message.answer(
        "<b>Операции с балансом</b>\nВыберите действие:",
        reply_markup=admin_balance_menu_kb()
    )

async def _admin_only_cq(cq: CallbackQuery) -> bool:
    """Мини-проверка роли по tg_id для callback'ов"""
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == cq.from_user.id))
        if not user or user.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору.", show_alert=True)
            return False
        return True

@router.callback_query(F.data == "admin_balance:add")
async def cb_admin_balance_add(cq: CallbackQuery, state: FSMContext):
    """Callback для кнопки 'Зачислить на баланс'"""
    if not await _admin_only_cq(cq):
        return
    await state.set_state(BalopStates.op)
    await state.update_data(op="ADD")
    await state.set_state(BalopStates.user_id)
    await cq.message.answer("Введите <b>users.id</b> или <b>tg_id</b>, или ответьте реплаем на сообщение пользователя.")
    await cq.answer()

@router.callback_query(F.data == "admin_balance:sub")
async def cb_admin_balance_sub(cq: CallbackQuery, state: FSMContext):
    """Callback для кнопки 'Снять с баланса'"""
    if not await _admin_only_cq(cq):
        return
    await state.set_state(BalopStates.op)
    await state.update_data(op="SUB")
    await state.set_state(BalopStates.user_id)
    await cq.message.answer("Введите <b>users.id</b> или <b>tg_id</b>, или ответьте реплаем на сообщение пользователя.")
    await cq.answer()
