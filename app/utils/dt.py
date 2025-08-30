# app/utils/dt.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

TZ = timezone.utc  # единая таймзона проекта (UTC)

def now_tz() -> datetime:
    """Текущее время в UTC с tzinfo (aware)."""
    return datetime.now(TZ)

def to_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Приводит datetime к aware-UTC.
    - None -> None
    - naive -> .replace(tzinfo=UTC)
    - aware -> .astimezone(UTC)
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)

def seconds_left(until: datetime) -> int:
    """Сколько секунд осталось до 'until' (оба в UTC-aware).
    Если 'until' naive — считаем, что это UTC.
    """
    _until = to_aware_utc(until)
    return int((_until - now_tz()).total_seconds())

def fmt_mmss(total_seconds: int) -> str:
    """Форматирует секунды в формат MM:SS."""
    if total_seconds < 0:
        total_seconds = 0
    m, s = divmod(total_seconds, 60)
    return f"{m:02d}:{s:02d}"
