import asyncio
import logging
import signal

import discord
from discord import app_commands
from discord.ext import commands

import backup
import storage
from common import reply
from config import DISCORD_TOKEN, GUILD_ID

log = logging.getLogger("predictionbot")

EXTENSIONS = ["cogs.season", "cogs.weeks", "cogs.user", "cogs.help", "cogs.backups"]


class PredictionTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != GUILD_ID:
            await reply(interaction, "This bot only works inside its own server.")
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            message = str(error) or "You can't use this command."
        else:
            log.exception("Unhandled error in /%s", getattr(interaction.command, "name", "?"), exc_info=error)
            message = (
                "Something went wrong, and it may not have gone through. "
                "Try once more; if it keeps happening, tell a host."
            )
        await reply(interaction, message)


class PredictionBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            tree_cls=PredictionTree,
            help_command=None,
        )

    async def setup_hook(self):
        # Hosts stop bots with SIGTERM; turn it into a normal shutdown so close() can post a final backup.
        try:
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.close()))
        except (NotImplementedError, RuntimeError):
            pass  # not supported on Windows; Ctrl+C still shuts down cleanly

        try:
            outcome = await backup.prepare_storage(self)
        except backup.BackupError as e:
            log.error("Startup stopped: %s", e)
            raise
        log.info(
            {
                "existing": "Opened the existing database.",
                "restored": "Fresh install: restored all data from the newest backup.",
                "new": "Fresh install with no backups yet: starting empty.",
            }[outcome]
        )
        try:
            await backup.get_backup_channel(self)
        except backup.BackupError as e:
            log.warning("Backups won't work until this is fixed: %s", e)

        for extension in EXTENSIONS:
            await self.load_extension(extension)

        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d slash commands to the server", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s", self.user)
        if self.get_guild(GUILD_ID) is None:
            log.warning("The bot isn't in the server with GUILD_ID=%s. Invite it there first.", GUILD_ID)
        for guild in self.guilds:
            if guild.id != GUILD_ID:
                log.warning("Also in %s, but commands there are refused (locked to GUILD_ID=%s).", guild.name, GUILD_ID)

    async def close(self):
        # Last chance to get recent changes into the backup channel before the process ends.
        try:
            if await asyncio.to_thread(storage.is_dirty):
                await asyncio.wait_for(backup.post_backup(self, "shutdown"), timeout=15)
        except Exception as e:
            log.warning("Final backup skipped: %s", e)
        await super().close()
        storage.close_db()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    PredictionBot().run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
