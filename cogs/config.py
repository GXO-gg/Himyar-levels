"""
Himyar Levels — configuration and staff tools.

No dashboard, no website. `/setup` gets a server going, `/config …` tunes every
knob, and `/levels …` holds the staff operations — including the one-time seed
that reads members' existing level roles and gives them the XP to match.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import levels, rules, util, views
from core.db import parse_ts, utcnow
from core.strings import DEFAULT_STRINGS, PLACEHOLDERS, StringBag

log = logging.getLogger(__name__)

MANAGE = discord.Permissions(manage_guild=True)
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_color(raw: str) -> Optional[int]:
    match = HEX_RE.match((raw or "").strip())
    if match:
        return int(match.group(1), 16)
    named = {
        "blurple": 0x5865F2, "green": 0x2ECC71, "red": 0xE74C3C, "blue": 0x1E90FF,
        "gold": 0xF1C40F, "purple": 0x9B59B6, "black": 0x2B2D31, "white": 0xFFFFFF,
        "orange": 0xE67E22, "teal": 0x1ABC9C, "pink": 0xE91E63,
    }
    return named.get((raw or "").strip().lower())


class MessageModal(discord.ui.Modal, title="Edit message"):
    def __init__(self, cog: "Config", key: str, current: str):
        super().__init__(timeout=600)
        self.cog = cog
        self.key = key
        self.field = discord.ui.TextInput(
            label=util.truncate(key, 45), style=discord.TextStyle.paragraph,
            default=current[:4000], max_length=2000, required=True,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.bot.db.set_string(interaction.guild.id, self.key, str(self.field.value))
        settings = await self.cog.bot.db.get_guild(interaction.guild.id)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"`{self.key}` updated."), ephemeral=True
        )


class Config(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    config = app_commands.Group(
        name="config", description="Configure levelling", guild_only=True,
        default_permissions=MANAGE,
    )
    exclude_group = app_commands.Group(
        name="exclude", description="Channels that earn no XP", parent=config
    )
    multiplier_group = app_commands.Group(
        name="multiplier", description="Bonus XP for roles", parent=config
    )
    messages_group = app_commands.Group(
        name="messages", description="Edit any text the bot sends", parent=config
    )

    levels_group = app_commands.Group(
        name="levels", description="Level rewards and staff XP tools",
        guild_only=True, default_permissions=MANAGE,
    )
    role_group = app_commands.Group(
        name="role", description="Level role rewards", parent=levels_group
    )
    xp_group = app_commands.Group(
        name="xp", description="Adjust a member's XP", parent=levels_group
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

    async def message_key_autocomplete(self, interaction: discord.Interaction, current: str):
        current = (current or "").lower()
        return [app_commands.Choice(name=k, value=k)
                for k in DEFAULT_STRINGS if current in k.lower()][:25]

    # ─── /setup ───────────────────────────────────────────────────────────────
    @app_commands.command(name="setup", description="Set up levelling on this server")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        staff_role="Role allowed to use the staff commands (server managers always can)",
        levelup="Where level-up messages go",
        levelup_channel="Channel for level-ups, if you chose a dedicated channel",
        text_xp="Earn XP from messages",
        voice_xp="Earn XP from voice",
    )
    @app_commands.choices(levelup=[
        app_commands.Choice(name="Reply in the channel they were chatting in", value="channel"),
        app_commands.Choice(name="One dedicated channel", value="dedicated"),
        app_commands.Choice(name="DM the member", value="dm"),
        app_commands.Choice(name="Off", value="off"),
    ])
    async def setup_command(
        self, interaction: discord.Interaction,
        staff_role: Optional[discord.Role] = None,
        levelup: Optional[app_commands.Choice[str]] = None,
        levelup_channel: Optional[discord.TextChannel] = None,
        text_xp: bool = True,
        voice_xp: bool = True,
    ) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)

        mode = levelup.value if levelup else "channel"
        warnings = []
        if mode == "dedicated" and levelup_channel is None:
            mode = "channel"
            warnings.append(
                "You picked a dedicated channel but didn't name one, so level-ups "
                "will reply in the chat channel for now."
            )
        if not guild.me.guild_permissions.manage_roles:
            warnings.append(
                "I don't have **Manage Roles**, so I can't hand out level reward roles."
            )

        await self.bot.db.update_guild(
            guild.id,
            staff_role_id=staff_role.id if staff_role else None,
            levelup_mode=mode,
            levelup_channel_id=levelup_channel.id if levelup_channel else None,
            text_enabled=int(text_xp), voice_enabled=int(voice_xp),
            setup_complete=1,
        )
        settings = await self.bot.db.get_guild(guild.id)

        embed = util.base_embed(
            settings, title="✅ Himyar Levels is set up",
            description="Members start earning XP immediately. Nothing here is shared "
                        "with any other server.",
        )
        embed.add_field(name="Text XP", value="on" if text_xp else "off")
        embed.add_field(name="Voice XP", value="on" if voice_xp else "off")
        embed.add_field(name="Level-ups", value=mode)
        embed.add_field(name="Staff role",
                        value=staff_role.mention if staff_role else "*server managers only*")
        embed.add_field(
            name="Pacing",
            value=f"Level 10 ≈ {levels.messages_to_level(10):,} messages\n"
                  f"Level 20 ≈ {levels.messages_to_level(20):,} messages",
        )
        if warnings:
            embed.add_field(name="⚠️", value="\n".join(f"• {w}" for w in warnings), inline=False)
        embed.add_field(
            name="Next steps",
            value="`/levels role add level:5 role:@Bronze` — reward roles\n"
                  "`/config exclude add` — stop a channel earning XP\n"
                  "`/levels seed` — give existing level-role holders the XP they've earned\n"
                  "`/config view` — everything at a glance",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /config view ─────────────────────────────────────────────────────────
    @config.command(name="view", description="Show every setting for this server")
    async def config_view(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await self.bot.db.get_guild(guild.id)
        excluded = await self.bot.db.get_excluded_channels(guild.id)
        mults = await self.bot.db.get_role_multipliers(guild.id)
        level_roles = await self.bot.db.get_level_roles(guild.id)
        ranked = await self.bot.db.member_count(guild.id, "xp")

        rate = float(settings.get("xp_rate") or 1.0)
        embed = util.base_embed(settings, title="⚙️ Levelling configuration")
        embed.add_field(
            name="Text XP",
            value=("on" if settings.get("text_enabled") else "**off**")
                  + f"\n{settings.get('xp_min')}–{settings.get('xp_max')} per message"
                  + f"\n{settings.get('xp_cooldown')}s cooldown",
        )
        embed.add_field(
            name="Voice XP",
            value=("on" if settings.get("voice_enabled") else "**off**")
                  + f"\n{settings.get('voice_xp_per_minute')} per minute"
                  + f"\ndaily cap: {settings.get('voice_daily_cap') or 'none'}",
        )
        embed.add_field(
            name="Voice rules",
            value=("• needs company\n" if settings.get("voice_require_others") else "")
                  + ("• no XP muted\n" if settings.get("voice_ignore_muted") else "")
                  + ("• AFK ignored" if settings.get("voice_ignore_afk") else "")
                  or "*all relaxed*",
        )
        embed.add_field(name="XP rate", value=f"×{rate:g}")
        embed.add_field(
            name="Pacing at this rate",
            value=f"L10 ≈ {levels.messages_to_level(10, multiplier=rate):,} msgs\n"
                  f"L20 ≈ {levels.messages_to_level(20, multiplier=rate):,} msgs",
        )
        mode = settings.get("levelup_mode")
        channel_id = settings.get("levelup_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        embed.add_field(
            name="Level-ups",
            value=f"{mode}" + (f"\n{channel.mention}" if channel else ""),
        )
        embed.add_field(
            name="Reward roles",
            value=(f"{len(level_roles)} configured\n"
                   + ("highest only" if not settings.get("stack_roles") else "stacking")),
        )
        staff_id = settings.get("staff_role_id")
        staff = guild.get_role(int(staff_id)) if staff_id else None
        embed.add_field(name="Staff role", value=staff.mention if staff else "*managers only*")
        embed.add_field(name="Ranked members", value=str(ranked))

        event = float(settings.get("event_multiplier") or 1.0)
        if event != 1.0:
            ends = parse_ts(settings.get("event_ends_at"))
            embed.add_field(
                name="⚡ Double XP active",
                value=f"×{event:g}" + (f" until {util.ts(ends, 'R')}" if ends else ""),
                inline=False,
            )
        if excluded:
            embed.add_field(
                name=f"Excluded channels ({len(excluded)})",
                value=util.truncate(" ".join(f"<#{c}>" for c in list(excluded)[:20]), 1024),
                inline=False,
            )
        if mults:
            embed.add_field(
                name="Role multipliers",
                value="\n".join(f"<@&{rid}> → ×{m:g}" for rid, m in mults.items()),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── /config text & voice ─────────────────────────────────────────────────
    @config.command(name="text", description="Message XP settings")
    @app_commands.describe(
        enabled="Earn XP from messages",
        minimum="Lowest XP per message", maximum="Highest XP per message",
        cooldown="Seconds between XP awards for one member",
        rate="Overall XP rate multiplier — 2 makes everyone level twice as fast",
    )
    async def config_text(
        self, interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        minimum: Optional[app_commands.Range[int, 1, 500]] = None,
        maximum: Optional[app_commands.Range[int, 1, 500]] = None,
        cooldown: Optional[app_commands.Range[int, 0, 3600]] = None,
        rate: Optional[app_commands.Range[float, 0.1, 10.0]] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        updates: dict = {}
        if enabled is not None:
            updates["text_enabled"] = int(enabled)
        if minimum is not None:
            updates["xp_min"] = int(minimum)
        if maximum is not None:
            updates["xp_max"] = int(maximum)
        if cooldown is not None:
            updates["xp_cooldown"] = int(cooldown)
        if rate is not None:
            updates["xp_rate"] = float(rate)
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )

        low = updates.get("xp_min", settings.get("xp_min"))
        high = updates.get("xp_max", settings.get("xp_max"))
        if int(low) > int(high):
            return await interaction.response.send_message(
                embed=util.err_embed(
                    f"Minimum ({low}) can't be above maximum ({high})."
                ),
                ephemeral=True,
            )

        await self.bot.db.update_guild(interaction.guild.id, **updates)
        new_rate = float(updates.get("xp_rate", settings.get("xp_rate") or 1.0))
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                "Text XP updated.\n\n"
                f"At this rate, level 10 is about **{levels.messages_to_level(10, multiplier=new_rate):,} "
                f"messages** and level 20 about **{levels.messages_to_level(20, multiplier=new_rate):,}**.",
            ),
            ephemeral=True,
        )

    @config.command(name="voice", description="Voice XP settings and anti-farm rules")
    @app_commands.describe(
        enabled="Earn XP from voice",
        per_minute="XP awarded per minute in a call",
        daily_cap="Most voice XP one member can earn per day (0 = unlimited)",
        require_others="Only earn XP when someone else is in the channel",
        ignore_muted="Earn nothing while muted or deafened",
        ignore_afk="Never earn XP in the AFK channel",
    )
    async def config_voice(
        self, interaction: discord.Interaction,
        enabled: Optional[bool] = None,
        per_minute: Optional[app_commands.Range[int, 0, 200]] = None,
        daily_cap: Optional[app_commands.Range[int, 0, 100000]] = None,
        require_others: Optional[bool] = None,
        ignore_muted: Optional[bool] = None,
        ignore_afk: Optional[bool] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        updates: dict = {}
        for name, value, column in (
            ("enabled", enabled, "voice_enabled"),
            ("require_others", require_others, "voice_require_others"),
            ("ignore_muted", ignore_muted, "voice_ignore_muted"),
            ("ignore_afk", ignore_afk, "voice_ignore_afk"),
        ):
            if value is not None:
                updates[column] = int(value)
        if per_minute is not None:
            updates["voice_xp_per_minute"] = int(per_minute)
        if daily_cap is not None:
            updates["voice_daily_cap"] = int(daily_cap)
        if not updates:
            return await interaction.response.send_message(
                embed=util.err_embed("Give me at least one thing to change."), ephemeral=True
            )
        await self.bot.db.update_guild(interaction.guild.id, **updates)

        warning = ""
        if updates.get("voice_require_others") == 0:
            warning = ("\n\n⚠️ With the company rule off, someone can earn XP sitting "
                       "alone in a call indefinitely.")
        await interaction.response.send_message(
            embed=util.ok_embed(settings, "Voice XP updated." + warning), ephemeral=True
        )

    @config.command(name="levelup", description="Where level-up messages go")
    @app_commands.describe(mode="How to announce", channel="Channel, for dedicated mode")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Reply in the channel they were chatting in", value="channel"),
        app_commands.Choice(name="One dedicated channel", value="dedicated"),
        app_commands.Choice(name="DM the member", value="dm"),
        app_commands.Choice(name="Off", value="off"),
    ])
    async def config_levelup(self, interaction: discord.Interaction,
                             mode: app_commands.Choice[str],
                             channel: Optional[discord.TextChannel] = None) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        if mode.value == "dedicated" and channel is None:
            return await interaction.response.send_message(
                embed=util.err_embed("Pick a channel to use dedicated mode."), ephemeral=True
            )
        await self.bot.db.update_guild(
            interaction.guild.id, levelup_mode=mode.value,
            levelup_channel_id=channel.id if channel else settings.get("levelup_channel_id"),
        )
        note = ""
        if mode.value == "channel":
            note = ("\n\nVoice level-ups have no chat channel of their own — set a "
                    "channel here too and they'll land there instead of being dropped.")
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"Level-ups: **{mode.name}**." + note),
            ephemeral=True,
        )

    @config.command(name="roles", description="Whether level roles stack or replace")
    @app_commands.describe(stack="On: keep every earned role. Off: keep only the highest.")
    async def config_roles(self, interaction: discord.Interaction, stack: bool) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await self.bot.db.update_guild(interaction.guild.id, stack_roles=int(stack))
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                ("Level roles now **stack** — members keep every one they earn."
                 if stack else
                 "Level roles now **replace** — only the highest earned role is kept.")
                + "\n\nThis applies from the next level-up; run `/levels sync` to apply it "
                  "to everyone now.",
            ),
            ephemeral=True,
        )

    @config.command(name="appearance", description="Embed colour")
    @app_commands.describe(color="Hex like #1E90FF, or a name like blurple")
    async def config_appearance(self, interaction: discord.Interaction, color: str) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        parsed = parse_color(color)
        if parsed is None:
            return await interaction.response.send_message(
                embed=util.err_embed("That colour didn't parse. Try `#1E90FF`."), ephemeral=True
            )
        await self.bot.db.update_guild(interaction.guild.id, embed_color=parsed)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"Colour set to `#{parsed:06X}`."), ephemeral=True
        )

    # ─── /config exclude … ────────────────────────────────────────────────────
    @exclude_group.command(name="add", description="Stop a channel earning XP")
    @app_commands.describe(channel="Text or voice channel (or a category, to cover everything in it)")
    async def exclude_add(self, interaction: discord.Interaction,
                          channel: discord.abc.GuildChannel) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await self.bot.db.add_excluded_channel(interaction.guild.id, channel.id)
        note = ""
        if isinstance(channel, discord.CategoryChannel):
            note = "\n\nThreads inside this category are covered too."
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"{channel.mention} no longer earns XP.{note}"),
            ephemeral=True,
        )

    @exclude_group.command(name="remove", description="Let a channel earn XP again")
    @app_commands.describe(channel="The channel to un-exclude")
    async def exclude_remove(self, interaction: discord.Interaction,
                             channel: discord.abc.GuildChannel) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        removed = await self.bot.db.remove_excluded_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            embed=(util.ok_embed(settings, f"{channel.mention} earns XP again.") if removed
                   else util.err_embed(f"{channel.mention} wasn't excluded.")),
            ephemeral=True,
        )

    @exclude_group.command(name="list", description="Channels that earn no XP")
    async def exclude_list(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        excluded = await self.bot.db.get_excluded_channels(interaction.guild.id)
        if not excluded:
            return await interaction.response.send_message(
                embed=util.base_embed(
                    settings, description="No channels are excluded. Every text and voice "
                                          "channel earns XP."),
                ephemeral=True,
            )
        await interaction.response.send_message(
            embed=util.base_embed(
                settings, title=f"🚫 Excluded channels ({len(excluded)})",
                description=" ".join(f"<#{c}>" for c in excluded),
            ),
            ephemeral=True,
        )

    # ─── /config multiplier … ─────────────────────────────────────────────────
    @multiplier_group.command(name="add", description="Give a role bonus XP")
    @app_commands.describe(role="The role to reward", multiplier="e.g. 1.5 for 50% more XP")
    async def multiplier_add(self, interaction: discord.Interaction, role: discord.Role,
                             multiplier: app_commands.Range[float, 1.1, 10.0]) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mults = await self.bot.db.get_role_multipliers(interaction.guild.id)
        mults[role.id] = float(multiplier)
        await self.bot.db.set_role_multipliers(interaction.guild.id, mults)
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                f"{role.mention} now earns **×{multiplier:g}** XP.\n\n"
                "A member holding several bonus roles gets the highest, not the product.",
            ),
            ephemeral=True,
        )

    @multiplier_group.command(name="remove", description="Remove a role's bonus XP")
    @app_commands.describe(role="The role to remove")
    async def multiplier_remove(self, interaction: discord.Interaction,
                                role: discord.Role) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mults = await self.bot.db.get_role_multipliers(interaction.guild.id)
        if role.id not in mults:
            return await interaction.response.send_message(
                embed=util.err_embed(f"{role.mention} has no multiplier."), ephemeral=True
            )
        mults.pop(role.id, None)
        await self.bot.db.set_role_multipliers(interaction.guild.id, mults)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"{role.mention} earns normal XP again."),
            ephemeral=True,
        )

    @multiplier_group.command(name="list", description="Roles with bonus XP")
    async def multiplier_list(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        mults = await self.bot.db.get_role_multipliers(interaction.guild.id)
        if not mults:
            return await interaction.response.send_message(
                embed=util.base_embed(
                    settings,
                    description="No role multipliers. `/config multiplier add` to reward "
                                "boosters or VIPs with faster levelling."),
                ephemeral=True,
            )
        await interaction.response.send_message(
            embed=util.base_embed(
                settings, title="⚡ Role multipliers",
                description="\n".join(f"<@&{rid}> → **×{m:g}**"
                                      for rid, m in sorted(mults.items(), key=lambda kv: -kv[1])),
            ),
            ephemeral=True,
        )

    # ─── /levels role … ───────────────────────────────────────────────────────
    @role_group.command(name="add", description="Give a role as a reward at a level")
    @app_commands.describe(level="The level that earns it", role="The role to give")
    async def role_add(self, interaction: discord.Interaction,
                       level: app_commands.Range[int, 1, 500], role: discord.Role) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        guild = interaction.guild
        if role >= guild.me.top_role:
            return await interaction.response.send_message(
                embed=util.err_embed(
                    f"{role.mention} sits above my own role, so I can't hand it out. "
                    "Drag my role higher in **Server Settings → Roles**."
                ),
                ephemeral=True,
            )
        if role.managed:
            return await interaction.response.send_message(
                embed=util.err_embed(
                    f"{role.mention} is managed by an integration — Discord won't let "
                    "anyone assign it."
                ),
                ephemeral=True,
            )
        await self.bot.db.set_level_role(guild.id, int(level), role.id)
        messages = levels.messages_to_level(
            int(level), multiplier=float(settings.get("xp_rate") or 1.0)
        )
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                f"{role.mention} will be given at **level {level}** "
                f"(about {messages:,} messages).\n\n"
                "Existing members get it when they next level up — or run "
                "`/levels sync` to apply rewards to everyone now.",
            ),
            ephemeral=True,
        )

    @role_group.command(name="remove", description="Stop giving a role at a level")
    @app_commands.describe(level="The level to clear")
    async def role_remove(self, interaction: discord.Interaction,
                          level: app_commands.Range[int, 1, 500]) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        removed = await self.bot.db.remove_level_role(interaction.guild.id, int(level))
        await interaction.response.send_message(
            embed=(util.ok_embed(settings, f"Level {level} no longer gives a role.")
                   if removed else util.err_embed(f"Level {level} had no reward role.")),
            ephemeral=True,
        )

    @role_group.command(name="list", description="All level role rewards")
    async def role_list(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        rows = await self.bot.db.get_level_roles(interaction.guild.id)
        if not rows:
            return await interaction.response.send_message(
                embed=util.base_embed(
                    settings, description="No level rewards yet. `/levels role add` to make one."),
                ephemeral=True,
            )
        lines = []
        for row in rows:
            role = interaction.guild.get_role(int(row["role_id"]))
            lines.append(f"**Level {row['level']}** → "
                         f"{role.mention if role else '*deleted role*'}")
        await interaction.response.send_message(
            embed=util.base_embed(settings, title="🎖️ Level rewards",
                                  description="\n".join(lines)),
            ephemeral=True,
        )

    # ─── /levels xp … ─────────────────────────────────────────────────────────
    async def _apply_xp(self, interaction: discord.Interaction, member: discord.Member,
                        new_total: int, settings: dict, verb: str) -> None:
        new_total = max(0, int(new_total))
        new_level = levels.level_from_xp(new_total)
        await self.bot.db.set_xp(interaction.guild.id, member.id, new_total, new_level)
        xp_cog = self.bot.get_cog("XP")
        if xp_cog is not None:
            await xp_cog.apply_level_roles(member, new_level, settings)
        await interaction.followup.send(
            embed=util.ok_embed(
                settings,
                f"{verb} — **{member.display_name}** is now level **{new_level}** "
                f"with **{new_total:,} XP**.",
            ),
            ephemeral=True,
        )

    @xp_group.command(name="add", description="Give a member XP")
    @app_commands.describe(member="Who to give XP to", amount="How much XP")
    async def xp_add(self, interaction: discord.Interaction, member: discord.Member,
                     amount: app_commands.Range[int, 1, 10_000_000]) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        record = await self.bot.db.get_member(interaction.guild.id, member.id)
        await self._apply_xp(interaction, member,
                             int(record.get("xp") or 0) + int(amount), settings, "Added")

    @xp_group.command(name="remove", description="Take XP away from a member")
    @app_commands.describe(member="Who to take XP from", amount="How much XP")
    async def xp_remove(self, interaction: discord.Interaction, member: discord.Member,
                        amount: app_commands.Range[int, 1, 10_000_000]) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        record = await self.bot.db.get_member(interaction.guild.id, member.id)
        await self._apply_xp(interaction, member,
                             int(record.get("xp") or 0) - int(amount), settings, "Removed")

    @xp_group.command(name="set", description="Set a member's XP to an exact amount")
    @app_commands.describe(member="Whose XP to set", amount="The new total XP")
    async def xp_set(self, interaction: discord.Interaction, member: discord.Member,
                     amount: app_commands.Range[int, 0, 10_000_000]) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._apply_xp(interaction, member, int(amount), settings, "Set")

    @xp_group.command(name="setlevel", description="Set a member to an exact level")
    @app_commands.describe(member="Whose level to set", level="The new level")
    async def xp_setlevel(self, interaction: discord.Interaction, member: discord.Member,
                          level: app_commands.Range[int, 0, 500]) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._apply_xp(interaction, member,
                             levels.total_xp_for_level(int(level)), settings, "Set")

    # ─── /levels reset, sync, seed, event ─────────────────────────────────────
    @levels_group.command(name="reset", description="Reset a member's XP, or everyone's")
    @app_commands.describe(member="Leave blank to reset the whole server")
    async def levels_reset(self, interaction: discord.Interaction,
                           member: Optional[discord.Member] = None) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        if member is not None:
            await self.bot.db.reset_member(interaction.guild.id, member.id)
            return await interaction.response.send_message(
                embed=util.ok_embed(settings, f"**{member.display_name}** is back to level 0."),
                ephemeral=True,
            )

        ranked = await self.bot.db.member_count(interaction.guild.id, "xp")
        view = views.ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Reset every member?",
                description=f"This wipes XP and levels for **{ranked} members** on this "
                            "server. Reward roles already given are not removed — run "
                            "`/levels sync` afterwards to tidy those up.\n\nThis cannot be undone.",
                color=discord.Color(0xE74C3C),
            ),
            view=view, ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Cancelled."), ephemeral=True
            )
        cleared = await self.bot.db.reset_guild_members(interaction.guild.id)
        await interaction.followup.send(
            embed=util.ok_embed(settings, f"Reset **{cleared}** members to level 0."),
            ephemeral=True,
        )

    @levels_group.command(
        name="sync", description="Re-apply reward roles to everyone based on their level")
    async def levels_sync(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        rows = await self.bot.db.get_level_roles(interaction.guild.id)
        if not rows:
            return await interaction.response.send_message(
                embed=util.err_embed("No level rewards are configured."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)

        xp_cog = self.bot.get_cog("XP")
        if xp_cog is None:
            return await interaction.followup.send(
                embed=util.err_embed("The XP engine isn't loaded."), ephemeral=True
            )
        changed = 0
        for record in await self.bot.db.all_members(interaction.guild.id):
            member = interaction.guild.get_member(int(record["user_id"]))
            if member is None:
                continue
            level = levels.level_from_xp(int(record.get("xp") or 0))
            before = {r.id for r in member.roles}
            await xp_cog.apply_level_roles(member, level, settings)
            if {r.id for r in member.roles} != before:
                changed += 1
        await interaction.followup.send(
            embed=util.ok_embed(settings, f"Reward roles re-applied. **{changed}** members changed."),
            ephemeral=True,
        )

    @levels_group.command(
        name="seed",
        description="Give members the XP for the level role they already hold")
    @app_commands.describe(
        overwrite="Also lower members who currently have MORE XP than their role implies")
    async def levels_seed(self, interaction: discord.Interaction,
                          overwrite: bool = False) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        rows = await self.bot.db.get_level_roles(interaction.guild.id)
        if not rows:
            return await interaction.response.send_message(
                embed=util.err_embed(
                    "Set up your level rewards first with `/levels role add`, mapping each "
                    "existing tier role to the level it should represent. Then run this."
                ),
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True, thinking=True)

        role_to_level: dict[int, int] = {}
        for row in rows:
            role_to_level[int(row["role_id"])] = int(row["level"])

        # Work out who would change before touching anything.
        planned: list[tuple[discord.Member, int, int]] = []
        for member in interaction.guild.members:
            if member.bot:
                continue
            held = [role_to_level[r.id] for r in member.roles if r.id in role_to_level]
            if not held:
                continue
            target_level = max(held)
            target_xp = levels.total_xp_for_level(target_level)
            record = await self.bot.db.peek_member(interaction.guild.id, member.id)
            current = int(record.get("xp") or 0) if record else 0
            if target_xp > current or (overwrite and target_xp != current):
                planned.append((member, current, target_xp))

        if not planned:
            return await interaction.followup.send(
                embed=util.base_embed(
                    settings,
                    description="Nobody needs seeding — everyone with a tier role already "
                                "has at least the XP it represents."),
                ephemeral=True,
            )

        preview = "\n".join(
            f"• **{util.truncate(m.display_name, 24)}** {before:,} → {after:,} XP "
            f"(level {levels.level_from_xp(after)})"
            for m, before, after in planned[:10]
        )
        more = f"\n…and {len(planned) - 10} more" if len(planned) > 10 else ""
        view = views.ConfirmView(interaction.user.id)
        await interaction.followup.send(
            embed=util.base_embed(
                settings, title=f"Seed {len(planned)} members?",
                description=f"{preview}{more}\n\nThis reads each member's existing tier role "
                            "and grants the XP for that level.",
            ),
            view=view, ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Cancelled — nothing changed."),
                ephemeral=True,
            )

        for member, _, target_xp in planned:
            await self.bot.db.set_xp(
                interaction.guild.id, member.id, target_xp,
                levels.level_from_xp(target_xp), text_xp=target_xp,
            )
        await interaction.followup.send(
            embed=util.ok_embed(
                settings,
                f"Seeded **{len(planned)}** members. Run `/leaderboard` to check it looks right.",
            ),
            ephemeral=True,
        )

    @levels_group.command(name="event", description="Start or stop a double-XP event")
    @app_commands.describe(
        multiplier="XP multiplier while it runs — 1 to stop an event early",
        duration="How long — 2h, 48h, 1w",
        announce="Channel to announce it in (optional)",
    )
    async def levels_event(
        self, interaction: discord.Interaction,
        multiplier: app_commands.Range[float, 1.0, 10.0],
        duration: Optional[str] = None,
        announce: Optional[discord.TextChannel] = None,
    ) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        bag = StringBag(await self.bot.db.get_strings(interaction.guild.id))

        if float(multiplier) == 1.0:
            await self.bot.db.update_guild(
                interaction.guild.id, event_multiplier=1.0, event_ends_at=None
            )
            return await interaction.response.send_message(
                embed=util.ok_embed(settings, "Double XP stopped. " + bag.get("event_ended")),
                ephemeral=True,
            )

        if not duration:
            return await interaction.response.send_message(
                embed=util.err_embed("Tell me how long it should run — e.g. `48h`."),
                ephemeral=True,
            )
        try:
            seconds = util.parse_duration(duration)
        except util.DurationError as exc:
            return await interaction.response.send_message(
                embed=util.err_embed(str(exc)), ephemeral=True
            )

        ends = utcnow() + dt.timedelta(seconds=seconds)
        await self.bot.db.update_guild(
            interaction.guild.id, event_multiplier=float(multiplier), event_ends_at=ends
        )
        await interaction.response.send_message(
            embed=util.ok_embed(
                settings,
                f"**×{multiplier:g} XP** is live until {util.ts(ends, 'F')} "
                f"({util.ts(ends, 'R')}).",
            ),
            ephemeral=True,
        )
        if announce is not None:
            text = bag.get("event_started", multiplier=f"{multiplier:g}",
                           duration=util.human_duration(seconds))
            try:
                await announce.send(
                    embed=util.base_embed(settings, title="⚡ Double XP", description=text)
                )
            except discord.HTTPException:
                pass

    # ─── /config messages … ───────────────────────────────────────────────────
    @messages_group.command(name="list", description="Every message you can rewrite")
    async def messages_list(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        bag = StringBag(await self.bot.db.get_strings(interaction.guild.id))
        embed = util.base_embed(
            settings, title="✏️ Editable messages",
            description="`/config messages set key:<name>` opens an editor.\n"
                        "✏️ = rewritten here · `{}` placeholders are filled in.",
        )
        for key in DEFAULT_STRINGS:
            marker = "✏️ " if bag.is_custom(key) else ""
            embed.add_field(
                name=f"{marker}{key}",
                value=util.truncate(
                    f"{bag.raw(key)}\n\n*Placeholders:* `{PLACEHOLDERS.get(key, '—')}`", 1024),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @messages_group.command(name="set", description="Rewrite one of the bot's messages")
    @app_commands.describe(key="Which message to rewrite")
    async def messages_set(self, interaction: discord.Interaction, key: str) -> None:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not util.is_staff(interaction.user, settings):
            return await interaction.response.send_message(
                embed=util.err_embed("Staff only."), ephemeral=True
            )
        if key not in DEFAULT_STRINGS:
            return await interaction.response.send_message(
                embed=util.err_embed(f"`{key}` isn't a message key."), ephemeral=True
            )
        overrides = await self.bot.db.get_strings(interaction.guild.id)
        await interaction.response.send_modal(
            MessageModal(self, key, StringBag(overrides).raw(key))
        )

    @messages_set.autocomplete("key")
    async def set_key_ac(self, interaction: discord.Interaction, current: str):
        return await self.message_key_autocomplete(interaction, current)

    @messages_group.command(name="reset", description="Put one message back to the default")
    @app_commands.describe(key="Which message to reset")
    async def messages_reset(self, interaction: discord.Interaction, key: str) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        if key not in DEFAULT_STRINGS:
            return await interaction.response.send_message(
                embed=util.err_embed(f"`{key}` isn't a message key."), ephemeral=True
            )
        await self.bot.db.reset_string(interaction.guild.id, key)
        await interaction.response.send_message(
            embed=util.ok_embed(settings, f"`{key}` is back to the default."), ephemeral=True
        )

    @messages_reset.autocomplete("key")
    async def reset_key_ac(self, interaction: discord.Interaction, current: str):
        return await self.message_key_autocomplete(interaction, current)

    @config.command(name="reset", description="Wipe this server's levelling data")
    async def config_reset(self, interaction: discord.Interaction) -> None:
        settings = await self.guard(interaction)
        if settings is None:
            return
        view = views.ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚠️ Reset everything?",
                description="This deletes this server's settings, **every member's XP and "
                            "level**, the reward roles and the exclusions. It cannot be undone.",
                color=discord.Color(0xE74C3C),
            ),
            view=view, ephemeral=True,
        )
        await view.wait()
        if not view.value:
            return await interaction.followup.send(
                embed=util.base_embed(settings, description="Cancelled."), ephemeral=True
            )
        await self.bot.db.wipe_guild(interaction.guild.id)
        await interaction.followup.send(
            embed=util.ok_embed(settings, "Wiped. Run `/setup` to start again."), ephemeral=True
        )

    # ─── /help and greeting ───────────────────────────────────────────────────
    @app_commands.command(name="help", description="How to use Himyar Levels")
    @app_commands.guild_only()
    async def help_command(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_guild(interaction.guild.id)
        rate = float(settings.get("xp_rate") or 1.0)
        embed = util.base_embed(
            settings, title="📊 Himyar Levels",
            description="XP and levels from chatting and voice. Configured entirely "
                        "inside Discord.",
        )
        embed.add_field(
            name="Everyone",
            value="`/rank` — your level, XP and progress\n"
                  "`/leaderboard` — top members, split by text or voice\n"
                  "`/levelroles` — what roles you can earn",
            inline=False,
        )
        embed.add_field(
            name="How XP works",
            value=f"{settings.get('xp_min')}–{settings.get('xp_max')} XP per message "
                  f"(once every {settings.get('xp_cooldown')}s)\n"
                  f"{settings.get('voice_xp_per_minute')} XP per minute in voice\n"
                  f"Level 10 ≈ {levels.messages_to_level(10, multiplier=rate):,} messages",
            inline=False,
        )
        embed.add_field(
            name="Staff",
            value="`/setup` · `/config view` · `/config text` · `/config voice`\n"
                  "`/levels role add` — reward roles · `/levels sync`\n"
                  "`/config exclude add` — stop a channel earning XP\n"
                  "`/levels xp add|remove|set` · `/levels reset`\n"
                  "`/levels event` — double XP · `/levels seed` — import existing tiers",
            inline=False,
        )
        embed.set_footer(text="Part of the Himyar bot suite · himyar.org")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.bot.db.get_guild(guild.id)
        embed = discord.Embed(
            title="👋 Thanks for adding Himyar Levels",
            description=(
                "Members start earning XP right away. Run **`/setup`** to tune it.\n\n"
                "• `/rank` and `/leaderboard` for members\n"
                "• `/levels role add` — give roles as level rewards\n"
                "• `/config exclude add` — stop a channel earning XP\n"
                "• `/help` — everything else"
            ),
            color=discord.Color(0x1E90FF),
        )
        target = guild.system_channel
        if target is None or not target.permissions_for(guild.me).send_messages:
            target = next(
                (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
                None,
            )
        if target is not None:
            try:
                await target.send(embed=embed)
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Config(bot))
