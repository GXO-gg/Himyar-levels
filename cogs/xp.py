"""
Himyar Levels — earning XP.

Message XP with a per-member cooldown, and voice XP awarded by a ticking loop
rather than by tracking join/leave sessions. The loop approach means a restart
mid-call loses nothing: the next tick simply looks at who's in voice right now.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

import discord
from discord.ext import commands, tasks

from core import levels, rules, util
from core.db import parse_ts, utcnow
from core.strings import StringBag

log = logging.getLogger(__name__)

VOICE_TICK_SECONDS = 60


class XP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Message cooldowns live in memory: losing them on restart is harmless,
        # and it saves a database read on every single message.
        self._cooldowns: dict[tuple[int, int], float] = {}

    async def cog_load(self) -> None:
        self.voice_tick.start()

    async def cog_unload(self) -> None:
        self.voice_tick.cancel()

    async def context(self, guild_id: int) -> tuple[dict, StringBag]:
        settings = await self.bot.db.get_guild(guild_id)
        bag = StringBag(await self.bot.db.get_strings(guild_id))
        return settings, bag

    # ─── multipliers ──────────────────────────────────────────────────────────
    async def current_event_multiplier(self, guild_id: int, settings: dict) -> float:
        """A double-XP event that has run out stops counting immediately, and is
        tidied away the first time anyone notices."""
        multiplier = float(settings.get("event_multiplier") or 1.0)
        if multiplier == 1.0:
            return 1.0
        ends = parse_ts(settings.get("event_ends_at"))
        if ends is not None and ends <= utcnow():
            await self.bot.db.update_guild(guild_id, event_multiplier=1.0, event_ends_at=None)
            return 1.0
        return multiplier

    async def multiplier_for_member(self, member: discord.Member, settings: dict) -> float:
        role_mults = await self.bot.db.get_role_multipliers(member.guild.id)
        role_multiplier = levels.multiplier_for(
            [r.id for r in getattr(member, "roles", [])], role_mults
        )
        event = await self.current_event_multiplier(member.guild.id, settings)
        return levels.effective_multiplier(
            float(settings.get("xp_rate") or 1.0), role_multiplier, event
        )

    # ─── message XP ───────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        guild_id = message.guild.id
        settings = await self.bot.db.get_guild(guild_id)
        excluded = await self.bot.db.get_excluded_channels(guild_id)

        channel_id = message.channel.id
        parent_id = getattr(getattr(message.channel, "parent", None), "id", None)
        is_excluded = channel_id in excluded or (parent_id is not None and parent_id in excluded)

        eligible, _ = rules.text_eligible(
            is_bot=message.author.bot,
            is_excluded_channel=is_excluded,
            is_dm=False,
            content_length=len((message.content or "").strip()),
            text_enabled=bool(settings.get("text_enabled")),
        )
        if not eligible:
            return

        key = (guild_id, message.author.id)
        now = time.monotonic()
        cooldown = int(settings.get("xp_cooldown") or 0)
        if cooldown and now < self._cooldowns.get(key, 0.0):
            return
        self._cooldowns[key] = now + cooldown

        low = int(settings.get("xp_min") or 15)
        high = max(low, int(settings.get("xp_max") or 25))
        multiplier = await self.multiplier_for_member(message.author, settings)
        amount = max(1, int(round(random.randint(low, high) * multiplier)))

        record = await self.bot.db.add_text_xp(guild_id, message.author.id, amount)
        await self.check_level_up(message.author, record, settings, channel=message.channel)

    # ─── voice XP ─────────────────────────────────────────────────────────────
    @tasks.loop(seconds=VOICE_TICK_SECONDS)
    async def voice_tick(self) -> None:
        """Award a minute of voice XP to everyone currently eligible.

        Looking at the live voice states each minute, rather than remembering when
        people joined, means a redeploy in the middle of a busy call costs nobody
        anything.
        """
        day = util.today_key()
        for guild in list(self.bot.guilds):
            try:
                settings = await self.bot.db.get_guild(guild.id)
                if not settings.get("voice_enabled"):
                    continue
                excluded = await self.bot.db.get_excluded_channels(guild.id)
                afk_id = guild.afk_channel.id if guild.afk_channel else None
                per_minute = int(settings.get("voice_xp_per_minute") or 0)
                if per_minute <= 0:
                    continue
                cap = int(settings.get("voice_daily_cap") or 0)

                for channel in guild.voice_channels:
                    humans = [m for m in channel.members if not m.bot]
                    if not humans:
                        continue
                    context = rules.VoiceContext(
                        channel_id=channel.id,
                        humans_in_channel=len(humans),
                        is_afk_channel=(afk_id is not None and channel.id == afk_id),
                        is_excluded=channel.id in excluded,
                    )
                    for member in humans:
                        state = member.voice
                        if state is None:
                            continue
                        voice_state = rules.VoiceState(
                            user_id=member.id,
                            is_bot=member.bot,
                            self_muted=bool(state.self_mute),
                            self_deafened=bool(state.self_deaf),
                            server_muted=bool(state.mute),
                            server_deafened=bool(state.deaf),
                        )
                        allowed, _ = rules.voice_eligible(voice_state, context, settings)
                        if not allowed:
                            continue

                        multiplier = await self.multiplier_for_member(member, settings)
                        amount = max(1, int(round(per_minute * multiplier)))
                        already = await self.bot.db.voice_xp_today(guild.id, member.id, day)
                        amount = rules.apply_daily_cap(amount, already, cap)
                        if amount <= 0:
                            continue

                        record = await self.bot.db.add_voice_xp(
                            guild.id, member.id, amount, VOICE_TICK_SECONDS, day
                        )
                        await self.check_level_up(member, record, settings, channel=None)
            except Exception:
                log.exception("Voice XP pass failed for guild %s", guild.id)

    @voice_tick.before_loop
    async def before_voice(self) -> None:
        await self.bot.wait_until_ready()

    # ─── levelling up ─────────────────────────────────────────────────────────
    async def check_level_up(self, member: discord.Member, record: dict, settings: dict,
                             channel: Optional[discord.abc.Messageable]) -> None:
        total_xp = int(record.get("xp") or 0)
        stored_level = int(record.get("level") or 0)
        new_level = levels.level_from_xp(total_xp)
        if new_level <= stored_level:
            return

        await self.bot.db.set_level(member.guild.id, member.id, new_level)
        granted = await self.apply_level_roles(member, new_level, settings)
        await self.announce(member, new_level, granted, settings, channel)

    async def apply_level_roles(self, member: discord.Member, level: int,
                                settings: dict) -> Optional[discord.Role]:
        """Give the reward role for this level and take back the ones it replaces.

        Returns the role just earned, if any, so the announcement can mention it.
        """
        rows = await self.bot.db.get_level_roles(member.guild.id)
        if not rows:
            return None
        pairs = [(int(r["level"]), int(r["role_id"])) for r in rows]
        keep_ids, drop_ids = rules.roles_for_level(
            level, pairs, stack=bool(settings.get("stack_roles"))
        )

        me = member.guild.me
        if not me.guild_permissions.manage_roles:
            return None

        held = {r.id for r in member.roles}
        to_add, to_remove = [], []
        for role_id in keep_ids - held:
            role = member.guild.get_role(role_id)
            if role is not None and role < me.top_role:
                to_add.append(role)
        for role_id in drop_ids & held:
            role = member.guild.get_role(role_id)
            if role is not None and role < me.top_role:
                to_remove.append(role)

        try:
            if to_add:
                await member.add_roles(*to_add, reason=f"Himyar Levels: reached level {level}")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Himyar Levels: replaced by a higher tier")
        except discord.HTTPException:
            log.warning("Could not update level roles for %s in %s", member.id, member.guild.id)

        # The role earned at exactly this level, if this level has one.
        for lvl, role_id in pairs:
            if lvl == level:
                return member.guild.get_role(role_id)
        return None

    async def announce(self, member: discord.Member, level: int,
                       role: Optional[discord.Role], settings: dict,
                       channel: Optional[discord.abc.Messageable]) -> None:
        mode = settings.get("levelup_mode") or "channel"
        if mode == "off":
            return

        bag = StringBag(await self.bot.db.get_strings(member.guild.id))
        key = "levelup_reward" if role is not None else "levelup"
        text = bag.get(key, user=member.mention, level=level,
                       role=role.mention if role else "", guild=member.guild.name)

        target: Optional[discord.abc.Messageable] = None
        if mode == "dm":
            try:
                await member.send(
                    text.replace(member.mention, "You").replace("  ", " ")
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            return
        if mode == "dedicated":
            channel_id = settings.get("levelup_channel_id")
            if channel_id:
                found = member.guild.get_channel(int(channel_id))
                if isinstance(found, discord.TextChannel):
                    target = found
        else:
            target = channel

        # Voice level-ups have no channel of their own; fall back to the
        # configured one so they aren't silently dropped.
        if target is None:
            channel_id = settings.get("levelup_channel_id")
            if channel_id:
                found = member.guild.get_channel(int(channel_id))
                if isinstance(found, discord.TextChannel):
                    target = found
        if target is None:
            return

        permissions = target.permissions_for(member.guild.me)
        if not (permissions.send_messages and permissions.embed_links):
            return
        try:
            await target.send(
                text, allowed_mentions=discord.AllowedMentions(users=True, roles=False)
            )
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(XP(bot))
