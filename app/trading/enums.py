"""
Перечисления для модуля торгов
"""

from enum import StrEnum

class TradeType(StrEnum):
    """Тип торговой операции"""
    BUY = "BUY"      # Покупка USDT за RUB
    SELL = "SELL"    # Продажа USDT за RUB

class TradeStage(StrEnum):
    """Этап торгов"""
    DRAFT = "DRAFT"           # Черновик
    PREPARING = "PREPARING"   # Подготовка
    ACTIVE = "ACTIVE"         # Активные торги
    CLOSED = "CLOSED"         # Закрыто
    COMPLETED = "COMPLETED"   # Завершено
    CANCELLED = "CANCELLED"   # Отменено

class TradeStatus(StrEnum):
    """Статус торгов"""
    PENDING = "PENDING"       # Ожидает
    RUNNING = "RUNNING"       # Выполняется
    SUCCESS = "SUCCESS"       # Успешно
    FAILED = "FAILED"         # Неудачно
    TIMEOUT = "TIMEOUT"       # Таймаут
