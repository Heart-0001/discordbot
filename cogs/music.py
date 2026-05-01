import asyncio
import json
import logging
import re
import sys
import urllib.parse
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

FFMPEG_PATH = r'C:\Users\Heart\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe'

FFMPEG_BEFORE_OPTS = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -thread_queue_size 4096 -probesize 32'
FFMPEG_OPTS = '-vn -b:a 96k'  # 限制輸出 96kbps，符合 Discord 上限


def make_source(url: str, volume: float) -> discord.PCMVolumeTransformer:
    return discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            url,
            executable=FFMPEG_PATH,
            before_options=FFMPEG_BEFORE_OPTS,
            options=FFMPEG_OPTS,
        ),
        volume=volume,
    )


class GuildMusicState:
    def __init__(self):
        self.queue: list[dict] = []
        self.current: Optional[dict] = None
        self.volume: float = 0.5
        self.autoplay: bool = False
        self.history: list[str] = []       # 最近播過的 webpage_url（避免 autoplay 重複）
        self.autoplay_prefetch: Optional[dict] = None  # 預載好的下一首（含串流 URL）


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._states: dict[int, GuildMusicState] = {}

    def get_state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildMusicState()
        return self._states[guild_id]

    async def _run_ytdlp(self, args: list, timeout: int = 30) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise Exception('取得音樂超時')
        if not stdout:
            err = stderr.decode('utf-8', errors='replace')
            raise Exception(err[:300] or '無法取得音樂')
        return stdout.decode('utf-8', errors='replace')

    def _parse_ytdlp_lines(self, text: str, stream_url: bool = True) -> list[dict]:
        results = []
        for line in text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                # flat-playlist entries have 'id', full entries have 'url'
                webpage = d.get('webpage_url') or (
                    f"https://www.youtube.com/watch?v={d['id']}" if d.get('id') else ''
                )
                results.append({
                    'url': d.get('url', '') if stream_url else '',
                    'webpage_url': webpage,
                    'title': d.get('title', 'Unknown'),
                    'duration': d.get('duration', 0),
                    'thumbnail': d.get('thumbnail', ''),
                    'uploader': d.get('uploader', ''),
                })
            except (json.JSONDecodeError, KeyError):
                continue
        return results

    async def _ytmusic_search(self, query: str) -> list[dict]:
        """從 YouTube Music 搜尋，回傳第一個結果；失敗回傳空串列。"""
        try:
            encoded = urllib.parse.quote(query)
            out = await self._run_ytdlp([
                sys.executable, '-m', 'yt_dlp',
                '--flat-playlist', '--dump-json', '--quiet', '--no-warnings',
                '--playlist-items', '1',
                f'https://music.youtube.com/search?q={encoded}',
            ], timeout=15)
            results = self._parse_ytdlp_lines(out, stream_url=False)
            if results:
                log.info(f'YouTube Music 結果: {results[0]["title"]}')
            return results[:1]
        except Exception as e:
            log.info(f'YouTube Music 搜尋失敗 (退回 YouTube): {e}')
            return []

    async def fetch_info(self, query: str) -> list[dict]:
        is_url = query.startswith('http')

        # Radio Mix URL (list=RD...) → 只播 v= 那首影片，autoplay 會自己接推薦
        if is_url and re.search(r'[?&]list=RD', query):
            m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', query)
            if m:
                query = f'https://www.youtube.com/watch?v={m.group(1)}'
                log.info(f'Radio Mix URL → 只播單首: {query}')

        is_playlist = is_url and 'list=' in query

        log.info(f'fetch_info 開始: {query[:80]}')

        if is_playlist:
            # Fast path: flat-playlist (just metadata, no stream URLs)
            out = await self._run_ytdlp([
                sys.executable, '-m', 'yt_dlp',
                '--flat-playlist', '--dump-json', '--quiet', '--no-warnings',
                '--playlist-items', '1:50',
                query,
            ], timeout=30)
            results = self._parse_ytdlp_lines(out, stream_url=False)
        else:
            if is_url:
                # 直接抓指定 URL
                out = await self._run_ytdlp([
                    sys.executable, '-m', 'yt_dlp',
                    '--dump-json', '--quiet', '--no-warnings',
                    '--no-playlist',
                    '--format', 'bestaudio[abr<=96]/bestaudio/best',
                    '--ffmpeg-location', FFMPEG_PATH,
                    query,
                ], timeout=30)
                results = self._parse_ytdlp_lines(out, stream_url=True)
            else:
                # 優先用 YouTube Music（音源導向），失敗才退回 YouTube
                results = await self._ytmusic_search(query)
                if not results:
                    out = await self._run_ytdlp([
                        sys.executable, '-m', 'yt_dlp',
                        '--dump-json', '--quiet', '--no-warnings',
                        '--no-playlist',
                        '--format', 'bestaudio[abr<=96]/bestaudio/best',
                        '--ffmpeg-location', FFMPEG_PATH,
                        f'ytsearch1:{query}',
                    ], timeout=30)
                    results = self._parse_ytdlp_lines(out, stream_url=True)

        log.info(f'fetch_info 完成: {len(results)} 首')
        return results

    async def fetch_stream_url(self, webpage_url: str) -> str:
        """播放前取得實際串流 URL。"""
        out = await self._run_ytdlp([
            sys.executable, '-m', 'yt_dlp',
            '--dump-json', '--quiet', '--no-warnings',
            '--no-playlist',
            '--format', 'bestaudio[abr<=96]/bestaudio/best',
            '--ffmpeg-location', FFMPEG_PATH,
            webpage_url,
        ], timeout=30)
        data = json.loads(out.strip().split('\n')[0])
        return data['url']

    def _after_play(self, error, guild_id: int, voice_client: discord.VoiceClient):
        if error:
            log.error(f'播放回呼錯誤: {error}')
        asyncio.run_coroutine_threadsafe(
            self._play_next(guild_id, voice_client), self.bot.loop
        )

    async def _get_autoplay_songs(self, webpage_url: str, history: list[str]) -> list[dict]:
        """根據目前歌曲取得 YouTube 推薦的下一首（跳過已播過的）。"""
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', webpage_url)
        if not match:
            return []
        video_id = match.group(1)
        mix_url = f'https://www.youtube.com/watch?v={video_id}&list=RD{video_id}&start_radio=1'
        try:
            # 多抓幾首備用，才能過濾掉已播過的
            out = await self._run_ytdlp([
                sys.executable, '-m', 'yt_dlp',
                '--flat-playlist', '--dump-json', '--quiet', '--no-warnings',
                '--playlist-items', '2:8',
                mix_url,
            ], timeout=20)
            candidates = self._parse_ytdlp_lines(out, stream_url=False)
            # 過濾掉最近播過的歌
            history_set = set(history)
            filtered = [s for s in candidates if s['webpage_url'] not in history_set]
            return filtered[:1] if filtered else candidates[:1]
        except Exception as e:
            log.error(f'Autoplay 取得推薦失敗: {e}')
            return []

    async def _prefetch_autoplay(self, guild_id: int):
        """趁目前歌曲還在播，背景預載下一首 autoplay（含 YTMusic 音源 + 串流 URL）。"""
        state = self.get_state(guild_id)
        if state.autoplay_prefetch:
            return  # 已有預載，不重複
        if not state.current:
            return  # 歌已停止，不需要預載
        try:
            # 1. 從 YouTube Mix 取得推薦
            candidates = await self._get_autoplay_songs(
                state.current['webpage_url'], state.history
            )
            if not candidates:
                return
            candidate = candidates[0]

            # 2. 用標題去 YouTube Music 找音源版
            ytm = await self._ytmusic_search(candidate['title'])
            song = ytm[0] if ytm else candidate
            log.info(f'Autoplay 預載: {"YTMusic" if ytm else "YouTube"} → {song["title"]}')

            # 3. 抓串流 URL
            if not song.get('url'):
                song['url'] = await self.fetch_stream_url(song['webpage_url'])

            state.autoplay_prefetch = song
            log.info(f'Autoplay 預載完成: {song["title"]}')
        except Exception as e:
            log.error(f'Autoplay 預載失敗: {e}')

    async def _play_next(self, guild_id: int, voice_client: discord.VoiceClient):
        if not voice_client.is_connected():
            return

        state = self.get_state(guild_id)

        # 隊列空了且 autoplay 開啟
        if not state.queue and state.autoplay and state.current:
            if state.autoplay_prefetch:
                # 預載好了，直接用（零等待）
                log.info('Autoplay: 使用預載歌曲')
                state.queue.append(state.autoplay_prefetch)
                state.autoplay_prefetch = None
            else:
                # 來不及預載，即時抓（備用路徑）
                log.info('Autoplay: 即時抓取推薦...')
                new_songs = await self._get_autoplay_songs(
                    state.current['webpage_url'], state.history
                )
                if new_songs:
                    state.queue.extend(new_songs)

        if not state.queue:
            state.current = None
            return

        next_song = state.queue.pop(0)

        # 取得串流 URL（預載的歌已有，flat 結果沒有）
        if not next_song.get('url'):
            try:
                next_song['url'] = await self.fetch_stream_url(next_song['webpage_url'])
            except Exception as e:
                log.error(f'重新取得 URL 失敗，跳過: {e}')
                await self._play_next(guild_id, voice_client)
                return

        state.current = next_song

        try:
            source = make_source(next_song['url'], state.volume)
            voice_client.play(source, after=lambda e: self._after_play(e, guild_id, voice_client))
            log.info(f'開始播放: {next_song["title"]}')
            # 記錄播放歷史（最多保留 20 首）
            if next_song.get('webpage_url'):
                state.history.append(next_song['webpage_url'])
                if len(state.history) > 20:
                    state.history.pop(0)
            # 歌開始播就立刻在背景預載下一首
            if state.autoplay and not state.queue and not state.autoplay_prefetch:
                asyncio.ensure_future(self._prefetch_autoplay(guild_id))
            elif state.queue and not state.queue[0].get('url'):
                asyncio.ensure_future(self._prefetch_next(state))
        except Exception as e:
            log.error(f'播放失敗: {e}')

    async def _prefetch_next(self, state: 'GuildMusicState'):
        if not state.queue:
            return
        next_song = state.queue[0]
        if next_song.get('url'):
            return
        try:
            next_song['url'] = await self.fetch_stream_url(next_song['webpage_url'])
            log.info(f'預載完成: {next_song["title"]}')
        except Exception as e:
            log.error(f'預載失敗: {e}')

    def fmt_duration(self, seconds) -> str:
        if not seconds:
            return '未知'
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'

    def song_embed(self, title: str, song: dict, color: discord.Color) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=f'[{song["title"]}]({song["webpage_url"]})',
            color=color,
        )
        embed.add_field(name='長度', value=self.fmt_duration(song['duration']))
        if song['uploader']:
            embed.add_field(name='頻道', value=song['uploader'])
        if song['thumbnail']:
            embed.set_thumbnail(url=song['thumbnail'])
        return embed

    @app_commands.command(name='play', description='播放 YouTube / YouTube Music 音樂（網址或搜尋）')
    @app_commands.describe(query='YouTube 連結或歌曲名稱關鍵字')
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            await interaction.response.send_message('❌ 請先加入一個語音頻道！', ephemeral=True)
            return

        await interaction.response.defer()

        vc = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect()
        elif vc.channel != interaction.user.voice.channel:
            await vc.move_to(interaction.user.voice.channel)

        state = self.get_state(interaction.guild_id)

        try:
            songs = await self.fetch_info(query)
        except Exception as e:
            log.error(f'fetch_info 失敗: {e}')
            await interaction.followup.send(f'❌ 無法取得音樂：{e}')
            return

        if not songs:
            await interaction.followup.send('❌ 找不到音樂')
            return

        log.info(f'取得音樂: {songs[0]["title"]} (共 {len(songs)} 首)')

        if vc.is_playing() or vc.is_paused():
            state.queue.extend(songs)
            if len(songs) > 1:
                embed = discord.Embed(
                    title='✅ 播放清單已加入隊列',
                    description=f'加入了 **{len(songs)}** 首歌曲',
                    color=discord.Color.green(),
                )
            else:
                embed = self.song_embed('✅ 已加入隊列', songs[0], discord.Color.green())
                embed.add_field(name='隊列位置', value=str(len(state.queue)))
            await interaction.followup.send(embed=embed)
        else:
            first, rest = songs[0], songs[1:]
            state.queue.extend(rest)

            # Fetch stream URL if not available (playlist flat items)
            if not first.get('url'):
                try:
                    first['url'] = await self.fetch_stream_url(first['webpage_url'])
                except Exception as e:
                    await interaction.followup.send(f'❌ 無法取得串流：{e}')
                    return

            state.current = first

            try:
                source = make_source(first['url'], state.volume)
                vc.play(source, after=lambda e: self._after_play(e, interaction.guild_id, vc))
                log.info(f'開始播放: {first["title"]}')
                # 第一首也要加進 history，避免 autoplay 重複推薦
                if first.get('webpage_url'):
                    state.history.append(first['webpage_url'])
                    if len(state.history) > 20:
                        state.history.pop(0)
                # 歌開始播就立刻預載 autoplay 下一首
                if state.autoplay and not state.queue and not state.autoplay_prefetch:
                    asyncio.ensure_future(self._prefetch_autoplay(interaction.guild_id))
            except Exception as e:
                log.error(f'播放失敗: {e}')
                await interaction.followup.send(f'❌ 播放失敗：{e}')
                return

            embed = self.song_embed('🎵 正在播放', first, discord.Color.blue())
            if rest:
                embed.set_footer(text=f'播放清單中還有 {len(rest)} 首歌曲已加入隊列')
            await interaction.followup.send(embed=embed)

    @app_commands.command(name='pause', description='暫停播放')
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message('⏸️ 已暫停')
        else:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)

    @app_commands.command(name='resume', description='繼續播放')
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message('▶️ 繼續播放')
        else:
            await interaction.response.send_message('❌ 目前沒有暫停', ephemeral=True)

    @app_commands.command(name='skip', description='跳過目前歌曲')
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            # 跳歌時清掉舊的預載，讓新的歌重新預載推薦
            self.get_state(interaction.guild_id).autoplay_prefetch = None
            vc.stop()
            await interaction.response.send_message('⏭️ 已跳過')
        else:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)

    @app_commands.command(name='stop', description='停止播放並清空隊列（留在頻道）')
    async def stop(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        vc = interaction.guild.voice_client

        state.queue.clear()
        state.current = None
        state.autoplay_prefetch = None

        if vc:
            vc.stop()

        await interaction.response.send_message('⏹️ 已停止並清空隊列')

    @app_commands.command(name='queue', description='查看播放隊列')
    async def show_queue(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        embed = discord.Embed(title='🎵 播放隊列', color=discord.Color.purple())

        if state.current:
            t = state.current['title']
            t = t[:60] + '…' if len(t) > 60 else t
            embed.description = f'**🎶 正在播放**\n[{t}]({state.current["webpage_url"]}) `{self.fmt_duration(state.current["duration"])}`'

        if state.queue:
            lines = []
            total = 0
            for i, s in enumerate(state.queue[:20], 1):
                title = s['title'][:45] + '…' if len(s['title']) > 45 else s['title']
                line = f'`{i}.` {title} `{self.fmt_duration(s["duration"])}`'
                total += len(line) + 1
                if total > 3800:
                    lines.append(f'*... 還有更多首*')
                    break
                lines.append(line)
            if len(state.queue) > 20:
                lines.append(f'*... 還有 {len(state.queue) - 20} 首*')
            embed.description = (embed.description or '') + '\n\n**📋 待播清單**\n' + '\n'.join(lines)
        elif not state.current:
            embed.description = '隊列是空的，用 `/play` 來新增音樂！'

        # autoplay 預載提示（排在使用者 queue 之後）
        if state.autoplay_prefetch:
            t = state.autoplay_prefetch['title'][:45] + '…' if len(state.autoplay_prefetch['title']) > 45 else state.autoplay_prefetch['title']
            embed.description = (embed.description or '') + f'\n\n🔀 **Autoplay 下一首**\n{t}'

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='nowplaying', description='查看目前播放的歌曲')
    async def nowplaying(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        if not state.current:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)
            return

        song = state.current
        embed = discord.Embed(
            title='🎵 正在播放',
            description=f'[{song["title"]}]({song["webpage_url"]})',
            color=discord.Color.blue(),
        )
        embed.add_field(name='長度', value=self.fmt_duration(song['duration']))
        if song['uploader']:
            embed.add_field(name='頻道', value=song['uploader'])
        if song['thumbnail']:
            embed.set_image(url=song['thumbnail'])

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='volume', description='調整音量 (0-100)')
    @app_commands.describe(level='音量大小 (0-100)')
    async def volume(self, interaction: discord.Interaction, level: int):
        if not 0 <= level <= 100:
            await interaction.response.send_message('❌ 音量必須在 0 到 100 之間', ephemeral=True)
            return

        state = self.get_state(interaction.guild_id)
        state.volume = level / 100

        vc = interaction.guild.voice_client
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume

        await interaction.response.send_message(f'🔊 音量已設定為 **{level}%**')

    @app_commands.command(name='remove', description='從隊列移除歌曲（單首或範圍）')
    @app_commands.describe(start='要移除的位置（從 1 開始）', end='範圍結尾（不填就只移除單首）')
    async def remove(self, interaction: discord.Interaction, start: int, end: Optional[int] = None):
        state = self.get_state(interaction.guild_id)
        q = state.queue

        if not q:
            await interaction.response.send_message('❌ 隊列是空的', ephemeral=True)
            return

        end = end or start
        if start < 1 or end > len(q) or start > end:
            await interaction.response.send_message(
                f'❌ 範圍無效，隊列目前有 **{len(q)}** 首', ephemeral=True)
            return

        removed = q[start - 1:end]
        del q[start - 1:end]

        if len(removed) == 1:
            msg = f'🗑️ 已移除：**{removed[0]["title"]}**'
        else:
            msg = f'🗑️ 已移除第 {start} 到 {end} 首，共 **{len(removed)}** 首'

        await interaction.response.send_message(msg)

    @app_commands.command(name='autoplay', description='開啟/關閉自動播放（根據當前歌曲推薦）')
    async def autoplay(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)
        state.autoplay = not state.autoplay
        status = '✅ 開啟' if state.autoplay else '❌ 關閉'
        await interaction.response.send_message(f'🔀 自動播放已 **{status}**')
        # 剛開啟時，如果歌正在播且 queue 空，立刻開始預載
        if state.autoplay and state.current and not state.queue and not state.autoplay_prefetch:
            asyncio.ensure_future(self._prefetch_autoplay(interaction.guild_id))

    @app_commands.command(name='skipautoplay', description='跳過 Autoplay 預載的下一首，重新抓一首推薦')
    async def skipautoplay(self, interaction: discord.Interaction):
        state = self.get_state(interaction.guild_id)

        if not state.autoplay:
            await interaction.response.send_message('❌ Autoplay 目前是關閉的', ephemeral=True)
            return
        if not state.current:
            await interaction.response.send_message('❌ 目前沒有在播放', ephemeral=True)
            return

        await interaction.response.defer()

        # 清掉舊的預載，加目前那首到 history 讓下一首不重複
        old = state.autoplay_prefetch
        state.autoplay_prefetch = None
        if old and old.get('webpage_url'):
            state.history.append(old['webpage_url'])
            if len(state.history) > 20:
                state.history.pop(0)

        # 重新預載
        await self._prefetch_autoplay(interaction.guild_id)

        if state.autoplay_prefetch:
            t = state.autoplay_prefetch['title']
            url = state.autoplay_prefetch.get('webpage_url', '')
            await interaction.followup.send(f'🔀 已換掉，Autoplay 下一首改為：\n**[{t}]({url})**')
        else:
            await interaction.followup.send('⚠️ 找不到新的推薦，queue 空了之後會再試一次')

    @app_commands.command(name='disconnect', description='讓 Bot 離開語音頻道')
    async def disconnect(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc:
            state = self.get_state(interaction.guild_id)
            state.queue.clear()
            state.current = None
            state.autoplay_prefetch = None
            state.history.clear()
            vc.stop()
            await vc.disconnect()
            await interaction.response.send_message('👋 已離開語音頻道')
        else:
            await interaction.response.send_message('❌ Bot 不在語音頻道中', ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCog(bot))
