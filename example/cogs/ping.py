import logging

import discord
from discord import app_commands
from discord.ext import commands

from botkit import log_command
from botkit.panel import PanelView

logger = logging.getLogger("mybot")


class Ping(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Проверка связи")
    async def ping(self, interaction: discord.Interaction):
        await log_command(interaction)
        await interaction.response.send_message(view=PanelView(f"Понг: {self.bot.latency * 1000:.0f} мс"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Ping(bot))
