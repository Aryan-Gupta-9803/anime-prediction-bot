import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import backup
import exporter
import storage
from common import ConfirmView, reply
from config import BACKUP_EVERY_DAYS
from permissions import is_admin

log = logging.getLogger("predictionbot")


class Backups(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.autosave.start()

    async def cog_unload(self):
        self.autosave.cancel()

    async def save_if_due(self, reason: str = "scheduled"):
        """Posts a backup when something changed and the last one is at least BACKUP_EVERY_DAYS old.
        Milestones (a week scored, season end, reset, restore, /backup) post their own, so this is
        the safety net for everything else. Never raises."""
        try:
            if not await storage.aio.is_dirty():
                return
            last = await storage.aio.last_backup_at()
            if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(days=BACKUP_EVERY_DAYS):
                return
            await backup.post_backup(self.bot, reason)
            backup.state.last_error = None
        except backup.BackupError as e:
            if str(e) != backup.state.last_error:
                log.warning("Scheduled backup failed: %s", e)
            backup.state.last_error = str(e)
        except Exception:
            log.exception("Scheduled backup crashed")

    @tasks.loop(hours=1)
    async def autosave(self):
        await self.save_if_due()

    @autosave.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="backup", description="Post a backup file to the backup channel right now")
    @is_admin()
    async def backup_now(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            message = await backup.post_backup(interaction.client, "manual", keep=True)
        except backup.BackupError as e:
            await reply(interaction, f"The backup failed. {e}")
            return
        await reply(interaction, f"Backup posted: {message.jump_url}. Manual backups are never pruned.")

    @app_commands.command(
        name="restore", description="Restore everything from a backup (the newest one unless you attach a file)"
    )
    @is_admin()
    @app_commands.describe(file="A backup .db file. Leave empty to use the newest backup that has data")
    async def restore(self, interaction: discord.Interaction, file: discord.Attachment = None):
        await interaction.response.defer(ephemeral=True)
        try:
            if file is not None:
                if file.size > backup.MAX_BACKUP_BYTES:
                    await reply(interaction, "That file is too large to be one of my backups.")
                    return
                data = await file.read()
                info = await asyncio.to_thread(storage.inspect_snapshot, data)
                source = f"your file `{file.filename}`"
            else:
                found = await backup.latest_valid_backup(interaction.client, require_data=True)
                if found is None:
                    await reply(interaction, "I found no usable backups with data in the backup channel.")
                    return
                message, data, info = found
                source = f"the newest backup that has data ({backup.when(message)})"
        except backup.BackupError as e:
            await reply(interaction, str(e))
            return
        except storage.InvalidBackup as e:
            await reply(interaction, f"I can't use that file: {e}.")
            return
        except discord.HTTPException as e:
            await reply(interaction, f"Couldn't download that file ({e.status}).")
            return

        current = await storage.aio.data_summary()
        view = ConfirmView(interaction.user.id, "Replace everything")
        await interaction.edit_original_response(
            content=(
                f"Restore from {source}? It holds {info['weeks']} week(s), {info['submissions']} submission(s) "
                f"and {info['titles']} title(s), saved {info['snapshot_at']}.\n"
                f"This **replaces everything** currently stored ({current['weeks']} week(s), "
                f"{current['submissions']} submission(s)). I'll post a backup of the current data first, so "
                "you can undo this with another `/restore`."
            ),
            view=view,
        )
        await view.wait()
        if not view.confirmed:
            if view.timed_out:
                await interaction.edit_original_response(content="Timed out. Nothing changed.", view=None)
            return

        try:
            safety = await backup.post_backup(interaction.client, "before restore", keep=True)
        except backup.BackupError as e:
            await interaction.edit_original_response(
                content=f"I couldn't save a backup of the current data first, so **nothing was changed**. {e}"
            )
            return
        await asyncio.to_thread(storage.restore_snapshot, data)
        await interaction.edit_original_response(
            content=(
                f"Restored from {source}. What was there before is saved in [this backup]({safety.jump_url}). "
                "Run `/help-admin` to check everything looks right."
            )
        )

    @app_commands.command(name="export", description="Get all the data as an Excel file (only you can see it)")
    @is_admin()
    async def export(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        tables = await asyncio.to_thread(storage.dump_tables)
        data = await asyncio.to_thread(exporter.build_xlsx, tables)
        name = f"prediction-export-{datetime.now(timezone.utc):%Y%m%d}.xlsx"
        await interaction.followup.send(
            "Everything as of now. This is a read-only copy for looking at; use `/restore` with a backup "
            "file to roll back.",
            file=discord.File(io.BytesIO(data), filename=name),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Backups(bot))
