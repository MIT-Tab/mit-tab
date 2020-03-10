import discord, os, sys
from mit_tab_bot import MITTabClient

kwargs = {}
guild_id = sys.argv[1] if len(sys.argv) > 1 else None

if guild_id:
    kwargs['guild_id'] = guild_id

client = MITTabClient(**kwargs)
client.run(os.environ.get('DISCORD_BOT_TOKEN'))
