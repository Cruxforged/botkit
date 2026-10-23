"""
Основа бота: загрузка модулей по одному, только свой сервер, синхронизация
команд один раз за запуск, ники вместо ID в консоли, понятные ответы на
ошибки команд. `run()` — запуск со сторожем зависаний и перезапуском.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from botkit import db, restart, watchdog
from botkit.logs import logger, set_name_resolver, setup_console

GENERIC_ERROR = "Что-то пошло не так при выполнении команды. Попробуй ещё раз чуть позже."


class BaseBot(commands.Bot):
    """`extensions` — модули (cogs) по порядку загрузки. `home_guild_id` —
    свой сервер: команды синхронизируются на нём мгновенно, с остальных бот
    уходит. `db_path` — открыть базу до загрузки модулей.
    `prepare()` — своя подготовка после базы (схема, кэши)."""

    def __init__(
        self, *, extensions: Iterable[str] = (), home_guild_id: int | None = None,
        db_path: str | None = None, intents: discord.Intents | None = None,
        strip_name: Callable[[str], str] | None = None, **options,
    ):
        options.setdefault("command_prefix", commands.when_mentioned)
        options.setdefault("help_command", None)
        super().__init__(intents=intents or discord.Intents.default(), **options)
        self.extension_names = tuple(extensions)
        self.home_guild_id = home_guild_id
        self.db_path = db_path
        self.strip_name = strip_name
        self.started_at = time.time()
        self._commands_synced = False
        self.tree.on_error = self.on_app_command_error

    async def prepare(self) -> None:
        pass

    async def setup_hook(self) -> None:
        if self.db_path:
            await db.connect(self.db_path)
        await self.prepare()
        await self._load_extensions()

    async def _load_extensions(self) -> None:
        """Сломанный модуль не роняет остальные."""
        loaded, failed = 0, []
        for name in self.extension_names:
            try:
                await self.load_extension(name)
                loaded += 1
            except Exception:
                failed.append(name)
                logger.exception("Не удалось загрузить модуль %s — остальные всё равно загружаются", name)
        if self.extension_names:
            logger.info("Модулей загружено: %d из %d", loaded, len(self.extension_names))
        if failed:
            logger.error("Не загрузились: %s", ", ".join(failed))

    def log_name(self, snowflake: int) -> str | None:
        """Имя для консоли вместо ID: ник, #канал, @роль, сервер."""
        for guild in list(self.guilds):
            if guild.id == snowflake:
                return guild.name
            member = guild.get_member(snowflake)
            if member:
                return self.strip_name(member.display_name) if self.strip_name else member.display_name
            channel = guild.get_channel_or_thread(snowflake)
            if channel:
                return f"#{channel.name}"
            role = guild.get_role(snowflake)
            if role:
                return f"@{role.name}"
        user = self.get_user(snowflake)
        return user.name if user else None

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._leave_if_not_home(guild)

    async def _leave_if_not_home(self, guild: discord.Guild) -> bool:
        if not self.home_guild_id or guild.id == self.home_guild_id:
            return False
        logger.warning("Бот добавлен на чужой сервер %s — выхожу.", guild.name)
        try:
            await guild.leave()
        except discord.HTTPException:
            logger.exception("Не удалось покинуть чужой сервер %s", guild.id)
        return True

    async def on_ready(self) -> None:
        set_name_resolver(self.log_name)
        logger.info("Запущен как %s", self.user)
        # on_ready приходит при каждом переподключении — синхронизация один раз.
        if self._commands_synced:
            return
        self._commands_synced = True
        for guild in list(self.guilds):
            await self._leave_if_not_home(guild)
        try:
            if self.home_guild_id:
                guild = discord.Object(id=self.home_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info("Синхронизировано команд на сервере %s: %d", self.home_guild_id, len(synced))
            else:
                synced = await self.tree.sync()
                logger.info("Синхронизировано команд глобально: %d (до часа на раскатку)", len(synced))
        except discord.HTTPException:
            logger.exception("Не удалось синхронизировать команды — бот продолжает работать")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError,
    ) -> None:
        async def reply(text: str) -> None:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(text, ephemeral=True)
                else:
                    await interaction.response.send_message(text, ephemeral=True)
            except discord.HTTPException:
                pass

        command_name = interaction.command.qualified_name if interaction.command else "?"
        if isinstance(error, app_commands.CheckFailure):
            await reply(str(error) or "У тебя нет прав для использования этой команды.")
        elif isinstance(error, app_commands.CommandOnCooldown):
            await reply(f"Слишком часто. Попробуй через {error.retry_after:.0f} с.")
        elif isinstance(error, app_commands.TransformerError):
            logger.info("Команда /%s: не распознано введённое значение (%s)", command_name, error)
            await reply(f"Не нашёл «{error.value}» — открой поле заново и **выбери** вариант из подсказки Discord.")
        else:
            logger.exception("Необработанная ошибка команды /%s", command_name, exc_info=error)
            await reply(GENERIC_ERROR)

    async def close(self) -> None:
        await db.close()
        await super().close()


def _log_loop_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    exception = context.get("exception")
    logger.error(
        "Необработанная ошибка в фоне: %s", context.get("message", "без описания"),
        exc_info=(type(exception), exception, exception.__traceback__) if exception else None,
    )


def run(
    bot: BaseBot, token: str | None, *, app: str | None = None, banner: str | Path | None = None,
    log_level: str | int = "INFO", problems: Iterable[str] = (),
    also: Iterable[Callable[[], Awaitable[None]]] = (),
) -> None:
    """Запуск бота: консоль (баннер — текст или путь к файлу), сторож
    зависаний, `/restart` через `restart.request`. `also` — корутины, которые
    идут рядом с ботом (веб-сервер и т.п.). `problems` — что не так в настройках."""
    if isinstance(banner, Path) or (isinstance(banner, str) and banner.endswith(".txt")):
        path = Path(banner)
        banner = path.read_text(encoding="utf-8") if path.is_file() else None
    level = logging.getLevelName(log_level.upper()) if isinstance(log_level, str) else log_level
    setup_console(level if isinstance(level, int) else logging.INFO, app=app, banner=banner)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for problem in problems:
        logger.warning("Настройки: %s", problem)
    if not token:
        logger.critical("Токен Discord не задан — проверь .env (DISCORD_TOKEN=...)")
        return

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_log_loop_exception)
        watchdog.start(loop)
        async with bot:
            await asyncio.gather(bot.start(token), *(factory() for factory in also))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C.")
    except Exception:
        logger.exception("Бот остановился из-за ошибки")
        raise
    finally:
        restart.exec_if_requested()
