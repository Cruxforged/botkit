"""
Фоновые задачи cog-ов (`discord.ext.tasks.loop`) без «тихой смерти».

По умолчанию любая неожиданная ошибка внутри задачи останавливает её
навсегда: одна строка в логе — и задача больше не идёт. `start_guarded` запускает задачу и после падения
поднимает её снова через RETRY_SECONDS.
"""

from __future__ import annotations

import asyncio
import logging

from discord.ext import tasks

logger = logging.getLogger("botkit")

RETRY_SECONDS = 30


def start_guarded(loop: tasks.Loop, name: str) -> None:
    def _restart() -> None:
        if loop.is_running():
            return
        old = loop.get_task()
        if old is not None and old.done() and not old.cancelled():
            old.exception()  # уже в логе — иначе asyncio напишет трейсбек второй раз
        loop.start()

    async def on_error(*args) -> None:
        logger.error("Фоновая задача «%s» упала — перезапуск через %d с", name, RETRY_SECONDS, exc_info=args[-1])
        asyncio.get_running_loop().call_later(RETRY_SECONDS, _restart)

    loop.error(on_error)
    loop.start()
