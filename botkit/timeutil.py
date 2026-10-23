"""
Единый формат даты и времени: в базе — наивный UTC ISO-8601,
например `2026-07-26T18:03:11.123456`.
"""

from __future__ import annotations

import datetime

UTC = datetime.timezone.utc


def utc_now() -> datetime.datetime:
    """Текущее время UTC без tzinfo — ровно то, что раньше давал utcnow()."""
    return datetime.datetime.now(UTC).replace(tzinfo=None)


def utc_now_iso() -> str:
    """Строка для записи в базу. Формат идентичен прошлым версиям бота."""
    return utc_now().isoformat()


def parse(value: str | None) -> datetime.datetime | None:
    """Разбирает то, что лежит в базе. Терпим к записям со смещением (+00:00)
    и к пустым значениям — возвращает None вместо исключения."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def month_start_iso() -> str:
    """Начало текущего календарного месяца (UTC), для помесячных топов —
    строковое сравнение с `created_at` работает как обычно, ISO сортируется
    лексикографически так же, как по времени."""
    return utc_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


# Начало «суток» для дневных лимитов, час UTC. 3 — 6:00 по Москве.
GAME_DAY_START_UTC_HOUR = 3


def game_day_start(moment: datetime.datetime) -> datetime.datetime:
    """Начало суток (GAME_DAY_START_UTC_HOUR), в которые попадает `moment` (UTC)."""
    start = moment.replace(hour=GAME_DAY_START_UTC_HOUR, minute=0, second=0, microsecond=0)
    if moment < start:
        start -= datetime.timedelta(days=1)
    return start


def day_start_iso() -> str:
    """Начало текущих суток (GAME_DAY_START_UTC_HOUR)."""
    return game_day_start(utc_now()).isoformat()


def date_only(value: str | None, fallback: str = "?") -> str:
    """`2026-07-26` из полной отметки времени."""
    parsed = parse(value)
    return parsed.strftime("%Y-%m-%d") if parsed else fallback


def discord_timestamp(value: str | None, style: str = "D") -> str:
    """Метка времени Discord (`<t:...:D>`) — показывается в часовом поясе
    того, кто смотрит. Пустая строка, если дату разобрать не удалось."""
    parsed = parse(value)
    if parsed is None:
        return ""
    unix = int(parsed.replace(tzinfo=UTC).timestamp())
    return f"<t:{unix}:{style}>"
