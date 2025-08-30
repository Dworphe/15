"""
Модуль торгов - новая архитектура (патч №21)
Скелет для функционала покупки/продажи USDT
"""

from .enums import TradeType, TradeStage, TradeStatus
from .models_trade import Trade, TradeBid
from .service import TradingService
from .scheduler import TradingScheduler

__all__ = [
    "TradeType", "TradeStage", "TradeStatus",
    "Trade", "TradeBid", 
    "TradingService", "TradingScheduler"
]
