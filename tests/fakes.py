"""In-memory stand-ins for discord.py objects, faithful to the calls the bot makes."""
import datetime
import itertools

import discord


# ---- discord fakes -----------------------------------------------------------------------------

_ids = itertools.count(1234567890123456001)


class FakePerms:
    def __init__(self, **overrides):
        self.view_channel = self.send_messages = self.embed_links = self.read_message_history = self.attach_files = True
        self.manage_guild = False
        for k, v in overrides.items():
            setattr(self, k, v)


class FakeRole:
    def __init__(self, name):
        self.name = name


class FakeUser:
    def __init__(self, name, manage_guild=False, roles=()):
        self.id = next(_ids)
        self.name = name
        self.display_name = name.title()
        self.guild_permissions = FakePerms(manage_guild=manage_guild)
        self.roles = [FakeRole(r) for r in roles]

    def __str__(self):
        return self.name


class FakeGuild:
    def __init__(self, gid):
        self.id = gid
        self.me = FakeUser("predictionbot")
        self.filesize_limit = 10 * 1024 * 1024


class FakeAttachment:
    def __init__(self, filename, data):
        self.filename = filename
        self._data = data
        self.size = len(data)

    async def read(self):
        return self._data


class FakeMessage:
    def __init__(self, channel, embed=None, content=None, file=None, author=None):
        self.id = next(_ids)
        self.channel = channel
        self.embed = embed
        self.content = content
        self.author = author or channel.client_user
        self.created_at = datetime.datetime.now(datetime.timezone.utc)
        self.attachments = []
        if file is not None:
            self.attachments.append(FakeAttachment(file.filename, file.fp.read()))
        self.deleted = False

    @property
    def jump_url(self):
        return f"https://discord.com/channels/{self.channel.guild.id}/{self.channel.id}/{self.id}"

    async def edit(self, *, embed=None, **kw):
        self.embed = embed

    async def delete(self):
        self.deleted = True
        self.channel.messages.pop(self.id, None)


class FakeChannel(discord.abc.Messageable):
    def __init__(self, guild, name, perms=None, forbid_send=False, channel_id=None, client_user=None):
        self.id = channel_id or next(_ids)
        self.guild = guild
        self.name = name
        self.mention = f"<#{self.id}>"
        self.messages = {}
        self.sent = []
        self._perms = perms or FakePerms()
        self.forbid_send = forbid_send
        self.forbid_history = False
        self.client_user = client_user or guild.me

    async def _get_channel(self):
        return self

    def permissions_for(self, member):
        return self._perms

    async def send(self, content=None, *, embed=None, file=None, **kw):
        if self.forbid_send:
            raise discord.Forbidden(type("R", (), {"status": 403, "reason": "forbidden"})(), "Missing Permissions")
        msg = FakeMessage(self, embed=embed, content=content, file=file)
        self.messages[msg.id] = msg
        self.sent.append((content, embed, msg))
        return msg

    async def fetch_message(self, message_id):
        if message_id not in self.messages:
            raise discord.NotFound(type("R", (), {"status": 404, "reason": "nf"})(), "Unknown Message")
        return self.messages[message_id]

    async def history(self, limit=100, **kw):
        if self.forbid_history:
            raise discord.Forbidden(type("R", (), {"status": 403, "reason": "forbidden"})(), "Missing Access")
        for message in sorted(self.messages.values(), key=lambda m: m.id, reverse=True)[:limit]:
            yield message

    def plant(self, filename, data, author=None):
        """Puts a file in the channel as if someone had posted it."""
        class _F:
            pass
        f = _F()
        f.filename = filename
        f.fp = __import__("io").BytesIO(data)
        msg = FakeMessage(self, file=f, author=author or self.client_user)
        self.messages[msg.id] = msg
        return msg


class FakeClient:
    def __init__(self, guild):
        self.guild = guild
        self.user = guild.me
        self.channels = {}

    def add_channel(self, channel):
        self.channels[channel.id] = channel
        return channel

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        if channel_id not in self.channels:
            raise discord.NotFound(type("R", (), {"status": 404, "reason": "nf"})(), "Unknown Channel")
        return self.channels[channel_id]

    def get_cog(self, name):
        return None


class Sent:
    def __init__(self, kind, content, embed, view, ephemeral, file=None):
        self.kind, self.content, self.embed, self.view, self.ephemeral = kind, content, embed, view, ephemeral
        self.file = file

    @property
    def text(self):
        parts = []
        if self.content:
            parts.append(self.content)
        if self.embed is not None:
            parts.append((self.embed.title or "") + "\n" + (self.embed.description or ""))
            for f in self.embed.fields:
                parts.append(f"{f.name}\n{f.value}")
        return "\n".join(parts)


class FakeResponse:
    def __init__(self, inter):
        self.inter = inter
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, content=None, *, embed=None, view=None, ephemeral=False, **kw):
        assert not self._done, "responded twice"
        self._done = True
        self.inter.sent.append(Sent("response", content, embed, view, ephemeral))

    async def defer(self, ephemeral=False, **kw):
        assert not self._done, "responded twice"
        self._done = True
        self.inter.deferred = True

    async def send_modal(self, modal):
        assert not self._done, "responded twice"
        self._done = True
        self.inter.modal = modal

    async def edit_message(self, content=None, view=None, **kw):
        self._done = True
        self.inter.sent.append(Sent("edit", content, None, view, True))


class FakeFollowup:
    def __init__(self, inter):
        self.inter = inter

    async def send(self, content=None, *, embed=None, view=None, ephemeral=False, file=None, **kw):
        self.inter.sent.append(Sent("followup", content, embed, view, ephemeral, file))


class FakeInteraction:
    def __init__(self, client, guild, user, command=None):
        self.client = client
        self.guild = guild
        self.guild_id = guild.id
        self.user = user
        self.command = command
        self.sent = []
        self.deferred = False
        self.modal = None
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)
        self.original_edits = []
        self.original_views = []

    async def edit_original_response(self, *, content=None, view=None, **kw):
        self.original_edits.append(content)
        self.original_views.append(view)

    @property
    def last(self):
        return self.sent[-1]

    @property
    def all_text(self):
        return "\n".join(s.text for s in self.sent)
