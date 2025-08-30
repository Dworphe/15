from __future__ import annotations
import random, string
from typing import Iterable
from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, RoleEnum

def mask_card(card_number: str) -> str:
    digits = "".join(ch for ch in card_number if ch.isdigit())
    tail = digits[-4:] if len(digits) >= 4 else digits
    return f"**** **** **** {tail}" if tail else "****"

def generate_token(length: int = 20) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))

async def ensure_user(session: AsyncSession, tg_user: TgUser) -> User:
    user = await session.scalar(select(User).where(User.tg_id == tg_user.id))
    if user:
        # Автоапдейт роли для админов
        if tg_user.id in settings.admins and user.role != RoleEnum.admin:
            user.role = RoleEnum.admin
            user.is_active = True
            await session.commit()
        return user
    # Создаём нового (неактивный трейдер до ввода токена)
    role = RoleEnum.admin if tg_user.id in settings.admins else RoleEnum.trader
    user = User(tg_id=tg_user.id, role=role, is_active=(role == RoleEnum.admin), is_online=False)
    session.add(user)
    await session.commit()
    return user

def role_caption(role: RoleEnum) -> str:
    return {
        RoleEnum.admin: "Администратор",
        RoleEnum.trader: "Трейдер",
        RoleEnum.operator: "Оператор",
    }[role]
