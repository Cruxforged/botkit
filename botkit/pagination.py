"""
Универсальная постоянная панель со страницами: наследник описывает только,
как собрать страницу.

Ключевое свойство сохранено: кнопки НЕ хранят номер страницы в себе. Он
каждый раз читается из текста самой панели, поэтому листание продолжает
работать и после перезапуска бота (это же требование к постоянным кнопкам:
у них должен быть custom_id и они регистрируются в `cog_load`).
"""

from __future__ import annotations

import discord

from botkit import ui
from botkit.logs import logger
from botkit.panel import Panel, PanelView


class PaginatedPanel(PanelView):
    """Постоянная панель с кнопками «назад / обновить / вперёд».

    Наследник обязан задать `custom_id_prefix` и реализовать `build_page`
    как classmethod (страница собирается ещё до того, как есть готовый
    экземпляр view). Дополнительные кнопки — через `extra_buttons()`,
    отдельным рядом после навигации.
    """

    custom_id_prefix: str = "panel"
    prev_emoji: str = "◀️"
    refresh_emoji: str = "🔄"
    next_emoji: str = "▶️"

    def __init__(self, page_panel: str | Panel = "", page: int = 0, total_pages: int = 1):
        # Без аргументов конструируется при регистрации постоянных кнопок в
        # cog_load — Discord важен только custom_id → обработчик, содержимое
        # той регистрационной панели никто не увидит.
        super().__init__(page_panel, timeout=None)
        self._build_navigation(page, total_pages)
        extra = self.extra_buttons()
        if extra:
            self.panel.row(*extra)

    # -- переопределяется наследниками ------------------------------------

    @classmethod
    async def build_page(cls, guild: discord.Guild, page: int) -> tuple[Panel, int, int]:
        """Возвращает (панель, фактическая_страница, всего_страниц).

        Фактическая страница может отличаться от запрошенной — `ui.paginate`
        зажимает её в границы (например, если на последней странице что-то
        удалили и она опустела)."""
        raise NotImplementedError

    def extra_buttons(self) -> list[discord.ui.Button]:
        return []

    # -- внутреннее --------------------------------------------------------

    def _build_navigation(self, page: int, total_pages: int) -> None:
        prefix = self.custom_id_prefix
        self.panel.row(
            ui.make_button(
                self._make_nav(-1), emoji=self.prev_emoji, custom_id=f"{prefix}:prev",
                disabled=page <= 0,
            ),
            ui.make_button(
                self._make_nav(0), emoji=self.refresh_emoji, label="Обновить",
                custom_id=f"{prefix}:refresh",
            ),
            ui.make_button(
                self._make_nav(1), emoji=self.next_emoji, custom_id=f"{prefix}:next",
                disabled=page >= total_pages - 1,
            ),
        )

    def _make_nav(self, delta: int):
        async def handler(interaction: discord.Interaction) -> None:
            await ui.defer(interaction, thinking=False)
            current = ui.read_page_from_message(interaction.message)
            page_panel, actual, total_pages = await type(self).build_page(interaction.guild, current + delta)
            await ui.edit_response(interaction, view=type(self)(page_panel, actual, total_pages))
        return handler

    # -- публичные помощники ----------------------------------------------

    @classmethod
    async def send_new(cls, interaction: discord.Interaction) -> discord.Message:
        """Публикует панель и возвращает отправленное сообщение."""
        page_panel, actual, total_pages = await cls.build_page(interaction.guild, 0)
        await ui.respond(
            interaction, view=cls(page_panel, actual, total_pages), allowed_mentions=ui.NO_PING
        )
        return await interaction.original_response()

    @classmethod
    async def refresh_message(cls, guild: discord.Guild, location: tuple[int, int] | None) -> None:
        """Обновляет ранее опубликованную панель на месте, сохраняя открытую
        страницу: правка данных не должна сбрасывать того, кто сейчас
        листает список."""
        if not location:
            return
        channel_id, message_id = location
        channel = guild.get_channel(channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return

        current = ui.read_page_from_message(message)
        page_panel, actual, total_pages = await cls.build_page(guild, current)
        try:
            await ui.safe_edit(
                message, view=cls(page_panel, actual, total_pages), allowed_mentions=ui.NO_PING
            )
        except discord.HTTPException:
            logger.debug("Не удалось обновить панель %s", cls.__name__, exc_info=True)
