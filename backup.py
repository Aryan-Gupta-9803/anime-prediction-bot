"""Posting backups to the private Discord channel, and restoring from them.

A backup is a consistent snapshot of the database attached to a message in BACKUP_CHANNEL_ID.
"""
import asyncio
import io
import logging
import os
from datetime import datetime, timezone

import discord

import storage
from config import BACKUP_CHANNEL_ID, BACKUP_KEEP

log = logging.getLogger("predictionbot")

BACKUP_PREFIX = "prediction-backup-"
MAX_BACKUP_BYTES = 25 * 1024 * 1024
HISTORY_LIMIT = 200


class _State:
    """Shared with /help-admin: why the last automatic backup failed, if it did."""

    last_error: "str | None" = None


state = _State()


class BackupError(Exception):
    """Something about the backup channel or file prevented the operation. The message is
    written for a host to read."""


def _attachment(message: discord.Message):
    for a in message.attachments:
        if a.filename.startswith(BACKUP_PREFIX) and a.filename.endswith(".db"):
            return a
    return None


def when(message: discord.Message) -> str:
    return f"{message.created_at:%Y-%m-%d %H:%M} UTC"


async def get_backup_channel(client) -> discord.abc.Messageable:
    channel = client.get_channel(BACKUP_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(BACKUP_CHANNEL_ID)
        except discord.HTTPException as e:
            raise BackupError(
                f"I can't see the backup channel <#{BACKUP_CHANNEL_ID}> (is BACKUP_CHANNEL_ID right, and can "
                f"I view it?). Discord said: {e.text or e.status}"
            ) from e
    return channel


async def post_backup(client, reason: str, keep: bool = False) -> discord.Message:
    """Uploads a fresh snapshot to the backup channel and records it as backed up.
    keep=True marks it as a milestone (before a reset or restore, or a manual backup) that
    automatic pruning never deletes."""
    channel = await get_backup_channel(client)
    data, revision = await asyncio.to_thread(storage.snapshot)
    summary = await asyncio.to_thread(storage.data_summary)

    limit = getattr(getattr(channel, "guild", None), "filesize_limit", 10 * 1024 * 1024)
    if len(data) > limit:
        raise BackupError(
            f"The backup is {len(data) / 1e6:.1f} MB, over this server's {limit / 1e6:.0f} MB upload limit."
        )

    tag = "-keep" if keep else ""
    name = f"{BACKUP_PREFIX}{datetime.now(timezone.utc):%Y%m%d-%H%M%S}{tag}.db"
    try:
        message = await channel.send(
            content=f"Backup ({reason}): {summary['weeks']} week(s), {summary['submissions']} submission(s).",
            file=discord.File(io.BytesIO(data), filename=name),
        )
    except discord.Forbidden as e:
        raise BackupError(
            "I'm not allowed to post files in the backup channel. It needs View Channel, Send Messages, "
            "Attach Files and Read Message History for me."
        ) from e
    except discord.HTTPException as e:
        raise BackupError(f"Discord rejected the backup upload ({e.status}: {e.text}).") from e

    await asyncio.to_thread(storage.mark_backed_up, revision)
    await _prune(client, channel)
    return message


async def try_backup(client, reason: str, keep: bool = False) -> "str | None":
    """Best-effort backup for milestones (a week scored, season end). Returns an error message
    for the caller to mention, or None on success; never raises."""
    try:
        await post_backup(client, reason, keep=keep)
    except BackupError as e:
        state.last_error = str(e)
        return str(e)
    state.last_error = None
    return None


async def _prune(client, channel):
    """Keeps only the newest BACKUP_KEEP backups this bot posted."""
    me = getattr(client.user, "id", None)
    try:
        mine = [
            m
            async for m in channel.history(limit=HISTORY_LIMIT)
            if m.author.id == me and _attachment(m) and not _attachment(m).filename.endswith("-keep.db")
        ]
        for old in mine[BACKUP_KEEP:]:
            await old.delete()
    except discord.HTTPException as e:
        log.warning("Couldn't prune old backups: %s", e)


async def iter_backups(client):
    """Backup messages in the channel, newest first."""
    channel = await get_backup_channel(client)
    try:
        async for message in channel.history(limit=HISTORY_LIMIT):
            if _attachment(message):
                yield message
    except discord.Forbidden as e:
        raise BackupError(
            "I can't read the backup channel's history (I need Read Message History there)."
        ) from e


async def read_backup(message: discord.Message) -> bytes:
    attachment = _attachment(message)
    if attachment is None:
        raise BackupError("That message has no backup file.")
    if attachment.size > MAX_BACKUP_BYTES:
        raise BackupError("That backup file is too large to restore.")
    try:
        return await attachment.read()
    except discord.HTTPException as e:
        raise BackupError(f"Couldn't download the backup file ({e.status}).") from e


async def latest_valid_backup(client, require_data: bool = False):
    """(message, bytes, summary) for the newest backup that passes validation, or None.
    Damaged or unrelated files are skipped rather than blocking a restore. With require_data,
    backups of an empty season are skipped too: after a /reset the automatic backup of the empty
    state is the newest file, and someone running /restore wants the season they just cleared."""
    async for message in iter_backups(client):
        try:
            data = await read_backup(message)
            info = await asyncio.to_thread(storage.inspect_snapshot, data)
        except (BackupError, storage.InvalidBackup) as e:
            log.warning("Skipping backup from %s: %s", when(message), e)
            continue
        if require_data and not any(info[k] for k in ("weeks", "submissions", "titles")):
            continue
        return message, data, info
    return None


async def prepare_storage(client, path: "str | None" = None) -> str:
    """Opens the database at startup. A brand-new database first looks for a backup to restore,
    so a host that wiped its disk comes back with the data. Returns 'existing', 'restored' or 'new'."""
    fresh = await asyncio.to_thread(storage.open_db, path)
    if not fresh:
        await asyncio.to_thread(storage.seed_default_templates)
        return "existing"

    try:
        found = await latest_valid_backup(client)
    except BackupError as e:
        # Don't leave behind an empty database: the next start would think it's an established one.
        await asyncio.to_thread(storage.discard_db)
        raise BackupError(
            "This looks like a fresh install, and I can't check the backup channel for data to restore. "
            f"Fix that first so nothing is lost. {e}"
        ) from e

    if found is None:
        await asyncio.to_thread(storage.seed_default_templates)
        return "new"

    message, data, info = found
    await asyncio.to_thread(storage.restore_snapshot, data)
    await asyncio.to_thread(storage.seed_default_templates)
    log.info("Restored data from the backup of %s (%s weeks, %s submissions).", when(message), info["weeks"], info["submissions"])
    return "restored"
