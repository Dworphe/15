# app/bot.py
from __future__ import annotations

import importlib
import logging
from typing import Optional, Iterable

import aiogram
from aiogram import Bot, Dispatcher

try:
    # Есть в aiogram 3.7+
    from aiogram.client.default import DefaultBotProperties  # type: ignore[attr-defined]
except Exception:
    DefaultBotProperties = None  # для 3.0.0–3.6.x

log = logging.getLogger(__name__)

# Защита от дублирования сессий
_BOT_SINGLETON = None
_DP_SINGLETON = None


def aiogram_version_tuple() -> tuple[int, int, int]:
    """
    Возвращает версию aiogram в виде кортежа (major, minor, patch).
    """
    parts = (aiogram.__version__ or "3.0.0").split(".")
    major = int(parts[0]) if len(parts) > 0 else 3
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    return major, minor, patch


def create_bot(token: str) -> Bot:
    """
    Создает Bot с учетом версии aiogram.
    3.7+ -> DefaultBotProperties(parse_mode="HTML")
    3.0.0–3.6.x -> Bot(..., parse_mode="HTML")
    """
    if DefaultBotProperties is not None:
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
        log.info("Bot created with DefaultBotProperties (aiogram >= 3.7).")
    else:
        bot = Bot(token=token, parse_mode="HTML")  # aiogram 3.0.0–3.6.x
        log.info("Bot created with parse_mode param (aiogram < 3.7).")
    return bot


def include_if_exists(dp: Dispatcher, module_path: str, attr: str = "router") -> None:
    """
    Пытается импортировать модуль и добавить его router в диспетчер.
    Если модуля или router нет — просто логируем предупреждение.
    """
    try:
        mod = importlib.import_module(module_path)
        router = getattr(mod, attr, None)
        if router is not None:
            dp.include_router(router)
            log.info("Router included: %s.%s", module_path, attr)
        else:
            log.warning("No attribute '%s' in module %s", attr, module_path)
    except Exception as e:
        log.warning("Router import failed: %s (%s)", module_path, e)


def setup_routers(dp: Dispatcher) -> None:
    """
    Подключает все доступные роутеры проекта.
    Список покрывает текущую структуру; отсутствующие модули игнорируются.
    """
    modules: Iterable[str] = [
        "app.handlers.common",
        "app.handlers.auth",
        "app.handlers.trader",
        "app.handlers.admin",
        "app.handlers.admin_balance",
        "app.handlers.admin_rate",
        "app.handlers.admin_settings",
        "app.handlers.admin_deals",
        "app.handlers.admin_deals_sell",
        "app.handlers.buy_master",
        "app.handlers.buy_auction",
        "app.handlers.buy_winner",
        "app.handlers.sell_auction",
        "app.handlers.sell_winner",
        "app.handlers.deal_responses",
    ]
    for m in modules:
        include_if_exists(dp, m)


def create_bot_and_dp(token: str) -> tuple[Bot, Dispatcher]:
    global _BOT_SINGLETON, _DP_SINGLETON
    if _BOT_SINGLETON and _DP_SINGLETON:
        return _BOT_SINGLETON, _DP_SINGLETON
    bot = create_bot(token)
    dp = Dispatcher()
    setup_routers(dp)
    _BOT_SINGLETON, _DP_SINGLETON = bot, dp
    return bot, dp
