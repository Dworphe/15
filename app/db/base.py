from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.logging_setup import get_logger

logger = get_logger(__name__)

# Создаем асинхронный движок
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=StaticPool,  # Для SQLite
    pool_pre_ping=True,
)

# Создаем фабрику сессий
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Контекстный менеджер для сессий
async_session = async_session_maker

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Получить асинхронную сессию БД."""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Инициализация базы данных."""
    logger.info("Инициализация базы данных...")
    
    # Создаем таблицы
    from app.db.models import Base, SystemSettings
    
    async with engine.begin() as conn:
        # Включаем WAL режим и внешние ключи для SQLite
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        
        # Создаем все таблицы
        await conn.run_sync(Base.metadata.create_all)

    # гарантируем единственную строку настроек
    from sqlalchemy import select
    async with async_session() as session:
        row = await session.scalar(select(SystemSettings).limit(1))
        if not row:
            row = SystemSettings()  # с дефолтами
            session.add(row)
            await session.commit()
    
    logger.info("База данных инициализирована")


async def close_db() -> None:
    """Закрытие соединений с БД."""
    logger.info("Закрытие соединений с базой данных...")
    await engine.dispose()
    logger.info("Соединения с базой данных закрыты")


# Событие для настройки SQLite при подключении
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Настройка SQLite при подключении."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=10000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()
