"""
SQLite на весь бот: одно соединение-писатель + читатели, кэш чтения, схема.

Соединение открывается один раз при старте и живёт до остановки бота
(открывать своё на каждый запрос — секунды на слабом хостинге, после которых
Discord выдаёт «Unknown interaction»):
- включён режим WAL — чтение не блокируется записью;
- `synchronous=NORMAL` — на порядок меньше обращений к диску, при WAL это
  по-прежнему безопасно для целостности базы;
- есть повтор при «database is locked» вместо мгновенной ошибки;
- запись идёт под общей блокировкой, поэтому две команды одновременно не
  ломают друг другу транзакции.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Iterable, Sequence

import aiosqlite

logger = logging.getLogger("botkit")

_connection: aiosqlite.Connection | None = None
_path: str = ""
_write_lock = asyncio.Lock()
# Отдельные соединения только для чтения: в WAL читатели не ждут писателя и
# друг друга: тяжёлый отчёт не держит остальные запросы.
READERS = 3
_readers: list[aiosqlite.Connection] = []
_reader_started: list[list[float]] = []  # начало каждого идущего запроса

BUSY_TIMEOUT_MS = 5000
RETRY_ATTEMPTS = 3
RETRY_DELAY = 0.1
SLOW_QUERY_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Кэш чтения
# ---------------------------------------------------------------------------
# Ответ SELECT запоминается, пока в его таблицы никто не писал: каждая запись через
# execute/execute_many поднимает «версию» своей таблицы, и всё, что читало
# эту таблицу, считается заново. Пишет в базу только этот процесс и только
# через этот модуль, поэтому устаревшего ответа не бывает.

READ_CACHE_ENABLED = True
READ_CACHE_MAX_ENTRIES = 8000
READ_CACHE_MAX_ROWS = 5000

_table_versions: dict[str, int] = {}
_all_version = 0  # запись, у которой не понять таблицу, — сброс всего
_read_cache: "OrderedDict[tuple, tuple[tuple, Any]]" = OrderedDict()
read_cache_stats = {"hits": 0, "misses": 0}

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WRITE_TARGET = re.compile(
    r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=2048)
def _read_deps(sql: str) -> tuple[str, ...] | None:
    """Все слова запроса — заведомо больше, чем таблиц в нём (лишнее слово
    только изредка сбросит кэш зря). None — запрос не кэшируется."""
    head = sql.lstrip()[:6].upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        return None
    return tuple(sorted({word.lower() for word in _WORD.findall(sql)}))


def _versions(deps: tuple[str, ...]) -> tuple:
    return (_all_version, *(_table_versions.get(word, 0) for word in deps))


def _bump(sql: str) -> None:
    global _all_version
    match = _WRITE_TARGET.match(sql)
    if match is None:
        _all_version += 1
        return
    table = match.group(1).lower()
    _table_versions[table] = _table_versions.get(table, 0) + 1


def clear_read_cache() -> None:
    """Сброс всего — после правки базы в обход execute (VACUUM, внешние
    скрипты) и в проверках."""
    global _all_version
    _all_version += 1
    _read_cache.clear()


async def _cached_read(kind: str, sql: str, params: Sequence[Any], run):
    deps = _read_deps(sql) if READ_CACHE_ENABLED else None
    if deps is None:
        return await run()
    key = (kind, sql, tuple(params))
    versions = _versions(deps)
    hit = _read_cache.get(key)
    if hit is not None and hit[0] == versions:
        _read_cache.move_to_end(key)
        read_cache_stats["hits"] += 1
        return hit[1]
    read_cache_stats["misses"] += 1
    # Версии сняты ДО чтения: если запись прошла, пока шёл запрос, версии
    # уже другие, и запомненный ответ при следующем чтении не подойдёт.
    value = await run()
    if kind != "all" or len(value) <= READ_CACHE_MAX_ROWS:
        _read_cache[key] = (versions, value)
        if len(_read_cache) > READ_CACHE_MAX_ENTRIES:
            _read_cache.popitem(last=False)
    return value


class DatabaseNotReady(RuntimeError):
    """Обращение к базе до её открытия — почти всегда ошибка порядка запуска."""


async def connect(path: str) -> aiosqlite.Connection:
    """Открывает соединение (идемпотентно). `:memory:` — без читателей."""
    global _connection, _path
    if _connection is not None:
        return _connection

    _path = path
    _connection = await aiosqlite.connect(path)
    for pragma in (
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA foreign_keys=ON",
        "PRAGMA temp_store=MEMORY",
        f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
    ):
        await _connection.execute(pragma)
    await _connection.commit()
    if path != ":memory:":
        for _ in range(READERS):
            reader = await aiosqlite.connect(path)
            for pragma in (
                "PRAGMA query_only=ON",
                "PRAGMA temp_store=MEMORY",
                f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
            ):
                await reader.execute(pragma)
            _readers.append(reader)
            _reader_started.append([])
    logger.info("База данных открыта: %s (WAL, читателей: %d)", path, len(_readers))
    return _connection


async def close() -> None:
    global _connection
    for reader in _readers:
        await reader.close()
    _readers.clear()
    _reader_started.clear()
    if _connection is not None:
        await _connection.close()
        _connection = None
        clear_read_cache()
        logger.info("Соединение с базой закрыто.")


def _require() -> aiosqlite.Connection:
    if _connection is None:
        raise DatabaseNotReady(
            "База ещё не открыта. connect() вызывается один раз при старте бота, до загрузки cogs."
        )
    return _connection


async def _retrying(coro_factory, sql: str = ""):
    """Повтор при кратковременной блокировке базы (например, во время VACUUM)."""
    last_error: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            started = time.monotonic()
            result = await coro_factory()
            elapsed = time.monotonic() - started
            # Соединение одно: долгий запрос задерживает все остальные, и
            # панель у всех висит. Такие запросы видно в консоли.
            if elapsed >= SLOW_QUERY_SECONDS:
                logger.warning("Медленный запрос к базе: %.1f с — %s", elapsed, " ".join(sql.split())[:200])
            return result
        except aiosqlite.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last_error = exc
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------


async def _read(sql: str, params: Sequence[Any], one: bool):
    """SELECT на самом свободном читателе (нет читателей — на основном)."""
    connection = _require()
    started = time.monotonic()
    queue = None
    if _readers:
        # Свободный, а если свободных нет — тот, где очередь короче и запросы
        # начались позже: долгий запрос (отчёт) там, где первый идёт давно.
        index = min(
            range(len(_readers)),
            key=lambda i: (len(_reader_started[i]), -_reader_started[i][0] if _reader_started[i] else 0.0),
        )
        connection = _readers[index]
        queue = _reader_started[index]
        queue.append(started)
    try:
        cursor = await connection.execute(sql, params)
        try:
            return await cursor.fetchone() if one else tuple(await cursor.fetchall())
        finally:
            await cursor.close()
    finally:
        if queue is not None:
            queue.remove(started)


async def fetch_one(sql: str, params: Sequence[Any] = ()) -> tuple | None:
    async def run():
        return await _read(sql, params, one=True)

    return await _cached_read("one", sql, params, lambda: _retrying(run, sql))


async def fetch_all(sql: str, params: Sequence[Any] = ()) -> list[tuple]:
    async def run():
        return await _read(sql, params, one=False)

    # Список — копия: вызывающий может его сортировать и дописывать.
    return list(await _cached_read("all", sql, params, lambda: _retrying(run, sql)))


async def fetch_value(sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
    row = await fetch_one(sql, params)
    if row is None or row[0] is None:
        return default
    return row[0]


# ---------------------------------------------------------------------------
# Запись
# ---------------------------------------------------------------------------


async def execute(sql: str, params: Sequence[Any] = ()) -> tuple[int, int]:
    """Возвращает (lastrowid, rowcount) — этого хватает всем вызывающим."""

    async def run():
        connection = _require()
        cursor = await connection.execute(sql, params)
        try:
            await connection.commit()
            return cursor.lastrowid, cursor.rowcount
        finally:
            await cursor.close()

    async with _write_lock:
        try:
            return await _retrying(run, sql)
        finally:
            _bump(sql)


async def execute_many(statements: Iterable[tuple[str, Sequence[Any]]]) -> None:
    """Несколько запросов одной транзакцией: либо всё, либо ничего."""

    async def run():
        connection = _require()
        try:
            for sql, params in statements:
                await connection.execute(sql, params)
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise

    statements = list(statements)
    async with _write_lock:
        try:
            await _retrying(run, "транзакция из нескольких запросов")
        finally:
            for sql, _ in statements:
                _bump(sql)


async def execute_quiet(sql: str, params: Sequence[Any] = ()) -> bool:
    """Для миграций: выполняет и возвращает False, если запрос не подошёл
    (например, колонка уже добавлена). Ошибку не поднимает."""
    try:
        await execute(sql, params)
        return True
    except Exception:
        return False


async def table_exists(name: str) -> bool:
    row = await fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return row is not None


async def backup(path: str) -> None:
    """Копия базы в файл `path` (SQLite backup API) — под блокировкой записи,
    чтобы копия была целой."""
    async with _write_lock:
        connection = _require()
        # Открытая транзакция (например, после неудавшегося запроса) не даёт
        # снять копию — backup ждал бы её вечно.
        await connection.commit()
        target = await aiosqlite.connect(path)
        try:
            await connection.backup(target)
        finally:
            await target.close()


async def vacuum() -> None:
    """Сжимает файл базы. VACUUM нельзя выполнять внутри транзакции, поэтому
    делается отдельно и под блокировкой записи."""
    async with _write_lock:
        connection = _require()
        await connection.commit()
        await connection.execute("VACUUM")
        await connection.commit()
    clear_read_cache()


async def apply_schema(
    tables: Iterable[str], indexes: Iterable[str] = (), migrations: Iterable[str] = (),
) -> None:
    """Досоздаёт схему: `CREATE TABLE IF NOT EXISTS …`, затем `ALTER TABLE …
    ADD COLUMN …` (повтор безвреден — уже добавленная колонка пропускается),
    затем индексы. Ничего не удаляет."""
    for ddl in tables:
        await execute(ddl)
    for statement in migrations:
        await execute_quiet(statement)
    for statement in indexes:
        await execute_quiet(statement)
    await execute_quiet("PRAGMA optimize=0x10002")


async def backup_rotating(directory: str, keep: int = 3, prefix: str = "bot") -> str:
    """Копия базы в `directory/<prefix>-ГГГГММДД-ЧЧММСС.db`, старше `keep` копий удаляются."""
    import os
    import time as _time

    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{prefix}-{_time.strftime('%Y%m%d-%H%M%S')}.db")
    await backup(path)
    copies = sorted(
        name for name in os.listdir(directory) if name.startswith(f"{prefix}-") and name.endswith(".db")
    )
    for old in copies[:-keep] if keep > 0 else copies:
        try:
            os.remove(os.path.join(directory, old))
        except OSError:
            pass
    return path
