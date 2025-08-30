from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

log = logging.getLogger(__name__)

_SCHED: Optional[AsyncIOScheduler] = None


def init_scheduler(db_url: str) -> AsyncIOScheduler:
    """Создает и сохраняет singleton APScheduler с SQLAlchemyJobStore (SQLite)."""
    global _SCHED
    if _SCHED:
        return _SCHED
    
    # Безопасная инициализация с очисткой битых задач
    jobstores = {
        "default": SQLAlchemyJobStore(url=db_url.replace("+aiosqlite", ""))  # sqlite:///...
    }
    
    try:
        _SCHED = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
        log.info("Scheduler initialized successfully")
    except Exception as e:
        log.warning(f"Scheduler initialization failed: {e}. Clearing jobstore...")
        # Очищаем битый файл планировщика
        import os
        scheduler_db = db_url.replace("+aiosqlite", "").replace("sqlite:///", "")
        if os.path.exists(scheduler_db):
            try:
                os.remove(scheduler_db)
                log.info(f"Removed corrupted scheduler database: {scheduler_db}")
            except Exception as del_e:
                log.error(f"Failed to remove scheduler database: {del_e}")
        
        # Создаем новый планировщик без jobstore
        _SCHED = AsyncIOScheduler(timezone="UTC")
        log.info("Scheduler initialized without jobstore")
    
    return _SCHED


def get_scheduler() -> AsyncIOScheduler:
    """Возвращает ранее проинициализированный планировщик."""
    if _SCHED is None:
        raise RuntimeError("Scheduler is not initialized. Call init_scheduler(db_url) first.")
    return _SCHED

# Функции планировщика для аукционов отключены в патче №20
# Оставлены только базовые функции для таймеров

async def schedule_countdown_message(chat_id: int, message_id: int, until_iso: str, job_id: str, db_url: str):
    from datetime import datetime
    dt_until = datetime.fromisoformat(until_iso)
    scheduler = get_scheduler()
    scheduler.add_job(
        "app.workers.timers:update_countdown",
        "interval",
        seconds=5,
        args=[chat_id, message_id, until_iso],
        id=job_id,
        replace_existing=True,
        end_date=dt_until,
    )

def register_background_jobs(scheduler: AsyncIOScheduler) -> None:
    """Регистрирует фоновые задачи в планировщике"""
    from apscheduler.jobstores.base import JobLookupError
    from app.workers.timers import update_countdown
    
    # 2.1 Удалить старые варианты countdown
    try:
        scheduler.remove_job("update_countdown")
    except JobLookupError:
        pass
    
    # На всякий случай уберём все джобы, что указывают на старый update_countdown с args/kwargs
    for job in scheduler.get_jobs():
        try:
            if getattr(job, "func_ref", "").endswith("update_countdown") and (job.args or job.kwargs):
                scheduler.remove_job(job.id)
                log.info(f"Removed old countdown job: {job.id}")
        except Exception:
            continue
    
    # 2.2 Зарегистрировать наш единый job без аргументов
    scheduler.add_job(
        update_countdown,
        trigger="interval",
        seconds=5,
        id="update_countdown",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3,
    )
    log.info("Background job 'update_countdown' registered (clean)")
