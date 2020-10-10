import discord

from .utils import (
    get_or_create_guild,
    handle_message
)
from .roles import (
    update_member_role
)


class MITTabClient(discord.Client):
    guild_id = None
    guild = None

    def __init__(self, tournament_name='defaulttournament', guild_id=None, *args, **kwargs):
        self.tournament_name = tournament_name
        self.guild_id = guild_id
        super().__init__(*args, **kwargs)
    
    async def on_ready(self):
        self.guild = await get_or_create_guild(
            self,
            self.tournament_name
        )

        self.guild_id = self.guild.id

    async def on_message(self, message):
        await update_member_role(self.guild, str(message.author))

        await handle_message(self.guild, message)

    async def on_member_join(self, member):
        await update_member_role(self.guild, str(member))
