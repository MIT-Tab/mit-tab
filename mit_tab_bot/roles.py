import discord

from .api import (
    is_judge,
    is_debater
)

ADMIN = [
    'Talos#5956'
]

ROLES = [
    {
        'name': 'superadmin',
        'permissions': discord.Permissions(
            administrator=True
        ),
        'colour': discord.Colour.purple(),
        'hoist': True,
        'mentionable': True
    },
    {
        'name': 'staff',
        'permissions': discord.Permissions.all(),
        'color': discord.Colour.teal(),
        'hoist': True,
        'mentionable': True
    },
    {
        'name': 'judges',
        'permissions': discord.Permissions(
            add_reactions=True
        ),
        'colour': discord.Colour.green(),
        'hoist': True,
        'mentionable': True
    },
    {
        'name': 'debaters',
        'permissions': discord.Permissions(
            add_reactions=True
        ),
        'colour': discord.Colour.blue(),
        'hoist': True,
        'mentionable': True
    }
]

async def delete_roles(guild):
    roles = await guild.fetch_roles()
    for role in roles:
        if not role.name == '@everyone':
            await role.delete()


async def get_role(guild, role_name):
    roles = await guild.fetch_roles()
    for role in roles:
        if role.name == role_name:
            return role

    return None


async def create_roles(guild):
    everyone = await get_role(guild, '@everyone')
    await everyone.edit(permissions=discord.Permissions.none())
    
    for role in ROLES:
        new_role = await guild.create_role(**role)
        print ('Created %s' % (new_role,))


async def get_member(guild, member_str):
    member = None

    async for _member in guild.fetch_members(limit=None):
        if str(_member) == member_str:
            member = _member

    return member


def has_role(member, role):
    return role in [role.name for role in member.roles]


async def update_member_role(guild, member_str):
    member = await get_member(guild, member_str)

    if not member:
        return
    
    if str(member) in ADMIN:
        await member.add_roles(await get_role(guild, 'superadmin'))
        
    if is_judge(str(member)):
        await member.add_roles(await get_role(guild, 'judges'))
        
    if is_debater(str(member)):
        await member.add_roles(await get_role(guild, 'debaters'))
