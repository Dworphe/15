# app/services/balance.py
from __future__ import annotations
from sqlalchemy import select, update
from app.db.base import async_session
from app.db.models import User

async def ensure_usdt_margin(user_id: int, need_usdt: float) -> bool:
    async with async_session() as s:
        u = await s.get(User, user_id)
        return float(u.balance_usdt) >= need_usdt

async def reserve_usdt(user_id: int, amount: float) -> bool:
    async with async_session() as s:
        u = await s.get(User, user_id)
        if float(u.balance_usdt) < amount: return False
        u.balance_usdt = float(u.balance_usdt) - amount
        u.reserved_usdt = float(u.reserved_usdt) + amount
        await s.commit(); return True

async def release_usdt(user_id: int, amount: float) -> None:
    async with async_session() as s:
        u = await s.get(User, user_id)
        u.reserved_usdt = max(0.0, float(u.reserved_usdt) - amount)
        u.balance_usdt = float(u.balance_usdt) + amount
        await s.commit()

async def consume_reserved_usdt(user_id: int, amount: float) -> bool:
    async with async_session() as s:
        u = await s.get(User, user_id)
        if float(u.reserved_usdt) < amount: return False
        u.reserved_usdt = float(u.reserved_usdt) - amount
        await s.commit(); return True
