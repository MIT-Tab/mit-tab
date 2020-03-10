from django.core.management.base import BaseCommand

from django.conf import settings

from mittab.apps.tab.models import TabSettings

import discord

import asyncio


class MyClient(discord.Client):
    async def on_ready(self):
        guild = self.get_guild(TabSettings.get("guild_id"))
        await guild.delete()

        await self.close()


class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
