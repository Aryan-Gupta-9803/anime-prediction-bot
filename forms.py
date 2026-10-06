"""The ten-box ranked form, used for members' picks and for the host's real chart.

Discord limits a form to 5 text boxes, so this is two linked forms (1-5, then 6-10). The first one
is validated immediately; nothing is saved until the second step completes (or the member chooses
"save, keep 6-10 as they are"). Boxes may be left empty, but anything typed must be a title on the
season list and must not repeat another box.
"""
import logging

import discord

import storage
from common import SafeModal, reply
from validation import validate_slots

log = logging.getLogger("predictionbot")

BOXES = 5


class SlotModal(SafeModal):
    """Five text boxes: positions 1-5 or 6-10."""

    def __init__(self, form: "RankedForm", part: int):
        super().__init__(title=form.modal_title(part))
        self.form, self.part = form, part
        first = 1 + (part - 1) * BOXES
        defaults = form.prefill[first - 1 : first - 1 + BOXES]
        self.inputs = []
        for offset in range(BOXES):
            box = discord.ui.TextInput(
                label=form.label(first + offset),
                default=defaults[offset] or None,
                required=False,
                max_length=100,
                placeholder="Title from /anime-list, or leave empty",
            )
            self.inputs.append(box)
            self.add_item(box)

    async def on_submit(self, interaction: discord.Interaction):
        entries = [box.value.strip() for box in self.inputs]
        if self.part == 1:
            await self.form.part_one_submitted(interaction, entries)
        else:
            await self.form.part_two_submitted(interaction, entries)


class ContinueView(discord.ui.View):
    def __init__(self, form: "RankedForm", user_id: int):
        super().__init__(timeout=600)
        self.form, self.user_id = form, user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await reply(interaction, "That form belongs to someone else.")
            return False
        return True

    async def on_timeout(self) -> None:
        await self.form.expire()

    @discord.ui.button(label="Next: 6-10", style=discord.ButtonStyle.primary)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.form.open_second(interaction)

    @discord.ui.button(label="Save, keep 6-10 as they are", style=discord.ButtonStyle.secondary)
    async def save_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.form.save_now(interaction)


class RankedForm:
    """State for one person filling in the two-step form.

    finish(interaction, canonical) is called once the full, valid 10-slot list is ready and is
    responsible for saving it and answering the user. precheck(interaction) -> bool runs after the
    first step and must reply itself when it returns False.
    """

    def __init__(self, *, noun: str, prefill: list, source_note: str, reject_prefix: str, finish, precheck=None):
        self.noun = noun
        self.prefill = (list(prefill) + [""] * 10)[:10]
        self.source_note = source_note
        self.reject_prefix = reject_prefix
        self.finish = finish
        self.precheck = precheck
        self.part_one: list = []
        self.first_interaction: "discord.Interaction | None" = None

    def label(self, pos: int) -> str:
        return f"{self.noun} #{pos}" + (" (top of the chart)" if pos == 1 else "")

    def modal_title(self, part: int) -> str:
        span = "1-5" if part == 1 else "6-10"
        half = self.prefill[:5] if part == 1 else self.prefill[5:]
        base = f"{self.noun}s {span}"
        if self.source_note and any(half):
            return f"{base} ({self.source_note})"
        return f"{base} of 10"

    def _rejected(self, errors: list) -> str:
        return self.reject_prefix + "\n" + "\n".join(f"- {e}" for e in errors)

    async def open_first(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SlotModal(self, 1))

    async def part_one_submitted(self, interaction: discord.Interaction, entries: list):
        errors, canonical = validate_slots(entries, await storage.aio.get_anime_list(), 1, self.noun)
        if errors:
            await reply(interaction, self._rejected(errors))
            return
        if self.precheck is not None and not await self.precheck(interaction):
            return

        self.part_one, self.first_interaction = canonical, interaction
        shown = "\n".join(f"{i}. {t or '(empty)'}" for i, t in enumerate(canonical, start=1))
        keep = [t for t in self.prefill[5:] if t]
        tail = f" It currently has {len(keep)} entr{'y' if len(keep) == 1 else 'ies'} in 6-10." if keep else ""
        await interaction.response.send_message(
            f"{self.noun}s 1-5 are good:\n{shown}\n\nContinue with 6-10, or save now and keep 6-10 as they are.{tail}",
            view=ContinueView(self, interaction.user.id),
            ephemeral=True,
        )

    async def open_second(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SlotModal(self, 2))

    async def part_two_submitted(self, interaction: discord.Interaction, entries: list):
        await self._complete(interaction, self.part_one + entries)

    async def save_now(self, interaction: discord.Interaction):
        await self._complete(interaction, self.part_one + self.prefill[5:])

    async def _complete(self, interaction: discord.Interaction, entries: list):
        errors, canonical = validate_slots(entries, await storage.aio.get_anime_list(), 1, self.noun)
        if not errors and not any(canonical):
            errors.append("Every box is empty. Fill in at least one.")
        if errors:
            await reply(interaction, self._rejected(errors))
            return
        await self.finish(interaction, canonical)

    async def expire(self):
        if self.first_interaction is None:
            return
        try:
            await self.first_interaction.edit_original_response(
                content="That form timed out. Run the command again.", view=None
            )
        except discord.HTTPException:
            pass
