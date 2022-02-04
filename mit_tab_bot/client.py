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

    def __init__(self, guild_id=None, *args, **kwargs):
        self.guild_id = guild_id
        kwargs['chunk_guilds_at_startup'] = False

        print ('initializing client')

        super().__init__(*args, **kwargs)
    
    async def on_ready(self):
        print ('in on ready')

        self.guild = await get_or_create_guild(
            self,
        )

        print ('built guild' + str(self.guild))

    async def on_message(self, message):
        if not self.guild:
            return

        try:
            await update_member_role(self.guild, str(message.author))
        except:
            pass

        await handle_message(self.guild, message)

    async def on_member_join(self, member):
        await update_member_role(self.guild, str(member))
