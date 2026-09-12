"""
Himyar Levels — who earns XP, and when.

Pure predicates taking plain values rather than Discord objects, so every
combination of the anti-farm rules can be tested directly. Voice XP is the part
people try hardest to cheat, so it's worth being able to prove the rules hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class VoiceState:
    """Everything about a member in a call that affects whether they earn XP."""
    user_id: int
    is_bot: bool = False
    self_muted: bool = False
    self_deafened: bool = False
    server_muted: bool = False
    server_deafened: bool = False


@dataclass(frozen=True)
class VoiceContext:
    channel_id: int
    humans_in_channel: int
    is_afk_channel: bool = False
    is_excluded: bool = False


def text_eligible(*, is_bot: bool, is_excluded_channel: bool, is_dm: bool,
                  content_length: int, text_enabled: bool,
                  min_length: int = 1) -> tuple[bool, str]:
    """Whether a message earns XP. Returns (eligible, reason-if-not)."""
    if is_bot:
        return False, "bot"
    if is_dm:
        return False, "dm"
    if not text_enabled:
        return False, "text xp disabled"
    if is_excluded_channel:
        return False, "channel excluded"
    if content_length < max(1, int(min_length)):
        return False, "message too short"
    return True, ""


def voice_eligible(state: VoiceState, context: VoiceContext,
                   settings: dict) -> tuple[bool, str]:
    """Whether a member currently in a call earns XP this tick."""
    if state.is_bot:
        return False, "bot"
    if not settings.get("voice_enabled"):
        return False, "voice xp disabled"
    if context.is_excluded:
        return False, "channel excluded"
    if settings.get("voice_ignore_afk") and context.is_afk_channel:
        return False, "afk channel"
    if settings.get("voice_require_others") and context.humans_in_channel < 2:
        return False, "alone in channel"
    if settings.get("voice_ignore_muted"):
        # Server mutes count too: being muted by a moderator shouldn't pay either.
        if state.self_muted or state.self_deafened:
            return False, "self muted or deafened"
        if state.server_muted or state.server_deafened:
            return False, "server muted or deafened"
    return True, ""


def apply_daily_cap(amount: int, already_today: int, cap: int) -> int:
    """Trim an award so the member's daily voice total never exceeds the cap.

    A cap of 0 means unlimited.
    """
    amount = max(0, int(amount))
    cap = int(cap or 0)
    if cap <= 0:
        return amount
    remaining = cap - max(0, int(already_today))
    if remaining <= 0:
        return 0
    return min(amount, remaining)


def roles_for_level(level: int, level_roles: list[tuple[int, int]],
                    stack: bool = False) -> tuple[set[int], set[int]]:
    """Work out which reward roles a member should hold at `level`.

    Returns (roles to have, roles to not have). With stacking off — the default,
    matching a tier ladder — only the highest earned role is kept and every lower
    one is taken back.
    """
    earned = sorted((int(lvl), int(rid)) for lvl, rid in level_roles if int(lvl) <= int(level))
    all_reward_roles = {int(rid) for _, rid in level_roles}
    if not earned:
        return set(), all_reward_roles
    if stack:
        keep = {rid for _, rid in earned}
    else:
        keep = {earned[-1][1]}
    return keep, all_reward_roles - keep
