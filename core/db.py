"""
Himyar Levels — storage layer.

Every row is keyed by guild_id, so a member's level on one server has nothing to
do with their level on another. Nothing about any single server is hardcoded.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from typing import Any, Optional

import aiosqlite

DEFAULT_DB_PATH = os.environ.get("HIMYAR_DB_PATH", "data/levels.db")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat()


def parse_ts(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS guilds (
    guild_id             INTEGER PRIMARY KEY,
    text_enabled         INTEGER NOT NULL DEFAULT 1,
    voice_enabled        INTEGER NOT NULL DEFAULT 1,
    xp_min               INTEGER NOT NULL DEFAULT 15,
    xp_max               INTEGER NOT NULL DEFAULT 25,
    xp_cooldown          INTEGER NOT NULL DEFAULT 60,
    voice_xp_per_minute  INTEGER NOT NULL DEFAULT 5,
    voice_daily_cap      INTEGER NOT NULL DEFAULT 2000,
    voice_require_others INTEGER NOT NULL DEFAULT 1,
    voice_ignore_muted   INTEGER NOT NULL DEFAULT 1,
    voice_ignore_afk     INTEGER NOT NULL DEFAULT 1,
    xp_rate              REAL    NOT NULL DEFAULT 1.0,
    levelup_mode         TEXT    NOT NULL DEFAULT 'channel',
    levelup_channel_id   INTEGER,
    stack_roles          INTEGER NOT NULL DEFAULT 0,
    staff_role_id        INTEGER,
    role_multipliers     TEXT    NOT NULL DEFAULT '{}',
    event_multiplier     REAL    NOT NULL DEFAULT 1.0,
    event_ends_at        TEXT,
    embed_color          INTEGER NOT NULL DEFAULT 2003199,
    setup_complete       INTEGER NOT NULL DEFAULT 0,
    board_enabled        INTEGER NOT NULL DEFAULT 0,
    board_channel_id     INTEGER,
    board_message_id     INTEGER,
    board_type           TEXT    NOT NULL DEFAULT 'xp',
    board_size           INTEGER NOT NULL DEFAULT 10,
    board_interval       INTEGER NOT NULL DEFAULT 10,
    board_last_at        TEXT,
    created_at           TEXT
);

CREATE TABLE IF NOT EXISTS members (
    guild_id       INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    xp             INTEGER NOT NULL DEFAULT 0,
    text_xp        INTEGER NOT NULL DEFAULT 0,
    voice_xp       INTEGER NOT NULL DEFAULT 0,
    messages       INTEGER NOT NULL DEFAULT 0,
    voice_seconds  INTEGER NOT NULL DEFAULT 0,
    level          INTEGER NOT NULL DEFAULT 0,
    voice_xp_today INTEGER NOT NULL DEFAULT 0,
    voice_day      TEXT,
    created_at     TEXT,
    updated_at     TEXT,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_xp ON members(guild_id, xp DESC);
CREATE INDEX IF NOT EXISTS idx_members_text ON members(guild_id, text_xp DESC);
CREATE INDEX IF NOT EXISTS idx_members_voice ON members(guild_id, voice_xp DESC);

CREATE TABLE IF NOT EXISTS level_roles (
    guild_id INTEGER NOT NULL,
    level    INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);
CREATE INDEX IF NOT EXISTS idx_levelroles_guild ON level_roles(guild_id, level);

CREATE TABLE IF NOT EXISTS excluded_channels (
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS strings (
    guild_id INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    value    TEXT    NOT NULL,
    PRIMARY KEY (guild_id, key)
);
"""

GUILD_DEFAULTS = {
    "text_enabled": 1, "voice_enabled": 1,
    "xp_min": 15, "xp_max": 25, "xp_cooldown": 60,
    "voice_xp_per_minute": 5, "voice_daily_cap": 2000,
    "voice_require_others": 1, "voice_ignore_muted": 1, "voice_ignore_afk": 1,
    "xp_rate": 1.0, "levelup_mode": "channel", "levelup_channel_id": None,
    "stack_roles": 0, "staff_role_id": None, "role_multipliers": "{}",
    "event_multiplier": 1.0, "event_ends_at": None,
    "embed_color": 0x1E90FF, "setup_complete": 0,
    "board_enabled": 0, "board_channel_id": None, "board_message_id": None,
    "board_type": "xp", "board_size": 10, "board_interval": 10,
    "board_last_at": None,
}
GUILD_COLUMNS = set(GUILD_DEFAULTS)

# Columns added after the first release. CREATE TABLE IF NOT EXISTS never touches
# a table that already exists, so an existing database needs them added by hand.
GUILD_MIGRATIONS = {
    "board_enabled": "INTEGER NOT NULL DEFAULT 0",
    "board_channel_id": "INTEGER",
    "board_message_id": "INTEGER",
    "board_type": "TEXT NOT NULL DEFAULT 'xp'",
    "board_size": "INTEGER NOT NULL DEFAULT 10",
    "board_interval": "INTEGER NOT NULL DEFAULT 10",
    "board_last_at": "TEXT",
}


class Database:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    # ─── lifecycle ────────────────────────────────────────────────────────────
    async def connect(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Add any columns introduced after this database was first created."""
        async with self._conn.execute("PRAGMA table_info(guilds)") as cur:
            existing = {row[1] for row in await cur.fetchall()}
        for column, ddl in GUILD_MIGRATIONS.items():
            if column not in existing:
                await self._conn.execute(
                    f"ALTER TABLE guilds ADD COLUMN {column} {ddl}"
                )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() was never awaited")
        return self._conn

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _execute(self, sql: str, params: tuple = ()) -> int:
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cur.lastrowid

    # ─── guild settings ───────────────────────────────────────────────────────
    async def get_guild(self, guild_id: int) -> dict:
        row = await self._fetchone("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        if row is None:
            await self._execute(
                "INSERT OR IGNORE INTO guilds (guild_id, created_at) VALUES (?, ?)",
                (guild_id, iso(utcnow())),
            )
            row = await self._fetchone("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        return row or {"guild_id": guild_id, **GUILD_DEFAULTS}

    async def update_guild(self, guild_id: int, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in GUILD_COLUMNS}
        if not fields:
            return
        await self.get_guild(guild_id)
        if isinstance(fields.get("event_ends_at"), dt.datetime):
            fields["event_ends_at"] = iso(fields["event_ends_at"])
        if isinstance(fields.get("role_multipliers"), dict):
            fields["role_multipliers"] = json.dumps(
                {str(k): float(v) for k, v in fields["role_multipliers"].items()}
            )
        assignments = ", ".join(f"{k} = ?" for k in fields)
        await self._execute(
            f"UPDATE guilds SET {assignments} WHERE guild_id = ?",
            (*fields.values(), guild_id),
        )

    async def all_guilds(self) -> list[dict]:
        return await self._fetchall("SELECT * FROM guilds", ())

    async def get_role_multipliers(self, guild_id: int) -> dict[int, float]:
        settings = await self.get_guild(guild_id)
        raw = settings.get("role_multipliers")
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            data = {}
        return {int(k): float(v) for k, v in data.items()}

    async def set_role_multipliers(self, guild_id: int, mults: dict[int, float]) -> None:
        await self.update_guild(guild_id, role_multipliers=mults)

    async def wipe_guild(self, guild_id: int) -> None:
        for table in ("members", "level_roles", "excluded_channels", "strings", "guilds"):
            await self._execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))

    # ─── members ──────────────────────────────────────────────────────────────
    async def get_member(self, guild_id: int, user_id: int) -> dict:
        row = await self._fetchone(
            "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        if row is None:
            now = iso(utcnow())
            await self._execute(
                "INSERT OR IGNORE INTO members (guild_id, user_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (guild_id, user_id, now, now),
            )
            row = await self._fetchone(
                "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
            )
        return row or {"guild_id": guild_id, "user_id": user_id, "xp": 0, "text_xp": 0,
                       "voice_xp": 0, "messages": 0, "voice_seconds": 0, "level": 0,
                       "voice_xp_today": 0, "voice_day": None}

    async def peek_member(self, guild_id: int, user_id: int) -> Optional[dict]:
        """Read without creating a row — used by /rank so looking someone up
        doesn't invent a record for a member who has never spoken."""
        return await self._fetchone(
            "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )

    async def add_text_xp(self, guild_id: int, user_id: int, amount: int) -> dict:
        await self.get_member(guild_id, user_id)
        await self._execute(
            "UPDATE members SET xp = xp + ?, text_xp = text_xp + ?, messages = messages + 1, "
            "updated_at = ? WHERE guild_id = ? AND user_id = ?",
            (int(amount), int(amount), iso(utcnow()), guild_id, user_id),
        )
        return await self.get_member(guild_id, user_id)

    async def add_voice_xp(self, guild_id: int, user_id: int, amount: int,
                           seconds: int, day: str) -> dict:
        """Add voice XP and roll the daily counter over when the date changes."""
        member = await self.get_member(guild_id, user_id)
        today_total = int(member.get("voice_xp_today") or 0)
        if member.get("voice_day") != day:
            today_total = 0
        await self._execute(
            "UPDATE members SET xp = xp + ?, voice_xp = voice_xp + ?, "
            "voice_seconds = voice_seconds + ?, voice_xp_today = ?, voice_day = ?, "
            "updated_at = ? WHERE guild_id = ? AND user_id = ?",
            (int(amount), int(amount), int(seconds), today_total + int(amount), day,
             iso(utcnow()), guild_id, user_id),
        )
        return await self.get_member(guild_id, user_id)

    async def voice_xp_today(self, guild_id: int, user_id: int, day: str) -> int:
        member = await self.peek_member(guild_id, user_id)
        if not member or member.get("voice_day") != day:
            return 0
        return int(member.get("voice_xp_today") or 0)

    async def set_level(self, guild_id: int, user_id: int, level: int) -> None:
        await self._execute(
            "UPDATE members SET level = ? WHERE guild_id = ? AND user_id = ?",
            (int(level), guild_id, user_id),
        )

    async def set_xp(self, guild_id: int, user_id: int, xp: int, level: int,
                     *, text_xp: Optional[int] = None) -> None:
        await self.get_member(guild_id, user_id)
        if text_xp is None:
            await self._execute(
                "UPDATE members SET xp = ?, level = ?, updated_at = ? "
                "WHERE guild_id = ? AND user_id = ?",
                (max(0, int(xp)), int(level), iso(utcnow()), guild_id, user_id),
            )
        else:
            await self._execute(
                "UPDATE members SET xp = ?, text_xp = ?, level = ?, updated_at = ? "
                "WHERE guild_id = ? AND user_id = ?",
                (max(0, int(xp)), max(0, int(text_xp)), int(level), iso(utcnow()),
                 guild_id, user_id),
            )

    async def reset_member(self, guild_id: int, user_id: int) -> None:
        await self._execute(
            "DELETE FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )

    async def reset_guild_members(self, guild_id: int) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM members WHERE guild_id = ?", (guild_id,)
        )
        count = int(row["n"]) if row else 0
        await self._execute("DELETE FROM members WHERE guild_id = ?", (guild_id,))
        return count

    async def leaderboard(self, guild_id: int, column: str = "xp",
                          limit: int = 10, offset: int = 0) -> list[dict]:
        if column not in ("xp", "text_xp", "voice_xp"):
            column = "xp"
        return await self._fetchall(
            f"SELECT * FROM members WHERE guild_id = ? AND {column} > 0 "
            f"ORDER BY {column} DESC, user_id ASC LIMIT ? OFFSET ?",
            (guild_id, int(limit), int(offset)),
        )

    async def member_count(self, guild_id: int, column: str = "xp") -> int:
        if column not in ("xp", "text_xp", "voice_xp"):
            column = "xp"
        row = await self._fetchone(
            f"SELECT COUNT(*) AS n FROM members WHERE guild_id = ? AND {column} > 0",
            (guild_id,),
        )
        return int(row["n"]) if row else 0

    async def rank_of(self, guild_id: int, user_id: int, column: str = "xp") -> Optional[int]:
        if column not in ("xp", "text_xp", "voice_xp"):
            column = "xp"
        member = await self.peek_member(guild_id, user_id)
        if not member or int(member.get(column) or 0) <= 0:
            return None
        row = await self._fetchone(
            f"SELECT COUNT(*) AS n FROM members WHERE guild_id = ? AND {column} > ?",
            (guild_id, int(member[column])),
        )
        return (int(row["n"]) if row else 0) + 1

    async def all_members(self, guild_id: int) -> list[dict]:
        return await self._fetchall(
            "SELECT * FROM members WHERE guild_id = ?", (guild_id,)
        )

    # ─── level role rewards ───────────────────────────────────────────────────
    async def set_level_role(self, guild_id: int, level: int, role_id: int) -> None:
        await self._execute(
            "INSERT INTO level_roles (guild_id, level, role_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, level) DO UPDATE SET role_id = excluded.role_id",
            (guild_id, int(level), int(role_id)),
        )

    async def remove_level_role(self, guild_id: int, level: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "DELETE FROM level_roles WHERE guild_id = ? AND level = ?",
                (guild_id, int(level)),
            )
            await self.conn.commit()
            return cur.rowcount > 0

    async def get_level_roles(self, guild_id: int) -> list[dict]:
        return await self._fetchall(
            "SELECT level, role_id FROM level_roles WHERE guild_id = ? ORDER BY level ASC",
            (guild_id,),
        )

    # ─── excluded channels ────────────────────────────────────────────────────
    async def add_excluded_channel(self, guild_id: int, channel_id: int) -> None:
        await self._execute(
            "INSERT OR IGNORE INTO excluded_channels (guild_id, channel_id) VALUES (?, ?)",
            (guild_id, int(channel_id)),
        )

    async def remove_excluded_channel(self, guild_id: int, channel_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "DELETE FROM excluded_channels WHERE guild_id = ? AND channel_id = ?",
                (guild_id, int(channel_id)),
            )
            await self.conn.commit()
            return cur.rowcount > 0

    async def get_excluded_channels(self, guild_id: int) -> set[int]:
        rows = await self._fetchall(
            "SELECT channel_id FROM excluded_channels WHERE guild_id = ?", (guild_id,)
        )
        return {int(r["channel_id"]) for r in rows}

    # ─── editable strings ─────────────────────────────────────────────────────
    async def get_strings(self, guild_id: int) -> dict[str, str]:
        rows = await self._fetchall(
            "SELECT key, value FROM strings WHERE guild_id = ?", (guild_id,)
        )
        return {r["key"]: r["value"] for r in rows}

    async def set_string(self, guild_id: int, key: str, value: str) -> None:
        await self._execute(
            "INSERT INTO strings (guild_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
            (guild_id, key, value),
        )

    async def reset_string(self, guild_id: int, key: str) -> None:
        await self._execute(
            "DELETE FROM strings WHERE guild_id = ? AND key = ?", (guild_id, key)
        )
