from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import TabSettings, Round

class MyClient(discord.Client):
    async def on_ready(self):
        guild = self.get_guild(TabSettings.get("guild_id"))        
        current_round = TabSettings.get("cur_round") - 1

        announcement_channel_id = TabSettings.get("announcement_channel_id")
        announcement_channel = await self.fetch_channel(announcement_channel_id)

        await announcement_channel.send('Round %s Missing Ballots' % (current_round,))

        for round in Round.objects.filter(round_number=current_round).filter(victor=Round.NONE).all():
            judge = guild.get_member_named(round.chair.discord_id)
            await announcement_channel.send('%s -- round between %s and %s %s' % (
                ', '.join([str(j) for j in round.judges.all()]),
                round.gov_team,
                round.opp_team,
                '(' + judge.mention + ')' if judge else ''
            ))

        await self.close()

class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
