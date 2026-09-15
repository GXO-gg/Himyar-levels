"""
Himyar Levels — the standing leaderboard.

One message in one channel, edited on a timer rather than reposted, so the
channel stays clean and the message keeps its permalink. The buttons underneath
are persistent (fixed custom_ids, no timeout), so they still work after a
restart without the bot having to remember anything in memory.

The timer is deliberately coarse. Discord rate-limits message edits, and a
leaderboard that changes every thirty seconds is noise, not information.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import util
from core.db import iso, parse_ts, utcnow
from core.strings import StringBag

log = logging.getLogger(__name__)

MANAGE = discord.Permissions(manage_guild=True)

REFRESH_ID = "himyar_levels:board:refresh"
TEXT_ID = "himyar_levels:board:text"
VOICE_ID = "himyar_levels:board:voice"

BUTTON_COOLDOWN = 30.0     # seconds between forced refreshes of one board
MIN_INTERVAL = 5           # minutes — below this Discord starts throttling edits
MAX_INTERVAL = 1440
MIN_SIZE = 3
MAX_SIZE = 25

BOARD_LABELS = {"xp": "Combined", "text": "Text", "voice": "Voice"}


class BoardView(discord.ui.View):
    """Buttons under the standing leaderboard.

    Refresh edits the shared message for everyone. The two peek buttons reply
    privately instead, so one member looking at the voice board doesn't change
    what the channel shows to everybody else.
    """

    def __init__(self, cog: "Board"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Refresh", emoji="🔄",
                       style=discord.ButtonStyle.secondary, custom_id=REFRESH_ID)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_refresh(interaction)

    @discord.ui.button(label="Text", emoji="💬",
                       style=discord.ButtonStyle.secondary, custom_id=TEXT_ID)
    async def text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_peek(interaction, "text")

    @discord.ui.button(label="Voice", emoji="🔊",
                       style=discord.ButtonStyle.secondary, custom_id=VOICE_ID)
    async def voice(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_peek(interaction, "voice")


class Board(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_button: dict[int, float] = {}

    async def cog_load(self) -> None:
        self.bot.add_view(BoardView(self))
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    # ─── rendering ────────────────────────────────────────────────────────────
    async def render(self, guild: discord.Guild, key: str, size: int,
                     interval: int) -> Optional[discord.Embed]:
        """Build the board embed by reusing the /leaderboard renderer."""
        ranks = self.bot.get_cog("Ranks")
        if ranks is None:
            log.error("Ranks cog not loaded — cannot render the board")
            return None
        embed = await ranks.render_board(guild, key, 0, limit=size)
        embed.set_footer(text=f"{BOARD_LABELS.get(key, 'Combined')} · "
                              f"updates every {interval} min")
        embed.timestamp = utcnow()
        return embed

    # ─── publishing ───────────────────────────────────────────────────────────
    async def publish(self, guild: discord.Guild,
                      settings: Optional[dict] = None) -> Optional[discord.Message]:
        """Edit the standing message, or post it if it isn't there any more."""
        settings = settings or await self.bot.db.get_guild(guild.id)
        channel_id = settings.get("board_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel is None:
            await self.bot.db.update_guild(guild.id, board_enabled=0)
            log.warning("Board channel gone for guild %s — turned the board off", guild.id)
            return None

        key = str(settings.get("board_type") or "xp")
        size = max(MIN_SIZE, min(MAX_SIZE, int(settings.get("board_size") or 10)))
        interval = max(MIN_INTERVAL, int(settings.get("board_interval") or 10))

        embed = await self.render(guild, key, size, interval)
        if embed is None:
            return None

        message = None
        message_id = settings.get("board_message_id")
        if message_id:
            try:
                message = await channel.fetch_message(int(message_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None   # deleted or unreachable — post a fresh one

        try:
            if message is not None:
                await message.edit(embed=embed, view=BoardView(self))
            else:
                message = await channel.send(embed=embed, view=BoardView(self))
                await self.bot.db.update_guild(guild.id, board_message_id=message.id)
        except discord.Forbidden:
            await self.bot.db.update_guild(guild.id, board_enabled=0)
            log.warning("No permission to post the board in guild %s — turned it off",
                        guild.id)
            return None
        except discord.HTTPException:
            log.exception("Board update failed for guild %s", guild.id)
            return None

        await self.bot.db.update_guild(guild.id, board_last_at=iso(utcnow()))
        return message

    # ─── the timer ────────────────────────────────────────────────────────────
    @tasks.loop(minutes=1)
    async def tick(self) -> None:
        for settings in await self.bot.db.all_guilds():
            if not settings.get("board_enabled"):
                continue
            guild = self.bot.get_guild(int(settings["guild_id"]))
            if guild is None:
                continue
            interval = max(MIN_INTERVAL, int(settings.get("board_interval") or 10))
            last = parse_ts(settings.get("board_last_at"))
            if last and (utcnow() - last).total_seconds() < interval * 60:
                continue
            try:
                await self.publish(guild, settings)
            except Exception:
                log.exception("Board tick failed for guild %s", settings.get("guild_id"))

    @tick.before_loop
    async def before_tick(self) -> None:
        await self.bot.wait_until_ready()

    # ─── button handlers ──────────────────────────────────────────────────────
    async def handle_refresh(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        now = time.monotonic()
        previous = self._last_button.get(guild.id, 0.0)
        if now - previous < BUTTON_COOLDOWN:
            wait = int(BUTTON_COOLDOWN - (now - previous)) + 1
            return await interaction.response.send_message(
                f"🔄 Just updated. Try again in {wait}s.", ephemeral=True
            )
        self._last_button[guild.id] = now

        settings = await self.bot.db.get_guild(guild.id)
        key = str(settings.get("board_type") or "xp")
        size = max(MIN_SIZE, min(MAX_SIZE, int(settings.get("board_size") or 10)))
        interval = max(MIN_INTERVAL, int(settings.get("board_interval") or 10))
        embed = await self.render(guild, key, size, interval)
        if embed is None:
            return await interaction.response.send_message(
                "❌ Couldn't build the leaderboard.", ephemeral=True
            )
        await interaction.response.edit_message(embed=embed, view=BoardView(self))
        await self.bot.db.update_guild(guild.id, board_last_at=iso(utcnow()))

    async def handle_peek(self, interaction: discord.Interaction, key: str) -> None:
        """Show one member a different board without touching the shared message."""
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await self.bot.db.get_guild(guild.id)
        size = max(MIN_SIZE, min(MAX_SIZE, int(settings.get("board_size") or 10)))
        ranks = self.bot.get_cog("Ranks")
        if ranks is None:
            return await interaction.followup.send("❌ Leaderboard unavailable.",
                                                   ephemeral=True)
        embed = await ranks.render_board(guild, key, 0, limit=size)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /board … ─────────────────────────────────────────────────────────────
    board = app_commands.Group(
        name="board", description="The standing auto-updating leaderboard",
        guild_only=True, default_permissions=MANAGE,
    )

    async def guard(self, interaction: discord.Interaction) -> Optional[dict]:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            bag = StringBag(await self.bot.db.get_strings(interaction.guild.id))
            await interaction.response.send_message(
                embed=util.err_embed(bag.get("staff_only")), ephemeral=True
            )
            return None
        return settings

    @board.command(name="setup", description="Post a leaderboard that updates itself")
    @app_commands.describe(
        channel="Where the leaderboard lives",
        board="Which board to show",
        size="How many members to list (3-25)",
        interval="Minutes between updates (5-1440)",
    )
    @app_commands.choices(board=[
        app_commands.Choice(name="Combined", value="xp"),
        app_commands.Choice(name="Text only", value="text"),
        app_commands.Choice(name="Voice only", value="voice"),
    ])
    async def board_setup(
        self, interaction: discord.Interaction,
        channel: discord.TextChannel,
        board: Optional[app_commands.Choice[str]] = None,
        size: Optional[app_commands.Range[int, MIN_SIZE, MAX_SIZE]] = None,
        interval: Optional[app_commands.Range[int, MIN_INTERVAL, MAX_INTERVAL]] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return

        me = interaction.guild.me
        perms = channel.permissions_for(me)
        missing = [name for name, ok in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Embed Links", perms.embed_links),
        ) if not ok]
        if missing:
            return await interaction.response.send_message(
                embed=util.err_embed(
                    f"I need **{', '.join(missing)}** in {channel.mention}."
                ), ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        # A new channel means the old standing message is no longer ours to edit.
        old_channel_id = settings.get("board_channel_id")
        if old_channel_id and int(old_channel_id) != channel.id:
            await self.bot.db.update_guild(interaction.guild.id, board_message_id=None)

        await self.bot.db.update_guild(
            interaction.guild.id,
            board_enabled=1,
            board_channel_id=channel.id,
            board_type=(board.value if board else str(settings.get("board_type") or "xp")),
            board_size=int(size) if size else int(settings.get("board_size") or 10),
            board_interval=(int(interval) if interval
                            else int(settings.get("board_interval") or 10)),
            board_last_at=None,
        )
        message = await self.publish(interaction.guild)
        if message is None:
            return await interaction.followup.send(
                embed=util.err_embed("Couldn't post the leaderboard — check my "
                                     "permissions in that channel."),
                ephemeral=True,
            )
        fresh = await self.bot.db.get_guild(interaction.guild.id)
        await interaction.followup.send(
            embed=util.ok_embed(
                fresh,
                f"Leaderboard is live in {channel.mention}.\n\n"
                f"Showing the **{BOARD_LABELS.get(str(fresh.get('board_type')), 'Combined')}** "
                f"board, top **{fresh.get('board_size')}**, updating every "
                f"**{fresh.get('board_interval')} minutes**.\n"
                f"[Jump to it]({message.jump_url})",
            ),
            ephemeral=True,
        )

    @board.command(name="now", description="Update the leaderboard immediately")
    async def board_now(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        if not settings.get("board_enabled"):
            return await interaction.response.send_message(
                embed=util.err_embed("No standing leaderboard yet — `/board setup` first."),
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await self.publish(interaction.guild)
        if message is None:
            return await interaction.followup.send(
                embed=util.err_embed("Update failed — check my permissions."), ephemeral=True
            )
        await interaction.followup.send(
            embed=util.ok_embed(settings, f"Updated. [Jump to it]({message.jump_url})"),
            ephemeral=True,
        )

    @board.command(name="off", description="Stop the leaderboard updating")
    @app_commands.describe(delete="Also delete the leaderboard message")
    async def board_off(self, interaction: discord.Interaction,
                        delete: Optional[bool] = False) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        if delete:
            channel_id = settings.get("board_channel_id")
            message_id = settings.get("board_message_id")
            channel = (interaction.guild.get_channel(int(channel_id))
                       if channel_id else None)
            if channel is not None and message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        await self.bot.db.update_guild(
            interaction.guild.id, board_enabled=0,
            board_message_id=None if delete else settings.get("board_message_id"),
        )
        await interaction.followup.send(
            embed=util.ok_embed(
                settings,
                "Leaderboard stopped." + (" Message deleted." if delete else
                                          " The message is still there, just frozen."),
            ),
            ephemeral=True,
        )

    @board.command(name="status", description="Show the standing leaderboard settings")
    async def board_status(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel_id = settings.get("board_channel_id")
        channel = interaction.guild.get_channel(int(channel_id)) if channel_id else None
        last = parse_ts(settings.get("board_last_at"))

        embed = util.base_embed(settings, title="🏆 Standing leaderboard")
        embed.add_field(name="Status",
                        value="on" if settings.get("board_enabled") else "**off**")
        embed.add_field(name="Channel",
                        value=channel.mention if channel else "*not set*")
        embed.add_field(
            name="Board",
            value=BOARD_LABELS.get(str(settings.get("board_type") or "xp"), "Combined"),
        )
        embed.add_field(name="Size", value=f"top {settings.get('board_size')}")
        embed.add_field(name="Interval",
                        value=f"every {settings.get('board_interval')} min")
        embed.add_field(name="Last updated",
                        value=util.ts(last, "R") if last else "*never*")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Board(bot))
