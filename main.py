import discord
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import os
import sys
import json
import re
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import logging
from google import genai
from collections import defaultdict
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

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
logger = logging.getLogger(__name__)

# ---------------- Token ----------------
load_dotenv()
token = os.getenv('d_t')

spotify_id = os.getenv('SPOTIPY_CLIENT_ID')
spotify_secret = os.getenv('SPOTIPY_CLIENT_SECRET')
sp = None
if spotify_id and spotify_secret:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=spotify_id, client_secret=spotify_secret))

# ---------------- Intents ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix=commands.when_mentioned_or('!'), intents=intents, help_command=None)

# ---------------- Config ----------------
FFMPEG_EXE = r"C:\discordbot.py\bin\ffmpeg.exe"

# ── Build yt-dlp options with bot-detection bypass ────────────────
_COOKIES_FILE = os.path.join(script_dir, 'cookies.txt')
_cookies_found = os.path.exists(_COOKIES_FILE)

# Primary options: ios is the most reliable player client for datacenter IPs
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'extractor_args': {
        'youtube': {
            # ios → most reliable on server IPs; mweb as backup
            'player_client': ['ios', 'mweb'],
        },
    },
    'source_address': '0.0.0.0',
    **(({'cookiefile': _COOKIES_FILE}) if _cookies_found else {}),
}

# Fallback options: cookies only (used if primary fails)
YTDL_OPTIONS_FALLBACK = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'source_address': '0.0.0.0',
    **(({'cookiefile': _COOKIES_FILE}) if _cookies_found else {}),
}

# Logged at startup — check via !botlogs to confirm cookies are loaded
if _cookies_found:
    logging.getLogger(__name__).info('[yt-dlp] ✅ cookies.txt found — YouTube auth enabled.')
else:
    logging.getLogger(__name__).warning('[yt-dlp] ⚠️ cookies.txt NOT found — YouTube may block on datacenter IPs.')


FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -filter:a "volume=1.0"'
}

song_queue = []
loop_mode = "off"   # "off", "song", "queue"
current_song = None
now_playing_channel = None

LOG_CHANNEL_NAME = "musico-logs"
RESTART_FLAG = "restart.flag"
BOT_START_TIME = time.time()

# ─── Persistence files ────────────────────────────────────────────
DATA_DIR = os.path.join(script_dir, "data")
os.makedirs(DATA_DIR, exist_ok=True)

WARNS_FILE     = os.path.join(DATA_DIR, "warns.json")
AUTOMOD_FILE   = os.path.join(DATA_DIR, "automod.json")
AUTOROLE_FILE  = os.path.join(DATA_DIR, "autorole.json")
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")
STATS_FILE     = os.path.join(DATA_DIR, "stats.json")

def _load(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

warns_db     = _load(WARNS_FILE, {})        # {guild_id: {user_id: [reason, ...]}}
automod_cfg  = _load(AUTOMOD_FILE, {})      # {guild_id: {enabled, words, spam_limit, exempt_roles}}
autorole_cfg = _load(AUTOROLE_FILE, {})     # {guild_id: role_id}
reminders    = _load(REMINDERS_FILE, [])    # [{user_id, channel_id, time, text}]
cmd_stats    = _load(STATS_FILE, {})        # {command_name: count}

# Spam tracking (in-memory)
spam_tracker = defaultdict(list)   # {user_id: [timestamps]}

# ================================================================
#  Music Controls  —  interactive buttons on the Now Playing embed
# ================================================================
class MusicControls(discord.ui.View):
    """Buttons attached to the Now Playing embed."""

    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    def _vc(self):
        return self.ctx.voice_client

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.primary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if not vc:
            return await interaction.response.send_message("Not in a voice channel.", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸"
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, custom_id="skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭ Skipped!", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="👋", style=discord.ButtonStyle.danger, custom_id="leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        global loop_mode, current_song
        vc = self._vc()
        song_queue.clear()
        loop_mode = "off"
        current_song = None
        if vc:
            await vc.disconnect()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)


# ================================================================
#  Build / send the Now Playing embed + buttons
# ================================================================
def _build_now_playing_embed(song: dict) -> discord.Embed:
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{song['title']}**",
        color=discord.Color.from_rgb(88, 101, 242)
    )
    if song.get('thumbnail'):
        embed.set_thumbnail(url=song['thumbnail'])
    if song.get('duration'):
        mins, secs = divmod(song['duration'], 60)
        embed.set_footer(text=f"Duration: {mins}:{secs:02d}")
    return embed


# ================================================================
#  Core playback
# ================================================================
def play_next(ctx):
    global current_song, now_playing_channel

    if loop_mode == "song" and current_song:
        song = current_song
    elif song_queue:
        song = song_queue.pop(0)
        if loop_mode == "queue":
            song_queue.append(song)
        current_song = song
    else:
        current_song = None
        return

    source = discord.FFmpegPCMAudio(song['url'], executable=FFMPEG_EXE, **FFMPEG_OPTIONS)
    ctx.voice_client.play(source, after=lambda e: play_next(ctx))

    async def send_now_playing():
        channel = now_playing_channel or ctx.channel
        embed = _build_now_playing_embed(song)
        view = MusicControls(ctx)
        await channel.send(embed=embed, view=view)

    asyncio.run_coroutine_threadsafe(send_now_playing(), ctx.bot.loop)


# ================================================================
#  Log Channel helpers
# ================================================================
async def get_or_create_log_channel(guild):
    existing = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if existing:
        return existing
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    }
    channel = await guild.create_text_channel(LOG_CHANNEL_NAME, overwrites=overwrites)
    await channel.send("📋 Log channel created.")
    return channel

async def log_action(guild, embed: discord.Embed):
    ch = await get_or_create_log_channel(guild)
    await ch.send(embed=embed)


# ================================================================
#  Analytics helpers
# ================================================================
def track_cmd(name: str):
    cmd_stats[name] = cmd_stats.get(name, 0) + 1
    _save(STATS_FILE, cmd_stats)


# ================================================================
#  Bot Ready
# ================================================================
@bot.event
async def on_ready():
    global BOT_START_TIME
    BOT_START_TIME = time.time()
    logger.info(f'{bot.user} is online.')
    check_reminders.start()
    for guild in bot.guilds:
        log_channel = await get_or_create_log_channel(guild)
        if os.path.exists(RESTART_FLAG):
            os.remove(RESTART_FLAG)
            await log_channel.send("🔄 Bot restarted successfully.")
        else:
            embed = discord.Embed(
                title="✅ Bot Online",
                description=f"**{bot.user}** is now online and ready!",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            await log_channel.send(embed=embed)


# ================================================================
#  Auto-Mod  —  message listener
# ================================================================
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    gid = str(message.guild.id)
    cfg = automod_cfg.get(gid, {})

    if cfg.get("enabled", False):
        # Check exempt roles
        exempt_roles = cfg.get("exempt_roles", [])
        member_role_ids = [str(r.id) for r in message.author.roles]
        is_exempt = any(rid in member_role_ids for rid in exempt_roles)

        if not is_exempt:
            # Bad-word filter
            content_lower = message.content.lower()
            blocked = cfg.get("words", [])
            for word in blocked:
                if word.lower() in content_lower:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    warn_user(gid, str(message.author.id), f"Auto-mod: banned word '{word}'")
                    try:
                        warn_embed = discord.Embed(
                            description=f"🚫 {message.author.mention} — your message was removed (banned word).",
                            color=discord.Color.red()
                        )
                        await message.channel.send(embed=warn_embed, delete_after=5)
                    except Exception:
                        pass
                    # Log it
                    log_embed = discord.Embed(
                        title="🤖 Auto-Mod: Banned Word",
                        description=f"**User:** {message.author} (`{message.author.id}`)\n**Word:** `{word}`\n**Channel:** {message.channel.mention}",
                        color=discord.Color.orange(),
                        timestamp=datetime.now(timezone.utc)
                    )
                    asyncio.create_task(log_action(message.guild, log_embed))
                    await bot.process_commands(message)
                    return

            # Spam detection
            spam_limit = cfg.get("spam_limit", 5)
            now = time.time()
            uid = message.author.id
            spam_tracker[uid] = [t for t in spam_tracker[uid] if now - t < 5]
            spam_tracker[uid].append(now)
            if len(spam_tracker[uid]) >= spam_limit:
                spam_tracker[uid].clear()
                try:
                    await message.author.timeout(timedelta(minutes=2), reason="Auto-mod: spam")
                    warn_user(gid, str(message.author.id), "Auto-mod: spam detection")
                    sp_embed = discord.Embed(
                        description=f"⏱️ {message.author.mention} has been timed out for 2 minutes (spam).",
                        color=discord.Color.red()
                    )
                    await message.channel.send(embed=sp_embed, delete_after=6)
                except Exception:
                    pass
                log_embed = discord.Embed(
                    title="🤖 Auto-Mod: Spam Timeout",
                    description=f"**User:** {message.author} (`{message.author.id}`)\n**Channel:** {message.channel.mention}",
                    color=discord.Color.orange(),
                    timestamp=datetime.now(timezone.utc)
                )
                asyncio.create_task(log_action(message.guild, log_embed))

    await bot.process_commands(message)


# ================================================================
#  Member join  — auto-role
# ================================================================
@bot.event
async def on_member_join(member):
    gid = str(member.guild.id)
    role_id = autorole_cfg.get(gid)
    if role_id:
        role = member.guild.get_role(int(role_id))
        if role:
            try:
                await member.add_roles(role, reason="Auto-role on join")
            except Exception:
                pass
    # Log
    log_embed = discord.Embed(
        title="📥 Member Joined",
        description=f"{member.mention} (`{member.id}`) joined the server.",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    log_embed.set_thumbnail(url=member.display_avatar.url)
    log_embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, 'R'))
    asyncio.create_task(log_action(member.guild, log_embed))


@bot.event
async def on_member_remove(member):
    log_embed = discord.Embed(
        title="📤 Member Left",
        description=f"{member.name} (`{member.id}`) left the server.",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    asyncio.create_task(log_action(member.guild, log_embed))


# ================================================================
#  Moderation helpers
# ================================================================
def warn_user(guild_id: str, user_id: str, reason: str):
    warns_db.setdefault(guild_id, {}).setdefault(user_id, []).append(reason)
    _save(WARNS_FILE, warns_db)

def get_warns(guild_id: str, user_id: str):
    return warns_db.get(guild_id, {}).get(user_id, [])

def clear_warns(guild_id: str, user_id: str):
    warns_db.setdefault(guild_id, {}).pop(user_id, None)
    _save(WARNS_FILE, warns_db)


# ================================================================
#  MODERATION COMMANDS
# ================================================================

# ── !kick ────────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    track_cmd("kick")
    await member.kick(reason=reason)
    embed = discord.Embed(
        title="👢 Member Kicked",
        description=f"**{member}** has been kicked.\n**Reason:** {reason}",
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)
    log_embed = discord.Embed(
        title="👢 Kick",
        description=f"**User:** {member} (`{member.id}`)\n**Mod:** {ctx.author}\n**Reason:** {reason}",
        color=discord.Color.orange(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)

@kick.error
async def kick_error(ctx, error):
    await ctx.send(f"❌ {error}")


# ── !ban ─────────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    track_cmd("ban")
    await member.ban(reason=reason, delete_message_days=1)
    embed = discord.Embed(
        title="🔨 Member Banned",
        description=f"**{member}** has been banned.\n**Reason:** {reason}",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed)
    log_embed = discord.Embed(
        title="🔨 Ban",
        description=f"**User:** {member} (`{member.id}`)\n**Mod:** {ctx.author}\n**Reason:** {reason}",
        color=discord.Color.red(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)

@ban.error
async def ban_error(ctx, error):
    await ctx.send(f"❌ {error}")


# ── !unban ───────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, user_id: int):
    track_cmd("unban")
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user)
        await ctx.send(f"✅ Unbanned **{user}**.")
        log_embed = discord.Embed(
            title="✅ Unban",
            description=f"**User:** {user} (`{user_id}`)\n**Mod:** {ctx.author}",
            color=discord.Color.green(), timestamp=datetime.now(timezone.utc)
        )
        await log_action(ctx.guild, log_embed)
    except discord.NotFound:
        await ctx.send("❌ User not found or not banned.")


# ── !mute (timeout) ───────────────────────────────────────────────
@bot.command()
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int = 10, *, reason="No reason provided"):
    track_cmd("mute")
    until = timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    embed = discord.Embed(
        title="🔇 Member Muted",
        description=f"**{member}** muted for **{minutes} min**.\n**Reason:** {reason}",
        color=discord.Color.greyple()
    )
    await ctx.send(embed=embed)
    log_embed = discord.Embed(
        title="🔇 Mute",
        description=f"**User:** {member} (`{member.id}`)\n**Duration:** {minutes} min\n**Mod:** {ctx.author}\n**Reason:** {reason}",
        color=discord.Color.greyple(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)

@mute.error
async def mute_error(ctx, error):
    await ctx.send(f"❌ {error}")


# ── !unmute ───────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    track_cmd("unmute")
    await member.timeout(None)
    await ctx.send(f"🔊 **{member}** has been unmuted.")
    log_embed = discord.Embed(
        title="🔊 Unmute",
        description=f"**User:** {member} (`{member.id}`)\n**Mod:** {ctx.author}",
        color=discord.Color.green(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)


# ── !warn ─────────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason="No reason provided"):
    track_cmd("warn")
    gid = str(ctx.guild.id)
    warn_user(gid, str(member.id), reason)
    count = len(get_warns(gid, str(member.id)))
    embed = discord.Embed(
        title="⚠️ Warning Issued",
        description=f"**{member}** has been warned.\n**Reason:** {reason}\n**Total Warnings:** {count}",
        color=discord.Color.yellow()
    )
    await ctx.send(embed=embed)
    try:
        await member.send(f"⚠️ You were warned in **{ctx.guild.name}**.\n**Reason:** {reason}")
    except Exception:
        pass
    log_embed = discord.Embed(
        title="⚠️ Warn",
        description=f"**User:** {member} (`{member.id}`)\n**Mod:** {ctx.author}\n**Reason:** {reason}\n**Total:** {count}",
        color=discord.Color.yellow(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)


# ── !warns ────────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_messages=True)
async def warns(ctx, member: discord.Member):
    track_cmd("warns")
    w = get_warns(str(ctx.guild.id), str(member.id))
    if not w:
        return await ctx.send(f"✅ **{member}** has no warnings.")
    text = "\n".join(f"`{i+1}.` {r}" for i, r in enumerate(w))
    embed = discord.Embed(
        title=f"⚠️ Warnings for {member}",
        description=text,
        color=discord.Color.yellow()
    )
    await ctx.send(embed=embed)


# ── !clearwarns ───────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_messages=True)
async def clearwarns(ctx, member: discord.Member):
    track_cmd("clearwarns")
    clear_warns(str(ctx.guild.id), str(member.id))
    await ctx.send(f"✅ Cleared all warnings for **{member}**.")


# ── !clear ────────────────────────────────────────────────────────
@bot.command(aliases=['purge'])
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = 10):
    track_cmd("clear")
    if amount < 1 or amount > 200:
        return await ctx.send("❌ Amount must be between 1 and 200.")
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"🗑️ Deleted **{len(deleted)-1}** messages.", delete_after=4)
    log_embed = discord.Embed(
        title="🗑️ Messages Purged",
        description=f"**{len(deleted)-1}** messages deleted in {ctx.channel.mention}\n**Mod:** {ctx.author}",
        color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)


# ── !slowmode ─────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int = 0):
    track_cmd("slowmode")
    await ctx.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await ctx.send("✅ Slowmode disabled.")
    else:
        await ctx.send(f"✅ Slowmode set to **{seconds}s**.")


# ── !lock / !unlock ───────────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx):
    track_cmd("lock")
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 Channel locked.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    track_cmd("unlock")
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
    await ctx.send("🔓 Channel unlocked.")


# ================================================================
#  AUTO-MOD COMMANDS
# ================================================================

@bot.group(name="automod", invoke_without_command=True)
@commands.has_permissions(manage_guild=True)
async def automod(ctx):
    gid = str(ctx.guild.id)
    cfg = automod_cfg.get(gid, {})
    status = "✅ Enabled" if cfg.get("enabled") else "❌ Disabled"
    words = ", ".join(f"`{w}`" for w in cfg.get("words", [])) or "None"
    spam = cfg.get("spam_limit", 5)
    exempt = ", ".join(f"<@&{r}>" for r in cfg.get("exempt_roles", [])) or "None"
    embed = discord.Embed(title="🤖 Auto-Mod Config", color=discord.Color.blurple())
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Spam Limit", value=f"{spam} msgs / 5s", inline=True)
    embed.add_field(name="Exempt Roles", value=exempt, inline=False)
    embed.add_field(name="Blocked Words", value=words, inline=False)
    await ctx.send(embed=embed)

@automod.command(name="on")
@commands.has_permissions(manage_guild=True)
async def automod_on(ctx):
    gid = str(ctx.guild.id)
    automod_cfg.setdefault(gid, {})["enabled"] = True
    _save(AUTOMOD_FILE, automod_cfg)
    await ctx.send("✅ Auto-mod **enabled**.")

@automod.command(name="off")
@commands.has_permissions(manage_guild=True)
async def automod_off(ctx):
    gid = str(ctx.guild.id)
    automod_cfg.setdefault(gid, {})["enabled"] = False
    _save(AUTOMOD_FILE, automod_cfg)
    await ctx.send("❌ Auto-mod **disabled**.")

@automod.command(name="addword")
@commands.has_permissions(manage_guild=True)
async def automod_addword(ctx, *, word: str):
    gid = str(ctx.guild.id)
    automod_cfg.setdefault(gid, {}).setdefault("words", [])
    if word.lower() not in automod_cfg[gid]["words"]:
        automod_cfg[gid]["words"].append(word.lower())
        _save(AUTOMOD_FILE, automod_cfg)
        await ctx.send(f"✅ Added `{word}` to blocked words.")
    else:
        await ctx.send("⚠️ Word already in list.")

@automod.command(name="removeword")
@commands.has_permissions(manage_guild=True)
async def automod_removeword(ctx, *, word: str):
    gid = str(ctx.guild.id)
    words = automod_cfg.get(gid, {}).get("words", [])
    if word.lower() in words:
        words.remove(word.lower())
        _save(AUTOMOD_FILE, automod_cfg)
        await ctx.send(f"✅ Removed `{word}` from blocked words.")
    else:
        await ctx.send("❌ Word not found.")

@automod.command(name="spamlimt", aliases=["spam"])
@commands.has_permissions(manage_guild=True)
async def automod_spam(ctx, limit: int = 5):
    gid = str(ctx.guild.id)
    automod_cfg.setdefault(gid, {})["spam_limit"] = max(3, limit)
    _save(AUTOMOD_FILE, automod_cfg)
    await ctx.send(f"✅ Spam limit set to **{limit}** messages per 5 seconds.")

@automod.command(name="exempt")
@commands.has_permissions(manage_guild=True)
async def automod_exempt(ctx, role: discord.Role):
    gid = str(ctx.guild.id)
    exempt = automod_cfg.setdefault(gid, {}).setdefault("exempt_roles", [])
    if str(role.id) not in exempt:
        exempt.append(str(role.id))
        _save(AUTOMOD_FILE, automod_cfg)
        await ctx.send(f"✅ {role.mention} is now **exempt** from auto-mod.")
    else:
        await ctx.send("⚠️ Role is already exempt.")

@automod.command(name="unexempt")
@commands.has_permissions(manage_guild=True)
async def automod_unexempt(ctx, role: discord.Role):
    gid = str(ctx.guild.id)
    exempt = automod_cfg.get(gid, {}).get("exempt_roles", [])
    if str(role.id) in exempt:
        exempt.remove(str(role.id))
        _save(AUTOMOD_FILE, automod_cfg)
        await ctx.send(f"✅ {role.mention} is no longer exempt.")
    else:
        await ctx.send("❌ Role was not exempt.")


# ================================================================
#  ROLE MANAGEMENT
# ================================================================

@bot.command()
@commands.has_permissions(manage_roles=True)
async def giverole(ctx, member: discord.Member, *, role: discord.Role):
    track_cmd("giverole")
    await member.add_roles(role)
    await ctx.send(f"✅ Gave **{role.name}** to **{member}**.")
    log_embed = discord.Embed(
        title="🎭 Role Added",
        description=f"**{role.name}** → {member.mention}\n**Mod:** {ctx.author}",
        color=discord.Color.green(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, *, role: discord.Role):
    track_cmd("removerole")
    await member.remove_roles(role)
    await ctx.send(f"✅ Removed **{role.name}** from **{member}**.")
    log_embed = discord.Embed(
        title="🎭 Role Removed",
        description=f"**{role.name}** ← {member.mention}\n**Mod:** {ctx.author}",
        color=discord.Color.red(), timestamp=datetime.now(timezone.utc)
    )
    await log_action(ctx.guild, log_embed)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def autorole(ctx, role: discord.Role = None):
    track_cmd("autorole")
    gid = str(ctx.guild.id)
    if role is None:
        current = autorole_cfg.get(gid)
        if current:
            r = ctx.guild.get_role(int(current))
            await ctx.send(f"📌 Auto-role is: **{r.name if r else 'Unknown'}**")
        else:
            await ctx.send("📌 No auto-role set.")
    else:
        autorole_cfg[gid] = str(role.id)
        _save(AUTOROLE_FILE, autorole_cfg)
        await ctx.send(f"✅ Auto-role set to **{role.name}** — new members will get this role.")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def clearautorole(ctx):
    track_cmd("clearautorole")
    gid = str(ctx.guild.id)
    autorole_cfg.pop(gid, None)
    _save(AUTOROLE_FILE, autorole_cfg)
    await ctx.send("✅ Auto-role cleared.")


# ================================================================
#  REMINDERS
# ================================================================
@bot.command(aliases=['remind', 'rm'])
async def reminder(ctx, minutes: float, *, text: str):
    track_cmd("reminder")
    fire_at = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    entry = {
        "user_id": ctx.author.id,
        "channel_id": ctx.channel.id,
        "time": fire_at,
        "text": text
    }
    reminders.append(entry)
    _save(REMINDERS_FILE, reminders)
    embed = discord.Embed(
        title="⏰ Reminder Set",
        description=f"I'll remind you in **{minutes} min**:\n> {text}",
        color=discord.Color.blurple()
    )
    await ctx.send(embed=embed)

@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.now(timezone.utc)
    due = [r for r in reminders if datetime.fromisoformat(r["time"]) <= now]
    for r in due:
        channel = bot.get_channel(r["channel_id"])
        if channel:
            try:
                embed = discord.Embed(
                    title="⏰ Reminder!",
                    description=f"<@{r['user_id']}> — {r['text']}",
                    color=discord.Color.gold()
                )
                await channel.send(embed=embed)
            except Exception:
                pass
        reminders.remove(r)
    if due:
        _save(REMINDERS_FILE, reminders)


# ================================================================
#  UTILITY TOOLS
# ================================================================

# ── !serverinfo ───────────────────────────────────────────────────
@bot.command(aliases=['si'])
async def serverinfo(ctx):
    track_cmd("serverinfo")
    g = ctx.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
    embed.set_thumbnail(url=g.icon.url if g.icon else "")
    embed.add_field(name="👑 Owner", value=str(g.owner), inline=True)
    embed.add_field(name="👥 Members", value=g.member_count, inline=True)
    embed.add_field(name="💬 Channels", value=len(g.text_channels), inline=True)
    embed.add_field(name="🔊 Voice", value=len(g.voice_channels), inline=True)
    embed.add_field(name="🎭 Roles", value=len(g.roles), inline=True)
    embed.add_field(name="😀 Emojis", value=len(g.emojis), inline=True)
    embed.add_field(name="📅 Created", value=discord.utils.format_dt(g.created_at, 'D'), inline=False)
    embed.set_footer(text=f"ID: {g.id}")
    await ctx.send(embed=embed)


# ── !userinfo ─────────────────────────────────────────────────────
@bot.command(aliases=['ui', 'whois'])
async def userinfo(ctx, member: discord.Member = None):
    track_cmd("userinfo")
    m = member or ctx.author
    roles = [r.mention for r in m.roles if r != ctx.guild.default_role]
    embed = discord.Embed(title=str(m), color=m.color, timestamp=datetime.now(timezone.utc))
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="🆔 ID", value=m.id, inline=True)
    embed.add_field(name="🤖 Bot", value="Yes" if m.bot else "No", inline=True)
    embed.add_field(name="📅 Joined", value=discord.utils.format_dt(m.joined_at, 'D') if m.joined_at else "N/A", inline=True)
    embed.add_field(name="🗓️ Created", value=discord.utils.format_dt(m.created_at, 'D'), inline=True)
    embed.add_field(name="⚠️ Warns", value=len(get_warns(str(ctx.guild.id), str(m.id))), inline=True)
    embed.add_field(name=f"🎭 Roles ({len(roles)})", value=" ".join(roles[:10]) or "None", inline=False)
    await ctx.send(embed=embed)


# ── !avatar ───────────────────────────────────────────────────────
@bot.command(aliases=['av', 'pfp'])
async def avatar(ctx, member: discord.Member = None):
    track_cmd("avatar")
    m = member or ctx.author
    embed = discord.Embed(title=f"{m}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=m.display_avatar.url)
    await ctx.send(embed=embed)


# ── !poll ─────────────────────────────────────────────────────────
@bot.command()
async def poll(ctx, question: str, *options):
    track_cmd("poll")
    if len(options) < 2:
        return await ctx.send("❌ Provide at least 2 options. Usage: `!poll \"Question\" \"Yes\" \"No\"`")
    if len(options) > 9:
        return await ctx.send("❌ Maximum 9 options.")
    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
    desc = "\n".join(f"{emojis[i]} {opt}" for i, opt in enumerate(options))
    embed = discord.Embed(title=f"📊 {question}", description=desc, color=discord.Color.blurple())
    embed.set_footer(text=f"Poll by {ctx.author.display_name}")
    msg = await ctx.send(embed=embed)
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])


# ── !say ──────────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_messages=True)
async def say(ctx, *, text: str):
    track_cmd("say")
    await ctx.message.delete()
    await ctx.send(text)


# ── !embed ────────────────────────────────────────────────────────
@bot.command()
@commands.has_permissions(manage_messages=True)
async def embed(ctx, title: str, *, description: str):
    track_cmd("embed")
    e = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    await ctx.send(embed=e)
    await ctx.message.delete()


# ── !coinflip ─────────────────────────────────────────────────────
@bot.command(aliases=['flip', 'coin'])
async def coinflip(ctx):
    track_cmd("coinflip")
    import random
    result = random.choice(["🪙 Heads!", "🪙 Tails!"])
    await ctx.send(result)


# ── !8ball ────────────────────────────────────────────────────────
@bot.command(name="8ball", aliases=["eightball"])
async def eightball(ctx, *, question: str):
    track_cmd("8ball")
    import random
    responses = [
        "✅ It is certain.", "✅ Without a doubt.", "✅ Yes, definitely.",
        "✅ Most likely.", "🤔 Reply hazy, try again.", "🤔 Cannot predict now.",
        "❌ Don't count on it.", "❌ Very doubtful.", "❌ My reply is no."
    ]
    embed = discord.Embed(
        title="🎱 Magic 8-Ball",
        description=f"**Q:** {question}\n**A:** {random.choice(responses)}",
        color=discord.Color.purple()
    )
    await ctx.send(embed=embed)


# ================================================================
#  ANALYTICS & UPTIME
# ================================================================

@bot.command(aliases=['up'])
async def uptime(ctx):
    track_cmd("uptime")
    elapsed = time.time() - BOT_START_TIME
    hours, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)
    embed = discord.Embed(
        title="📊 Bot Uptime & Stats",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="⏱️ Uptime", value=f"`{hours}h {mins}m {secs}s`", inline=True)
    embed.add_field(name="🏓 Latency", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    embed.add_field(name="🖥️ Servers", value=f"`{len(bot.guilds)}`", inline=True)
    embed.add_field(name="👥 Users", value=f"`{sum(g.member_count for g in bot.guilds)}`", inline=True)
    embed.set_footer(text=str(bot.user), icon_url=bot.user.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command(aliases=['analytics', 'stats'])
@commands.has_permissions(manage_guild=True)
async def insights(ctx):
    track_cmd("insights")
    if not cmd_stats:
        return await ctx.send("📊 No command stats yet.")
    sorted_cmds = sorted(cmd_stats.items(), key=lambda x: x[1], reverse=True)[:10]
    total = sum(cmd_stats.values())
    lines = "\n".join(f"`{i+1}.` **{cmd}** — {count} uses" for i, (cmd, count) in enumerate(sorted_cmds))
    embed = discord.Embed(
        title="📊 Command Insights (Top 10)",
        description=lines,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Total commands used: {total}")
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(manage_guild=True)
async def botlogs(ctx, lines: int = 20):
    track_cmd("botlogs")
    log_path = os.path.join(log_dir, "bot.log")
    if not os.path.exists(log_path):
        return await ctx.send("❌ No log file found.")
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.readlines()
    tail = content[-lines:]
    text = "".join(tail)
    if len(text) > 1900:
        text = text[-1900:]
    await ctx.send(f"```\n{text}\n```")


# ================================================================
#  MUSIC COMMANDS
# ================================================================

async def _yt_extract(search: str) -> dict | None:
    """Try primary yt-dlp options, fall back to fallback options on bot block."""
    loop = asyncio.get_event_loop()
    for opts, label in [(YTDL_OPTIONS, 'primary'), (YTDL_OPTIONS_FALLBACK, 'fallback')]:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda o=opts: yt_dlp.YoutubeDL(o).extract_info(search, download=False)
                )
            if info and 'entries' in info:
                info = info['entries'][0]
            logger.info(f'[yt-dlp] ✅ Extracted via {label}: {info.get("title")}')
            return info
        except Exception as e:
            err = str(e)
            logger.warning(f'[yt-dlp] ❌ {label} failed: {err}')
            if 'Sign in' not in err and 'bot' not in err.lower():
                # Not a bot-block error — no point retrying
                return None
    return None

@bot.command()
@commands.is_owner()
async def ping(ctx):
    track_cmd("ping")
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")


@bot.command(aliases=['j'])
async def join(ctx):
    track_cmd("join")
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
    track_cmd("play")
    global now_playing_channel
    if not ctx.voice_client:
        await ctx.invoke(join)

    if not search:
        return await ctx.send("Usage: !p <song name>")

    if "spotify.com" in search:
        if not sp:
            return await ctx.send("Spotify credentials are not configured yet.")
        try:
            if "track" in search:
                track = sp.track(search)
                search = f"{track['name']} {track['artists'][0]['name']} audio"
            elif "playlist" in search:
                playlist = sp.playlist_tracks(search, limit=1)
                if playlist['items']:
                    track = playlist['items'][0]['track']
                    search = f"{track['name']} {track['artists'][0]['name']} audio"
                    await ctx.send("🔍 Playlist link detected! I will play the **first track** for now.")
                else:
                    return await ctx.send("Playlist is empty.")
            elif "album" in search:
                album = sp.album_tracks(search, limit=1)
                if album['items']:
                    track = album['items'][0]
                    search = f"{track['name']} {track['artists'][0]['name']} audio"
                    await ctx.send("🔍 Album link detected! I will play the **first track** for now.")
        except Exception as e:
            return await ctx.send(f"Error reading Spotify link: {e}")

    async with ctx.typing():
        info = await _yt_extract(search)
        if info is None:
            return await ctx.send("❌ Could not find or stream that song. YouTube may be blocking requests — try again or use a different search term.")

        song_data = {
            'url': info['url'],
            'title': info['title'],
            'thumbnail': info.get('thumbnail'),
            'duration': info.get('duration'),
        }

        now_playing_channel = ctx.channel

        if not ctx.voice_client.is_playing():
            song_queue.append(song_data)
            play_next(ctx)
        else:
            song_queue.append(song_data)
            embed = discord.Embed(
                title="➕ Added to Queue",
                description=f"**{info['title']}**",
                color=discord.Color.from_rgb(57, 197, 187)
            )
            if song_data['thumbnail']:
                embed.set_thumbnail(url=song_data['thumbnail'])
            await ctx.send(embed=embed)


@bot.command(aliases=['pn'])
async def playnext(ctx, *, search=None):
    track_cmd("playnext")
    global now_playing_channel
    if not ctx.voice_client:
        await ctx.invoke(join)

    if not search:
        return await ctx.send("Usage: !pn <song name>")

    if "spotify.com" in search:
        if not sp:
            return await ctx.send("Spotify credentials are not configured yet.")
        try:
            if "track" in search:
                track = sp.track(search)
                search = f"{track['name']} {track['artists'][0]['name']} audio"
            elif "playlist" in search:
                playlist = sp.playlist_tracks(search, limit=1)
                if playlist['items']:
                    track = playlist['items'][0]['track']
                    search = f"{track['name']} {track['artists'][0]['name']} audio"
                    await ctx.send("🔍 Playlist link detected! I will queue the **first track** for now.")
                else:
                    return await ctx.send("Playlist is empty.")
            elif "album" in search:
                album = sp.album_tracks(search, limit=1)
                if album['items']:
                    track = album['items'][0]
                    search = f"{track['name']} {track['artists'][0]['name']} audio"
                    await ctx.send("🔍 Album link detected! I will queue the **first track** for now.")
        except Exception as e:
            return await ctx.send(f"Error reading Spotify link: {e}")

    async with ctx.typing():
        info = await _yt_extract(search)
        if info is None:
            return await ctx.send("❌ Could not find or stream that song. YouTube may be blocking requests — try again or use a different search term.")

        song_data = {
            'url': info['url'],
            'title': info['title'],
            'thumbnail': info.get('thumbnail'),
            'duration': info.get('duration'),
        }

        now_playing_channel = ctx.channel

        if not ctx.voice_client.is_playing():
            song_queue.append(song_data)
            play_next(ctx)
        else:
            song_queue.insert(0, song_data)
            embed = discord.Embed(
                title="⏭️ Queued Next",
                description=f"**{info['title']}**",
                color=discord.Color.from_rgb(250, 166, 26)
            )
            if song_data['thumbnail']:
                embed.set_thumbnail(url=song_data['thumbnail'])
            await ctx.send(embed=embed)


@bot.command(aliases=['q'])
async def queue(ctx):
    track_cmd("queue")
    if not song_queue:
        return await ctx.send("Queue is empty.")
    text = ""
    for i, song in enumerate(song_queue[:10]):
        text += f"{i+1}. {song['title']}\n"
    embed = discord.Embed(title="Upcoming Songs", description=text, color=discord.Color.blue())
    await ctx.send(embed=embed)


@bot.command(aliases=['s'])
async def skip(ctx):
    track_cmd("skip")
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭ Skipped.")
    else:
        await ctx.send("Nothing is playing.")


@bot.command(aliases=['pa'])
async def pause(ctx):
    track_cmd("pause")
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸ Paused.")
    else:
        await ctx.send("Nothing is playing.")


@bot.command(aliases=['res'])
async def resume(ctx):
    track_cmd("resume")
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed.")
    else:
        await ctx.send("Nothing is paused.")


@bot.command(aliases=['l', 'stop'])
async def leave(ctx):
    track_cmd("leave")
    global loop_mode, current_song
    song_queue.clear()
    loop_mode = "off"
    current_song = None
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected 👋.")


@bot.command(aliases=['lp'])
async def loop(ctx, mode=None):
    track_cmd("loop")
    global loop_mode
    if mode is None:
        if loop_mode == "off":
            loop_mode = "song"
            await ctx.send("🔂 Looping **current song**.")
        else:
            loop_mode = "off"
            await ctx.send("➡️ Loop **off**.")
    elif mode.lower() in ["queue", "q", "all"]:
        if loop_mode == "queue":
            loop_mode = "off"
            await ctx.send("➡️ Queue loop **off**.")
        else:
            loop_mode = "queue"
            await ctx.send("🔁 Looping **entire queue**.")
    elif mode.lower() in ["song", "s", "current", "one"]:
        if loop_mode == "song":
            loop_mode = "off"
            await ctx.send("➡️ Song loop **off**.")
        else:
            loop_mode = "song"
            await ctx.send("🔂 Looping **current song**.")
    elif mode.lower() in ["off", "none", "disable"]:
        loop_mode = "off"
        await ctx.send("➡️ Loop **off**.")
    else:
        await ctx.send("Usage: `!loop` / `!loop song` / `!loop queue` / `!loop off`")


# ================================================================
#  ADMIN COMMANDS
# ================================================================

@bot.command(aliases=['st'])
@commands.is_owner()
async def stopbot(ctx):
    await ctx.send("Shutting down.")
    await bot.close()


@bot.command(aliases=['rs'])
@commands.is_owner()
async def restart(ctx):
    log_channel = await get_or_create_log_channel(ctx.guild)
    await log_channel.send("🔄 Restarting bot...")
    with open(RESTART_FLAG, "w") as f:
        f.write("1")
    os.execv(sys.executable, ['python'] + sys.argv)


# ================================================================
#  GEMINI AI
# ================================================================
gemini_client = genai.Client(api_key=os.getenv('key'))

@bot.command(aliases=['ai', 'gemini'])
async def ask(ctx, *, question=None):
    track_cmd("ask")
    if not question:
        return await ctx.send("Usage: `!ask <your question>`")

    async with ctx.typing():
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=question
            )
            answer = response.text
            if len(answer) > 1900:
                answer = answer[:1900] + "..."
            await ctx.send(answer)
        except Exception as e:
            await ctx.send(f"Error: {e}")


# ================================================================
#  HELP
# ================================================================
@bot.command(aliases=['h'])
async def help(ctx):
    track_cmd("help")
    embed = discord.Embed(
        title="📖 Musico Bot — Help",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(name="🎵 Music", value=(
        "`!j` — Join voice\n"
        "`!p <song>` — Play music\n"
        "`!pn <song>` — Play next\n"
        "`!q` — Queue\n"
        "`!s` — Skip\n"
        "`!pa` — Pause\n"
        "`!res` — Resume\n"
        "`!lp [song/queue/off]` — Loop\n"
        "`!l` — Leave"
    ), inline=True)

    embed.add_field(name="🛡️ Moderation", value=(
        "`!kick <@user> [reason]`\n"
        "`!ban <@user> [reason]`\n"
        "`!unban <id>`\n"
        "`!mute <@user> [mins]`\n"
        "`!unmute <@user>`\n"
        "`!warn <@user> [reason]`\n"
        "`!warns <@user>`\n"
        "`!clearwarns <@user>`\n"
        "`!clear [amt]` — Purge\n"
        "`!slowmode [secs]`\n"
        "`!lock` / `!unlock`"
    ), inline=True)

    embed.add_field(name="🤖 Auto-Mod", value=(
        "`!automod` — Show config\n"
        "`!automod on/off`\n"
        "`!automod addword <word>`\n"
        "`!automod removeword <word>`\n"
        "`!automod spam <limit>`\n"
        "`!automod exempt <@role>`\n"
        "`!automod unexempt <@role>`"
    ), inline=True)

    embed.add_field(name="🎭 Roles", value=(
        "`!giverole <@user> <role>`\n"
        "`!removerole <@user> <role>`\n"
        "`!autorole [role]` — Auto-role on join\n"
        "`!clearautorole`"
    ), inline=True)

    embed.add_field(name="🛠️ Utility", value=(
        "`!si` — Server info\n"
        "`!ui [@user]` — User info\n"
        "`!av [@user]` — Avatar\n"
        "`!poll \"Q\" \"A\" \"B\"`\n"
        "`!say <text>`\n"
        "`!embed \"Title\" <desc>`\n"
        "`!flip` — Coin flip\n"
        "`!8ball <q>` — Magic 8-ball\n"
        "`!rm <mins> <text>` — Reminder"
    ), inline=True)

    embed.add_field(name="📊 Analytics", value=(
        "`!uptime` — Uptime & stats\n"
        "`!insights` — Top commands\n"
        "`!botlogs [lines]` — Bot logs"
    ), inline=True)

    embed.add_field(name="🤖 AI & Admin", value=(
        "`!ask <q>` — Gemini AI\n"
        "`!rs` — Restart (owner)\n"
        "`!st` — Stop (owner)"
    ), inline=True)

    embed.set_footer(text="Use !<command> to run a command")
    await ctx.send(embed=embed)


# ================================================================
#  Error handler
# ================================================================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.", delete_after=5)
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found.", delete_after=5)
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Bad argument: {error}", delete_after=5)
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore unknown commands silently
    else:
        logger.error(f"Unhandled error in {ctx.command}: {error}")


# ---------------- Run ----------------
bot.run(token)
