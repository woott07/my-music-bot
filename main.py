import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import sys
from dotenv import load_dotenv
import logging

# ---------------- Logging ----------------
script_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(script_dir, "logs")
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "bot.log"), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# ---------------- Token ----------------
load_dotenv()
token = os.getenv('d_t')

# ---------------- Intents ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ---------------- Config ----------------
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

LOG_CHANNEL_NAME = "musico-logs"
RESTART_FLAG = "restart.flag"

# ---------------- Log Channel ----------------
async def get_or_create_log_channel(guild):
    existing = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if existing:
        return existing

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True
        )
    }

    channel = await guild.create_text_channel(LOG_CHANNEL_NAME, overwrites=overwrites)
    await channel.send("Log channel created.")
    return channel

# ---------------- Music Logic ----------------
def play_next(ctx):
    if song_queue:
        song = song_queue.pop(0)
        source = discord.FFmpegPCMAudio(song['url'], executable=FFMPEG_EXE, **FFMPEG_OPTIONS)
        ctx.voice_client.play(source, after=lambda e: play_next(ctx))
        print(f"Playing: {song['title']}")
    else:
        print("Queue empty.")

# ---------------- Bot Ready ----------------
@bot.event
async def on_ready():
    print(f'{bot.user} is online.')

    for guild in bot.guilds:
        log_channel = await get_or_create_log_channel(guild)

        if os.path.exists(RESTART_FLAG):
            os.remove(RESTART_FLAG)
            await log_channel.send("Bot restarted successfully.")
        else:
            await log_channel.send("Bot is now online.")

# ---------------- Commands ----------------
@bot.command()
@commands.is_owner()
async def ping(ctx):
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")


@bot.command(aliases=['j'])
async def join(ctx):
    if not ctx.author.voice:
        return await ctx.send("Join a voice channel first.")

    channel = ctx.author.voice.channel

    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()

    await ctx.send(f"Joined {channel.name}.")


@bot.command(aliases=['p', 'add'])
async def play(ctx, *, search=None):
    if not ctx.voice_client:
        await ctx.invoke(join)

    if not search:
        return await ctx.send("Usage: !p <song name>")

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
                    await ctx.send(f"Now playing: {info['title']}")
                else:
                    song_queue.append(song_data)
                    await ctx.send(f"Added to queue: {info['title']}")

            except:
                await ctx.send("Song not found.")


@bot.command(aliases=['pn'])
async def playnext(ctx, *, search=None):
    if not ctx.voice_client:
        await ctx.invoke(join)

    if not search:
        return await ctx.send("Usage: !pn <song name>")

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
                    await ctx.send(f"Now playing: {info['title']}")
                else:
                    song_queue.insert(0, song_data)
                    await ctx.send(f"Queued next: {info['title']}")

            except:
                await ctx.send("Song not found.")


@bot.command(aliases=['q'])
async def queue(ctx):
    if not song_queue:
        return await ctx.send("Queue is empty.")

    text = ""
    for i, song in enumerate(song_queue[:10]):
        text += f"{i+1}. {song['title']}\n"

    embed = discord.Embed(title="Upcoming Songs", description=text, color=discord.Color.blue())
    await ctx.send(embed=embed)


@bot.command(aliases=['s'])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Skipped.")
    else:
        await ctx.send("Nothing is playing.")


@bot.command(aliases=['l', 'stop'])
async def leave(ctx):
    song_queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected.")


@bot.command(aliases=['st'])
@commands.is_owner()
async def stopbot(ctx):
    await ctx.send("Shutting down.")
    await bot.close()


# ---------------- Restart ----------------
@bot.command(aliases=['rs'])
@commands.is_owner()
async def restart(ctx):
    log_channel = await get_or_create_log_channel(ctx.guild)
    await log_channel.send("Restarting bot...")

    with open(RESTART_FLAG, "w") as f:
        f.write("1")

    os.execv(sys.executable, ['python'] + sys.argv)


# ---------------- Help ----------------
@bot.command(aliases=['h'])
async def help(ctx):
    text = """
Commands:
!j   Join voice
!p   Play music
!pn  Play next
!q   Show queue
!s   Skip
!l   Leave voice
!st  Stop bot
!rs  Restart bot
"""
    await ctx.send(text)


# ---------------- Run ----------------
bot.run(token)
