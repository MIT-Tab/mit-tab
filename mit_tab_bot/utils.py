import discord

from .channels import (
    clear_channels,
    create_channels,
    get_channel,
)

from .roles import (
    delete_roles,
    create_roles,
    get_member,
    has_role,
    update_member_role,
    get_role
)

from .invites import (
    create_invite,
    clear_invites,
    get_or_create_invite
)

from .api import (
    get_judge,
    get_debater,
    get_rooms,
    get_rounds
)

ROUND_PERMISSIONS = discord.PermissionOverwrite(
    view_channel=True,
    send_messages=True,
    speak=True,
    connect=True,
    stream=True,
    read_message_history=True,
    use_voice_activation=True
)

END_OF_ROUND_PERMISSIONS = discord.PermissionOverwrite(
    view_channel=None,
    send_messages=None,
    speak=None,
    connect=None,
    stream=None,
    read_message_history=None,
    use_voice_activation=None
)

VIDEO_LINK = 'https://discordapp.com/channels/'

async def get_or_create_guild(client, tournament_name='defaulttournament'):
    print (client.guild_id)
    to_return = None
    if client.guild_id:
        to_return = client.get_guild(int(client.guild_id))

    if not to_return:
        for guild in client.guilds:
            if guild.name == tournament_name:
                if to_return is not None:
                    await guild.delete()
                to_return = guild

    created = False

    if not to_return:
        guild = await client.create_guild(
            tournament_name,
            region=discord.VoiceRegion.us_east
        )

        to_return = guild
        created = True

    guild = to_return

    r = await get_role(guild, 'debaters')
    if not r:
        await create_roles(guild)

    return guild

async def handle_message(guild, message):
    if hasattr(message.channel, 'name') and (message.channel.name.startswith('Room') or message.channel.name.startswith('room')):
        return

    if not message.content.startswith('!'):
        return

    member = await get_member(guild, str(message.author))

    if has_role(member, 'staff') or has_role(member, 'superadmin'):
        for member in message.mentions:
            await update_member_role(guild, str(member))

        room_category = await get_channel(guild, '[ROOMS]')
        
        if room_category:
            for channel in room_category.channels:
                await channel.delete()            

        if message.content.startswith('!rooms'):
            rooms = get_rooms()

            for i in range(3):
                room_category = await get_channel(guild, '[ROOMS-%s]' % (i,))
                
                if room_category:
                    for channel in room_category.channels:
                        await channel.delete()
                else:
                    room_category = await guild.create_category('[ROOMS-%s]' % (i,))

            j = 0
            for room in rooms:
                room_category = await get_channel(guild, '[ROOMS-%s]' % (j % 3,))
                await room_category.create_text_channel(room['name'])
                await room_category.create_voice_channel(room['name'])
                j += 1

        if message.content.startswith('!setup'):
            await clear_channels(guild)
            #await delete_roles(guild)
            
            await create_roles(guild)
            await create_channels(guild)
            
            await clear_invites(await get_channel(guild, 'GA'))
            print (await create_invite(await get_channel(guild, 'GA')))

        if message.content.startswith('!send'):
            PERMISSIONS = ROUND_PERMISSIONS            
            for member in message.mentions:
                room = ' '.join(message.content.split('|')[1:]).strip()
                room_text_channel = await get_channel(guild, room.replace(' ', '-').lower())
                room_voice_channel = await get_channel(guild, room)
                
                await room_text_channel.set_permissions(
                    member,
                    overwrite=PERMISSIONS
                )
                
                await room_voice_channel.set_permissions(
                    member,
                    overwrite=PERMISSIONS
                )
                
                try:
                    await member.edit(
                        mute=False,
                        voice_channel=room_voice_channel
                    )
                except:
                    pass
                

        if message.content.startswith('!round'):
            round_number = message.content.split(' ')[1]
            rounds = get_rounds(round_number)

            action = message.content.split(' ')[2]

            if action == 'blast':
                announcements = await get_channel(guild, 'announcements')

                await announcements.send('Pairings for ROUND %s' % (round_number))

                for round in rounds:
                    await announcements.send('Gov: %s | Opp: %s | Judges: %s' % (
                        round['gov_team']['name'],
                        round['opp_team']['name'],
                        ', '.join([judge['name'] for judge in round['judges']])
                    ))

                await message.channel.send('Blasting!')
            elif action == 'send' or action == 'end':
                ga = await get_channel(guild, 'GA')
                await message.channel.send('Sending!')
                for round in rounds:
                    await message.channel.send('Working on %s' % (round['room']['name'],))

                    members = [await get_member(guild, debater['discord_id'])
                               for debater in round['gov_team']['debaters']]
                    members += [await get_member(guild, debater['discord_id'])
                                for debater in round['opp_team']['debaters']]

                    members += [await get_member(guild, judge['discord_id'])
                                for judge in round['judges']]

                    room_text_channel = await get_channel(guild, round['room']['name'].replace(' ', '-').lower())
                    room_voice_channel = await get_channel(guild, round['room']['name'])

                    members = [member for member in members if member]

                    PERMISSIONS = ROUND_PERMISSIONS if action == 'send' else END_OF_ROUND_PERMISSIONS

                    for member in members:
                        await room_text_channel.set_permissions(
                            member,
                            overwrite=PERMISSIONS
                        )

                        await room_voice_channel.set_permissions(
                            member,
                            overwrite=PERMISSIONS
                        )

                        try:
                            if action == 'send':
                                await member.edit(
                                    mute=False,
                                    voice_channel=room_voice_channel
                                )
                            else:
                                await member.edit(
                                    voice_channel=ga
                                )
                        except:
                            pass

                    if action == 'send':
                        await room_text_channel.send('Welcome to %s for round %s! %s %s' % (
                            round['room']['name'],
                            round_number,
                            (await get_role(guild, 'debaters')).mention,
                            (await get_role(guild, 'judges')).mention
                        ))
                        
                        await room_text_channel.send('Judge: %s\nGov: %s\nOpp: %s\n' % (
                            ', '.join([judge['name'] for judge in round['judges']]),
                            round['gov_team']['name'],
                            round['opp_team']['name']
                        ))
                        
                        await room_text_channel.send('Please click the following link to send you to the video.  You must already be in the voice channel -- you should have been auto-moved.\n<%s%s/%s>' % (VIDEO_LINK, guild.id, room_voice_channel.id))
                        
                        await room_text_channel.send('If the link does not work, please ensure you are in the voice channel whose name matches this text channel')
                await message.channel.send('Done!')

    if message.content.startswith('!spectate'):
        PERMISSIONS = ROUND_PERMISSIONS
        room = ' '.join(message.content.split(' ')[1:]).strip()
        room_text_channel = await get_channel(guild, room.replace(' ', '-').lower())
        room_voice_channel = await get_channel(guild, room)

        await room_text_channel.set_permissions(
            member,
            overwrite=PERMISSIONS
        )
        
        await room_voice_channel.set_permissions(
            member,
            overwrite=PERMISSIONS
        )
        
        try:
            await member.edit(
                mute=False,
                voice_channel=room_voice_channel
            )
        except:
            pass

    if message.content.startswith('!invite'):
        await message.channel.send(
            await get_or_create_invite(
                await get_channel(guild, 'GA')
            )
        )
    elif message.content.startswith('!code'):
        if not has_role(member, 'judges'):
            return

        judge = get_judge(str(message.author))
        
        if not judge:
            return
        else:
            await member.send(
                'Your ballot code is: %s' % (
                judge['ballot_code'],
                )
            )
