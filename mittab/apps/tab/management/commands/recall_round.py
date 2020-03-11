from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import Round, TabSettings

VIDEO_LINK = 'https://discordapp.com/channels/'

class MyClient(discord.Client):
    async def on_ready(self):
        guild = self.get_guild(TabSettings.get("guild_id"))

        current_round = TabSettings.get("cur_round") - 1

        rounds = Round.objects.filter(round_number=current_round)

        room_overwrite = discord.PermissionOverwrite()
        room_overwrite.view_channel = None
        room_overwrite.speak = None
        room_overwrite.connect = None
        room_overwrite.stream = None
        room_overwrite.read_message_history = None
        room_overwrite.use_voice_activation = None        

        ga_channel = self.get_channel(TabSettings.get("ga_channel_id"))

        for round in rounds:
            voice_channel = None
            text_channel = None

            if not round.room.voice_channel_id == '':
                voice_channel = self.get_channel(int(round.room.voice_channel_id))

            if not round.room.text_channel_id == '':
                text_channel = self.get_channel(int(round.room.text_channel_id))

            debaters = []
            debaters += [deb for deb in round.gov_team.debaters.all()]
            debaters += [deb for deb in round.opp_team.debaters.all()]

            judges = [judge for judge in round.judges.all()]

            users = debaters + judges
            for u in users:
                member = guild.get_member_named(u.discord_id)

                if member:
                    await voice_channel.set_permissions(
                        member,
                        overwrite=room_overwrite
                    )

                    await text_channel.set_permissions(
                        member,
                        overwrite=room_overwrite
                    )
                    
                    try:
                        await member.edit(
                            mute=True,
                            voice_channel=ga_channel
                        )
                    except:
                        pass

        await self.close()

class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
