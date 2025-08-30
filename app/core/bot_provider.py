"""
Провайдер бота для безопасного доступа из фоновых задач
"""

from aiogram import Bot

_bot: Bot | None = None

def set_bot(bot: Bot) -> None:
    """Устанавливает глобальный экземпляр бота"""
    global _bot
    _bot = bot

def get_bot() -> Bot:
    """Возвращает глобальный экземпляр бота"""
    if _bot is None:
        raise RuntimeError("Bot is not initialized. Call set_bot() on startup.")
    return _bot
