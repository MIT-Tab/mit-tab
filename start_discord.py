import discord, os, sys
from mit_tab_bot import MITTabClient

kwargs = {}
tournament_name = sys.argv[1] if len(sys.argv) > 1 else None

if tournament_name:
    kwargs['tournament_name'] = tournament_name

client = MITTabClient(**kwargs)
client.run(os.environ.get('DISCORD_BOT_TOKEN'))
