"""
Модели данных для торгов
"""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.dialects.sqlite import JSON

from app.db.models import Base
from .enums import TradeType, TradeStage, TradeStatus

TZ = ZoneInfo("Europe/Moscow")

class Trade(Base):
    """Модель торговой операции"""
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True)
    type = Column(String(10), nullable=False)  # TradeType
    stage = Column(String(20), nullable=False, default=TradeStage.DRAFT)  # TradeStage
    status = Column(String(20), nullable=False, default=TradeStatus.PENDING)  # TradeStatus
    
    # Суммы и проценты
    amount_rub = Column(Numeric(18, 2), nullable=False)  # Сумма в RUB
    amount_usdt = Column(Numeric(18, 2), nullable=False)  # Сумма в USDT
    base_pct = Column(Numeric(5, 2), nullable=False)  # Базовый процент
    service_fee_pct = Column(Numeric(5, 2), nullable=False, default=0.0)  # Комиссия сервиса
    
    # Временные метки
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(TZ))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(TZ), onupdate=lambda: datetime.now(TZ))
    
    # Дедлайны в JSON (пока как текст)
    deadlines = Column(Text, nullable=True)  # JSON строка с дедлайнами
    
    # Поля привязки UI и дедлайна (патч №23)
    admin_chat_id = Column(Integer, nullable=True)  # ID чата админа
    admin_message_id = Column(Integer, nullable=True)  # ID сообщения для редактирования
    countdown_until = Column(Text, nullable=True)  # ISO8601 UTC дедлайн для таймера
    
    # Связи
    bids = relationship("TradeBid", back_populates="trade", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Trade(id={self.id}, type={self.type}, amount_rub={self.amount_rub}, stage={self.stage})>"

class TradeBid(Base):
    """Модель ставки в торгах"""
    __tablename__ = "trade_bids"
    
    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"), nullable=False)
    trader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Данные ставки
    round_no = Column(Integer, nullable=False, default=1)  # Номер раунда
    bid_pct = Column(Numeric(5, 2), nullable=False)  # Процент ставки
    
    # Временная метка
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(TZ))
    
    # Связи
    trade = relationship("Trade", back_populates="bids")
    
    def __repr__(self):
        return f"<TradeBid(id={self.id}, trade_id={self.trade_id}, bid_pct={self.bid_pct})>"
