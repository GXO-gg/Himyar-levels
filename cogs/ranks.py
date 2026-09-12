"""
Himyar Levels — /rank and /leaderboard.

Rendered as embeds rather than generated images: instant, no extra dependencies,
and Arabic display names render correctly without font juggling.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import levels, util, views
from core.strings import StringBag

log = logging.getLogger(__name__)

PAGE_SIZE = 10
BOARDS = {
    "xp": ("Combined", "xp"),
    "text": ("Text", "text_xp"),
    "voice": ("Voice", "voice_xp"),
}


class Ranks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def context(self, guild_id: int) -> tuple[dict, StringBag]:
        settings = await self.bot.db.get_guild(guild_id)
        bag = StringBag(await self.bot.db.get_strings(guild_id))
        return settings, bag

    # ─── /rank ────────────────────────────────────────────────────────────────
    @app_commands.command(name="rank", description="Show your level and XP")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose rank to show (defaults to you)")
    async def rank(self, interaction: discord.Interaction,
                   member: Optional[discord.Member] = None) -> None:
        settings, bag = await self.context(interaction.guild.id)
        target = member or interaction.user
        await interaction.response.defer(thinking=True)

        record = await self.bot.db.peek_member(interaction.guild.id, target.id)
        if not record or int(record.get("xp") or 0) <= 0:
            return await interaction.followup.send(
                embed=util.base_embed(
                    settings,
                    description=bag.get("rank_none", name=target.display_name),
                )
            )

        total_xp = int(record["xp"])
        level, into, needed = levels.progress(total_xp)
        position = await self.bot.db.rank_of(interaction.guild.id, target.id, "xp")
        total_ranked = await self.bot.db.member_count(interaction.guild.id, "xp")

        embed = util.base_embed(
            settings, title=bag.get("rank_title", name=target.display_name)
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Level", value=f"**{level}**")
        embed.add_field(
            name="Rank",
            value=(f"**#{position}** of {total_ranked}" if position
                   else bag.get("rank_unranked")),
        )
        embed.add_field(name="Total XP", value=f"{total_xp:,}")
        embed.add_field(
            name=f"Progress to level {level + 1}",
            value=f"{levels.format_progress_bar(into, needed)}\n"
                  f"{into:,} / {needed:,} XP  ·  {needed - into:,} to go",
            inline=False,
        )

        stats = []
        if int(record.get("messages") or 0):
            stats.append(f"💬 {int(record['messages']):,} messages "
                         f"({int(record.get('text_xp') or 0):,} XP)")
        if int(record.get("voice_seconds") or 0):
            stats.append(f"🔊 {util.human_voice_time(record['voice_seconds'])} in voice "
                         f"({int(record.get('voice_xp') or 0):,} XP)")
        if stats:
            embed.add_field(name="Activity", value="\n".join(stats), inline=False)

        next_role = await self.next_role_hint(interaction.guild, level)
        if next_role:
            embed.set_footer(text=next_role)
        await interaction.followup.send(embed=embed)

    async def next_role_hint(self, guild: discord.Guild, level: int) -> str:
        rows = await self.bot.db.get_level_roles(guild.id)
        upcoming = [(int(r["level"]), int(r["role_id"])) for r in rows
                    if int(r["level"]) > level]
        if not upcoming:
            return ""
        next_level, role_id = min(upcoming, key=lambda item: item[0])
        role = guild.get_role(role_id)
        if role is None:
            return ""
        return f"Next reward: {role.name} at level {next_level}"

    # ─── /leaderboard ─────────────────────────────────────────────────────────
    @app_commands.command(name="leaderboard", description="Top members on this server")
    @app_commands.guild_only()
    @app_commands.describe(board="Which board to show")
    @app_commands.choices(board=[
        app_commands.Choice(name="Combined", value="xp"),
        app_commands.Choice(name="Text only", value="text"),
        app_commands.Choice(name="Voice only", value="voice"),
    ])
    async def leaderboard(self, interaction: discord.Interaction,
                          board: Optional[app_commands.Choice[str]] = None) -> None:
        key = board.value if board else "xp"
        await interaction.response.defer(thinking=True)

        column = BOARDS.get(key, BOARDS["xp"])[1]
        total = await self.bot.db.member_count(interaction.guild.id, column)
        if total == 0:
            settings, bag = await self.context(interaction.guild.id)
            return await interaction.followup.send(
                embed=util.base_embed(settings, description=bag.get("leaderboard_empty"))
            )

        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        embed = await self.render_board(interaction.guild, key, 0)
        view = views.LeaderboardView(self.render_board, 0, pages, key, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view)

    async def render_board(self, guild: discord.Guild, key: str, page: int) -> discord.Embed:
        settings, bag = await self.context(guild.id)
        label, column = BOARDS.get(key, BOARDS["xp"])
        offset = max(0, page) * PAGE_SIZE
        rows = await self.bot.db.leaderboard(guild.id, column, PAGE_SIZE, offset)
        total = await self.bot.db.member_count(guild.id, column)

        lines = []
        for index, row in enumerate(rows):
            position = offset + index + 1
            user_id = int(row["user_id"])
            member = guild.get_member(user_id)
            name = member.display_name if member else f"Member {user_id}"
            value = int(row.get(column) or 0)
            level = levels.level_from_xp(int(row.get("xp") or 0))

            detail = f"Level {level} · {value:,} XP"
            if key == "voice":
                detail = (f"Level {level} · {util.human_voice_time(row.get('voice_seconds'))} "
                          f"· {value:,} XP")
            elif key == "text":
                detail = f"Level {level} · {int(row.get('messages') or 0):,} msgs · {value:,} XP"
            lines.append(f"{util.medal(position)} **{util.truncate(name, 28)}** — {detail}")

        embed = util.base_embed(
            settings,
            title=bag.get("leaderboard_title", guild=guild.name) + f" · {label}",
            description="\n".join(lines) if lines else bag.get("leaderboard_empty"),
        )
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        embed.set_footer(text=f"Page {page + 1} of {pages} · {total} ranked members")
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        return embed

    # ─── /levels roles ────────────────────────────────────────────────────────
    @app_commands.command(name="levelroles",
                          description="Show the level role rewards on this server")
    @app_commands.guild_only()
    async def level_roles(self, interaction: discord.Interaction) -> None:
        settings, bag = await self.context(interaction.guild.id)
        await interaction.response.defer(thinking=True)
        rows = await self.bot.db.get_level_roles(interaction.guild.id)
        if not rows:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description=bag.get("no_level_roles"))
            )
        lines = []
        for row in rows:
            role = interaction.guild.get_role(int(row["role_id"]))
            messages = levels.messages_to_level(
                int(row["level"]), multiplier=float(settings.get("xp_rate") or 1.0)
            )
            label = role.mention if role else "*deleted role*"
            lines.append(
                f"**Level {row['level']}** → {label}  ·  ~{messages:,} messages"
            )
        embed = util.base_embed(
            settings, title="🎖️ Level rewards", description="\n".join(lines)
        )
        embed.set_footer(
            text="Only the highest earned role is kept"
            if not settings.get("stack_roles") else "All earned roles are kept"
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ranks(bot))
