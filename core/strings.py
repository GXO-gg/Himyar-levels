"""
Himyar Levels — every user-facing string.

Defaults are English; any server can rewrite any of them with
`/config messages set`, which is how a server runs the bot in Arabic or in a
client's own voice without touching code.
"""

from __future__ import annotations

DEFAULT_STRINGS: dict[str, str] = {
    "levelup": "🎉 {user} reached **level {level}**!",
    "levelup_reward": "🎉 {user} reached **level {level}** and earned {role}!",
    "rank_title": "📊 {name}",
    "rank_none": "{name} hasn't earned any XP yet.",
    "rank_unranked": "unranked",
    "leaderboard_title": "🏆 {guild} leaderboard",
    "leaderboard_empty": "Nobody has earned XP yet.",
    "xp_disabled": "❌ Levelling is turned off on this server.",
    "event_started": "⚡ **{multiplier}x XP** is active for the next {duration}!",
    "event_ended": "XP is back to normal.",
    "staff_only": "❌ You need to be staff on this server to do that.",
    "no_level_roles": "No level role rewards are set up. `/levels role add` to add one.",
}

PLACEHOLDERS: dict[str, str] = {
    "levelup": "{user} {level} {guild}",
    "levelup_reward": "{user} {level} {role} {guild}",
    "rank_title": "{name}",
    "rank_none": "{name}",
    "rank_unranked": "—",
    "leaderboard_title": "{guild}",
    "leaderboard_empty": "—",
    "xp_disabled": "—",
    "event_started": "{multiplier} {duration}",
    "event_ended": "—",
    "staff_only": "—",
    "no_level_roles": "—",
}


class StringBag:
    """Per-guild strings: custom overrides on top of the English defaults."""

    def __init__(self, overrides: dict[str, str] | None = None):
        self.overrides = overrides or {}

    def raw(self, key: str) -> str:
        return self.overrides.get(key, DEFAULT_STRINGS.get(key, ""))

    def is_custom(self, key: str) -> bool:
        return key in self.overrides

    def get(self, key: str, **kwargs) -> str:
        """Format tolerantly — a staff typo in a custom string must never stop
        someone levelling up."""
        template = self.raw(key)
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            out = template
            for name, value in kwargs.items():
                out = out.replace("{" + name + "}", str(value))
            return out
