from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import (
    Judge,
    Debater,
    TabSettings,
    CheckIn
)


ADMIN_USERS = [
    'Talos#5956',
    'pdvt#0397'
]


class MyClient(discord.Client):
    async def get_role(self, guild, role_name):
        roles = await guild.fetch_roles()
        for role in roles:
            if role.name == role_name:
                return role

        return None

    async def configure_users(self, guild):
        members = guild.fetch_members(limit=None)

        async for member in members:
            print ('Searching for %s' % (member,))
            if str(member) in ADMIN_USERS:
                print ('Found as ADMIN')
                await member.add_roles(await self.get_role(guild, 'admin'))

            judge = Judge.objects.filter(discord_id=str(member)).first()
            if judge:
                print ('Found as JUDGE')
                await member.add_roles(await self.get_role(guild, 'judges'))

                await member.edit(nick=judge.name.upper())

            debater = Debater.objects.filter(discord_id=str(member)).first()
            if debater:
                print ('Found as DEBATER')
                await member.add_roles(await self.get_role(guild, 'debaters'))

                await member.edit(nick=debater.name.upper())

    async def on_ready(self):
        guild = self.get_guild(TabSettings.get("guild_id"))

        await self.configure_users(guild)

        CheckIn.objects.all().delete()
        for debater in Debater.objects.all():
            for team in debater.team_set.all():
                team.checked_in = False
                team.save()

        judges_here = []
        debaters_here = []

        members = guild.fetch_members(limit=None)

        async for member in members:
            judges = list(Judge.objects.filter(discord_id=str(member)).all())
            judges_here += judges
            
            debaters = list(Debater.objects.filter(discord_id=str(member)).all())
            debaters_here += debaters

        for judge in judges_here:
            for round in range(1, TabSettings.objects.get(key="tot_rounds").value + 1):
                CheckIn.objects.create(
                    judge=judge,
                    round_number=round
                )

        for debater in debaters_here:
            for team in debater.team_set.all():
                team.checked_in = True
                team.save()

        print (judges_here)
        print (debaters_here)

        await self.close()


class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
