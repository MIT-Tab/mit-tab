import discord, os


class Client(discord.Client):
    async def on_ready(self):
        for guild in self.guilds:
            await guild.delete()


client = Client()
client.run(os.environ.get('DISCORD_BOT_TOKEN'))
