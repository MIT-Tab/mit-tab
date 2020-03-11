import os

from django.core.management.base import BaseCommand

from django.conf import settings

import discord

from mittab.apps.tab.models import Judge, Debater, TabSettings

class MyClient(discord.Client):
    async def delete_invites(self, channel):
        invites = await channel.invites()
        for invite in invites:
            await invite.delete()

    async def delete_roles(self, guild):
        roles = await guild.fetch_roles()
        for role in roles:
            if not role.name == '@everyone':
                await role.delete()

    async def get_role(self, guild, role_name):
        roles = await guild.fetch_roles()
        for role in roles:
            if role.name == role_name:
                return role

        return None

    async def create_roles(self, guild):
        admin_permission = discord.Permissions(
            administrator=True
        )

        tournament_administrator_role = await guild.create_role(
            name='admin',
            permissions=admin_permission,
            colour=discord.Colour.purple(),
            hoist=True,
            mentionable=True
        )

        judge_permission = discord.Permissions.none()
        judge_permission.update(add_reactions=True)

        debater_permission = discord.Permissions.none()
        debater_permission.update(add_reactions=True)

        judge_role = await guild.create_role(
            name='judges',
            permissions=judge_permission,
            colour=discord.Colour.green(),
            hoist=True,
            mentionable=True
        )

        debater_role = await guild.create_role(
            name='debaters',
            permissions=debater_permission,
            colour=discord.Colour.blue(),
            hoist=True,
            mentionable=True
        )

        everyone = await self.get_role(guild, '@everyone')
        everyone_permissions = everyone.permissions

        everyone_permissions = discord.Permissions.none()
        
        await everyone.edit(permissions=everyone_permissions)

    async def create_channels(self, guild):
        general_channels = await guild.create_category('[GENERAL]')

        ga_overwrite = discord.PermissionOverwrite()
        ga_overwrite.connect = True
        ga_overwrite.view_channel = True
        
        ga_channel = await guild.create_voice_channel(
            'GA',
            category=general_channels
        )

        TabSettings.set("ga_channel_id", ga_channel.id)

        await ga_channel.set_permissions(
            await self.get_role(guild, '@everyone'),
            overwrite=ga_overwrite
        )

        announcement_overwrite = discord.PermissionOverwrite()
        announcement_overwrite.view_channel = True
        announcement_overwrite.read_message_history = True

        announcement_channel = await guild.create_text_channel(
            'Announcements',
            category=general_channels
        )

        TabSettings.set("announcement_channel_id", announcement_channel.id)        

        await announcement_channel.set_permissions(
            await self.get_role(guild, '@everyone'),
            overwrite=announcement_overwrite
        )

        await announcement_channel.send('If any information looks wrong, please contact any user who is an ADMIN ASAP')

        staff_category = await guild.create_category('[TOURNAMENT ADMINISTRATION]')

        main_tab = await guild.create_voice_channel(
            'Tabroom',
            category=staff_category
        )

        private_room_one = await guild.create_voice_channel(
            'Private Room 1',
            category=staff_category
        )

        private_room_two = await guild.create_voice_channel(
            'Private Room 2',
            category=staff_category
        )

        private_room_three = await guild.create_voice_channel(
            'Private Room 3',
            category=staff_category
        )

        private_text = await guild.create_text_channel(
            'Private Text Channel',
            category=staff_category
        )

        invite = await ga_channel.create_invite(max_age=0,
                                                max_uses=0)
    
    async def on_ready(self):
        guild = await self.create_guild(
            os.environ.get("TOURNAMENT_NAME", 'testing-tournament'),
            region=discord.VoiceRegion.us_east
        )

        TabSettings.set("guild_id", guild.id)

        for channel in guild.channels:
            await channel.delete()

        await self.delete_invites(guild)
        await self.delete_roles(guild)

        await self.create_roles(guild)
        await self.create_channels(guild)

        print('Logged on as {0}!'.format(self.user))

        await self.close()


class Command(BaseCommand):
    def handle(self, *args, **options):
        client = MyClient()
        client.run(settings.BOT_TOKEN)
