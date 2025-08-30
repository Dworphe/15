from pydantic import BaseModel
from dotenv import load_dotenv
import os
from typing import Set

load_dotenv()

class Settings(BaseModel):
    bot_token: str
    database_url: str
    scheduler_db_url: str
    admins: Set[int] = set()
    tz: str = "Europe/Amsterdam"

def _parse_admins(raw: str | None) -> Set[int]:
    if not raw:
        return set()
    out = set()
    for part in raw.replace(";", ",").split(","):
        p = part.strip()
        if p:
            try:
                out.add(int(p))
            except ValueError:
                pass
    return out

settings = Settings(
    bot_token=os.getenv("BOT_TOKEN", ""),
    database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/app.db"),
    scheduler_db_url=os.getenv("SCHEDULER_DB_URL", "sqlite:///data/scheduler.db"),
    admins=_parse_admins(os.getenv("ADMINS")),
)
