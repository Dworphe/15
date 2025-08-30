from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select
import importlib

from app.db.base import async_session
from app.db.models import User, TraderProfile, AccessToken, RoleEnum

router = Router(name="auth")
TZ = ZoneInfo("Europe/Moscow")

class AuthStates(StatesGroup):
    waiting_token = State()

WELCOME_TEXT = (
    "Привет! Для доступа требуется токен.\n"
    "Отправьте ваш 20‑значный токен ответным сообщением.\n"
    "Если потеряли — обратитесь к администратору."
)

async def _show_main_menu(message: Message) -> bool:
    """Пытаемся вызвать существующий рендер главного меню из проекта.
    Возвращает True, если удалось; иначе False (покажем фоллбэк‑текст)."""
    candidates = (
        ("app.handlers.trader", "show_menu"),
        ("app.handlers.trader", "open_menu"),
        ("app.handlers.trader", "menu"),
        ("app.handlers.common", "show_menu"),
    )
    for modname, fname in candidates:
        try:
            mod = importlib.import_module(modname)
            fn = getattr(mod, fname, None)
            if callable(fn):
                await fn(message)
                return True
        except Exception:
            continue
    # фоллбэк — без падений
    await message.answer("Готово! Доступ активирован. Откройте меню командой /menu.")
    return False

async def _activate_token(tg_user, token_str: str) -> tuple[bool, str]:
    token = (token_str or "").strip().replace(" ", "").upper()
    if not (16 <= len(token) <= 32):
        return False, "Неверный формат токена."
    async with async_session() as s:
        row = await s.scalar(select(AccessToken).where(AccessToken.token == token))
        if not row:
            return False, "Токен не найден."
        if row.consumed_at is not None:
            return False, "Токен уже использован."
        # найдём/создадим пользователя по tg_id
        u = await s.scalar(select(User).where(User.tg_id == tg_user.id))
        if not u:
            u = User(tg_id=tg_user.id, role=RoleEnum.trader, is_active=True)
            s.add(u)
            await s.flush()
        else:
            u.is_active = True
        # привязываем профиль
        prof = await s.get(TraderProfile, row.profile_id)
        if prof and not prof.user_id:
            prof.user_id = u.id
        # помечаем токен израсходованным
        from app.utils.dt import now_tz
        row.consumed_at = now_tz()
        await s.commit()
    return True, "Доступ активирован."

@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    # если уже активен — сразу показываем меню
    from sqlalchemy import select as _select
    async with async_session() as s:
        u = await s.scalar(_select(User).where(User.tg_id == message.from_user.id))
        if u and u.is_active:
            await _show_main_menu(message)
            return
    await state.clear()
    await state.set_state(AuthStates.waiting_token)
    await message.answer(WELCOME_TEXT)

# Поддержим и старую команду, но делаем её необязательной
@router.message(Command("access"))
async def access_cmd(message: Message, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        ok, msg = await _activate_token(message.from_user, parts[1])
        if ok:
            await state.clear()
            await message.answer("✅ " + msg)
            await _show_main_menu(message)
            return
        await message.answer("❌ " + msg + "\nПопробуйте снова. Отправьте токен сообщением.")
        await state.set_state(AuthStates.waiting_token)
        return
    # если токен не передан в команде — переводим в состояние ожидания
    await state.set_state(AuthStates.waiting_token)
    await message.answer(WELCOME_TEXT)

# Пришёл токен как обычный текст, пока ждём токен
@router.message(AuthStates.waiting_token, F.text)
async def token_as_text(message: Message, state: FSMContext):
    ok, msg = await _activate_token(message.from_user, message.text or "")
    if ok:
        await state.clear()
        await message.answer("✅ " + msg)
        await _show_main_menu(message)
        return
    await message.answer("❌ " + msg + "\nОтправьте корректный токен ещё раз.")


