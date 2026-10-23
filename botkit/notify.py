"""
Уведомления игроку и в канал.

Личные сообщения (`dm_card`) — карточкой: заголовок с
эмодзи, цветная полоса по смыслу (зелёная — хорошее, красная — наказание и
т.д.) и строки «Причина: …». Без картинок. В канал, где выполнили действие,
по-прежнему уходит кодовый блок ```ansi``` (`announce`).
"""

from __future__ import annotations

import discord

from botkit import ansi, ui
from botkit.logs import logger
from botkit.panel import Panel, PanelView

# Подпись внизу каждой карточки ЛС; пусто — без подписи.
FOOTER = ""

# Цвета полосы карточки.
GREEN = 0x6FDC8C
RED = 0xFF6A4D
AMBER = 0xFFD27A
ORANGE = 0xFFB347
BRAND = 0x7A9CFF



def build_card(
    title: str, *, emoji: str = "", lead: str = "", fields: list[tuple[str, str]] | None = None,
    color: int = BRAND,
) -> PanelView:
    """Карточка ЛС. Пустые поля пропускаются."""
    panel = Panel(color=discord.Color(color))
    panel.text(f"## {emoji} {title}" if emoji else f"## {title}")
    if lead:
        panel.text(lead)
    rows = [f"**{label}:** {value}" for label, value in (fields or []) if value]
    if rows:
        panel.separator()
        panel.text("\n".join(rows))
    if FOOTER:
        panel.separator()
        panel.text(f"-# {FOOTER}")
    return PanelView(panel, timeout=None)


async def dm_card(member: discord.abc.User | None, title: str, **card) -> bool:
    """Карточку — игроку в личные сообщения. False — его нет на сервере,
    ЛС закрыты или он заблокировал бота."""
    if member is None:
        return False
    view = build_card(title, **card)
    try:
        await ui.safe_send(member, view=view)
        return True
    except discord.HTTPException:
        logger.info(
            "Не удалось отправить ЛС пользователю %s — закрыты личные сообщения или блок",
            getattr(member, "id", "?"),
        )
        return False


def card_text(view: discord.ui.LayoutView) -> str:
    """Весь текст карточки одной строкой — для логов и проверок."""
    return "\n".join(
        c.content for c in ui.walk_components(view.children) if isinstance(c, discord.ui.TextDisplay)
    )


async def announce(
    channel: discord.abc.Messageable | None, lines: list[str],
    *, ping: str | None = None, by: str | None = None,
) -> None:
    """Тот же кодовый блок — в канал, где было выполнено действие.

    Меншны внутри ```ansi``` блока Discord не парсит, поэтому настоящий пинг
    (`ping`, строка вида `<@id>`) и имя того, кто выполнил действие (`by`)
    уходят отдельной мелкой строкой под блоком."""
    if channel is None:
        return
    content = ansi.block(lines)
    footer = " • ".join(part for part in (ping, f"оформил {by}" if by else None) if part)
    if footer:
        content += f"\n-# {footer}"
    allowed = (
        discord.AllowedMentions(everyone=False, users=True, roles=False)
        if ping else discord.AllowedMentions.none()
    )
    try:
        await channel.send(content=content, allowed_mentions=allowed)
    except discord.HTTPException:
        logger.warning(
            "Не удалось отправить объявление в канал %s", getattr(channel, "id", "?"), exc_info=True
        )


async def announce_card(channel: discord.abc.Messageable | None, panel: Panel, *, ping: str | None = None) -> None:
    """Карточка-панель (тот же стиль, что у обычных панелей бота — эмодзи,
    цветная полоса, аватар) — в канал, где выполнили действие.

    Discord не уведомляет по меншну внутри Components V2, поэтому настоящий
    пинг (`ping`, строка вида `<@id>`) уходит отдельной мелкой строкой следом."""
    if channel is None:
        return
    try:
        await ui.safe_send(channel, view=PanelView(panel))
        if ping:
            await channel.send(
                content=f"-# {ping}",
                allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=False),
            )
    except discord.HTTPException:
        logger.warning(
            "Не удалось отправить объявление в канал %s", getattr(channel, "id", "?"), exc_info=True
        )
