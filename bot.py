import asyncio
import logging
import os
import sys
import discord
from discord.ext import commands
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)


@bot.event
async def on_ready():
    # 同步到每個伺服器（即時生效，不用等 1 小時）
    for guild in bot.guilds:
        await bot.tree.sync(guild=guild)
        log.info(f'已同步指令到伺服器: {guild.name}')
    log.info(f'✅ {bot.user} 已上線！連接到 {len(bot.guilds)} 個伺服器')


async def main():
    async with bot:
        await bot.load_extension('cogs.music')
        await bot.start(os.getenv('DISCORD_TOKEN'))


if __name__ == '__main__':
    asyncio.run(main())
