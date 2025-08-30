#!/usr/bin/env python3
"""
Точка входа для запуска Telegram-бота аукционов.
Запуск: python -m app
"""

import asyncio
import sys
from pathlib import Path

# Добавляем корневую папку в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import main


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)
