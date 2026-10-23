"""
Перезапуск процесса бота: команда `/restart` и сторож зависаний
(botkit/watchdog.py).

Перезапуск — это замена процесса самим собой (`os.execv`): тот же PID, та же
консоль хостинга, хостингу не нужно ничего делать. Перед заменой бот мягко
закрывается (Discord, веб-сервер, база), дочерние процессы (`child_pids`)
гасятся обязательно.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections.abc import Awaitable, Callable

logger = logging.getLogger("botkit")

# Сколько ждать мягкого закрытия, прежде чем перезапустить принудительно.
GRACEFUL_TIMEOUT = 30.0

_requested = False
_stoppers: list[Callable[[], Awaitable[None] | None]] = []
# PID дочерних процессов (например, туннеля), которые надо убить и при жёстком перезапуске,
# когда event loop завис и мягко их не остановить.
child_pids: set[int] = set()


def is_requested() -> bool:
    return _requested


def on_shutdown(stopper: Callable[[], Awaitable[None] | None]) -> None:
    """Что остановить перед перезапуском (веб-сервер, туннель)."""
    _stoppers.append(stopper)


async def request(bot, reason: str) -> None:
    """Мягкий перезапуск: всё закрывается, `botkit.run` после выхода из
    asyncio.run вызывает `exec_if_requested()`."""
    global _requested
    if _requested:
        return
    _requested = True
    logger.warning("Перезапуск бота: %s", reason)
    timer = threading.Timer(GRACEFUL_TIMEOUT, hard_restart)
    timer.daemon = True
    timer.start()
    for stopper in _stoppers:
        try:
            result = stopper()
            if result is not None:
                await result
        except Exception:
            logger.exception("Ошибка при остановке перед перезапуском")
    await bot.close()


def _kill_children() -> None:
    for pid in list(child_pids):
        try:
            os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
        except OSError:
            pass
    child_pids.clear()


def _exec() -> None:
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, *sys.argv])


def hard_restart() -> None:
    """Без участия event loop-а — из потока сторожа или по таймауту."""
    logger.critical("Принудительный перезапуск процесса.")
    _kill_children()
    _exec()


def exec_if_requested() -> None:
    if _requested:
        _kill_children()
        _exec()
