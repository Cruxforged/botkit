"""
Сторож процесса: ловит «тихие» поломки, после которых бот молчит, а в
консоли хостинга пусто.

- Зависание event loop-а (синхронный код в обработчике, долгий вызов без
  await). Пока loop стоит, не отвечают ни Discord, ни логи —
  отдельный поток раз в несколько секунд проверяет, что loop жив. Через
  FREEZE_WARN_SECONDS пишет в консоль, где именно он застрял (стек главного
  потока), через FREEZE_RESTART_SECONDS перезапускает процесс.
- Падение самого интерпретатора (segfault в C-расширении) — `faulthandler`
  печатает трейсбек в stderr вместо полной тишины.
- Раз в час — строка с памятью процесса: хостинг убивает процесс по лимиту
  RAM без единой строки в логе, а так рост видно заранее.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import sys
import threading
import time
import traceback

from botkit import restart

logger = logging.getLogger("botkit")

CHECK_INTERVAL = 5.0
FREEZE_WARN_SECONDS = 15.0
FREEZE_RESTART_SECONDS = 180.0
MEMORY_LOG_INTERVAL = 3600.0


def _memory_mb() -> float | None:
    """Текущая занятая память (RSS) — только Linux, на хостинге этого хватает."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        return None
    return None


def _main_thread_stack() -> str:
    frame = sys._current_frames().get(threading.main_thread().ident)
    if frame is None:
        return "(стек недоступен)"
    return "".join(traceback.format_stack(frame)[-12:])


class _Watchdog:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.last_beat = time.monotonic()
        self.next_memory_log = time.monotonic() + 60

    def _beat(self) -> None:
        self.last_beat = time.monotonic()

    def run(self) -> None:
        warned = False
        while not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self._beat)
            except RuntimeError:
                return  # loop закрыт — процесс останавливается
            time.sleep(CHECK_INTERVAL)
            stuck = time.monotonic() - self.last_beat
            if stuck >= FREEZE_RESTART_SECONDS:
                logger.critical("Бот завис на %.0f с — перезапускаю процесс.", stuck)
                restart.hard_restart()
                return
            if stuck >= FREEZE_WARN_SECONDS and not warned:
                warned = True
                logger.error("Бот не отвечает уже %.0f с. Где завис:\n%s", stuck, _main_thread_stack())
            elif stuck < FREEZE_WARN_SECONDS and warned:
                warned = False
                logger.warning("Бот снова отвечает.")
            if time.monotonic() >= self.next_memory_log:
                self.next_memory_log = time.monotonic() + MEMORY_LOG_INTERVAL
                memory = _memory_mb()
                if memory is not None:
                    logger.info("Память процесса: %.0f МБ", memory)


def start(loop: asyncio.AbstractEventLoop) -> None:
    faulthandler.enable()
    watchdog = _Watchdog(loop)
    threading.Thread(target=watchdog.run, name="watchdog", daemon=True).start()
