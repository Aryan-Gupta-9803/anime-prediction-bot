"""The look of every embed the bot posts, copied from the server's own announcement style:
a gold accent bar, a large heading, a bold subheading, and `label :: value` rows.
"""
import discord

from textutil import chunk_lines

ACCENT = discord.Colour(0xE7CA4D)   # the gold bar on the server's existing embeds
PAGE_LIMIT = 3500                   # room left for the heading inside Discord's 4096 cap


def row(label, value) -> str:
    return f"{label} :: {value}"


def pts(n) -> str:
    """'1 pt', '2 pts', '-1 pt'."""
    return f"{n} pt" if str(n) in ("1", "-1") else f"{n} pts"


def embed(title: str, *sections, footer: "str | None" = None) -> discord.Embed:
    """sections are (subheading, lines) pairs; either half may be None.

    # Title
    ### Subheading
    label :: value
    """
    parts = [f"# {title}"]
    for subheading, lines in sections:
        if subheading:
            parts.append(f"### {subheading}")
        if lines:
            parts.append("\n".join(lines))
    result = discord.Embed(description="\n".join(parts), colour=ACCENT)
    if footer:
        result.set_footer(text=footer)
    return result


def paged(title: str, lines: list, *, subheading: "str | None" = None, footer: "str | None" = None) -> list:
    """A long list as one or more embeds that each fit Discord's limits. Every page repeats the
    heading with (n/total); the footer goes on the last page."""
    pages = chunk_lines(lines, PAGE_LIMIT) or [""]
    embeds = []
    for i, page in enumerate(pages, start=1):
        heading = title if len(pages) == 1 else f"{title} ({i}/{len(pages)})"
        embeds.append(
            embed(
                heading,
                (subheading if i == 1 else None, [page] if page else None),
                footer=footer if i == len(pages) else None,
            )
        )
    return embeds
