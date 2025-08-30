from __future__ import annotations
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import text

from app.db.base import async_session, engine
from app.handlers.utils import ensure_user
from app.db.models import RoleEnum

router = Router(name="common")

@router.message(Command("start"))
async def start(message: Message):
    async with async_session() as session:
        user = await ensure_user(session, message.from_user)
        if user.role == RoleEnum.admin:
            await message.answer("Привет! Роль: Администратор.\nПанель: /admin")
        elif user.role == RoleEnum.trader:
            if not user.is_active:
                await message.answer("Привет! Для доступа требуется токен. Команда: /access")
            else:
                await message.answer("Привет! Открой меню: /menu")
        else:
            await message.answer("Привет! Роль: Оператор (пока без UI).")

@router.message(Command("ping"))
async def ping(message: Message):
    await message.answer("pong")

@router.message(Command("health"))
async def health(message: Message):
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        await message.answer("ok")
    except Exception as e:
        await message.answer(f"error: {e!r}")
