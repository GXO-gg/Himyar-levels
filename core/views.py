"""Himyar Levels — small interactive views (leaderboard paging, confirmations)."""

from __future__ import annotations

from typing import Callable, Optional

import discord


class LeaderboardView(discord.ui.View):
    """Paging buttons. Short-lived by design — a stale leaderboard page is worse
    than one that simply expires and asks you to run the command again."""

    def __init__(self, render: Callable, page: int, pages: int, board: str,
                 author_id: int, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.render = render
        self.page = page
        self.pages = pages
        self.board = board
        self.author_id = author_id
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Run `/leaderboard` yourself to page through it.", ephemeral=True
            )
            return False
        return True

    async def _show(self, interaction: discord.Interaction) -> None:
        self._sync()
        embed = await self.render(interaction.guild, self.board, self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        await self._show(interaction)


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ This confirmation isn't yours.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()
