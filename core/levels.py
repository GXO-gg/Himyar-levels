"""
Himyar Levels — the XP curve.

Pure functions, no Discord objects, so the maths that decides everyone's rank
can be tested directly rather than inferred from behaviour in a live server.

The curve matches the one most Discord users already know (MEE6's): going from
level n to n+1 costs 5n² + 50n + 100 XP. Familiarity matters here — members
arrive with an intuition for what level 10 means, and a novel curve just makes
the number meaningless to them.
"""

from __future__ import annotations

from typing import Iterable

MAX_LEVEL = 1000


def xp_for_next_level(level: int) -> int:
    """XP needed to go from `level` to `level + 1`."""
    level = max(0, int(level))
    return 5 * level * level + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    """Cumulative XP required to have reached `level` from zero.

    Closed form of the sum, so seeding a thousand members doesn't loop a
    thousand times each.
    """
    n = max(0, int(level))
    if n == 0:
        return 0
    # sum_{k=0}^{n-1} (5k^2 + 50k + 100)
    return (5 * (n - 1) * n * (2 * n - 1)) // 6 + 25 * (n - 1) * n + 100 * n


def level_from_xp(total_xp: int) -> int:
    """Highest level fully paid for by `total_xp`."""
    total_xp = max(0, int(total_xp))
    if total_xp < 100:
        return 0
    # Walk upward from a rough estimate rather than from zero.
    level = max(0, int((total_xp / 5) ** (1 / 3)) - 1)
    while total_xp_for_level(level + 1) <= total_xp and level < MAX_LEVEL:
        level += 1
    while level > 0 and total_xp_for_level(level) > total_xp:
        level -= 1
    return level


def progress(total_xp: int) -> tuple[int, int, int]:
    """(level, xp into this level, xp needed for the next). """
    level = level_from_xp(total_xp)
    floor = total_xp_for_level(level)
    needed = xp_for_next_level(level)
    return level, max(0, int(total_xp) - floor), needed


def multiplier_for(role_ids: Iterable[int], role_multipliers: dict[int, float]) -> float:
    """Best role multiplier a member qualifies for.

    Highest wins rather than multiplying together — someone holding Booster and
    VIP gets the better of the two, not the product. Stacking multipliers is how
    a leaderboard quietly becomes meaningless.
    """
    if not role_multipliers:
        return 1.0
    values = [float(role_multipliers[int(r)]) for r in role_ids
              if int(r) in role_multipliers]
    if not values:
        return 1.0
    return max(1.0, max(values))


def effective_multiplier(base_rate: float, role_multiplier: float,
                         event_multiplier: float) -> float:
    """Server rate × best role bonus × any running double-XP event."""
    total = float(base_rate or 1.0) * float(role_multiplier or 1.0) * float(event_multiplier or 1.0)
    return max(0.0, min(total, 100.0))


def messages_to_level(level: int, average_xp: float = 20.0,
                      multiplier: float = 1.0) -> int:
    """Rough number of messages to reach a level — used in /help and /config
    so staff can see what changing the rate actually does to their members."""
    per_message = max(0.01, average_xp * max(0.01, multiplier))
    return int(round(total_xp_for_level(level) / per_message))


def format_progress_bar(current: int, needed: int, width: int = 16) -> str:
    if needed <= 0:
        return "█" * width
    filled = int(round(width * current / needed))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)
