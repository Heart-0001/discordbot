import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
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


bot.start_time = datetime.now(timezone.utc)


@bot.event
async def on_ready():
    # 1. 把 global 指令複製進每個 guild，即時生效
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info(f'已同步 {len(synced)} 個指令到伺服器: {guild.name}')
    # 2. 清掉 Discord 上的 global 指令，避免與 guild 版重複出現
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    log.info(f'✅ {bot.user} 已上線！連接到 {len(bot.guilds)} 個伺服器')


@bot.command(name='sync')
async def sync_commands(ctx):
    """強制重新同步 slash commands 到目前伺服器（!sync）"""
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f'✅ 已重新同步 {len(synced)} 個指令到 **{ctx.guild.name}**')


async def main():
    async with bot:
        await bot.load_extension('cogs.music')
        await bot.start(os.getenv('DISCORD_TOKEN'))


if __name__ == '__main__':
    asyncio.run(main())
