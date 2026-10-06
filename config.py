import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        sys.exit(f"Missing required setting {name}. Copy .env.example to .env (or set it in your host's dashboard).")
    return value


def _numeric_id(name: str, what: str) -> int:
    value = _required(name)
    if not value.isdigit():
        sys.exit(f"{name} must be {what}'s numeric ID (enable Developer Mode in Discord, right-click it, Copy ID).")
    return int(value)


DISCORD_TOKEN = _required("DISCORD_TOKEN")
# This bot keeps one season in one database, so it is locked to a single server.
GUILD_ID = _numeric_id("GUILD_ID", "your server")
# Private channel where the bot posts a backup file after changes. It is also where a fresh
# install looks for a backup to restore, so it must be configured here, not inside the database.
BACKUP_CHANNEL_ID = _numeric_id("BACKUP_CHANNEL_ID", "the backup channel")

DATA_DIR = os.environ.get("DATA_DIR", "data")
os.environ["DATA_DIR"] = DATA_DIR
ADMIN_ROLE_NAME = os.environ.get("ADMIN_ROLE_NAME", "").strip() or "Event Host"   # a blank value means the default

try:
    BACKUP_KEEP = max(5, int(os.environ.get("BACKUP_KEEP", "50")))
except ValueError:
    sys.exit("BACKUP_KEEP must be a whole number (how many backup files to keep in the channel).")

try:
    # An automatic backup is posted at most this often (and only if something changed). Weekly
    # scoring, season end, reset, restore and /backup always post one regardless.
    BACKUP_EVERY_DAYS = max(1, int(os.environ.get("BACKUP_EVERY_DAYS", "7")))
except ValueError:
    sys.exit("BACKUP_EVERY_DAYS must be a whole number of days.")
