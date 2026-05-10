from __future__ import annotations

import io
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont

from .bigtwo_logic import Card, Suit, Value, VALUE_SYM, SUIT_SYM

CARD_W, CARD_H = 82, 116
RADIUS = 10
GAP = 5
LIFT = 20       # selected cards are raised by this many pixels
PAD = 12

WHITE = (255, 255, 255)
SEL_BG = (210, 235, 255)
FELT = (34, 100, 54)        # green felt table background
RED = (208, 30, 30)
BLACK = (18, 18, 18)
BORDER_NORMAL = (190, 190, 190)
BORDER_SEL = (60, 130, 220)


@lru_cache(maxsize=1)
def _fonts():
    candidates = ['arialbd.ttf', 'arial.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans.ttf']
    for name in candidates:
        try:
            return ImageFont.truetype(name, 22), ImageFont.truetype(name, 14)
        except Exception:
            pass
    f = ImageFont.load_default()
    return f, f


def render_hand(cards: list[Card], selected: set[int]) -> bytes:
    n = len(cards)
    font_lg, font_sm = _fonts()

    if n == 0:
        img = Image.new('RGB', (200, CARD_H + PAD * 2 + LIFT), FELT)
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue()

    total_w = PAD * 2 + CARD_W * n + GAP * (n - 1)
    total_h = PAD * 2 + CARD_H + LIFT
    img = Image.new('RGB', (total_w, total_h), FELT)
    draw = ImageDraw.Draw(img)

    for i, card in enumerate(cards):
        sel = i in selected
        x = PAD + i * (CARD_W + GAP)
        y = PAD + (0 if sel else LIFT)

        # Drop shadow
        shadow_img = Image.new('RGBA', (CARD_W + 6, CARD_H + 6), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow_img)
        sd.rounded_rectangle([3, 3, CARD_W + 3, CARD_H + 3], radius=RADIUS, fill=(0, 0, 0, 90))
        img.paste(shadow_img, (x, y), shadow_img)

        # Card face
        fill = SEL_BG if sel else WHITE
        border = BORDER_SEL if sel else BORDER_NORMAL
        draw.rounded_rectangle(
            [x, y, x + CARD_W, y + CARD_H],
            radius=RADIUS, fill=fill, outline=border, width=2,
        )

        color = RED if card.is_red() else BLACK
        val = VALUE_SYM[card.value]
        suit = SUIT_SYM[card.suit]

        # Top-left pip
        draw.text((x + 5, y + 3), val, fill=color, font=font_sm)
        draw.text((x + 5, y + 17), suit, fill=color, font=font_sm)

        # Centre suit symbol
        bb = draw.textbbox((0, 0), suit, font=font_lg)
        sw, sh = bb[2] - bb[0], bb[3] - bb[1]
        draw.text((x + (CARD_W - sw) // 2, y + (CARD_H - sh) // 2), suit, fill=color, font=font_lg)

        # Bottom-right pip (mirrored)
        draw.text((x + CARD_W - 16, y + CARD_H - 32), suit, fill=color, font=font_sm)
        draw.text((x + CARD_W - 16, y + CARD_H - 18), val, fill=color, font=font_sm)

    buf = io.BytesIO()
    img.save(buf, 'PNG')
    buf.seek(0)
    return buf.getvalue()
