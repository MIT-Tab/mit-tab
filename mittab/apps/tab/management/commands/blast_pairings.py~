from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import TabSettings

class MyClient(discord.Client):
    async def on_ready(self):
        channel_id = TabSettings.get("ga_channel_id")
        channel = await self.fetch_channel(channel_id)

        invites = await channel.invites()
        invite = invites[0]

        print (invite)

        announcement_channel_id = TabSettings.get("announcement_channel_id")
        announcement_channel = await self.fetch_channel(announcement_channel_id)

        await announcement_channel.send('This is the join link for the tournament!')
        await announcement_channel.send(invite)

        await self.close()

class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
