import logging

import discord
from discord import app_commands

import storage
import style

log = logging.getLogger("predictionbot")

REQUIRED_PICKS_PERMS = ("view_channel", "send_messages", "embed_links", "read_message_history")
REQUIRED_ANNOUNCE_PERMS = ("view_channel", "send_messages", "embed_links")
REQUIRED_BACKUP_PERMS = ("view_channel", "send_messages", "attach_files", "read_message_history")


async def reply(interaction: discord.Interaction, content=None, **kwargs):
    """Responds whether or not the interaction has already been acknowledged."""
    kwargs.setdefault("ephemeral", True)
    if isinstance(content, str) and len(content) > 1990:
        content = content[:1987] + "..."
    if interaction.response.is_done():
        return await interaction.followup.send(content, **kwargs)
    return await interaction.response.send_message(content, **kwargs)


class SafeModal(discord.ui.Modal):
    """Modal whose failures tell the user something instead of leaving them on 'thinking...'."""

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Modal %s failed", type(self).__name__, exc_info=error)
        await reply(
            interaction,
            "Something went wrong while saving that, and it may not have gone through. "
            "Please try once more; if it keeps happening, tell a host.",
        )


async def get_text_channel(client: discord.Client, key: str):
    """The configured announcement/picks channel, or None if unset, deleted, or not visible."""
    raw = await storage.aio.get_config(key)
    if not raw or not str(raw).isdigit():
        return None
    channel = client.get_channel(int(raw))
    return channel if isinstance(channel, discord.abc.Messageable) else None


def missing_permissions(channel, member, needed) -> list:
    perms = channel.permissions_for(member)
    return [name.replace("_", " ") for name in needed if not getattr(perms, name)]


async def post_or_edit_embed(channel, existing_message_id, embed: discord.Embed):
    """Edits the user's previous embed in place when it still exists, otherwise posts a new
    one. Returns the id of the message holding the embed."""
    if existing_message_id and str(existing_message_id).isdigit():
        try:
            message = await channel.fetch_message(int(existing_message_id))
            await message.edit(embed=embed)
            return int(existing_message_id)
        except discord.NotFound:
            pass
    return (await channel.send(embed=embed)).id


async def send_paged(channel, title: str, lines: list, footer: str = "", subheading=None, content=None):
    """Posts a long list as one or more embeds so nothing hits Discord's size limits. `content` is the
    greeting line shown above the first embed."""
    for i, page in enumerate(style.paged(title, lines, subheading=subheading, footer=footer or None)):
        await channel.send(content=content if i == 0 else None, embed=page)


def ranking(totals: dict) -> list:
    """[(rank, user_id, points)] best first, with ties sharing a rank (1, 1, 3)."""
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    result, previous, rank = [], None, 0
    for position, (user_id, points) in enumerate(ordered, start=1):
        if points != previous:
            rank, previous = position, points
        result.append((rank, user_id, points))
    return result


def season_totals(scores: list) -> tuple:
    totals, names = {}, {}
    for s in scores:
        uid = str(s["user_id"])
        totals[uid] = totals.get(uid, 0) + storage.score_points(s)
        names[uid] = s["username"]
    return totals, names


async def week_autocomplete(interaction: discord.Interaction, current: str):
    weeks = [w for w in await storage.aio.list_weeks() if current.lower() in str(w["week_id"]).lower()]
    return [app_commands.Choice(name=f"{w['week_id']} ({w['type']})", value=w["week_id"]) for w in weeks][-25:]


class ConfirmView(discord.ui.View):
    """Confirm / Cancel buttons that only the host who ran the command can press."""

    def __init__(self, user_id: int, confirm_label: str):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.confirmed = False
        self.timed_out = False
        self.confirm.label = confirm_label

    async def on_timeout(self) -> None:
        self.timed_out = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await reply(interaction, "That confirmation belongs to someone else.")
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        await interaction.response.edit_message(content="Working...", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelled. Nothing changed.", view=None)
        self.stop()
