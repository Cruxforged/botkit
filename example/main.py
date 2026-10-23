from pathlib import Path

import discord

from botkit import BaseBot, db, run
from botkit.config import env_int, env_str, load_env

BASE = load_env(Path(__file__).parent)

TABLES = (
    "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER, text TEXT)",
)


class MyBot(BaseBot):
    async def prepare(self) -> None:
        await db.apply_schema(TABLES)


intents = discord.Intents.default()
intents.members = True

bot = MyBot(
    extensions=("cogs.ping",),
    home_guild_id=env_int("GUILD_ID"),
    db_path=str(BASE / "bot.db"),
    intents=intents,
)

if __name__ == "__main__":
    run(bot, env_str("DISCORD_TOKEN"), app="mybot", banner=BASE / "banner.txt", log_level=env_str("LOG_LEVEL", "INFO"))
