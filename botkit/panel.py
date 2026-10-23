"""
Компоненты V2: панели строятся из `Container`'а внутри `LayoutView`, а не из
`discord.Embed` и отдельного `discord.ui.View` с кнопками под ним. Текст,
разделители и кнопки идут одним потоком внутри самого сообщения — то же,
что Discord называет Components V2.

`Panel` — контейнер-карточка (замена embed'а): `.text()`, `.footer()`,
`.separator()`, `.thumbnail()`, `.row()`/`.buttons()` (кнопки), `.action()`
(строка текста со стыкованной кнопкой).

`PanelView` — LayoutView на один `Panel`, прямая замена `discord.ui.View`:
наследник строит `self.panel` в `__init__`, а не добавляет кнопки по одной.

Требует discord.py >= 2.6 (Components V2 добавлены в 2.6).
"""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence

import discord
from discord.ui.media_gallery import MediaGalleryItem

from botkit.logs import logger

PANEL_COLOR = discord.Color(0x222222)
BUTTON_STYLE = discord.ButtonStyle.secondary

COMPONENT_ERROR_TEXT = "⚠️ Что-то пошло не так при обработке действия. Подробности — в логах бота."


class Panel(discord.ui.Container):
    """Карточка панели: текст, разделители и кнопки одним потоком.

    По духу — замена `discord.Embed`, но без автоматической сетки полей
    (Components V2 её не поддерживает): `add_field` просто дописывает ещё
    одну строку текста."""

    def __init__(self, text: str = "", *, title: str | None = None, color: discord.Color | None = None):
        super().__init__(accent_colour=color or PANEL_COLOR)
        if title:
            self.add_item(discord.ui.TextDisplay(f"### {title}"))
        if text:
            self.add_item(discord.ui.TextDisplay(text))

    def text(self, content: str) -> "Panel":
        """Ещё один блок текста (поддерживает обычную markdown-разметку Discord)."""
        if content:
            self.add_item(discord.ui.TextDisplay(content))
        return self

    def add_field(self, name: str, value: str, inline: bool = True) -> "Panel":
        self.add_item(discord.ui.TextDisplay(f"**{name}**\n{value}" if name else value))
        return self

    def footer(self, content: str) -> "Panel":
        """Мелкая серая подпись — то же, что раньше `embed.set_footer`."""
        return self.text(f"-# {content}")

    def separator(self) -> "Panel":
        self.add_item(discord.ui.Separator())
        return self

    def thumbnail(self, content: str, media: str | discord.File) -> "Panel":
        """Строка текста с картинкой сбоку — замена `embed.set_thumbnail`.
        `media` — прямая ссылка либо `discord.File` (тогда его нужно
        передать этим же файлом в `file=`/`files=` при отправке)."""
        self.add_item(discord.ui.Section(discord.ui.TextDisplay(content), accessory=discord.ui.Thumbnail(media)))
        return self

    def image(self, media: str | discord.File) -> "Panel":
        """Картинка на всю ширину — замена `embed.set_image`. `media` —
        прямая ссылка либо `discord.File` (передать тем же файлом в
        `file=`/`files=` при отправке)."""
        self.add_item(discord.ui.MediaGallery(MediaGalleryItem(media)))
        return self

    def action(self, content: str, button: discord.ui.Button) -> "Panel":
        """Строка текста с одной стыкованной кнопкой справа."""
        self.add_item(discord.ui.Section(discord.ui.TextDisplay(content), accessory=button))
        return self

    def row(self, *buttons: discord.ui.Button) -> "Panel":
        """Один ряд кнопок (до 5) — прямая замена ряда classic ActionRow."""
        if buttons:
            self.add_item(discord.ui.ActionRow(*buttons))
        return self

    def buttons(self, items: Sequence[discord.ui.Button], per_row: int = 5) -> "Panel":
        """Раскладывает плоский список кнопок по рядам, как раньше `ui.layout_rows`."""
        for start in range(0, len(items), per_row):
            self.row(*items[start:start + per_row])
        return self


class PanelView(discord.ui.LayoutView):
    """LayoutView на один `Panel` — прямая замена `discord.ui.View` + embed.

    Можно передать готовый `Panel` (используется как есть) либо текст —
    тогда `Panel` соберётся сам. Наследники обычно вызывают
    `super().__init__(text, title=..., color=...)`, а потом наполняют
    `self.panel` через `.row()`/`.action()`/`.text()`."""

    def __init__(
        self, content: str | Panel = "", *, title: str | None = None,
        color: discord.Color | None = None, timeout: float | None = 180,
    ):
        super().__init__(timeout=timeout)
        self.panel: Panel = content if isinstance(content, Panel) else Panel(content, title=title, color=color)
        self.add_item(self.panel)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        logger.exception("Ошибка в компоненте %r: %s", item, error)
        await _report_component_error(interaction, COMPONENT_ERROR_TEXT)


async def _report_component_error(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Готовые ответы (замена ui.embed/info/ok/warn/error)
# ---------------------------------------------------------------------------


def info(text: str) -> PanelView:
    return PanelView(text)


def ok(text: str) -> PanelView:
    return PanelView(f"✅ {text}" if not text.startswith(("✅", "➖", "✏️", "🗑️", "⭐", "📦")) else text)


def warn(text: str) -> PanelView:
    return PanelView(f"⚠️ {text}" if not text.startswith("⚠️") else text)


def error(text: str) -> PanelView:
    return PanelView(f"❌ {text}" if not text.startswith("❌") else text)


# ---------------------------------------------------------------------------
# Кнопки
# ---------------------------------------------------------------------------


def make_button(
    handler: Callable[[discord.Interaction], Awaitable[None]],
    *,
    emoji: str | None = None,
    label: str | None = None,
    style: discord.ButtonStyle = BUTTON_STYLE,
    custom_id: str | None = None,
    row: int | None = None,
    disabled: bool = False,
) -> discord.ui.Button:
    """Кнопка с произвольным обработчиком — без объявления класса на каждую."""
    button = discord.ui.Button(
        emoji=emoji, label=label, style=style, custom_id=custom_id, row=row, disabled=disabled
    )
    button.callback = handler
    return button


# ---------------------------------------------------------------------------
# Диалог подтверждения
# ---------------------------------------------------------------------------


class ConfirmView(PanelView):
    """Диалог «да/отмена». Вопрос и кнопки — одна карточка.

    `on_confirm(interaction) -> str` выполняет действие и возвращает текст
    итога, который заменяет собой вопрос. Кнопки доступны только тому, кто
    их вызвал (если передан `author_id`)."""

    def __init__(
        self,
        text: str,
        on_confirm: Callable[[discord.Interaction], Awaitable[str]],
        *,
        confirm_label: str = "Да, подтверждаю",
        confirm_emoji: str = "✅",
        author_id: int | None = None,
        timeout: float = 60,
    ):
        super().__init__(text, timeout=timeout)
        self._on_confirm = on_confirm
        self._author_id = author_id
        self.panel.row(
            make_button(self._confirm, label=confirm_label, emoji=confirm_emoji),
            make_button(self._cancel, label="Отмена", emoji="✖️"),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._author_id is None or interaction.user.id == self._author_id:
            return True
        await _report_component_error(interaction, "Это подтверждение открыл другой человек.")
        return False

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            pass
        else:
            await interaction.response.defer()
        text = await self._on_confirm(interaction)
        await _edit_or_respond(interaction, view=info(text))
        self.stop()

    async def _cancel(self, interaction: discord.Interaction) -> None:
        await _edit_or_respond(interaction, view=info("Отменено."))
        self.stop()


async def _edit_or_respond(interaction: discord.Interaction, **kwargs) -> None:
    edit = (interaction.edit_original_response if interaction.response.is_done()
            else interaction.response.edit_message)
    try:
        await edit(**kwargs)
    except discord.HTTPException:
        try:
            await interaction.followup.send(ephemeral=True, **kwargs)
        except discord.HTTPException:
            logger.exception("Не удалось ответить на подтверждение (%s)", interaction.id)
