import logging
from typing import Optional


def setup_logging(level: int = logging.INFO, log_format: Optional[str] = None) -> None:
    """Настройка логирования."""
    if log_format is None:
        log_format = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    
    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=[
            logging.StreamHandler(),
        ]
    )
    
    # Устанавливаем уровень для сторонних библиотек
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    logger.info("Логирование настроено")


def get_logger(name: str) -> logging.Logger:
    """Получить логгер с указанным именем."""
    return logging.getLogger(name)
