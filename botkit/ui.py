"""
Общий UI-инструментарий: безопасные ответы на взаимодействия, пагинация,
защита от отклонённых Discord'ом эмодзи.

Собственно построение панелей (текст, кнопки, разделители) живёт в
`botkit/panel.py` — `Panel`/`PanelView`/`ConfirmView`/`make_button` реэкспортированы
отсюда же, чтобы не переписывать импорты по всему боту.

**Про 3 секунды.** У Discord жёсткий лимит: первый ответ на взаимодействие
должен уйти в течение трёх секунд, иначе токен протухает и любой последующий
ответ падает с «Unknown interaction». Поэтому всё, что делает больше одного
запроса к базе, обязано сначала вызвать `defer(...)`, а потом отвечать через
`respond(...)`. `respond` сам разбирается, что использовать: первый ответ,
followup или правку исходного сообщения.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import discord

from collections.abc import Callable

from botkit.logs import logger
from botkit.panel import (  # noqa: F401  (реэкспорт — часть публичного интерфейса)
    BUTTON_STYLE,
    PANEL_COLOR,
    ConfirmView,
    Panel,
    PanelView,
    error,
    info,
    make_button,
    ok,
    warn,
)

# Чем заменить кастомное эмодзи, если Discord его не принял (бота нет на
# сервере-владельце). None — убрать эмодзи. Бот ставит свою таблицу замен.
emoji_fallback: Callable[[str], str | None] = lambda emoji: None


def set_emoji_fallback(fallback: Callable[[str], str | None]) -> None:
    global emoji_fallback
    emoji_fallback = fallback


NO_PING = discord.AllowedMentions.none()
PING_USERS = discord.AllowedMentions(users=True)
PING_ROLES = discord.AllowedMentions(roles=True)

EMBED_DESCRIPTION_LIMIT = 4000  # запас к настоящему лимиту в 4096
EMBED_FIELD_LIMIT = 1024  # разумный предел на один текстовый блок панели


def embed(text: str = "", *, title: str | None = None, color: discord.Color | None = None) -> Panel:
    """Собирает `Panel` — то же, чем раньше был `discord.Embed`. Не путать с
    `info()`: этот вариант отдаёт «голый» контейнер для дальнейшей сборки
    (например, чтобы потом добавить свои кнопки), `info()` — готовый к
    отправке `PanelView`."""
    return Panel(text, title=title, color=color)


def truncate(text: str, limit: int = EMBED_DESCRIPTION_LIMIT, note: str = "") -> str:
    """Обрезает по границе строки, чтобы влезло в лимит Discord."""
    if len(text) <= limit:
        return text
    if not note:
        note = "\n\n-# ⚠️ Показано не всё — слишком длинно для одного сообщения."
    budget = max(0, limit - len(note))
    cut = text[:budget]
    last_newline = cut.rfind("\n")
    if last_newline > 0:
        cut = cut[:last_newline]
    return cut + note


def chunk_text(text: str, limit: int = EMBED_DESCRIPTION_LIMIT) -> list[str]:
    """Режет длинный текст на части по границам строк, не разрывая слова."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining[:limit]
        split_at = cut.rfind("\n")
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Номер страницы: хранится в тексте самой панели, а не в кнопке
# ---------------------------------------------------------------------------

_PAGE_RE = re.compile(r"Страница (\d+) из (\d+)")


def page_footer(page: int, total_pages: int, extra: str = "") -> str:
    """Единый формат подписи страницы. Из неё же потом читается текущая страница."""
    base = f"Страница {page + 1} из {total_pages}"
    return f"{base} • {extra}" if extra else base


def read_page(text: str | None) -> int:
    """Номер страницы (с нуля) из текста подписи."""
    match = _PAGE_RE.search(text or "")
    return int(match.group(1)) - 1 if match else 0


def walk_components(components) -> list:
    """Плоский список компонентов сообщения, включая вложенные (Container →
    Section/ActionRow → TextDisplay/Button, и так далее)."""
    flat = []
    for component in components:
        flat.append(component)
        children = getattr(component, "children", None)
        if children:
            flat.extend(walk_components(children))
        accessory = getattr(component, "accessory", None)
        if accessory is not None:
            flat.append(accessory)
    return flat


def read_page_from_message(message: discord.Message | None) -> int:
    """Страница из самого сообщения (ищет строку вида «Страница N из M» среди
    текстовых блоков панели). Так кнопки листания не хранят состояние внутри
    себя и продолжают работать после перезапуска бота."""
    if message is None:
        return 0
    for component in walk_components(message.components):
        content = getattr(component, "content", None)
        if content and _PAGE_RE.search(content):
            return read_page(content)
    return 0


def paginate(items: Sequence[Any], page: int, per_page: int) -> tuple[list[Any], int, int]:
    """(срез, фактическая_страница, всего_страниц) с зажимом номера в границы."""
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return list(items[start:start + per_page]), page, total_pages


# ---------------------------------------------------------------------------
# Безопасные ответы на взаимодействия
# ---------------------------------------------------------------------------


async def defer(interaction: discord.Interaction, *, ephemeral: bool = False, thinking: bool = True) -> None:
    """Подтверждает взаимодействие, снимая лимит в три секунды.

    `thinking=True` — для команд и кнопок, которые пришлют новый ответ.
    `thinking=False` — для кнопок, которые правят уже существующее сообщение
    (иначе Discord покажет лишнее «бот думает…»).
    """
    if interaction.response.is_done():
        return
    try:
        if thinking:
            await interaction.response.defer(ephemeral=ephemeral)
        else:
            await interaction.response.defer()
    except discord.HTTPException:
        logger.debug("defer не прошёл (взаимодействие уже обработано)", exc_info=True)


# ---------------------------------------------------------------------------
# Защита от эмодзи, которые Discord не принимает на кнопках
# ---------------------------------------------------------------------------

INVALID_FORM_BODY = 50035

# ID кастомных эмодзи, которые Discord уже отверг. Держим в памяти процесса,
# чтобы не наступать на те же грабли в каждом следующем сообщении.
_rejected_emoji: set[int] = set()


def _looks_like_emoji_error(error: discord.HTTPException) -> bool:
    if getattr(error, "code", None) != INVALID_FORM_BODY:
        return False
    return "emoji" in str(error).lower()


def _replace_emoji(item: discord.ui.Item) -> bool:
    """Меняет кастомное эмодзи на юникодное. True, если что-то поменялось."""
    emoji = getattr(item, "emoji", None)
    if emoji is None or getattr(emoji, "id", None) is None:
        return False

    _rejected_emoji.add(emoji.id)
    replacement = emoji_fallback(str(emoji))
    item.emoji = replacement
    # У кнопки должно остаться хоть что-то видимое.
    if replacement is None and not getattr(item, "label", None):
        item.label = "•"
    return True


def sanitize_view(view: discord.ui.View | discord.ui.LayoutView | None) -> bool:
    """Убирает из view кастомные эмодзи, заменяя их юникодными (обходит и
    вложенные компоненты — Container/Section/ActionRow).

    Возвращает True, если что-то заменено — тогда есть смысл повторить отправку.
    """
    if view is None:
        return False

    changed = False
    for item in view.walk_children():
        changed |= _replace_emoji(item)
        for option in getattr(item, "options", ()) or ():
            changed |= _replace_emoji(option)
    return changed


def prune_known_bad(view: discord.ui.View | discord.ui.LayoutView | None) -> None:
    """Заранее убирает эмодзи, которые Discord уже отвергал в этом запуске."""
    if view is None or not _rejected_emoji:
        return
    for item in view.walk_children():
        emoji = getattr(item, "emoji", None)
        if emoji is not None and getattr(emoji, "id", None) in _rejected_emoji:
            _replace_emoji(item)


async def _with_emoji_guard(send, kwargs: dict):
    """Выполняет отправку. Если Discord ругается на эмодзи, заменяет их и
    пробует ещё раз — вместо того чтобы молча остаться без кнопок."""
    view = kwargs.get("view")
    prune_known_bad(view)
    try:
        return await send(**kwargs)
    except discord.HTTPException as error:
        if not (_looks_like_emoji_error(error) and sanitize_view(view)):
            raise
        logger.warning(
            "Discord не принял кастомные эмодзи на кнопках — заменил на юникодные и повторил. "
            "Скорее всего, бота нет на сервере-владельце этих эмодзи. Подробности: %s", error,
        )
        return await send(**kwargs)


def _for_send(kwargs: dict) -> dict:
    """Готовит аргументы для отправки нового сообщения.

    `send` не принимает `view=None`: discord.py ждёт либо объект View, либо
    отсутствие параметра, а на None падает с TypeError. При этом для правки
    уже существующего сообщения None осмыслен — он убирает кнопки. Поэтому
    чистим только путь отправки.
    """
    if "view" in kwargs and kwargs["view"] is None:
        return {key: value for key, value in kwargs.items() if key != "view"}
    return kwargs


async def safe_send(channel: discord.abc.Messageable, **kwargs):
    """channel.send с той же защитой (и без падения на view=None)."""
    return await _with_emoji_guard(channel.send, _for_send(kwargs))


def _for_edit(kwargs: dict) -> dict:
    """Готовит аргументы для правки существующего сообщения.

    Discord отклоняет запрос, если в теле есть `content`/`embed`/`embeds` —
    даже равные `None` — одновременно с флагом Components V2 (несмотря на то,
    что docstring discord.py советует явно обнулять их при переходе с
    классического embed'а на LayoutView). Поэтому при правке на LayoutView
    эти ключи не обнуляются, а просто убираются из запроса.
    """
    if isinstance(kwargs.get("view"), discord.ui.LayoutView):
        return {k: v for k, v in kwargs.items() if k not in ("content", "embed", "embeds")}
    return kwargs


async def safe_edit(message: discord.Message, **kwargs):
    """message.edit с той же защитой."""
    return await _with_emoji_guard(message.edit, _for_edit(kwargs))


async def resolve_channel(guild: discord.Guild, value: Any) -> discord.TextChannel | None:
    """Превращает выбор из `ChannelSelect` в полноценный канал.

    Discord присылает по такому выбору «облегчённый» объект `AppCommandChannel`:
    у него есть `id` и `mention`, но нет ни `send`, ни `history`. Обращение к
    ним падает с AttributeError. Поэтому канал всегда достаётся заново:
    сначала из кэша сервера, потом через API.
    """
    if value is None:
        return None
    if isinstance(value, discord.TextChannel):
        return value

    channel_id = getattr(value, "id", None)
    if channel_id is None:
        return None

    cached = guild.get_channel(channel_id)
    if cached is not None:
        return cached

    # Канала нет в кэше (бот только запустился, канал только создан) —
    # спрашиваем Discord напрямую.
    resolve = getattr(value, "resolve", None)
    if resolve is not None:
        resolved = resolve()
        if resolved is not None:
            return resolved
    try:
        fetch = getattr(value, "fetch", None)
        if fetch is not None:
            return await fetch()
        return await guild.fetch_channel(channel_id)
    except discord.HTTPException:
        logger.warning("Не удалось получить канал %s", channel_id, exc_info=True)
        return None


async def respond(interaction: discord.Interaction, *, ephemeral: bool = False, **kwargs: Any) -> None:
    """Отвечает независимо от того, был ли уже defer или ответ."""
    send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    try:
        await _with_emoji_guard(send, _for_send({**kwargs, "ephemeral": ephemeral}))
    except discord.HTTPException:
        logger.exception("Не удалось ответить на взаимодействие (%s)", interaction.id)


def _needs_recreate(message: discord.Message | None, view: Any) -> bool:
    """Правку с классического сообщения (текст и/или embed) на Components V2
    Discord отклоняет с «content field cannot be used with IS_COMPONENTS_V2»
    независимо от того, что передано в запросе — что явный `content=None`,
    что вообще без `content` (см. `_for_edit`; было перепробовано и то, и
    другое на реальном трафике). Единственный рабочий способ — не редактировать,
    а удалить старое сообщение и отправить новое, у которого этой истории нет."""
    if not isinstance(view, discord.ui.LayoutView) or message is None:
        return False
    return bool(message.content) or bool(message.embeds)


async def _delete_original(interaction: discord.Interaction) -> None:
    try:
        if interaction.response.is_done():
            await interaction.delete_original_response()
        elif interaction.message is not None:
            await interaction.message.delete()
    except discord.HTTPException:
        logger.debug("Не удалось удалить исходное сообщение перед пересозданием", exc_info=True)


async def edit_response(interaction: discord.Interaction, **kwargs: Any) -> None:
    """Правит сообщение, на котором нажали кнопку. Работает и после `defer`,
    и без него, и после отправки модального окна.

    Переход классического сообщения на Components V2 правкой не делается в
    принципе (см. `_needs_recreate`) — старое сообщение удаляется, новое
    отправляется отдельно, вместо того чтобы просто ловить ошибку и оставлять
    после себя нередактируемый дубль-«Выбери ранг:» на месте.

    `_for_edit` (срез content/embed/embeds под LayoutView) применяется ТОЛЬКО
    непосредственно перед настоящим `.edit()` — раньше резалось сразу для
    всей функции, из-за чего `content` терялся и в пути восстановления
    (`respond(...)` ниже) тоже, хотя там это уже обычная отправка нового
    сообщения, а не правка существующего, и Discord его не отклоняет.
    `payload` для восстановления строится из ИСХОДНЫХ kwargs, не урезанных."""
    payload = {k: v for k, v in kwargs.items() if k in {"content", "view"}}

    if _needs_recreate(interaction.message, kwargs.get("view")):
        ephemeral = bool(interaction.message and interaction.message.flags.ephemeral)
        await _delete_original(interaction)
        if payload:
            await respond(interaction, ephemeral=ephemeral, **payload)
        return

    edit_kwargs = _for_edit(kwargs)
    edit = (interaction.edit_original_response if interaction.response.is_done()
            else interaction.response.edit_message)
    try:
        await _with_emoji_guard(edit, edit_kwargs)
        return
    except discord.HTTPException:
        logger.exception("Не удалось отредактировать сообщение (%s), отправляю отдельно", interaction.id)

    # Не должно происходить за пределами _needs_recreate, но на случай другой,
    # ещё не встреченной причины отказа — тот же путь восстановления: убрать
    # то, что не удалось поправить, и показать результат отдельным сообщением.
    await _delete_original(interaction)
    if payload:
        await respond(interaction, ephemeral=True, **payload)


async def deny(interaction: discord.Interaction, text: str) -> None:
    """Короткий отказ (нет прав, ничего не найдено и т.п.)."""
    await respond(interaction, view=info(text), ephemeral=True)


async def fetch_message(
    interaction: discord.Interaction, channel: discord.TextChannel, raw_id: str
) -> discord.Message | None:
    """Достаёт сообщение по введённому ID, сама объясняя причину неудачи.

    Нужна и командам оформления, и меню ролей — поэтому лежит здесь, а не в
    одном из них: раньше подобное дублировалось в каждом файле."""
    try:
        message_id = int(raw_id.strip())
    except (ValueError, AttributeError):
        await deny(interaction, "ID сообщения должен быть числом.")
        return None

    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound:
        await deny(interaction, f"Сообщение не найдено в {channel.mention}.")
    except discord.Forbidden:
        await deny(interaction, f"Нет прав читать историю в {channel.mention}.")
    except discord.HTTPException:
        await deny(interaction, "Не удалось получить сообщение, попробуй ещё раз.")
    return None


# ---------------------------------------------------------------------------
# Чекбокс «Отправить в ЛС» для модалок, чьё решение уходит игроку
# ---------------------------------------------------------------------------


def dm_checkbox(*, description: str | None = None, default: bool = True) -> discord.ui.Label:
    """Чекбокс «Уведомить игрока в ЛС» как поле модального окна.

    Добавляется в модалки, где решение (кик, ЧС, штраф, отказ) обычно уходит
    игроку личным сообщением — админ может снять галочку и промолчать,
    например если предпочитает сообщить лично сам. Значение читается после
    сабмита как `<имя_поля>.component.value`.
    """
    return discord.ui.Label(
        text="Отправить уведомление в ЛС",
        description=description,
        component=discord.ui.Checkbox(default=default),
    )


# ---------------------------------------------------------------------------
# Раскладка кнопок для classic discord.ui.View
# ---------------------------------------------------------------------------


def layout_rows(view: discord.ui.View, buttons: Sequence[discord.ui.Button], per_row: int = 5) -> None:
    """Раскладывает кнопки по рядам ровно, без ручного подсчёта row= на
    каждую. Только для classic `discord.ui.View` (например, `role_buttons.py`,
    который декорирует произвольные, не бот-панельные сообщения) — панели на
    Components V2 используют `Panel.buttons(...)`."""
    for index, button in enumerate(buttons):
        button.row = index // per_row
        view.add_item(button)


# ---------------------------------------------------------------------------
# Имена участников
# ---------------------------------------------------------------------------


def member_name(guild: discord.Guild | None, user_id: int | None, fallback: str | None = None) -> str:
    """Имя без пинга — для списков, где сразу много человек."""
    if user_id is None:
        return fallback if fallback is not None else "?"
    member = guild.get_member(user_id) if guild else None
    if member:
        return member.display_name
    return fallback if fallback is not None else f"ID {user_id}"


def member_mention(guild: discord.Guild | None, user_id: int | None, fallback: str = "?") -> str:
    if user_id is None:
        return fallback
    return f"<@{user_id}>"
