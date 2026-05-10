from __future__ import annotations

import asyncio
import io
import json
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from games.bigtwo_logic import BigTwoGame, THREE_CLUBS
from games.card_image import render_hand

STATS_FILE = 'data/bigtwo_stats.json'
LOBBY_TIMEOUT = 300   # 5 minutes
TURN_TIMEOUT = 60     # seconds before auto-pass


def _member_name(guild: discord.Guild, uid: int) -> str:
    member = guild.get_member(uid)
    return member.display_name if member else str(uid)


def _hand_file(cards, selected: set[int]) -> discord.File:
    img = render_hand(cards, selected)
    return discord.File(io.BytesIO(img), filename='hand.png')


# ── Stats ─────────────────────────────────────────────────────────────────────

def _load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_stats(data: dict):
    os.makedirs('data', exist_ok=True)
    with open(STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _record_result(winner_id: int, player_ids: list[int]):
    stats = _load_stats()
    for pid in player_ids:
        stats.setdefault(str(pid), {'wins': 0, 'games': 0})
        stats[str(pid)]['games'] += 1
    stats[str(winner_id)]['wins'] += 1
    _save_stats(stats)


# ── LobbyView ─────────────────────────────────────────────────────────────────

class LobbyView(discord.ui.View):
    def __init__(self, game: BigTwoGame, cog: 'BigTwo'):
        super().__init__(timeout=LOBBY_TIMEOUT)
        self.game = game
        self.cog = cog
        self.message: Optional[discord.Message] = None

    def _embed(self, guild: discord.Guild) -> discord.Embed:
        lines = []
        for pid in self.game.players:
            name = _member_name(guild, pid)
            host = ' 👑' if pid == self.game.host_id else ''
            lines.append(f'• {name}{host}')
        embed = discord.Embed(title='🃏 大老二 — 等待玩家加入', color=0x27ae60)
        embed.add_field(name=f'玩家 ({len(self.game.players)}/4)', value='\n'.join(lines))
        embed.set_footer(text='需要 2~4 人 ‧ 5 分鐘未開始自動取消')
        return embed

    @discord.ui.button(label='加入', style=discord.ButtonStyle.success, custom_id='bt_join')
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, msg = self.game.add_player(interaction.user.id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        for child in self.children:
            if getattr(child, 'custom_id', '') == 'bt_start':
                child.disabled = not self.game.can_start()
        await interaction.response.edit_message(embed=self._embed(interaction.guild), view=self)

    @discord.ui.button(label='開始遊戲', style=discord.ButtonStyle.primary,
                       custom_id='bt_start', disabled=True)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.host_id:
            await interaction.response.send_message('只有房主可以開始遊戲', ephemeral=True)
            return
        if not self.game.can_start():
            await interaction.response.send_message('至少需要 2 人才能開始', ephemeral=True)
            return
        self.stop()
        self.game.start()

        sv = StatusView(self.game, self.cog)
        self.cog.status_views[self.game.guild_id] = sv
        embed = self.cog.build_status_embed(self.game, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=sv)
        msg = await interaction.original_response()
        self.cog.status_msgs[self.game.guild_id] = msg
        self.cog.start_turn_timer(self.game, interaction.channel)

    async def on_timeout(self):
        self.cog.games.pop(self.game.guild_id, None)
        if self.message:
            try:
                await self.message.edit(
                    content='⏰ 遊戲大廳已逾時自動取消', embed=None, view=None)
            except Exception:
                pass


# ── StatusView ────────────────────────────────────────────────────────────────

class StatusView(discord.ui.View):
    def __init__(self, game: BigTwoGame, cog: 'BigTwo'):
        super().__init__(timeout=None)
        self.game = game
        self.cog = cog

    @discord.ui.button(label='🃏 查看手牌 / 出牌', style=discord.ButtonStyle.primary)
    async def show_hand(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        uid = interaction.user.id
        if game.state == 'finished':
            await interaction.response.send_message('遊戲已結束', ephemeral=True)
            return
        if game.state != 'playing':
            await interaction.response.send_message('遊戲尚未開始', ephemeral=True)
            return
        if uid not in game.players:
            await interaction.response.send_message('你不在這局遊戲中', ephemeral=True)
            return
        if uid != game.current_player:
            hand = game.hands[uid]
            file = _hand_file(hand, set())
            await interaction.response.send_message(
                f'你有 **{len(hand)}** 張牌，還沒輪到你', file=file, ephemeral=True)
            return
        hand = game.hands[uid]
        file = _hand_file(hand, game.selected[uid])
        view = HandView(game, uid, self.cog)
        await interaction.response.send_message(
            embed=view.build_embed(), file=file, view=view, ephemeral=True)
        self.cog.reset_turn_timer(game, interaction.channel)

    @discord.ui.button(label='🏆 排行榜', style=discord.ButtonStyle.secondary)
    async def stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _send_stats(interaction)


# ── HandView ──────────────────────────────────────────────────────────────────

class HandView(discord.ui.View):
    def __init__(self, game: BigTwoGame, uid: int, cog: 'BigTwo'):
        super().__init__(timeout=None)
        self.game = game
        self.uid = uid
        self.cog = cog
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        hand = self.game.hands.get(self.uid, [])
        selected = self.game.selected.get(self.uid, set())
        n = len(hand)
        for i, card in enumerate(hand):
            btn = discord.ui.Button(
                label=str(card),
                style=discord.ButtonStyle.success if i in selected else discord.ButtonStyle.secondary,
                custom_id=f'hv_card_{i}',
                row=i // 5,
            )
            btn.callback = self._make_toggle(i)
            self.add_item(btn)
        action_row = min((n + 4) // 5, 4)
        play = discord.ui.Button(label='出牌 ✅', style=discord.ButtonStyle.primary,
                                 custom_id='hv_play', row=action_row)
        pas = discord.ui.Button(label='過牌 ⏭️', style=discord.ButtonStyle.danger,
                                custom_id='hv_pass', row=action_row)
        rst = discord.ui.Button(label='重選 🔄', style=discord.ButtonStyle.secondary,
                                custom_id='hv_reset', row=action_row)
        play.callback = self._play_cb
        pas.callback = self._pass_cb
        rst.callback = self._reset_cb
        self.add_item(play)
        self.add_item(pas)
        self.add_item(rst)

    def _make_toggle(self, idx: int):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.uid:
                await interaction.response.send_message('這不是你的手牌', ephemeral=True)
                return
            self.game.toggle(self.uid, idx)
            self._rebuild()
            hand = self.game.hands[self.uid]
            file = _hand_file(hand, self.game.selected[self.uid])
            await interaction.response.edit_message(
                embed=self.build_embed(), attachments=[file], view=self)
        return cb

    async def _play_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('這不是你的回合', ephemeral=True)
            return
        ok, msg = self.game.play(self.uid)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        self.cog.cancel_turn_timer(self.game.guild_id)
        if msg == 'win':
            await interaction.response.edit_message(
                content='🎉 你贏了！手牌出完！', embed=None, attachments=[], view=None)
            await self.cog.finish_game(interaction.guild, interaction.channel, self.game)
        else:
            await interaction.response.edit_message(
                content='✅ 出牌成功！', embed=None, attachments=[], view=None)
            await self.cog.after_action(interaction.guild, interaction.channel, self.game)

    async def _pass_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('這不是你的回合', ephemeral=True)
            return
        ok, msg = self.game.pass_turn(self.uid)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        self.cog.cancel_turn_timer(self.game.guild_id)
        await interaction.response.edit_message(
            content='⏭️ 你選擇過牌', embed=None, attachments=[], view=None)
        await self.cog.after_action(interaction.guild, interaction.channel, self.game)

    async def _reset_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message('這不是你的手牌', ephemeral=True)
            return
        self.game.clear_sel(self.uid)
        self._rebuild()
        hand = self.game.hands[self.uid]
        file = _hand_file(hand, set())
        await interaction.response.edit_message(
            embed=self.build_embed(), attachments=[file], view=self)

    def build_embed(self) -> discord.Embed:
        hand = self.game.hands.get(self.uid, [])
        sel = self.game.selected.get(self.uid, set())
        sel_cards = [hand[i] for i in sorted(sel) if i < len(hand)]
        sel_str = '  '.join(str(c) for c in sel_cards) if sel_cards else '（尚未選擇）'
        last = self.game.last_hand
        if last:
            last_str = f'{last.type_name()} ➜ {last.display()}'
        else:
            last_str = '自由出牌（任意牌型）'
        embed = discord.Embed(title=f'你的手牌（{len(hand)} 張）', color=0x2ecc71)
        embed.add_field(name='場上最後一手', value=last_str, inline=False)
        embed.add_field(name='已選擇', value=sel_str, inline=False)
        embed.set_image(url='attachment://hand.png')
        return embed


# ── Stats helper ──────────────────────────────────────────────────────────────

async def _send_stats(interaction: discord.Interaction):
    stats = _load_stats()
    if not stats:
        await interaction.response.send_message('還沒有任何紀錄', ephemeral=True)
        return
    ranked = sorted(stats.items(), key=lambda x: x[1]['wins'], reverse=True)
    lines = []
    medals = ['🥇', '🥈', '🥉']
    for rank, (uid_str, data) in enumerate(ranked[:10]):
        wins = data['wins']
        games = data['games']
        rate = f"{wins / games * 100:.0f}%" if games > 0 else "0%"
        name = _member_name(interaction.guild, int(uid_str))
        medal = medals[rank] if rank < 3 else f'{rank + 1}.'
        lines.append(f'{medal} **{name}**：{wins} 勝 / {games} 場 ({rate})')
    embed = discord.Embed(title='🏆 大老二排行榜', color=0xe74c3c,
                          description='\n'.join(lines))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class BigTwo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: dict[int, BigTwoGame] = {}
        self.status_msgs: dict[int, discord.Message] = {}
        self.status_views: dict[int, StatusView] = {}
        self.turn_tasks: dict[int, asyncio.Task] = {}

    # ── Status embed ──────────────────────────────────────────────────────────

    def build_status_embed(self, game: BigTwoGame, guild: discord.Guild) -> discord.Embed:
        counts = game.card_counts()
        lines = []
        for pid in game.players:
            name = _member_name(guild, pid)
            n = counts.get(pid, 0)
            arrow = ' ◀ 輪到他出牌' if game.state == 'playing' and pid == game.current_player else ''
            lines.append(f'• **{name}**：{n} 張{arrow}')
        last = game.last_hand
        last_str = f'{last.type_name()} ➜ {last.display()}' if last else '（新的一輪，自由出牌）'
        cur_name = _member_name(guild, game.current_player) if game.state == 'playing' else '？'
        embed = discord.Embed(title='🃏 大老二進行中', color=0x3498db)
        embed.add_field(name='玩家手牌數', value='\n'.join(lines), inline=False)
        embed.add_field(name='上一手', value=last_str, inline=False)
        embed.add_field(name='輪到', value=f'**{cur_name}**，請點下方按鈕出牌', inline=False)
        return embed

    async def update_status(self, game: BigTwoGame, guild: discord.Guild):
        msg = self.status_msgs.get(game.guild_id)
        sv = self.status_views.get(game.guild_id)
        if msg and sv:
            try:
                await msg.edit(embed=self.build_status_embed(game, guild), view=sv)
            except Exception:
                pass

    # ── Turn timer ────────────────────────────────────────────────────────────

    def start_turn_timer(self, game: BigTwoGame, channel):
        gid = game.guild_id
        self.cancel_turn_timer(gid)
        self.turn_tasks[gid] = asyncio.create_task(self._turn_timeout(game, channel))

    def reset_turn_timer(self, game: BigTwoGame, channel):
        self.start_turn_timer(game, channel)

    def cancel_turn_timer(self, gid: int):
        t = self.turn_tasks.pop(gid, None)
        if t:
            t.cancel()

    async def _turn_timeout(self, game: BigTwoGame, channel):
        await asyncio.sleep(TURN_TIMEOUT)
        if game.state != 'playing':
            return
        uid = game.current_player
        name = _member_name(channel.guild, uid)
        if game.first_turn:
            ok, result = game.auto_play_3c(uid)
            if ok:
                await channel.send(f'⏱️ {name} 超時，自動出 3♣')
                if result == 'win':
                    await self.finish_game(channel.guild, channel, game)
                    return
                await self.update_status(game, channel.guild)
                self.start_turn_timer(game, channel)
        else:
            ok, _ = game.pass_turn(uid)
            if ok:
                await channel.send(f'⏱️ {name} 超時，自動過牌')
                await self.update_status(game, channel.guild)
                self.start_turn_timer(game, channel)

    # ── Post-action helpers ───────────────────────────────────────────────────

    async def after_action(self, guild: discord.Guild, channel, game: BigTwoGame):
        await self.update_status(game, guild)
        self.start_turn_timer(game, channel)

    async def finish_game(self, guild: discord.Guild, channel, game: BigTwoGame):
        self.cancel_turn_timer(game.guild_id)
        winner_id = game.winner
        _record_result(winner_id, game.players)

        wname = _member_name(guild, winner_id)

        embed = discord.Embed(title='🎉 遊戲結束！', color=0xf39c12)
        embed.add_field(name='勝者', value=f'🥇 **{wname}**', inline=False)
        lines = []
        for pid in game.players:
            name = _member_name(guild, pid)
            remaining = len(game.hands.get(pid, []))
            lines.append(f'• {name}：剩 {remaining} 張')
        embed.add_field(name='各玩家結果', value='\n'.join(lines), inline=False)

        sv = self.status_views.get(game.guild_id)
        if sv:
            sv.stop()
        msg = self.status_msgs.get(game.guild_id)
        if msg:
            try:
                await msg.edit(embed=embed, view=None)
            except Exception:
                pass

        gid = game.guild_id
        self.games.pop(gid, None)
        self.status_msgs.pop(gid, None)
        self.status_views.pop(gid, None)

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name='bigtwo', description='開一局大老二（2~4 人）')
    async def bigtwo_cmd(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message('請在伺服器頻道中使用此指令', ephemeral=True)
            return
        gid = interaction.guild_id
        if gid in self.games:
            await interaction.response.send_message('這個伺服器已有一局遊戲進行中', ephemeral=True)
            return
        game = BigTwoGame(gid, interaction.channel_id, interaction.user.id)
        self.games[gid] = game
        view = LobbyView(game, self)
        embed = view._embed(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        view.message = msg

    @app_commands.command(name='bigtwo_stats', description='查看大老二勝負統計')
    async def stats_cmd(self, interaction: discord.Interaction):
        await _send_stats(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(BigTwo(bot))
