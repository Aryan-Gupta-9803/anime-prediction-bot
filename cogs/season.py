import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown as esc

import backup
import storage
from common import (
    ConfirmView,
    REQUIRED_ANNOUNCE_PERMS,
    REQUIRED_PICKS_PERMS,
    SafeModal,
    get_text_channel,
    missing_permissions,
    reply,
    send_paged,
)
from permissions import is_admin
from style import row
from validation import parse_lines


class StartModal(SafeModal, title="Season anime list"):
    entries = discord.ui.TextInput(
        label="One title per line (no numbering)",
        style=discord.TextStyle.paragraph,
        placeholder="Frieren: Beyond Journey's End\nOne Piece\n...",
        required=True,
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        titles = [t[:100] for t in parse_lines(self.entries.value)]
        distinct = {storage.normalize(t) for t in titles}
        if len(distinct) < 10:
            await reply(
                interaction,
                f"Need at least 10 different titles for top-10 picks to work, but found {len(distinct)}. "
                "Nothing was saved; run `/start` again.",
            )
            return

        await interaction.response.defer(ephemeral=True)
        saved = await storage.aio.replace_anime_list(titles)
        dropped = len(titles) - saved

        notes = []
        channel = await get_text_channel(interaction.client, "announcement_channel_id")
        if channel is None:
            notes.append("I couldn't post the list: the announcement channel isn't reachable. Re-run `/setup`.")
        else:
            ordered = await storage.aio.get_anime_list()
            await send_paged(
                channel,
                "New season: eligible anime",
                [row(i, esc(t)) for i, t in enumerate(ordered, start=1)],
                subheading="Pick from these titles with /pick. See them again any time with /anime-list.",
                content="Hello everyone! The **new season** is here, and these are the anime you can predict!",
            )

        text = f"Season started with **{saved}** titles"
        text += f" ({dropped} duplicate line(s) ignored)." if dropped else "."
        text += (
            " Open the first week with `/event-template` or `/new-event`. "
            "Missed a title or made a typo? Use `/add-anime` / `/remove-anime`."
        )
        if len(self.entries.value) >= 3990:
            notes.append("Your list was right at the form's size limit, so it may have been cut off. Check `/anime-list`.")
        await reply(interaction, "\n".join([text, *notes]))


async def anime_autocomplete(interaction: discord.Interaction, current: str):
    needle = current.lower()
    titles = [t for t in await storage.aio.get_anime_list() if needle in t.lower()]
    return [app_commands.Choice(name=t[:100], value=t[:100]) for t in titles][:25]


class Season(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Choose the announcement and user-picks channels")
    @is_admin()
    @app_commands.describe(
        announcement_channel="Where week announcements, locks and leaderboards get posted",
        user_channel="Where each member's picks get posted",
    )
    async def setup_channels(
        self,
        interaction: discord.Interaction,
        announcement_channel: discord.TextChannel,
        user_channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)
        me = interaction.guild.me
        problems = []
        for label, channel, needed in (
            ("announcements", announcement_channel, REQUIRED_ANNOUNCE_PERMS),
            ("picks", user_channel, REQUIRED_PICKS_PERMS),
        ):
            missing = missing_permissions(channel, me, needed)
            if missing:
                problems.append(f"- {channel.mention} ({label}): I'm missing **{', '.join(missing)}**.")

        await storage.aio.set_config("announcement_channel_id", str(announcement_channel.id))
        await storage.aio.set_config("user_channel_id", str(user_channel.id))

        text = (
            f"Announcements will post in {announcement_channel.mention}; "
            f"members' picks will post in {user_channel.mention}."
        )
        if problems:
            text += "\n\nFix these permissions before opening a week:\n" + "\n".join(problems)
        await reply(interaction, text)

    @app_commands.command(name="start", description="Start a new season: set the valid anime list and announce it")
    @is_admin()
    async def start(self, interaction: discord.Interaction):
        if not await storage.aio.get_config("announcement_channel_id"):
            await reply(interaction, "Run `/setup` first so I know which channels to use.")
            return
        existing = await storage.aio.get_anime_list()
        if existing:
            await reply(
                interaction,
                f"A season is already running with {len(existing)} titles. Use `/add-anime` or `/remove-anime` "
                "to adjust the list, or `/reset` to archive this season and start a new one.",
            )
            return
        await interaction.response.send_modal(StartModal())

    @app_commands.command(name="add-anime", description="Add one anime to the current season's valid list")
    @is_admin()
    @app_commands.describe(title="Title exactly as members should type it")
    async def add_anime(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 100]):
        added = await storage.aio.add_anime(title)
        await reply(interaction, f"Added **{title.strip()}**." if added else f"**{title.strip()}** is already on the list.")

    @app_commands.command(name="remove-anime", description="Remove one anime from the current season's valid list")
    @is_admin()
    @app_commands.describe(title="Title to remove")
    @app_commands.autocomplete(title=anime_autocomplete)
    async def remove_anime(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 100]):
        removed = await storage.aio.remove_anime(title)
        if removed:
            await reply(interaction, f"Removed **{removed}**. Picks already submitted with it are unaffected.")
        else:
            await reply(interaction, f"**{title}** isn't on the list.")

    @app_commands.command(name="reset", description="Save a backup, then clear this season so you can start a new one")
    @is_admin()
    async def reset(self, interaction: discord.Interaction):
        summary = await storage.aio.data_summary()
        view = ConfirmView(interaction.user.id, "Back up and reset")
        await interaction.response.send_message(
            "This posts a backup file of everything to the backup channel, then clears the current season "
            f"({summary['weeks']} week(s), {summary['submissions']} submission(s)) so you can run `/start` again. "
            "Channel setup and scoring templates are kept, and `/restore` can bring the season back. Continue?",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if not view.confirmed:
            if view.timed_out:
                await interaction.edit_original_response(content="Timed out. Nothing changed.", view=None)
            return

        try:
            message = await backup.post_backup(interaction.client, "before reset", keep=True)
        except backup.BackupError as e:
            await interaction.edit_original_response(
                content=f"I couldn't save the backup, so **nothing was cleared**. {e}"
            )
            return
        await storage.aio.reset_season()
        await interaction.edit_original_response(
            content=(
                f"Done. The old season is saved in [this backup]({message.jump_url}); `/restore` brings it back. "
                "Run `/start` for the next season."
            )
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Season(bot))
