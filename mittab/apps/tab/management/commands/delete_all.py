from django.core.management.base import BaseCommand

from django.conf import settings

import discord


class MyClient(discord.Client):
    async def on_ready(self):
        for guild in self.guilds:
            await guild.delete()

        await self.close()


class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
