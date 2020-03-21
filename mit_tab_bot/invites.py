async def create_invite(channel):
    return await channel.create_invite(max_age=0,
                                       max_uses=0)


async def clear_invites(channel):
    invites = await channel.invites()

    for invite in invites:
        await invite.delete()


async def get_invites(channel):
    return await channel.invites()


async def get_or_create_invite(channel):
    invites = await channel.invites()

    if len(invites) > 0:
        return invites[0]

    return await create_invite(channel)
