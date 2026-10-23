"""
Логирование в консоль бота.

Зачем отдельный модуль: discord.py не настраивает логирование сам, если бот
запущен через `bot.start()`, а ошибки внутри кнопок и модальных окон по
умолчанию не попадают никуда. `LoggedView` и `LoggedModal` это чинят.
"""

from __future__ import annotations

import logging
import os
import re
import sys

import discord

logger = logging.getLogger("botkit")


# -- Консоль: один формат для бота, discord.py и веб-сервера ------------------
#
#   29.09 16:42:05  ИНФО   airdrops   Аирдроп-чат: Winter запросил пинг
#   29.09 16:42:07  ОШИБКА discord    Shard ID None ...
#
# Раньше каждый писал по-своему: discord.py — своим цветным форматом, uvicorn —
# «INFO:     ...». Теперь всё идёт через корневой логгер и этот форматтер.

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"

# Палитра: синий, пергамент, коралл для бед.
_INK = "\033[38;5;230m"
_STAR = "\033[38;5;111m"

_LEVELS = {
    logging.DEBUG: ("·", "отладка", "\033[38;5;244m"),
    logging.INFO: ("✦", "инфо", "\033[38;5;111m"),
    logging.WARNING: ("▲", "внимание", "\033[38;5;221m"),
    logging.ERROR: ("✖", "ошибка", "\033[38;5;209m"),
    logging.CRITICAL: ("✖", "сбой", "\033[1;38;5;203m"),
}
_LEVEL_WIDTH = max(len(label) for _, label, _ in _LEVELS.values())

# Откуда запись — короткая метка вместо полного имени логгера. Для логгеров
# бота (`_own`) метка — имя файла, из которого пишут (airdrops, tunnel…).
_SOURCES = (
    ("uvicorn", "веб", "\033[38;5;80m"),
    ("fastapi", "веб", "\033[38;5;80m"),
    ("discord", "discord", "\033[38;5;111m"),
    ("httpx", "http", "\033[38;5;245m"),
    ("httpcore", "http", "\033[38;5;245m"),
)
# Разделы бота — каждый своим оттенком синего/фиолетового, цвет по имени файла.
_BOT_COLOURS = ("\033[38;5;75m", "\033[38;5;147m", "\033[38;5;117m", "\033[38;5;183m", "\033[38;5;153m", "\033[38;5;105m")
_SOURCE_WIDTH = 12
_SEP = " │ "

_BANNER_COLOURS = ("\033[38;5;61m", "\033[38;5;69m", "\033[38;5;75m", "\033[38;5;111m", "\033[38;5;147m")


def _centered(rows: list[str]) -> list[str]:
    """Рисунок — по центру строки лога: LOG_WIDTH в .env, иначе ширина
    терминала, у консоли хостинга (не терминал) — 100 колонок."""
    import shutil

    width = int(os.getenv("LOG_WIDTH", "0") or 0) or (shutil.get_terminal_size((100, 24)).columns if sys.stderr.isatty() else 100)
    indent = min((len(r) - len(r.lstrip()) for r in rows if r.strip()), default=0)
    rows = [r[indent:].rstrip() for r in rows]
    pad = " " * max(0, (width - max((len(r) for r in rows), default=0)) // 2)
    return [pad + r if r else r for r in rows]

# Числа-ID Discord в строке лога заменяются на ник/канал/роль, если бот их знает.
_SNOWFLAKE = re.compile(r"(?<![\w#@/.])\d{17,20}(?![\w/.])")
_name_resolver = None
# Логгеры «своего» кода: у них метка — имя файла, а не имя логгера.
_own = {"botkit"}


def set_name_resolver(resolver) -> None:
    """resolver(id) -> имя или None; ставит бот, когда кэш Discord доступен."""
    global _name_resolver
    _name_resolver = resolver

# Первое слово капсом («КОМАНДА /…», «ДЕЙСТВИЕ …») — выделяем как метку.
_TAG = re.compile(r"^([A-ZА-ЯЁ]{4,})(?=\s)")

# Служебные строки discord.py и uvicorn — по-русски, как всё остальное.
# None — строку не показываем вовсе (шум без смысла).
_REWRITES: tuple[tuple[re.Pattern, str | None], ...] = (
    (re.compile(r"^logging in using static token$"), "Вход в Discord по токену"),
    (re.compile(r"^Shard ID (\S+) has connected to Gateway \(Session ID: (\w+)\)\.?$"),
     r"Подключено к Discord (сессия \2)"),
    (re.compile(r"^Shard ID (\S+) has successfully RESUMED session (\w+)\.?$"), r"Сессия Discord \2 восстановлена"),
    (re.compile(r"^Shard ID (\S+) session has been invalidated\.?$"), "Сессия Discord сброшена, переподключаюсь"),
    (re.compile(r"^Started server process \[(\d+)\]$"), r"Веб-сервер запущен (pid \1)"),
    (re.compile(r"^Waiting for application (startup|shutdown)\.$"), None),
    (re.compile(r"^Application startup complete\.$"), "Панель готова к работе"),
    (re.compile(r"^Application shutdown complete\.$"), "Панель остановлена"),
    (re.compile(r"^Finished server process \[(\d+)\]$"), r"Веб-сервер остановлен (pid \1)"),
    (re.compile(r"^Shutting down$"), "Останавливаю веб-сервер"),
    (re.compile(r"^Uvicorn running on (\S+).*$"), r"Панель слушает \1"),
)


_RECONNECT = re.compile(r"^Attempting a reconnect in ([\d.]+)s$")


def _network_cause(exc: BaseException | None) -> str:
    """Коротко, почему оборвалась связь с Discord — вместо трейсбека на экран."""
    name = type(exc).__name__ if exc else ""
    if name == "ClientConnectorDNSError" or "gaierror" in name:
        return "не нашёлся адрес Discord (сбой DNS у хостинга)"
    if name in ("ClientConnectorError", "ClientConnectorSSLError", "ClientOSError", "ServerDisconnectedError"):
        return "нет соединения с Discord"
    if isinstance(exc, (TimeoutError,)) or name == "TimeoutError":
        return "Discord не ответил вовремя"
    if name == "ConnectionClosed":
        return f"Discord закрыл соединение (код {getattr(exc, 'code', '?')})"
    if name in ("GatewayNotFound", "DiscordServerError"):
        return "у Discord проблемы на их стороне"
    return f"{name}: {exc}" if exc else "связь оборвалась"


class _RewriteFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.split(".")[0] in _own:
            return True
        try:
            message = record.getMessage()
        except Exception:
            return True
        # Обрыв связи, после которого discord.py сам переподключается, — не
        # авария: одна строка-предупреждение с причиной вместо трейсбека.
        match = _RECONNECT.match(message)
        if match and record.name.startswith("discord"):
            exc = record.exc_info[1] if record.exc_info else None
            record.msg = f"Связь с Discord прервалась: {_network_cause(exc)}. Переподключение через {float(match.group(1)):.0f} с"
            record.args, record.exc_info, record.exc_text = (), None, None
            record.levelno, record.levelname = logging.WARNING, "WARNING"
            return True
        for pattern, replacement in _REWRITES:
            if pattern.match(message):
                if replacement is None:
                    return False
                record.msg, record.args = pattern.sub(replacement, message), ()
                break
        return True


class ConsoleFormatter(logging.Formatter):
    def __init__(self, colour: bool):
        super().__init__()
        self.colour = colour

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if self.colour else text

    def _source(self, record: logging.LogRecord) -> tuple[str, str]:
        if record.name.split(".")[0] in _own:
            return record.module, _BOT_COLOURS[sum(map(ord, record.module)) % len(_BOT_COLOURS)]
        for prefix, label, code in _SOURCES:
            if record.name.startswith(prefix):
                return label, code
        return record.name.split(".")[0], _DIM

    def _names(self, message: str, after: str) -> str:
        if _name_resolver is None:
            return message

        def swap(match: re.Match) -> str:
            try:
                name = _name_resolver(int(match.group(0)))
            except Exception:
                name = None
            if not name:
                return match.group(0)
            return f"{_BOLD}{_INK}{name}{_RESET}{after}" if self.colour else name

        return _SNOWFLAKE.sub(swap, message)

    def format(self, record: logging.LogRecord) -> str:
        mark, label, level_code = _LEVELS.get(record.levelno, ("·", record.levelname, ""))
        source, source_code = self._source(record)
        stamp = self.formatTime(record, "%d.%m %H:%M:%S")
        message = record.getMessage()
        after = level_code if self.colour and record.levelno >= logging.ERROR else ""
        if self.colour:
            message = _TAG.sub(lambda m: f"{_BOLD}{_STAR}{m.group(1)}{_RESET}{after}", message, count=1)
        message = self._names(message, after)
        if after:
            message = self._paint(message, level_code)

        sep = self._paint(_SEP, _DIM)
        line = sep.join((
            self._paint(stamp, _DIM),
            self._paint(f"{mark} {label.ljust(_LEVEL_WIDTH)}", level_code),
            self._paint(source[:_SOURCE_WIDTH].ljust(_SOURCE_WIDTH), source_code),
            message,
        ))

        details = ""
        if record.exc_info:
            details = self.formatException(record.exc_info)
        elif record.exc_text:
            details = record.exc_text
        if record.stack_info:
            details = f"{details}\n{self.formatStack(record.stack_info)}".strip("\n")
        if details:
            indent = " " * (len(stamp) + _LEVEL_WIDTH + 2 + _SOURCE_WIDTH + len(_SEP) * 2 + 1) + "┆ "
            body = "\n".join(indent + row for row in details.splitlines())
            line += "\n" + self._paint(body, _DIM)
        return line


def _colour_enabled(stream) -> bool:
    """LOG_COLOR=1/0 в .env — принудительно; иначе — если консоль умеет цвета.
    Панель хостинга часто не «терминал», но цвета показывает — тогда LOG_COLOR=1."""
    forced = os.getenv("LOG_COLOR", "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        wanted = True
    elif forced in ("0", "false", "no", "off"):
        return False
    else:
        wanted = discord.utils.stream_supports_colour(stream)
    if wanted and sys.platform == "win32":
        # Классическая консоль Windows понимает ANSI только после включения VT-режима.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
    return wanted


def banner_lines(text: str) -> list[str]:
    """Рисунок из файла: пустые строки по краям убираются, отступ слева — как в файле."""
    rows = text.rstrip().splitlines()
    while rows and not rows[0].strip():
        rows.pop(0)
    return rows


def setup_console(level: int = logging.INFO, *, app: str | None = None, banner: str | None = None) -> None:
    """Вместо `discord.utils.setup_logging`: один обработчик на корневом
    логгере — туда же пишут discord.py и uvicorn (он запускается с
    `log_config=None` и свои обработчики не ставит).

    `app` — имя логгера бота (`logging.getLogger(app)`), его записи
    подписываются именем файла. `banner` — рисунок, печатается один раз."""
    if app:
        _own.add(app.split(".")[0])
    stream = sys.stderr
    # Консоль хостинга/Windows без UTF-8 превращала кириллицу в И…
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    colour = _colour_enabled(stream)
    if banner:
        rows = _centered(banner_lines(banner))
        if colour:
            step = max(1, -(-len(rows) // len(_BANNER_COLOURS)))
            rows = [f"{_BANNER_COLOURS[min(i // step, len(_BANNER_COLOURS) - 1)]}{row}{_RESET}" for i, row in enumerate(rows)]
        stream.write("\n" + "\n".join(rows) + "\n\n")
        stream.flush()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(ConsoleFormatter(colour))
    handler.addFilter(_RewriteFilter())
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn мог успеть навесить свои обработчики — пусть пишет через корневой.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True


COMPONENT_ERROR_TEXT = "⚠️ Что-то пошло не так при обработке действия. Подробности — в логах бота."


def describe(interaction: discord.Interaction) -> str:
    command = interaction.command.qualified_name if interaction.command else "неизвестно"
    return f"/{command} | {interaction.user.id}"


async def log_command(interaction: discord.Interaction, extra: str = "") -> None:
    """Первая строка каждой команды — в консоль."""
    command_name = interaction.command.qualified_name if interaction.command else "неизвестно"
    where = f"#{interaction.channel}" if interaction.channel else "?"
    logger.info(
        "КОМАНДА /%s | %s | %s%s",
        command_name, interaction.user.id, where,
        f" | {extra}" if extra else "",
    )


async def log_action(
    guild: discord.Guild, actor_id: int | None, target_id: int, label: str, note: str = "",
) -> None:
    """Действие с конкретным игроком, выполненное из веб-панели
    или кнопкой/модалкой Discord-панели, а не слэш-командой напрямую —
    поэтому `log_command` тут не подходит, у него нет вызванной команды,
    только «неизвестно».

    Пишет в консоль."""
    logger.info(
        "ДЕЙСТВИЕ %s | цель %s | исполнитель %s%s",
        label, target_id, actor_id if actor_id else "авто",
        f" | {note}" if note else "",
    )


async def _report_component_error(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


class LoggedView(discord.ui.View):
    """View, которая не проглатывает ошибки: пишет traceback в консоль и
    честно сообщает человеку, что действие не выполнилось."""

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        logger.exception("Ошибка в компоненте %r: %s", item, error)
        await _report_component_error(interaction, COMPONENT_ERROR_TEXT)


class LoggedModal(discord.ui.Modal):
    """То же самое для модальных окон."""

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Ошибка в модальном окне %r: %s", self, error)
        await _report_component_error(interaction, COMPONENT_ERROR_TEXT)
