from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from app.db.base import async_session
from app.db.models import Deal, DealParticipant, User
from app.handlers.utils import ensure_user

router = Router(name="deal_responses")

@router.callback_query(F.data.startswith("deal:"))
async def handle_deal_response(cq: CallbackQuery):
    """Обработка ответов трейдеров на сделку (принять/отклонить)"""
    try:
        # Парсим callback_data: "deal:{deal_id}:{action}"
        parts = cq.data.split(":")
        if len(parts) != 3:
            await cq.answer("Ошибка формата", show_alert=True)
            return
            
        deal_id = int(parts[1])
        action = parts[2]
        
        if action not in ["accept", "decline"]:
            await cq.answer("Неизвестное действие", show_alert=True)
            return
        
        async with async_session() as s:
            # Проверяем, что сделка существует и активна
            deal = await s.get(Deal, deal_id)
            if not deal or deal.status.value != "OPEN":
                await cq.answer("Сделка недоступна", show_alert=True)
                return
            
            # Получаем пользователя
            user = await ensure_user(s, cq.from_user)
            if not user:
                await cq.answer("Ошибка пользователя", show_alert=True)
                return
            
            # Проверяем, не отвечал ли уже пользователь
            existing = await s.scalar(
                select(DealParticipant).where(
                    DealParticipant.deal_id == deal_id,
                    DealParticipant.user_id == user.id
                )
            )
            
            if existing:
                await cq.answer("Вы уже ответили на эту сделку", show_alert=True)
                return
            
            # Создаем запись об участии
            participant = DealParticipant(
                deal_id=deal_id,
                user_id=user.id,
                accepted=(action == "accept")
            )
            s.add(participant)
            await s.commit()
            
            # Отправляем подтверждение
            if action == "accept":
                await cq.message.edit_text(
                    "✅ <b>Сделка принята!</b>\n\n"
                    "Ждите начала аукциона...\n"
                    "⏰ Время на принятие: 60 сек",
                    parse_mode="HTML"
                )
                await cq.answer("✅ +1 REP за принятие сделки")
            else:
                await cq.message.edit_text(
                    "❌ <b>Сделка отклонена</b>\n\n"
                    "Вы можете участвовать в других сделках",
                    parse_mode="HTML"
                )
                await cq.answer("❌ -2 REP за отклонение сделки")
                
    except Exception as e:
        await cq.answer(f"Ошибка: {str(e)}", show_alert=True)
        print(f"Error in handle_deal_response: {e}")
