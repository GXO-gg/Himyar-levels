"""Himyar Levels — shared helpers."""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

import discord

from .strings import StringBag

DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")
UNIT_SECONDS = {
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400, "d": 86400,
    "week": 604800, "weeks": 604800, "wk": 604800, "wks": 604800, "w": 604800,
}
MIN_EVENT = 300            # 5 minutes
MAX_EVENT = 30 * 86400     # 30 days


class DurationError(ValueError):
    pass


def parse_duration(text: str) -> int:
    cleaned = (text or "").strip().lower()
    if not cleaned:
        raise DurationError("Tell me how long — for example `2h`, `48h`, or `1w`.")
    total, found, consumed = 0.0, False, 0
    for match in DURATION_RE.finditer(cleaned):
        if match.start() > consumed + 1:
            break
        unit = match.group(2)
        if unit not in UNIT_SECONDS:
            break
        total += float(match.group(1)) * UNIT_SECONDS[unit]
        found, consumed = True, match.end()
    if not found:
        raise DurationError(
            f"I couldn't read “{text.strip()}” as a length of time. Try `2h`, `48h`, or `1w`."
        )
    seconds = int(total)
    if seconds < MIN_EVENT:
        raise DurationError("That's too short — the minimum is 5 minutes.")
    if seconds > MAX_EVENT:
        raise DurationError("That's too long — the maximum is 30 days.")
    return seconds


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    parts = []
    for label, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count, seconds = divmod(seconds, size)
            parts.append(f"{count} {label}{'s' if count != 1 else ''}")
    if not parts:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    return " ".join(parts[:2])


def human_voice_time(seconds: int) -> str:
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def ts(moment: dt.datetime, style: str = "F") -> str:
    return f"<t:{int(moment.timestamp())}:{style}>"


def base_embed(settings: dict, title: str | None = None,
               description: str | None = None) -> discord.Embed:
    color = settings.get("embed_color") or 0x1E90FF
    return discord.Embed(title=title, description=description,
                         color=discord.Color(int(color)),
                         timestamp=discord.utils.utcnow())


def ok_embed(settings: dict, description: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"✅ {description}",
                         color=discord.Color(0x2ECC71))


def err_embed(description: str, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"❌ {description}",
                         color=discord.Color(0xE74C3C))


def is_staff(member: discord.Member, settings: dict) -> bool:
    perms = getattr(member, "guild_permissions", None)
    if perms and (perms.manage_guild or perms.administrator):
        return True
    role_id = settings.get("staff_role_id")
    if not role_id:
        return False
    return any(r.id == int(role_id) for r in getattr(member, "roles", []))


def truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def medal(position: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(position, f"`#{position}`")


def today_key(now: Optional[dt.datetime] = None) -> str:
    """UTC date string used to roll the daily voice cap over."""
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y-%m-%d")


async def safe_respond(interaction: discord.Interaction, *args, **kwargs) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(*args, **kwargs)
        else:
            await interaction.response.send_message(*args, **kwargs)
    except discord.HTTPException:
        pass


def bag_from(overrides: dict) -> StringBag:
    return StringBag(overrides)
