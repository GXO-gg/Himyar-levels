#!/usr/bin/env python3
"""
Himyar Levels — entry point.

Part of the Himyar bot suite (himyar.org). XP and levels from messages and
voice, multi-server by design: no guild, channel or role id is hardcoded
anywhere, and every server configures itself from inside Discord.

Environment:
    DISCORD_TOKEN     required — the bot token
    HIMYAR_DB_PATH    optional — sqlite path (default: data/levels.db)
    DEV_GUILD_ID      optional — sync commands instantly to one test server
    LOG_LEVEL         optional — INFO by default
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands

from core.db import Database


def load_env_file(path: str = ".env") -> None:
    """Tiny .env reader so the bot runs the same way under systemd or by hand."""
    env_path = Path(__file__).with_name(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("himyar.levels")

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
DEV_GUILD_ID = os.environ.get("DEV_GUILD_ID", "").strip()

COGS = ("cogs.xp", "cogs.ranks", "cogs.config", "cogs.board")


class HimyarLevels(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True          # resolve members, apply reward roles
        intents.voice_states = True     # see who is in a call for voice XP
        intents.message_content = False  # XP counts messages; it never reads them
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="/rank · everyone level up"
            ),
        )
        self.db = Database()

    async def setup_hook(self) -> None:
        await self.db.connect()
        log.info("Database ready at %s", self.db.path)

        for cog in COGS:
            await self.load_extension(cog)
            log.info("Loaded %s", cog)

        if DEV_GUILD_ID.isdigit():
            guild = discord.Object(id=int(DEV_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to dev guild %s", len(synced), DEV_GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) — in %d server(s)",
                 self.user, self.user.id, len(self.guilds))

    async def close(self) -> None:
        await self.db.close()
        await super().close()


async def main() -> None:
    if not TOKEN:
        log.error("DISCORD_TOKEN is not set. Put it in .env or the systemd unit.")
        raise SystemExit(1)
    bot = HimyarLevels()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down.")
