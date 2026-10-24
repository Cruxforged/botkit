# MEMORY

## Overview
- botkit — общее ядро Discord-ботов на discord.py (публичный репозиторий, без секретов).
- Установка: `https://github.com/Cruxforged/botkit/archive/refs/tags/v<версия>.zip` в requirements.txt (git на хостинге не нужен).
- Проверка: импорт всех модулей и пример `example/main.py` (`PYTHONPATH=.`); тестов пока нет.

## Structure
- `botkit/logs.py` — консоль: баннер (по центру строки лога), палитра, ники вместо ID (`set_name_resolver`), LoggedView/LoggedModal.
- `botkit/bot.py` — `BaseBot` (модули по одному, свой сервер, синхронизация раз, ошибки команд) и `run()` (сторож, перезапуск).
- `botkit/db.py` — SQLite: писатель + читатели (WAL), кэш чтения по таблицам, `apply_schema`, `backup_rotating`.
- `botkit/restart.py`, `watchdog.py`, `loops.py` — перезапуск процесса, сторож зависаний, фоновые задачи с подъёмом.
- `botkit/panel.py`, `ui.py`, `pagination.py`, `notify.py`, `ansi.py`, `timeutil.py`, `config.py` — Discord-утилиты, время, .env.
- `example/` — минимальный бот на botkit.

## Screens / states
- Интерфейса нет.

## Shared styles / components
- Палитра консоли — `botkit/logs.py` (`_LEVELS`, `_BOT_COLOURS`, `_BANNER_COLOURS`).

## Do not edit without asking
- Публичный API (`BaseBot`, `run`, `db.*`, `logs.setup_console`) — боты ставят его по тегу.

## Decisions
- Библиотека, а не шаблон-репозиторий (решение владельца 2026-10-24); баннер — вручную из `banner.txt`.
- Версии — теги `vX.Y.Z`; бот закрепляет тег.

## Done
- 2026-10-24 v0.2.0: баннер по центру строки лога.
- 2026-10-24 v0.1.0: первое ядро из Arion.

## Next / known issues
- Arion берёт logs, loops, restart, watchdog, ansi; TournamentBot — logs. Оба ставят нужный тег сами (`BOTKIT_VERSION` в `core/__init__.py`) — новый тег = поднять версию там и в requirements.txt.
- TournamentBot: свой restart (другой порядок остановки) — перевести, если botkit.restart получит `run_shutdown`.
