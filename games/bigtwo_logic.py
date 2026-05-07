from __future__ import annotations

import random
import time
from collections import Counter
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class Suit(IntEnum):
    CLUBS = 0
    DIAMONDS = 1
    HEARTS = 2
    SPADES = 3


class Value(IntEnum):
    THREE = 0
    FOUR = 1
    FIVE = 2
    SIX = 3
    SEVEN = 4
    EIGHT = 5
    NINE = 6
    TEN = 7
    JACK = 8
    QUEEN = 9
    KING = 10
    ACE = 11
    TWO = 12


SUIT_SYM = {
    Suit.CLUBS: '♣', Suit.DIAMONDS: '♦',
    Suit.HEARTS: '♥', Suit.SPADES: '♠',
}
VALUE_SYM = {
    Value.THREE: '3', Value.FOUR: '4', Value.FIVE: '5', Value.SIX: '6',
    Value.SEVEN: '7', Value.EIGHT: '8', Value.NINE: '9', Value.TEN: '10',
    Value.JACK: 'J', Value.QUEEN: 'Q', Value.KING: 'K', Value.ACE: 'A', Value.TWO: '2',
}


@dataclass(frozen=True, order=True)
class Card:
    value: Value
    suit: Suit

    def __str__(self):
        return f"{VALUE_SYM[self.value]}{SUIT_SYM[self.suit]}"

    def is_red(self):
        return self.suit in (Suit.HEARTS, Suit.DIAMONDS)


THREE_CLUBS = Card(Value.THREE, Suit.CLUBS)


class HandType(IntEnum):
    SINGLE = 1
    PAIR = 2
    STRAIGHT = 3
    FULL_HOUSE = 4
    IRON = 5          # 鐵支：四條 + 帶一張
    STRAIGHT_FLUSH = 6


HAND_NAMES = {
    HandType.SINGLE: '單張',
    HandType.PAIR: '對子',
    HandType.STRAIGHT: '順子',
    HandType.FULL_HOUSE: '葫蘆',
    HandType.IRON: '鐵支',
    HandType.STRAIGHT_FLUSH: '同花順',
}


@dataclass
class PlayedHand:
    cards: list
    hand_type: HandType
    rank: tuple

    def beats(self, other: Optional['PlayedHand']) -> bool:
        if other is None:
            return True
        self_bomb = self.hand_type in (HandType.IRON, HandType.STRAIGHT_FLUSH)
        other_bomb = other.hand_type in (HandType.IRON, HandType.STRAIGHT_FLUSH)
        if self_bomb and not other_bomb:
            return True
        if not self_bomb and other_bomb:
            return False
        if self.hand_type != other.hand_type:
            return False
        return self.rank > other.rank

    def display(self):
        return '  '.join(str(c) for c in self.cards)

    def type_name(self):
        return HAND_NAMES[self.hand_type]


def parse_hand(cards: list) -> Optional[PlayedHand]:
    n = len(cards)
    if n == 1:
        c = cards[0]
        return PlayedHand(cards, HandType.SINGLE, (c.value, c.suit))
    if n == 2:
        if cards[0].value == cards[1].value:
            high = max(cards, key=lambda c: (c.value, c.suit))
            return PlayedHand(cards, HandType.PAIR, (high.value, high.suit))
        return None
    if n == 5:
        return _parse_five(cards)
    return None


_A2345 = frozenset({Value.ACE, Value.TWO, Value.THREE, Value.FOUR, Value.FIVE})
_23456 = frozenset({Value.TWO, Value.THREE, Value.FOUR, Value.FIVE, Value.SIX})


def _is_straight(values: list) -> bool:
    """
    有效順子只有三種：
    1. A-2-3-4-5（最小）
    2. 3-4-5-6-7 到 10-J-Q-K-A（標準連續，不含 2）
    3. 2-3-4-5-6（最大）
    """
    unique = sorted(set(values))
    if len(unique) != 5:
        return False
    val_set = frozenset(unique)
    if val_set == _A2345 or val_set == _23456:
        return True
    return Value.TWO not in unique and unique[-1] - unique[0] == 4


def _straight_comp_card(cards: list) -> Card:
    """
    A-2-3-4-5 → 看 5；2-3-4-5-6 → 看 2；其他 → 看最大牌
    """
    val_set = frozenset(c.value for c in cards)
    if val_set == _A2345:
        return next(c for c in cards if c.value == Value.FIVE)
    if val_set == _23456:
        return next(c for c in cards if c.value == Value.TWO)
    return max(cards, key=lambda c: c.value)


def _parse_five(cards: list) -> Optional[PlayedHand]:
    s = sorted(cards)
    values = [c.value for c in s]
    suits = [c.suit for c in s]
    cnt = Counter(values)
    counts = sorted(cnt.values(), reverse=True)

    same_suit = len(set(suits)) == 1
    is_str = _is_straight(values)

    if same_suit and is_str:
        comp = _straight_comp_card(s)
        return PlayedHand(cards, HandType.STRAIGHT_FLUSH, (comp.value, comp.suit))

    if counts[0] == 4:
        quad = next(v for v, c in cnt.items() if c == 4)
        return PlayedHand(cards, HandType.IRON, (quad,))

    if counts[0] == 3 and counts[1] == 2:
        triple = next(v for v, c in cnt.items() if c == 3)
        return PlayedHand(cards, HandType.FULL_HOUSE, (triple,))

    if is_str:
        comp = _straight_comp_card(s)
        return PlayedHand(cards, HandType.STRAIGHT, (comp.value, comp.suit))

    return None


def make_deck() -> list:
    return [Card(Value(v), Suit(s)) for v in range(13) for s in range(4)]


class BigTwoGame:
    def __init__(self, guild_id: int, channel_id: int, host_id: int):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.host_id = host_id
        self.players: list[int] = [host_id]
        self.display_names: dict[int, str] = {}
        self.hands: dict[int, list] = {}
        self.selected: dict[int, set] = {}
        self.state = 'waiting'   # waiting | playing | finished
        self.current_idx = 0
        self.last_hand: Optional[PlayedHand] = None
        self.last_player: Optional[int] = None
        self.pass_count = 0
        self.passed_this_round: set[int] = set()
        self.first_turn = True
        self.winner: Optional[int] = None
        self.created_at = time.time()

    @property
    def current_player(self) -> int:
        return self.players[self.current_idx]

    def can_start(self) -> bool:
        return len(self.players) >= 2

    def add_player(self, uid: int, display_name: str = '') -> tuple[bool, str]:
        if self.state != 'waiting':
            return False, '遊戲已經開始'
        if uid in self.players:
            return False, '你已經在房間裡了'
        if len(self.players) >= 4:
            return False, '房間已滿（最多 4 人）'
        self.players.append(uid)
        self.display_names[uid] = display_name or str(uid)
        return True, 'ok'

    def start(self):
        deck = make_deck()
        random.shuffle(deck)
        n = len(self.players)
        if n == 2:
            # 保證 3♣ 在前 26 張
            three_idx = deck.index(THREE_CLUBS)
            if three_idx >= 26:
                swap = random.randrange(26)
                deck[three_idx], deck[swap] = deck[swap], deck[three_idx]
            for i, pid in enumerate(self.players):
                self.hands[pid] = sorted(deck[i * 13:(i + 1) * 13])
        else:
            # 3/4 人輪流發牌，確保 3♣ 一定被發出去
            for pid in self.players:
                self.hands[pid] = []
            for i, card in enumerate(deck):
                self.hands[self.players[i % n]].append(card)
            for pid in self.players:
                self.hands[pid].sort()
        for pid in self.players:
            self.selected[pid] = set()
        for i, pid in enumerate(self.players):
            if THREE_CLUBS in self.hands[pid]:
                self.current_idx = i
                break
        self.passed_this_round = set()
        self.state = 'playing'

    def toggle(self, uid: int, idx: int):
        s = self.selected[uid]
        if idx in s:
            s.discard(idx)
        else:
            s.add(idx)

    def clear_sel(self, uid: int):
        self.selected[uid].clear()

    def play(self, uid: int) -> tuple[bool, str]:
        if self.state != 'playing':
            return False, '遊戲未進行中'
        if uid != self.current_player:
            return False, '還沒輪到你'
        hand = self.hands[uid]
        idxs = sorted(self.selected[uid])
        if not idxs:
            return False, '請先選擇要出的牌'
        played_cards = [hand[i] for i in idxs]
        ph = parse_hand(played_cards)
        if ph is None:
            return False, '不是合法的牌型'
        if self.first_turn and THREE_CLUBS not in played_cards:
            return False, '第一手必須包含 3♣'
        if not ph.beats(self.last_hand):
            return False, '出的牌不夠大'
        for c in played_cards:
            hand.remove(c)
        self.hands[uid] = sorted(hand)
        self.last_hand = ph
        self.last_player = uid
        self.pass_count = 0
        self.passed_this_round.clear()
        self.first_turn = False
        self.selected[uid] = set()
        if not self.hands[uid]:
            self.winner = uid
            self.state = 'finished'
            return True, 'win'
        self._next()
        return True, 'ok'

    def pass_turn(self, uid: int) -> tuple[bool, str]:
        if self.state != 'playing':
            return False, '遊戲未進行中'
        if uid != self.current_player:
            return False, '還沒輪到你'
        if self.first_turn:
            return False, '第一手不能過牌'
        if self.last_hand is None:
            return False, '輪到你自由出牌，不能過牌'
        self.selected[uid] = set()
        self.passed_this_round.add(uid)
        self.pass_count += 1
        self._next()
        if self.pass_count >= len(self.players) - 1:
            self.last_hand = None
            self.last_player = None
            self.pass_count = 0
            self.passed_this_round.clear()
        return True, 'ok'

    def auto_play_3c(self, uid: int) -> tuple[bool, str]:
        """第一手逾時時自動出 3♣ 單張。"""
        if uid != self.current_player or not self.first_turn:
            return False, 'skip'
        idx = self.hands[uid].index(THREE_CLUBS)
        self.selected[uid] = {idx}
        return self.play(uid)

    def _next(self):
        self.current_idx = (self.current_idx + 1) % len(self.players)
        # 跳過本輪已 pass 的玩家
        while self.players[self.current_idx] in self.passed_this_round:
            self.current_idx = (self.current_idx + 1) % len(self.players)

    def card_counts(self) -> dict[int, int]:
        return {pid: len(h) for pid, h in self.hands.items()}
