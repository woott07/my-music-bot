import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import sys
from dotenv import load_dotenv
import logging
# Logging Setup 
script_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(script_dir, "logs")

if not os.path.exists(log_dir):
    os.makedirs(log_dir)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "bot.log"), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
# 1. Setup & Token
load_dotenv()
token = os.getenv('d_t')

# 2. Intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# 3. Configuration
FFMPEG_EXE = r"C:\discordbot.py\bin\ffmpeg.exe"

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -filter:a "volume=1.0"'
}

song_queue = []

# --- Helper Logic ---

def play_next(ctx):
    if len(song_queue) > 0:
        song = song_queue.pop(0)
        source = discord.FFmpegPCMAudio(song['url'], executable=FFMPEG_EXE, **FFMPEG_OPTIONS)
        ctx.voice_client.play(source, after=lambda e: play_next(ctx))
        print(f"Playing: {song['title']}")
    else:
        print("Queue Empty.")

# --- Events ---

@bot.event
async def on_ready():
    print(f'{bot.user} is online! Ready to play music.')

# --- Commands ---

@bot.command(aliases=['j'])
async def join(ctx):
    """Shortcut: !j"""
    if not ctx.author.voice:
        return await ctx.send("Please join a voice channel first!")
    channel = ctx.author.voice.channel
    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()
    await ctx.send(f"Joined **{channel.name}**! 🎙️")

@bot.command(aliases=['p', 'add'])
async def play(ctx, *, search: str = None):
    """Shortcut: !p, !add"""
    if not ctx.voice_client:
        await ctx.invoke(join)

    if not search:
        return await ctx.send("Usage: `!p <song name>`")

    async with ctx.typing():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(search, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                
                song_data = {'url': info['url'], 'title': info['title']}
                
                if not ctx.voice_client.is_playing():
                    song_queue.append(song_data)
                    play_next(ctx)
                    await ctx.send(f"🎶 Now playing: **{info['title']}**")
                else:
                    song_queue.append(song_data)
                    await ctx.send(f"✅ Added to queue: **{info['title']}**")
            except Exception as e:
                await ctx.send("Can't Find Song!")

@bot.command(aliases=['pn'])
async def playnext(ctx, *, search: str = None):
    """Shortcut: !pn. Queue mein sabse upar."""
    if not ctx.voice_client:
        await ctx.invoke(join)

    if not search:
        return await ctx.send("Usage: `!pn <song name>`")

    async with ctx.typing():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(search, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                
                song_data = {'url': info['url'], 'title': info['title']}
                
                if not ctx.voice_client.is_playing():
                    song_queue.append(song_data)
                    play_next(ctx)
                    await ctx.send(f"🎶 Now playing: **{info['title']}**")
                else:
                    song_queue.insert(0, song_data)
                    await ctx.send(f"⏭️ **Play Next:** `{info['title']}` agla baje ga!")
            except Exception as e:
                await ctx.send("Song not found!")

@bot.command(aliases=['q'])
async def queue(ctx):
    """Shortcut: !q"""
    if len(song_queue) == 0:
        return await ctx.send("Queue is empty!")
    
    q_msg = ""
    for i, song in enumerate(song_queue[:10]):
        q_msg += f"{i+1}. **{song['title']}**\n"
    
    embed = discord.Embed(title="Coming Up Next", description=q_msg, color=discord.Color.blue())
    await ctx.send(embed=embed)

@bot.command(aliases=['s'])
async def skip(ctx):
    """Shortcut: !s"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped!")
    else:
        await ctx.send("No Song is playing.")

@bot.command(aliases=['l', 'stop'])
async def leave(ctx):
    """Shortcut: !l, !stop"""
    song_queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Bye! 👋")

@bot.command(aliases=['st'])
@commands.is_owner()
async def stopbot(ctx):
    await ctx.send("Bot Turning off... Bye!")
    await bot.close()

@bot.command(aliases=['rs'])
@commands.is_owner()
async def restart(ctx):
    await ctx.send("Restarting... 🔄")
    os.execv(sys.executable, ['python'] + sys.argv)

@bot.command(aliases=['h'])
async def help(ctx):
    help_txt = """
    Help Command
    !j - Join
    !p - Play
    !pn - Play Next
    !q - Queue
    !s - Skip
    !l - Leave
    !st - Stop
    !rs - Restart
    """
    await ctx.send(help_txt)

# 4. Run
bot.run(token)