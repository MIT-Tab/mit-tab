import discord

from .invites import clear_invites
from .roles import get_role


CHANNELS = [
    {
        'name': '[GENERAL]',
        'channels': [
            {
                'name': 'ga',
                'type': 'text',
                'permissions': {
                    '@everyone': discord.PermissionOverwrite(
                        view_channel=True,
                        read_message_history=True,
                        send_messages=True,
                        add_reactions=True
                    ),
                }
            },
            {
                'name': 'GA',
                'type': 'voice',
                'permissions': {
                    '@everyone': discord.PermissionOverwrite(
                        connect=True,
                        view_channel=True
                    ),
                    'eos': discord.PermissionOverwrite(
                        speak=True,
                    )
                }
            },
            {
                'name': 'instructions',
                'type': 'text',
                'permissions': {
                    '@everyone': discord.PermissionOverwrite(
                        view_channel=True,
                        read_message_history=True,
                        add_reactions=False
                    )
                },
                'initial': [
                    'This is a list of the instructions for interacting with the APDA MIT-TAB [BOT]',
                    'All commands can be issued by typing in a text channel, or DMing the bot.  To DM the bot, click on the bot\'s icon in the right pane and sending it a message.  Sometimes it will issue a response in the channel the command was issued in, and sometimes it will DM you a response.  Anything communicated anywhere will automatically update your permissions as they should be in tab.',
                    """
The following commands can be used by anyone in the group @everyone:

!invite: this sends an invite to the server in the channel you are in.

And now for @judges:
!code: this will DM you your ballot code.

@debaters:
Erm -- in progress I suppose!
                    """
                ]
            },
            {
                'name': 'announcements',
                'type': 'text',
                'permissions': {
                    '@everyone': discord.PermissionOverwrite(
                        view_channel=True,
                        read_message_history=True
                    ),
                    'eos': discord.PermissionOverwrite(
                        send_messages=True
                    )
                },
                'initial': 'If any information here looks wrong, please contact any user who is an ADMIN ASAP'
            }
        ]
    },
    {
        'name': '[TOURNAMENT ADMINISTRATION]',
        'channels': [
            {
                'name': 'Tabroom',
                'type': 'voice',
            },
            {
                'name': 'bot-commands',
                'type': 'text',
            },
            {
                'name': 'tabroom',
                'type': 'text',
                'initial': """
!update @user: updates the mentioned user's roles
!rooms: deletes and/or creates the rooms for rounds to happen in

!round <round number> blast|send|end [ these can take a while ]
                """
            }
        ]
    },
    {
        'name': '[EQUITY]',
        'channels': [
            {
                'name': 'equity_and_staff',
                'type': 'text',
                'permissions': {
                    'eos': discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True
                    )
                }
            },
            {
                'name': 'Room 1',
                'type': 'voice',
                'permissions': {
                    'eos': discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,
                        speak=True
                    )
                }                
            },
            {
                'name': 'Room 2',
                'type': 'voice',
                'permissions': {
                    'eos': discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,
                        speak=True
                    )
                }                
            }
        ]
    }
]


async def get_channel(guild, name):
    await guild.fetch_channels()

    for channel in guild.channels:
        if channel.name == name:
            return channel

    return None


async def clear_channels(guild):
    channels = await guild.fetch_channels()
    for channel in channels:
        await channel.delete()


async def _create_channel(guild, channel, category=None):
    _channel = None

    if channel['type'] == 'voice':
        _channel = await guild.create_voice_channel(
            channel['name'],
            category=category
        )
    elif channel['type'] == 'text':
        _channel = await guild.create_text_channel(
            channel['name'],
            category=category
        )

        if 'initial' in channel:
            if type(channel['initial']) is list:
                for msg in channel['initial']:
                    await _channel.send(msg)
            else:
                await _channel.send(channel['initial'])

    if 'permissions' in channel:
        for role in channel['permissions']:
            role_obj = await get_role(guild, role)
            
            await _channel.set_permissions(
                role_obj,
                overwrite=channel['permissions'][role]
            )
        
async def create_channels(guild):
    for channel in CHANNELS:
        if 'channels' in channel:
            category = await guild.create_category(
                channel['name']
            )

            for _channel in channel['channels']:
                await _create_channel(
                    guild,
                    _channel,
                    category=category
                )
        else:
            await _create_channel(
                guild,
                channel
            )
