from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import TabSettings, Round

class MyClient(discord.Client):
    async def on_ready(self):
        current_round = TabSettings.get("cur_round") - 1

        announcement_channel_id = TabSettings.get("announcement_channel_id")
        announcement_channel = await self.fetch_channel(announcement_channel_id)

        await announcement_channel.send('Round %s Blast' % (current_round,))

        for round in Round.objects.filter(round_number=current_round).all():
            await announcement_channel.send('Gov: %s Opp: %s Judge: %s' % (round.gov_team,
                                                                           round.opp_team,
                                                                           ', '.join([str(j) for j in round.judges.all()])))
            print (round)

        await self.close()

class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
