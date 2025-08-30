"""
Планировщик торгов - обертка над общим планировщиком
"""

from __future__ import annotations
import logging
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.scheduler import get_scheduler
from app.utils.dt import now_tz

log = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Moscow")

class TradingScheduler:
    """Планировщик для торговых операций"""
    
    @staticmethod
    async def schedule_tick(trade_id: int, when: datetime) -> bool:
        """
        Планирует тик для торговой операции
        
        Args:
            trade_id: ID торговой операции
            when: Время выполнения
        
        Returns:
            True при успехе, False при ошибке
        """
        try:
            scheduler = get_scheduler()
            
            # Пока заглушка - в следующем патче добавим реальные тикеры
            job_id = f"trade:{trade_id}:tick"
            
            # Добавляем задачу (пока заглушка)
            scheduler.add_job(
                "app.trading.service:TradingService.get_time_left",
                "date",
                run_date=when,
                args=[trade_id],
                id=job_id,
                replace_existing=True
            )
            
            log.info(f"Scheduled tick for trade {trade_id} at {when}")
            return True
            
        except Exception as e:
            log.error(f"Failed to schedule tick for trade {trade_id}: {e}")
            return False
    
    @staticmethod
    async def cancel_trade_jobs(trade_id: int) -> bool:
        """
        Отменяет все задачи для торговой операции
        
        Args:
            trade_id: ID торговой операции
        
        Returns:
            True при успехе, False при ошибке
        """
        try:
            scheduler = get_scheduler()
            
            # Ищем и отменяем все задачи для данной торговой операции
            jobs_to_remove = []
            for job in scheduler.get_jobs():
                if job.id and f"trade:{trade_id}:" in job.id:
                    jobs_to_remove.append(job.id)
            
            # Удаляем найденные задачи
            for job_id in jobs_to_remove:
                try:
                    scheduler.remove_job(job_id)
                    log.info(f"Removed job: {job_id}")
                except Exception as e:
                    log.warning(f"Failed to remove job {job_id}: {e}")
            
            log.info(f"Cancelled {len(jobs_to_remove)} jobs for trade {trade_id}")
            return True
            
        except Exception as e:
            log.error(f"Failed to cancel jobs for trade {trade_id}: {e}")
            return False
    
    @staticmethod
    async def schedule_deadline(trade_id: int, deadline_type: str, when: datetime) -> bool:
        """
        Планирует дедлайн для торговой операции
        
        Args:
            trade_id: ID торговой операции
            deadline_type: Тип дедлайна (например, "payment", "confirmation")
            when: Время дедлайна
        
        Returns:
            True при успехе, False при ошибке
        """
        try:
            scheduler = get_scheduler()
            
            job_id = f"trade:{trade_id}:{deadline_type}"
            
            # Пока заглушка - в следующем патче добавим реальные обработчики
            scheduler.add_job(
                "app.trading.service:TradingService.advance_stage",
                "date",
                run_date=when,
                args=[trade_id, "CLOSED"],  # Пока просто закрываем
                id=job_id,
                replace_existing=True
            )
            
            log.info(f"Scheduled {deadline_type} deadline for trade {trade_id} at {when}")
            return True
            
        except Exception as e:
            log.error(f"Failed to schedule {deadline_type} deadline for trade {trade_id}: {e}")
            return False
