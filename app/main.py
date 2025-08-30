# app/main.py
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram import __version__ as AIROGRAM_VERSION

from app.bot import create_bot_and_dp, aiogram_version_tuple
from app.config import settings  # используем существующий объект settings
from app.logging_setup import setup_logging
from app.scheduler import init_scheduler
from app.db.base import init_db, close_db  # используем существующие функции


async def _run() -> None:
    bot, dp = create_bot_and_dp(settings.bot_token)

    # Инициализация БД
    await init_db()

    # Планировщик
    scheduler = init_scheduler(settings.database_url)
    scheduler.start()

    logging.getLogger(__name__).info("Starting polling (aiogram %s)", AIROGRAM_VERSION)
    await dp.start_polling(bot)


@asynccontextmanager
async def _temporary_bot(token: str):
    bot, dp = create_bot_and_dp(token)
    try:
        yield bot
    finally:
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Print environment & exit")
    args = parser.parse_args()

    setup_logging = globals().get("setup_logging")
    if callable(setup_logging):
        setup_logging()
    else:
        logging.basicConfig(level=logging.INFO)

    if args.check:
        print("== Environment check ==")
        print("AIROGRAM_VERSION:", AIROGRAM_VERSION, "->", aiogram_version_tuple())
        print("BOT_TOKEN set:", "YES" if os.getenv("BOT_TOKEN") else "NO")
        # здесь можно вывести и другие проверки при необходимости
        return

    asyncio.run(_run())


if __name__ == "__main__":
    main()
