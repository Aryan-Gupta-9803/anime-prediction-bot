"""Offline test setup: a temporary SQLite file and fake Discord objects. No network, no tokens needed."""
import asyncio
import os
import sys
import tempfile
import uuid

BACKUP_CHANNEL = 777000111222333444
os.environ.update(
    DISCORD_TOKEN="test-token",
    GUILD_ID="999000111222333444",
    BACKUP_CHANNEL_ID=str(BACKUP_CHANNEL),
    BACKUP_KEEP="5",
    ADMIN_ROLE_NAME="Event Host",
    DATA_DIR=tempfile.mkdtemp(prefix="prediction-tests-"),
)
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

import backup
import storage
from cogs.backups import Backups
from cogs.help import Help
from cogs.season import Season
from cogs.user import UserCommands
from cogs.weeks import Weeks
from fakes import FakeChannel, FakeClient, FakeGuild, FakeInteraction, FakeUser


class Env:
    """A fresh database plus the cogs, a guild, three channels and a few users."""

    def __init__(self):
        self.db_path = os.path.join(os.environ["DATA_DIR"], f"{uuid.uuid4().hex}.db")
        storage.open_db(self.db_path)
        storage.seed_default_templates()
        backup.state.last_error = None

        self.guild = FakeGuild(999000111222333444)
        self.client = FakeClient(self.guild)
        self.announce = self.client.add_channel(FakeChannel(self.guild, "announcements"))
        self.picks = self.client.add_channel(FakeChannel(self.guild, "picks"))
        self.backups_channel = self.client.add_channel(
            FakeChannel(self.guild, "bot-backups", channel_id=BACKUP_CHANNEL)
        )

        self.season, self.weeks, self.user_cog, self.help, self.backups = (
            Season(self.client), Weeks(self.client), UserCommands(self.client), Help(self.client),
            Backups(self.client),
        )
        self.admin = FakeUser("host", manage_guild=True)
        self.alice, self.bob, self.carol = FakeUser("alice"), FakeUser("bob"), FakeUser("carol")

    def inter(self, user, command=None):
        return FakeInteraction(self.client, self.guild, user, command)

    async def pick(self, user, ranks):
        """/pick with all ten rank fields: a title for each, and (leave empty) for an empty entry, so it
        replaces the member's picks the way filling in a whole form would. Returns the interaction."""
        from validation import CLEAR
        values = ((list(ranks) + [""] * 10)[:10])
        i = self.inter(user)
        await self.user_cog.pick.callback(self.user_cog, i, *[v or CLEAR for v in values])
        return i

    async def submit(self, inter, text):
        """Opens whatever form `inter` produced, 'types' text, and submits it as a fresh interaction.
        The ten-box ranked form takes one title per line (empty lines leave a box empty) and is
        driven through both steps; the result is the last interaction, where the outcome shows up."""
        modal = inter.modal
        assert modal is not None, f"no form was opened; got: {inter.all_text!r}"
        if hasattr(modal, "inputs"):
            return await self.submit_ranked(inter, text.split("\n") if isinstance(text, str) else list(text))
        modal.entries._value = text
        out = self.inter(inter.user)
        await modal.on_submit(out)
        return out

    async def submit_ranked(self, inter, entries, keep_second_half=False):
        """Fills boxes 1-5, presses Next (or 'Save, keep 6-10'), fills boxes 6-10. Returns the final interaction,
        or the first step's if that step was refused."""
        entries = (list(entries) + [""] * 10)[:10]
        modal = inter.modal
        for box, text in zip(modal.inputs, entries[:5]):
            box._value = text
        first = self.inter(inter.user)
        await modal.on_submit(first)
        view = first.sent[-1].view if first.sent else None
        if view is None:
            return first
        click = self.inter(inter.user)
        if keep_second_half:
            await view.children[1].callback(click)
            return click
        await view.children[0].callback(click)
        second = click.modal
        for box, text in zip(second.inputs, entries[5:]):
            box._value = text
        last = self.inter(inter.user)
        await second.on_submit(last)
        return last

    async def setup_channels(self):
        i = self.inter(self.admin)
        await self.season.setup_channels.callback(self.season, i, self.announce, self.picks)
        return i

    async def start_season(self, titles):
        i = self.inter(self.admin)
        await self.season.start.callback(self.season, i)
        return await self.submit(i, "\n".join(titles))

    async def play_week(self):
        """A complete scored week with two members, for tests that need data to back up or restore."""
        await self.setup_channels()
        await self.start_season(TITLES)
        await self.weeks.event_template.callback(self.weeks, self.inter(self.admin), "Standard", None, "")
        for user in (self.alice, self.bob):
            await self.pick(user, RESULTS)
        await self.weeks.lock.callback(self.weeks, self.inter(self.admin))
        i = self.inter(self.admin)
        await self.weeks.end_week.callback(self.weeks, i, "")
        await self.submit(i, "\n".join(RESULTS))

    async def click(self, view, index, user=None):
        """Presses a button on a confirmation view; returns the button interaction."""
        button_inter = self.inter(user or self.admin)
        await view.children[index].callback(button_inter)
        return button_inter

    async def start_command(self, coro_factory):
        """Runs a command that waits for a confirmation; returns (task, interaction) once the
        confirmation is on screen (or the command finished without asking)."""
        i = self.inter(self.admin)
        task = asyncio.create_task(coro_factory(i))
        for _ in range(300):
            if task.done() or i.original_views or any(s.view for s in i.sent):
                break
            await asyncio.sleep(0.01)
        return task, i

    @staticmethod
    def confirm_view(i):
        if i.original_views:
            return i.original_views[-1]
        return next(s.view for s in i.sent if s.view)


TITLES = [
    "Frieren: Beyond Journey's End", "One Piece", "Jujutsu Kaisen", "Chainsaw Man", "Spy x Family",
    "Demon Slayer", "Mob Psycho 100", "Vinland Saga", "Bleach", "Dandadan", "Re:Zero",
    "2.43: Seiin High School Boys Volleyball Team", "86",
]

RESULTS = ["One Piece", "Frieren: Beyond Journey's End", "Bleach", "Dandadan", "Re:Zero",
           "Jujutsu Kaisen", "Chainsaw Man", "Spy x Family", "Demon Slayer", "Vinland Saga"]


@pytest.fixture
def env():
    return Env()
