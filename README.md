# 🎵 Musico - Discord Music Bot

Musico is a feature-rich, low-latency Discord music bot built with Python. It seamlessly streams audio from YouTube, Spotify, and Apple Music directly into your Discord voice channels, providing an uninterrupted high-quality music experience.

## ✨ Features

- **Multi-Source Support:** Play music from YouTube URLs, YouTube search queries, Spotify links, and Apple Music links.
- **Smart Link Resolving:** Scrapes Open Graph tags for Spotify and Apple Music playlists/songs and lazy-loads them to ensure fast queueing.
- **Robust Playback:** Built on `yt-dlp` and `FFmpeg` for stable, high-quality audio streaming.
- **Advanced YouTube Parsing:** Bypasses YouTube bot protection using a `cookies.txt` file and multiple extraction fallbacks.
- **Interactive UI:** Sends elegant "Now Playing" embeds with song titles, thumbnails, and durations.
- **Queue Management:** Manage your music with custom queueing, skip, pause, resume, and queue clearing functionalities.
- **Loop Modes:** Toggle looping between a single song, the entire queue, or completely off.

## 🚀 Tech Stack

- **[Python 3](https://www.python.org/)** - Core programming language
- **[discord.py](https://github.com/Rapptz/discord.py)** - Official API wrapper for Discord
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** - Video/Audio extractor for YouTube
- **[aiohttp](https://docs.aiohttp.org/)** - Asynchronous HTTP client for scraping OG tags.
- **[FFmpeg](https://ffmpeg.org/)** - Multimedia framework to stream audio formats.

## 🛠️ Installation & Setup

### 1. Prerequisites
- Python 3.8+
- [FFmpeg](https://ffmpeg.org/download.html) installed and accessible. (Update the `FFmpeg` binary path in `main.py` if not in your System environment).

### 2. Clone the Repository
```bash
git clone https://github.com/woott07/my-music-bot
cd my-music-bot
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configuration
Create a `.env` file in the root directory and add your Discord bot token:
```env
d_t=YOUR_DISCORD_BOT_TOKEN
```

If you are experiencing blocking issues with YouTube, you can export your browser cookies as a `cookies.txt` file and place it in the same directory as `main.py`. This step is highly recommended for reliable playback.

### 5. Run the Bot
```bash
python main.py
```

## 📜 Commands List

The bot uses `!` as the default prefix (or when pinged).

| Command | Aliases | Description |
|---|---|---|
| `!play <query/url>` | `!p`, `!add` | Play a song or add it to the queue. (Spotify/Apple Music supported) |
| `!skip` | `!s` | Skip the currently playing song. |
| `!pause` | `!pa` | Pause the current playback. |
| `!resume` | `!res` | Resume the paused playback. |
| `!queue` | `!q` | Display the upcoming queue of songs. |
| `!nowplaying`| `!np`, `!now`| Show details of the currently playing track. |
| `!loop [mode]` | `!lp` | Toggle loop modes: `song`, `queue`, or `off`. |
| `!clear` | `!cl` | Clear the entire queue (keeps the current song). |
| `!join` | `!j` | Make the bot join your current voice channel. |
| `!leave` | `!l`, `!stop`| Disconnect the bot and clear the queue. |

## ⚠️ Notes / Troubleshooting

- **Error: Code 4017 / Missing Permissions:** Make sure the bot role has "Connect" and "Speak" permissions in the targeted voice channel.
- **YouTube Error 403 / Video Unavailable:** Ensure your `cookies.txt` file is updated. YouTube updates security measures rapidly which leads to old cookies expiring.
- **Missing FFMPEG:** Ensure the `FFMPEG_EXE` variable inside `main.py` accurately points to your local FFmpeg executable.

## 🤝 Contributing
Contributions, issues, and feature requests are welcome! Feel free to check the issues page.
