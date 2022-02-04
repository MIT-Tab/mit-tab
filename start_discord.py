import discord, os, sys
from mit_tab_bot import MITTabClient

client = MITTabClient(guild_id=sys.argv[1])
client.run(os.environ.get('DISCORD_BOT_TOKEN'))
