from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import Round, TabSettings

VIDEO_LINK = 'https://discordapp.com/channels/'

class MyClient(discord.Client):
    async def on_ready(self):
        guild = self.guilds[0]

        current_round = TabSettings.get("cur_round")

        rounds = Round.objects.filter(round_number=current_round)

        room_overwrite = discord.PermissionOverwrite()
        room_overwrite.view_channel = True
        room_overwrite.speak = True
        room_overwrite.connect = True
        room_overwrite.stream = True
        room_overwrite.read_message_history = True

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
                    try:
                        await member.move_to(voice_channel)
                    except:
                        pass
                    
                    await voice_channel.set_permissions(
                        member,
                        overwrite=room_overwrite
                    )

                    await text_channel.set_permissions(
                        member,
                        overwrite=room_overwrite
                    )

            await text_channel.send('Welcome to %s for round %s!' % (
                round.room,
                current_round
            ))

            await text_channel.send('Judge: %s\nGov: %s\nOpp: %s\n' % (
                round.judges.all()[0].name,
                round.gov_team.name,
                round.opp_team.name
            ))

            await text_channel.send('Please click the following link to send you to the video.  You must already be in the voice channel -- you should have been auto-moved.\n%s%s/%s' % (VIDEO_LINK, guild.id, voice_channel.id))
                                            

        await self.close()

class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
