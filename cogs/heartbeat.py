"""Check-in DMs from the bot to its owner: one when it starts, one every 24 hours, one when it shuts down cleanly.

A bot can only report that it is alive. If it dies, nothing can be sent, so the real warning is a check-in that
doesn't arrive: if 24 hours pass with no message, the bot (or its host) is down.
"""
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

import backup
import event_types
import storage
import style
from config import ALERT_USER_ID, HEARTBEAT_DM

log = logging.getLogger("predictionbot")

SHUTDOWN_TIMEOUT = 5          # seconds the goodbye DM may take; the host is waiting to stop us


def _stamp(iso: str) -> str:
    """A saved ISO time as a Discord timestamp that reads as '3 days ago'."""
    return f"<t:{int(datetime.fromisoformat(iso).timestamp())}:R>"


class Heartbeat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.started_at = datetime.now(timezone.utc)
        self.last_sent_at = None
        self.last_error = None
        self.recipient_name = None
        self._recipient = None
        self._first = True

    # ---- the schedule ------------------------------------------------------------------------------------------------

    async def cog_load(self):
        if HEARTBEAT_DM:
            self.daily.start()

    def cog_unload(self):
        self.daily.cancel()

    @tasks.loop(hours=24)
    async def daily(self):
        # The first run happens as soon as the bot is ready ("start now"); after that, every 24 hours.
        kind, self._first = ("started" if self._first else "daily"), False
        await self.check_in(kind)

    @daily.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    async def notify_shutdown(self):
        """Tells the owner the bot is stopping. Never raises and never holds up the shutdown for long."""
        if not HEARTBEAT_DM:
            return
        try:
            await asyncio.wait_for(self.check_in("shutdown"), timeout=SHUTDOWN_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - the goodbye is a courtesy
            log.warning("Shutdown DM skipped: %s", e)

    # ---- the message ---------------------------------------------------------------------------------------------------

    async def _find_recipient(self):
        if self._recipient is None:
            if ALERT_USER_ID:
                self._recipient = await self.bot.fetch_user(ALERT_USER_ID)
            else:
                info = await self.bot.application_info()
                self._recipient = info.team.owner if getattr(info, "team", None) else info.owner
            self.recipient_name = str(self._recipient)
        return self._recipient

    async def _details(self) -> list:
        lines = [f"Up since <t:{int(self.started_at.timestamp())}:R>."]
        try:
            week = await storage.aio.get_current_week()
            if week is None:
                lines.append("No event is open right now.")
            else:
                label = f"{style.week_label(week['week_id'])} ({event_types.type_label(week['type'])})"
                state = "locked" if storage.week_is_locked(week) else "open"
                count = await storage.aio.count_submissions(week)
                lines.append(f"{label} is {state} with {count} submission(s).")
            last = await storage.aio.last_backup_at()
            lines.append(f"Last backup: {_stamp(last)}." if last else "No backup has been posted yet.")
            if backup.state.last_error:
                lines.append(f"The last backup attempt failed: {backup.state.last_error}")
        except Exception as e:  # noqa: BLE001 - a status line must never stop the check-in
            lines.append(f"(Couldn't read the status: {e})")
        return lines

    async def _compose(self, kind: str) -> str:
        if kind == "shutdown":
            return (
                "**The prediction bot is shutting down.** The host sent it a stop signal. "
                "If you didn't stop it yourself, check the host's console.\n" + "\n".join(await self._details())
            )
        headline = (
            "**The prediction bot just started.**" if kind == "started" else "**Daily check-in: the prediction bot is online.**"
        )
        tail = "\nIf a check-in doesn't arrive within a day, the bot or its host is down." if kind == "daily" else ""
        return headline + "\n" + "\n".join(await self._details()) + tail

    async def check_in(self, kind: str = "daily") -> bool:
        """Sends one check-in DM. Returns whether it was delivered; a failure is remembered (and shown in /help-admin)
        instead of raised."""
        try:
            recipient = await self._find_recipient()
            await recipient.send(await self._compose(kind))
        except discord.Forbidden:
            self.last_error = "Discord wouldn't let me DM you. Allow direct messages from server members, and don't block the bot."
            log.warning("Check-in DM failed: %s", self.last_error)
            return False
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("Check-in DM failed: %s", self.last_error)
            return False
        self.last_sent_at, self.last_error = datetime.now(timezone.utc), None
        return True

    # ---- /help-admin ------------------------------------------------------------------------------------------------------

    def status_line(self) -> str:
        if not HEARTBEAT_DM:
            return "[ok] Check-in DMs: switched off (HEARTBEAT_DM=off)."
        if self.last_error:
            return f"[todo] Check-in DM: {self.last_error}"
        if self.last_sent_at is None:
            return "[ok] Check-in DM: the first one goes out as soon as the bot is ready, then every 24 hours."
        return (
            f"[ok] Check-in DM to {self.recipient_name}: last sent <t:{int(self.last_sent_at.timestamp())}:R>, then every 24 hours."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Heartbeat(bot))
