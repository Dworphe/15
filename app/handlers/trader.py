from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from app.db.base import async_session
from app.db.models import User, RoleEnum, TraderProfile, TraderCard
from app.handlers.utils import ensure_user, mask_card

# если есть сервис настроек — используем для конвертации (не обязательно)
try:
    from app.services.settings import get_settings
except Exception:
    get_settings = None  # graceful fallback

router = Router(name="trader")

def trader_menu_inline_kb(is_online: bool):
    b = InlineKeyboardBuilder()
    b.button(text=("🟢 Онлайн" if is_online else "⚪ Оффлайн"), callback_data="toggle_online")
    b.adjust(1)
    return b.as_markup()

def trader_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Баланс")],
            [KeyboardButton(text="🧑‍💼 Кабинет")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )

async def profile_text(user_id: int) -> str:
    async with async_session() as session:
        profile = await session.scalar(select(TraderProfile).where(TraderProfile.user_id == user_id))
        if not profile:
            return "Профиль не найден. Обратитесь к администратору."
        cards = (await session.execute(select(TraderCard).where(TraderCard.profile_id == profile.id))).scalars().all()

        lines = [
            "<b>Личный кабинет трейдера</b>",
            f"ID: {profile.external_id}",
            f"Ник: @{profile.nickname}",
            f"Имя: {profile.full_name}",
            f"Телефон: {profile.phone}",
            f"Реф. код: {profile.referral_code or '-'}",
            "",
            f"<b>Карты ({len(cards)}):</b>",
        ]
        for i, c in enumerate(cards, 1):
            bank_name = c.bank.value if c.bank.name != "OTHER" else (c.bank_other_name or "ДРУГОЙ БАНК")
            lines.append(
                f"{i}) {bank_name} • {mask_card(c.card_number)} • {c.holder_name} • "
                f"СБП: {'есть' if c.has_sbp else 'нет'}"
            )
            if c.has_sbp:
                lines.append(f"   СБП номер: {c.sbp_number}; СБП ФИО: {c.sbp_fullname}")
        return "\n".join(lines)

@router.message(Command("menu"))
async def show_menu(message: Message):
    async with async_session() as session:
        user = await ensure_user(session, message.from_user)
        if user.role != RoleEnum.trader:
            await message.answer("Меню трейдера доступно только трейдерам.")
            return
        if not user.is_active:
            await message.answer("Доступ не активирован. Введите токен: /access")
            return
        text = await profile_text(user.id)
        await message.answer(
            text + f"\n\nСтатус: {'Онлайн' if user.is_online else 'Оффлайн'}",
            reply_markup=trader_menu_inline_kb(user.is_online),
        )
        # показываем reply-клавиатуру для быстрых команд (в том числе Баланс)
        await message.answer("Меню действий:", reply_markup=trader_reply_kb())

# Кнопка/команда Баланс
@router.message(F.text.in_(["💰 Баланс", "Баланс"]))
@router.message(Command("balance"))
async def trader_balance(message: Message):
    async with async_session() as session:
        user = await ensure_user(session, message.from_user)
        if user.role != RoleEnum.trader:
            await message.answer("Доступно только трейдерам.")
            return
        if not user.is_active:
            await message.answer("Доступ не активирован. Введите токен: /access")
            return

        bal = float(user.balance_rub or 0.0)
        line = f"💰 <b>Ваш баланс</b>: <code>{bal:,.2f} RUB</code>".replace(",", " ")
        if get_settings:
            try:
                s = await get_settings()
                if s.rub_per_usdt > 0:
                    usdt = bal / float(s.rub_per_usdt)
                    line += f"\n≈ <code>{usdt:.2f} USDT</code> по курсу {float(s.rub_per_usdt):.4f}"
            except Exception:
                pass
        await message.answer(line, reply_markup=trader_reply_kb())

# Кнопка Кабинет
@router.message(F.text.in_(["🧑‍💼 Кабинет", "Кабинет"]))
async def trader_cabinet(message: Message):
    async with async_session() as session:
        user = await ensure_user(session, message.from_user)
        if user.role != RoleEnum.trader:
            await message.answer("Доступно только трейдерам.")
            return
        if not user.is_active:
            await message.answer("Доступ не активирован. Введите токен: /access")
            return
        
        text = await profile_text(user.id)
        await message.answer(
            text + f"\n\nСтатус: {'Онлайн' if user.is_online else 'Оффлайн'}",
            reply_markup=trader_menu_inline_kb(user.is_online),
        )

@router.callback_query(F.data == "toggle_online")
async def toggle_online(cq: CallbackQuery):
    async with async_session() as session:
        user = await session.scalar(select(User).where(User.tg_id == cq.from_user.id))
        if not user or user.role != RoleEnum.trader:
            await cq.answer("Доступно только трейдерам", show_alert=True)
            return
        if not user.is_active:
            await cq.answer("Сначала активируйте доступ через токен (/access).", show_alert=True)
            return

        user.is_online = not user.is_online
        await session.commit()

        text = await profile_text(user.id)
        await cq.message.edit_text(
            text + f"\n\nСтатус: {'Онлайн' if user.is_online else 'Оффлайн'}",
            reply_markup=trader_menu_inline_kb(user.is_online),
        )
        await cq.answer("Статус обновлён")
