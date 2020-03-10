from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import TabSettings, RoomCheckIn, Room

class MyClient(discord.Client):
    async def on_ready(self):
        guild = self.guilds[0]

        RoomCheckIn.objects.all().delete()

        for room in Room.objects.all():
            channel = await guild.create_category(
                name=room.name
            )

            room.voice_channel_id = ''
            room.text_channel_id = ''
            room.save()

            for round in range(1, TabSettings.get("tot_rounds") + 1):
                RoomCheckIn.objects.create(
                    room=room,
                    round_number=round
                )

            room_voice_channel = await guild.create_voice_channel(
                room.name,
                category=channel
            )

            room_text_channel = await guild.create_text_channel(
                room.name,
                category=channel
            )

            room.voice_channel_id = str(room_voice_channel.id)
            room.text_channel_id = str(room_text_channel.id)
            room.save()

        await self.close()

class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
