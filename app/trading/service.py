"""
Сервис торгов - фасад для бизнес-логики
"""

from __future__ import annotations
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from typing import Optional, Dict, Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import async_session
from app.db.models import User
from .models_trade import Trade, TradeBid
from .enums import TradeType, TradeStage, TradeStatus

log = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Moscow")

class TradingService:
    """Сервис для управления торговыми операциями"""
    
    @staticmethod
    async def create_trade(
        trade_type: TradeType,
        amount_rub: Decimal,
        amount_usdt: Decimal,
        base_pct: Decimal,
        service_fee_pct: Decimal = Decimal("0.0"),
        deadlines: Optional[Dict[str, Any]] = None
    ) -> Optional[Trade]:
        """
        Создает новую торговую операцию
        
        Args:
            trade_type: Тип операции (BUY/SELL)
            amount_rub: Сумма в RUB
            amount_usdt: Сумма в USDT
            base_pct: Базовый процент
            service_fee_pct: Комиссия сервиса
            deadlines: Дедлайны в виде словаря
        
        Returns:
            Созданная торговая операция или None при ошибке
        """
        try:
            # Валидация аргументов
            if amount_rub <= 0 or amount_usdt <= 0:
                log.error(f"Invalid amounts: RUB={amount_rub}, USDT={amount_usdt}")
                return None
            
            if not (-100 <= base_pct <= 100):
                log.error(f"Invalid base_pct: {base_pct}")
                return None
            
            if not (0 <= service_fee_pct <= 100):
                log.error(f"Invalid service_fee_pct: {service_fee_pct}")
                return None
            
            # Создание торговой операции
            trade = Trade(
                type=trade_type.value,
                stage=TradeStage.DRAFT.value,
                status=TradeStatus.PENDING.value,
                amount_rub=amount_rub,
                amount_usdt=amount_usdt,
                base_pct=base_pct,
                service_fee_pct=service_fee_pct,
                deadlines=str(deadlines) if deadlines else None
            )
            
            async with async_session() as session:
                session.add(trade)
                await session.commit()
                await session.refresh(trade)
            
            log.info(f"Created trade: id={trade.id}, type={trade_type}, amount_rub={amount_rub}")
            return trade
            
        except Exception as e:
            log.error(f"Failed to create trade: {e}")
            return None
    
    @staticmethod
    async def advance_stage(trade_id: int, new_stage: TradeStage) -> bool:
        """
        Переводит торговую операцию на следующий этап
        
        Args:
            trade_id: ID торговой операции
            new_stage: Новый этап
        
        Returns:
            True при успехе, False при ошибке
        """
        try:
            async with async_session() as session:
                trade = await session.get(Trade, trade_id)
                if not trade:
                    log.error(f"Trade not found: {trade_id}")
                    return False
                
                trade.stage = new_stage.value
                trade.updated_at = datetime.now(TZ)
                await session.commit()
                
                log.info(f"Advanced trade {trade_id} to stage: {new_stage}")
                return True
                
        except Exception as e:
            log.error(f"Failed to advance trade {trade_id}: {e}")
            return False
    
    @staticmethod
    async def place_bid(
        trade_id: int,
        trader_id: int,
        bid_pct: Decimal,
        round_no: int = 1
    ) -> Optional[TradeBid]:
        """
        Размещает ставку в торгах
        
        Args:
            trade_id: ID торговой операции
            trader_id: ID трейдера
            bid_pct: Процент ставки
            round_no: Номер раунда
        
        Returns:
            Созданная ставка или None при ошибке
        """
        try:
            # Валидация
            if not (-100 <= bid_pct <= 100):
                log.error(f"Invalid bid_pct: {bid_pct}")
                return None
            
            # Проверка существования торгов и трейдера
            async with async_session() as session:
                trade = await session.get(Trade, trade_id)
                if not trade:
                    log.error(f"Trade not found: {trade_id}")
                    return None
                
                trader = await session.get(User, trader_id)
                if not trader:
                    log.error(f"Trader not found: {trader_id}")
                    return None
                
                # Создание ставки
                bid = TradeBid(
                    trade_id=trade_id,
                    trader_id=trader_id,
                    round_no=round_no,
                    bid_pct=bid_pct
                )
                
                session.add(bid)
                await session.commit()
                await session.refresh(bid)
            
            log.info(f"Placed bid: trade_id={trade_id}, trader_id={trader_id}, bid_pct={bid_pct}")
            return bid
            
        except Exception as e:
            log.error(f"Failed to place bid: {e}")
            return None
    
    @staticmethod
    async def get_time_left(trade_id: int) -> Optional[int]:
        """
        Возвращает оставшееся время до дедлайна (в секундах)
        
        Args:
            trade_id: ID торговой операции
        
        Returns:
            Время в секундах или None при ошибке
        """
        try:
            # Пока заглушка - возвращаем None
            # В следующем патче добавим реальный расчет
            log.debug(f"Getting time left for trade {trade_id} (stub)")
            return None
            
        except Exception as e:
            log.error(f"Failed to get time left for trade {trade_id}: {e}")
            return None
    
    @staticmethod
    async def register_admin_view(trade_id: int, chat_id: int, message_id: int) -> None:
        """Регистрирует UI-сообщение для редактирования таймера"""
        try:
            async with async_session() as s:
                await s.execute(
                    update(Trade)
                    .where(Trade.id == trade_id)
                    .values(admin_chat_id=chat_id, admin_message_id=message_id)
                )
                await s.commit()
                log.info(f"Registered admin view for trade {trade_id}: chat_id={chat_id}, message_id={message_id}")
        except Exception as e:
            log.error(f"Failed to register admin view for trade {trade_id}: {e}")
    
    @staticmethod
    async def clear_admin_view(trade_id: int) -> None:
        """Очищает привязку UI-сообщения"""
        try:
            async with async_session() as s:
                await s.execute(
                    update(Trade)
                    .where(Trade.id == trade_id)
                    .values(admin_chat_id=None, admin_message_id=None)
                )
                await s.commit()
                log.info(f"Cleared admin view for trade {trade_id}")
        except Exception as e:
            log.error(f"Failed to clear admin view for trade {trade_id}: {e}")
    
    @staticmethod
    async def set_countdown(trade_id: int, seconds: int | None) -> None:
        """Установить дедлайн (UTC now + seconds) или снять, если None."""
        try:
            iso = None
            if isinstance(seconds, int) and seconds > 0:
                iso = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
            
            async with async_session() as s:
                await s.execute(
                    update(Trade)
                    .where(Trade.id == trade_id)
                    .values(countdown_until=iso)
                )
                await s.commit()
                
                if iso:
                    log.info(f"Set countdown for trade {trade_id}: {seconds}s until {iso}")
                else:
                    log.info(f"Cleared countdown for trade {trade_id}")
        except Exception as e:
            log.error(f"Failed to set countdown for trade {trade_id}: {e}")
    
    @staticmethod
    async def get_trade(trade_id: int) -> Optional[Trade]:
        """Получить сделку по ID"""
        try:
            async with async_session() as s:
                res = await s.execute(select(Trade).where(Trade.id == trade_id))
                return res.scalar_one_or_none()
        except Exception as e:
            log.error(f"Failed to get trade {trade_id}: {e}")
            return None
    
    @staticmethod
    def _fmt_pct(x: Optional[float]) -> str:
        """Форматирование процента с проверкой на None"""
        return f"{float(x):.2f}%" if x is not None else "—"
    
    @staticmethod
    async def render_trade_card(trade_id: int, time_left_sec: Optional[int]) -> str:
        """Вернуть текст карточки сделки для сообщения администратора."""
        try:
            trade = await TradingService.get_trade(trade_id)
            if not trade:
                return f"Сделка #{trade_id}\n⚠️ Не найдена"

            # Пытаемся вытащить реквизиты из JSON поля deadlines
            details = ""
            try:
                if trade.deadlines:
                    data = json.loads(trade.deadlines) if isinstance(trade.deadlines, str) else trade.deadlines
                    acc = data.get("account_details") if isinstance(data, dict) else None
                    if acc:
                        details = f"\nРеквизиты: {acc}"
            except Exception:
                pass

            timer_line = "Таймер: не запущен"
            if isinstance(time_left_sec, int):
                if time_left_sec <= 0:
                    timer_line = "⏱ 00:00"
                else:
                    mm, ss = divmod(time_left_sec, 60)
                    timer_line = f"⏳ Осталось: {mm:02d}:{ss:02d}"

            lines = [
                f"Сделка #{trade.id} - {trade.type}",
                f"Сумма: {trade.amount_rub:.2f} RUB" if trade.amount_rub is not None else None,
                f"USDT: {trade.amount_usdt:.2f}" if trade.amount_usdt is not None else None,
                f"Базовый %: {TradingService._fmt_pct(trade.base_pct)}",
                f"Комиссия: {TradingService._fmt_pct(trade.service_fee_pct)}",
            ]
            text = "💼 " + "\n".join([ln for ln in lines if ln]) + details + f"\n{timer_line}"
            return text
            
        except Exception as e:
            log.error(f"Failed to render trade card for {trade_id}: {e}")
            return f"Сделка #{trade_id}\n❌ Ошибка рендера"
