from __future__ import annotations

from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

_SCHED: Optional[AsyncIOScheduler] = None


def init_scheduler(db_url: str) -> AsyncIOScheduler:
    """Создает и сохраняет singleton APScheduler с SQLAlchemyJobStore (SQLite)."""
    global _SCHED
    if _SCHED:
        return _SCHED
    jobstores = {
        "default": SQLAlchemyJobStore(url=db_url.replace("+aiosqlite", ""))  # sqlite:///...
    }
    _SCHED = AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
    return _SCHED


def get_scheduler() -> AsyncIOScheduler:
    """Возвращает ранее проинициализированный планировщик."""
    if _SCHED is None:
        raise RuntimeError("Scheduler is not initialized. Call init_scheduler(db_url) first.")
    return _SCHED

async def schedule_stage1_end(deal_id: int, after_seconds: int, db_url: str):
    from datetime import timedelta
    from app.utils.dt import now_tz
    scheduler = get_scheduler()
    scheduler.add_job(
        "app.workers.auction:end_stage1", "date",
        run_date=now_tz()+timedelta(seconds=after_seconds),
        args=[deal_id], id=f"deal:{deal_id}:stage1", replace_existing=True
    )

async def schedule_round_end(deal_id: int, round_number: int, after_seconds: int, db_url: str):
    from datetime import timedelta
    from app.utils.dt import now_tz
    scheduler = get_scheduler()
    scheduler.add_job(
        "app.workers.auction:end_round", "date",
        run_date=now_tz()+timedelta(seconds=after_seconds),
        args=[deal_id, round_number], id=f"deal:{deal_id}:round:{round_number}",
        replace_existing=True
    )

async def schedule_pay_timeout(deal_id: int, after_minutes: int, db_url: str):
    from datetime import timedelta
    from app.utils.dt import now_tz
    scheduler = get_scheduler()
    scheduler.add_job(
        "app.workers.auction:pay_timeout", "date",
        run_date=now_tz()+timedelta(minutes=after_minutes),
        args=[deal_id], id=f"deal:{deal_id}:pay", replace_existing=True
    )

async def schedule_trader_confirm_window(deal_id: int, after_minutes: int, db_url: str):
    from datetime import timedelta
    from app.utils.dt import now_tz
    scheduler = get_scheduler()
    scheduler.add_job(
        "app.workers.auction:trader_confirm_timeout", "date",
        run_date=now_tz()+timedelta(minutes=after_minutes),
        args=[deal_id], id=f"deal:{deal_id}:confirm", replace_existing=True
    )

async def schedule_buy_timeout(deal_id: int, after_minutes: int, db_url: str):
    from datetime import timedelta
    from app.utils.dt import now_tz
    scheduler = get_scheduler()
    scheduler.add_job(
        "app.workers.auction:buy_pay_timeout", "date",
        run_date=now_tz()+timedelta(minutes=after_minutes),
        args=[deal_id], id=f"deal:{deal_id}:buy_pay", replace_existing=True
    )

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
