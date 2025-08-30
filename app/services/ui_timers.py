from __future__ import annotations
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.base import async_session
from app.db.models import UiTimerMessage


async def get_timer_msg(chat_id: int, deal_id: int, kind: str = "buy") -> Optional[int]:
    async with async_session() as s:
        res = await s.execute(
            select(UiTimerMessage.message_id).where(
                UiTimerMessage.chat_id == chat_id,
                UiTimerMessage.deal_id == deal_id,
                UiTimerMessage.kind == kind,
            )
        )
        row = res.first()
        return row[0] if row else None


async def upsert_timer_msg(chat_id: int, deal_id: int, message_id: int, kind: str = "buy") -> None:
    async with async_session() as s:
        obj = UiTimerMessage(chat_id=chat_id, deal_id=deal_id, kind=kind, message_id=message_id)
        s.add(obj)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            await s.execute(
                UiTimerMessage.__table__.update()
                .where(
                    UiTimerMessage.chat_id == chat_id,
                    UiTimerMessage.deal_id == deal_id,
                    UiTimerMessage.kind == kind,
                )
                .values(message_id=message_id)
            )
            await s.commit()


async def ensure_timer_message(bot: Bot, chat_id: int, deal_id: int, kind: str = "buy") -> int:
    msg_id = await get_timer_msg(chat_id, deal_id, kind)
    if msg_id:
        return msg_id
    m = await bot.send_message(chat_id, "⏳ Осталось: --:--")
    await upsert_timer_msg(chat_id, deal_id, m.message_id, kind)
    return m.message_id


async def edit_timer_text(bot: Bot, chat_id: int, deal_id: int, text: str, kind: str = "buy") -> None:
    msg_id = await get_timer_msg(chat_id, deal_id, kind)
    if not msg_id:
        msg_id = await ensure_timer_message(bot, chat_id, deal_id, kind)
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
    except TelegramBadRequest as e:
        # message is not modified / message to edit not found — пересоздадим
        if "message is not modified" in str(e):
            return
        m = await bot.send_message(chat_id, text)
        await upsert_timer_msg(chat_id, deal_id, m.message_id, kind)
