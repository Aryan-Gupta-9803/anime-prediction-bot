import discord
from discord import app_commands

from config import ADMIN_ROLE_NAME


class NotAdmin(app_commands.CheckFailure):
    pass


class WrongServer(app_commands.CheckFailure):
    pass


def has_admin_rights(user) -> bool:
    perms = getattr(user, "guild_permissions", None)
    if perms and perms.manage_guild:
        return True
    return any(r.name == ADMIN_ROLE_NAME for r in getattr(user, "roles", []))


def is_admin():
    def predicate(interaction: discord.Interaction) -> bool:
        if has_admin_rights(interaction.user):
            return True
        raise NotAdmin(
            f"Only members with the **{ADMIN_ROLE_NAME}** role or the Manage Server permission can use this command."
        )

    return app_commands.check(predicate)
