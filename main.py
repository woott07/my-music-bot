import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import sys
import re
import logging
from dotenv import load_dotenv
import aiohttp

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv('d_t')

FFMPEG_EXE = r"C:\discordbot.py\bin\ffmpeg.exe"

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

_COOKIES = os.path.join(os.path.dirname(__file__), 'cookies.txt')
_HAS_COOKIES = os.path.exists(_COOKIES)

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'extractor_args': {
        'youtube': {'player_client': ['android', 'mweb']},
    },
    'source_address': '0.0.0.0',
    **({'cookiefile': _COOKIES} if _HAS_COOKIES else {}),
}

YTDL_FALLBACK = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'source_address': '0.0.0.0',
    **({'cookiefile': _COOKIES} if _HAS_COOKIES else {}),
}

if _HAS_COOKIES:
    logger.info('cookies.txt found — YouTube auth enabled.')
else:
    logger.warning('cookies.txt NOT found — YouTube may block requests.')

# ── State ────────────────────────────────────────────────────────────
song_queue   = []
current_song = None
loop_mode    = 'off'   # 'off' | 'song' | 'queue'
np_channel   = None    # channel to send Now Playing embeds to

# ── Bot ──────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or('!'),
    intents=intents,
    help_command=None
)

# ════════════════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════════════════

async def yt_extract(search: str) -> dict | None:
    """Extract audio info from YouTube (search term or URL)."""
    loop = asyncio.get_event_loop()
    for opts, label in [(YTDL_OPTIONS, 'primary'), (YTDL_FALLBACK, 'fallback')]:
        try:
            info = await loop.run_in_executor(
                None, lambda o=opts: yt_dlp.YoutubeDL(o).extract_info(search, download=False)
            )
            if info and 'entries' in info:
                info = info['entries'][0]
            logger.info(f'[yt-dlp] ✅ {label}: {info.get("title")}')
            return info
        except Exception as e:
            logger.warning(f'[yt-dlp] ❌ {label}: {e}')
    return None


async def scrape_og(url: str, source: str) -> str | list | None:
    """Scrape Open Graph tags from Spotify / Apple Music links."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; Discordbot/2.0)'}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                html = await r.text()

        if 'playlist' in url or 'album' in url:
            urls = re.findall(r'<meta name="music:song" content="([^"]+)"', html)
            if urls:
                return urls[:50]

        title_m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        desc_m  = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        if not title_m:
            return None
        title = title_m.group(1).strip()
        if desc_m:
            for sep in ['·', ' - ', ' – ']:
                parts = [p.strip() for p in desc_m.group(1).split(sep)]
                if len(parts) >= 2:
                    artist = next((p for p in parts if p.lower() not in title.lower()), parts[0])
                    return f'{title} {artist} audio'
        return f'{title} audio'
    except Exception as e:
        logger.warning(f'[{source}] scrape error: {e}')
        return None


def now_playing_embed(song: dict) -> discord.Embed:
    embed = discord.Embed(
        title='🎵 Now Playing',
        description=f"**{song['title']}**",
        color=discord.Color.from_rgb(88, 101, 242)
    )
    if song.get('thumbnail'):
        embed.set_thumbnail(url=song['thumbnail'])
    if song.get('duration'):
        m, s = divmod(song['duration'], 60)
        embed.set_footer(text=f'Duration: {m}:{s:02d}')
    return embed


def play_next(ctx):
    global current_song, loop_mode, np_channel

    vc = ctx.voice_client
    if vc is None or not vc.is_connected():
        current_song = None
        return

    if loop_mode == 'song' and current_song:
        song = current_song
    elif song_queue:
        song = song_queue.pop(0)
        if loop_mode == 'queue':
            song_queue.append(song)
        current_song = song
    else:
        current_song = None
        return

    # Lazy songs (Spotify/Apple Music playlist items resolved at play-time)
    if song.get('is_lazy'):
        async def resolve():
            info = await yt_extract(song['search'])
            if not info:
                play_next(ctx)
                return
            song.update({
                'is_lazy': False,
                'url': info['url'],
                'title': info.get('title', song['search']),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
            })
            vc2 = ctx.voice_client
            if vc2 and vc2.is_connected():
                try:
                    vc2.play(
                        discord.FFmpegPCMAudio(song['url'], executable=FFMPEG_EXE, **FFMPEG_OPTIONS),
                        after=lambda e: play_next(ctx)
                    )
                    ch = np_channel or ctx.channel
                    asyncio.run_coroutine_threadsafe(
                        ch.send(embed=now_playing_embed(song)), ctx.bot.loop
                    )
                except discord.ClientException:
                    pass
        asyncio.run_coroutine_threadsafe(resolve(), ctx.bot.loop)
        return

    try:
        vc.play(
            discord.FFmpegPCMAudio(song['url'], executable=FFMPEG_EXE, **FFMPEG_OPTIONS),
            after=lambda e: play_next(ctx)
        )
    except discord.ClientException:
        return

    async def send_np():
        ch = np_channel or ctx.channel
        await ch.send(embed=now_playing_embed(song))

    asyncio.run_coroutine_threadsafe(send_np(), ctx.bot.loop)


# ════════════════════════════════════════════════════════════════════
#  Events
# ════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    logger.info(f'{bot.user} is online.')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f'❌ Missing argument. Try `!help`.')
    else:
        logger.error(f'Error in {ctx.command}: {error}')


# ════════════════════════════════════════════════════════════════════
#  Voice commands
# ════════════════════════════════════════════════════════════════════

@bot.command(aliases=['j'])
async def join(ctx):
    """Join your voice channel."""
    if not ctx.author.voice:
        return await ctx.send('❌ Join a voice channel first.')
    channel = ctx.author.voice.channel
    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
        return await ctx.send(f'↪️ Moved to **{channel.name}**.')
    try:
        await channel.connect(timeout=30.0, reconnect=False)
        await ctx.send(f'✅ Joined **{channel.name}**.')
    except Exception as e:
        code = getattr(e, 'code', None)
        if code == 4017 or '4017' in str(e):
            await ctx.send(
                '❌ **Rejected (code 4017 — Missing Permissions).**\n'
                f'Give me `Connect` + `Speak` in **{channel.name}** and try again.'
            )
        elif isinstance(e, asyncio.TimeoutError):
            await ctx.send('❌ Timed out connecting. Try again.')
        elif isinstance(e, discord.Forbidden):
            await ctx.send('❌ I don\'t have permission to join that channel.')
        else:
            await ctx.send(f'❌ Could not join: `{e}`')


@bot.command(aliases=['l', 'stop'])
async def leave(ctx):
    """Leave the voice channel and clear the queue."""
    global loop_mode, current_song
    song_queue.clear()
    loop_mode    = 'off'
    current_song = None
    if ctx.voice_client:
        await ctx.voice_client.disconnect(force=True)
    await ctx.send('👋 Disconnected.')


# ════════════════════════════════════════════════════════════════════
#  Playback commands
# ════════════════════════════════════════════════════════════════════

@bot.command(aliases=['p', 'add'])
async def play(ctx, *, search: str):
    """Play a song or add it to the queue. Supports YouTube, Spotify, Apple Music."""
    global np_channel
    np_channel = ctx.channel

    if not ctx.voice_client:
        await ctx.invoke(join)
        if not ctx.voice_client:
            return  # join failed

    # Spotify / Apple Music links
    if 'spotify.com' in search or 'music.apple.com' in search:
        source = 'Spotify' if 'spotify.com' in search else 'Apple Music'
        result = await scrape_og(search, source)
        if not result:
            return await ctx.send(f'❌ Could not read that {source} link.')
        if isinstance(result, list):
            await ctx.send(f'🎵 {source} playlist — queuing **{len(result)}** tracks…')
            songs = [{'is_lazy': True, 'search': u, 'title': 'Loading…'} for u in result]
            if not ctx.voice_client.is_playing():
                song_queue.append(songs.pop(0))
                play_next(ctx)
            song_queue.extend(songs)
            return
        search = result  # resolved to a search query

    async with ctx.typing():
        info = await yt_extract(search)
        if not info:
            return await ctx.send('❌ Could not find that song. Try a different search.')

    song = {
        'is_lazy': False,
        'url': info['url'],
        'title': info['title'],
        'thumbnail': info.get('thumbnail'),
        'duration': info.get('duration'),
    }

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        song_queue.append(song)
        play_next(ctx)
    else:
        song_queue.append(song)
        e = discord.Embed(
            title='➕ Added to Queue',
            description=f"**{info['title']}**",
            color=discord.Color.blurple()
        )
        if song['thumbnail']:
            e.set_thumbnail(url=song['thumbnail'])
        e.set_footer(text=f'Position: #{len(song_queue)}')
        await ctx.send(embed=e)


@bot.command(aliases=['s'])
async def skip(ctx):
    """Skip the current song."""
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send('⏭ Skipped.')
    else:
        await ctx.send('Nothing is playing.')


@bot.command(aliases=['pa'])
async def pause(ctx):
    """Pause playback."""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send('⏸ Paused.')
    else:
        await ctx.send('Nothing is playing.')


@bot.command(aliases=['res'])
async def resume(ctx):
    """Resume playback."""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send('▶️ Resumed.')
    else:
        await ctx.send('Not paused.')


@bot.command(aliases=['q'])
async def queue(ctx):
    """Show the song queue."""
    if not song_queue and not current_song:
        return await ctx.send('Queue is empty.')
    lines = []
    if current_song:
        lines.append(f"▶️ **{current_song['title']}** *(now playing)*")
    for i, s in enumerate(song_queue[:10], 1):
        lines.append(f"`{i}.` {s['title']}")
    if len(song_queue) > 10:
        lines.append(f'*… and {len(song_queue) - 10} more*')
    embed = discord.Embed(
        title='🎶 Queue',
        description='\n'.join(lines),
        color=discord.Color.blurple()
    )
    await ctx.send(embed=embed)


@bot.command(aliases=['np', 'now'])
async def nowplaying(ctx):
    """Show the current song."""
    if not current_song:
        return await ctx.send('Nothing is playing.')
    await ctx.send(embed=now_playing_embed(current_song))


@bot.command(aliases=['lp'])
async def loop(ctx, mode: str = None):
    """Loop modes: song, queue, off. No argument toggles song loop."""
    global loop_mode
    if mode is None:
        loop_mode = 'song' if loop_mode == 'off' else 'off'
        await ctx.send(f'🔂 Loop **{loop_mode}**.')
        return
    mode = mode.lower()
    if mode in ('song', 's', 'one'):
        loop_mode = 'song'
        await ctx.send('🔂 Looping **current song**.')
    elif mode in ('queue', 'q', 'all'):
        loop_mode = 'queue'
        await ctx.send('🔁 Looping **entire queue**.')
    elif mode in ('off', 'none', 'disable'):
        loop_mode = 'off'
        await ctx.send('➡️ Loop **off**.')
    else:
        await ctx.send('Usage: `!loop` / `!loop song` / `!loop queue` / `!loop off`')


@bot.command(aliases=['cl'])
async def clear(ctx):
    """Clear the queue (keeps current song playing)."""
    song_queue.clear()
    await ctx.send('🗑️ Queue cleared.')


# ════════════════════════════════════════════════════════════════════
#  Help
# ════════════════════════════════════════════════════════════════════

@bot.command(aliases=['h'])
async def help(ctx):
    embed = discord.Embed(
        title='🎵 Musico — Commands',
        color=discord.Color.from_rgb(88, 101, 242)
    )
    embed.add_field(name='Playback', value=(
        '`!p <song>` — Play / search\n'
        '`!s` — Skip\n'
        '`!pa` — Pause\n'
        '`!res` — Resume\n'
        '`!lp [song/queue/off]` — Loop\n'
        '`!np` — Now Playing'
    ), inline=True)
    embed.add_field(name='Queue & Voice', value=(
        '`!q` — Show queue\n'
        '`!cl` — Clear queue\n'
        '`!j` — Join voice\n'
        '`!l` — Leave voice'
    ), inline=True)
    embed.set_footer(text='Supports YouTube, Spotify & Apple Music links')
    await ctx.send(embed=embed)


# ════════════════════════════════════════════════════════════════════
#  Run
# ════════════════════════════════════════════════════════════════════
bot.run(TOKEN)
