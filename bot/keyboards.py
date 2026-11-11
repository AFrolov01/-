# -*- coding: utf-8 -*-
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from config import GRID_SIZE, MIN_MINES, MAX_MINES
from bot.game import multiplier_for
from bot.texts import fmt_num, fmt_mult, SIDE_EMOJI

MINE_EMOJIS = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}


def mine_count_kb(duel_id: int, rules_expanded=None) -> InlineKeyboardMarkup:
    """Выбор числа мин. `rules_expanded`: None — без кнопки правил; False — кнопка
    «📜 Правила» (раскрыть); True — «🔼 Свернуть» (правила сейчас раскрыты)."""
    builder = InlineKeyboardBuilder()
    for m in range(MIN_MINES, MAX_MINES + 1):
        builder.button(text=MINE_EMOJIS[m], callback_data=f"duel:mines:{duel_id}:{m}")
    builder.adjust(6)
    if rules_expanded is not None:
        label = "🔼 Свернуть правила" if rules_expanded else "📜 Полные правила"
        target = 0 if rules_expanded else 1
        builder.row(InlineKeyboardButton(text=label, callback_data=f"duel:rules:{duel_id}:{target}"))
    return builder.as_markup()


# Цвет открытой клетки зависит от множителя, который игрок «набрал» на этом шаге:
# чем дальше зашёл, тем горячее след на поле.
_TIERS = ((1.5, "🟩"), (2.5, "🟨"), (5.0, "🟧"))
_TIER_TOP = "🔥"


def tier_emoji(multiplier: float) -> str:
    for limit, emoji in _TIERS:
        if multiplier < limit:
            return emoji
    return _TIER_TOP


def _opened_emoji(order_index: int, mines, boost: float) -> str:
    """Эмодзи клетки, открытой k-й по счёту (order_index с нуля)."""
    if not mines:
        return "✅"
    return tier_emoji(multiplier_for(mines, order_index + 1, boost))


def board_kb(duel_id: int, opened_cells: list, mines=None, boost: float = 1.0,
             stake=None, current_multiplier=None) -> InlineKeyboardMarkup:
    """Игровое поле 5×5. Если известны mines/boost — открытые клетки красятся по
    уровню множителя (🟩→🟨→🟧→🔥). Если известны stake и current_multiplier — кнопка
    «Забрать» показывает, сколько именно сейчас на кону."""
    builder = InlineKeyboardBuilder()
    total = GRID_SIZE * GRID_SIZE
    for i in range(total):
        if i in opened_cells:
            text = _opened_emoji(opened_cells.index(i), mines, boost)
        else:
            text = "❓"
        builder.button(text=text, callback_data=f"duel:cell:{duel_id}:{i}")
    builder.adjust(GRID_SIZE)
    if opened_cells and stake and current_multiplier:
        label = f"💰 Забрать {fmt_mult(current_multiplier)} · {fmt_num(round(stake * current_multiplier))} Al"
    else:
        label = "✅ Забрать очки"
    builder.row(InlineKeyboardButton(text=label, callback_data=f"duel:cashout:{duel_id}"))
    return builder.as_markup()


def board_revealed_kb(opened_cells: list, mine_positions: list, portal_positions: list,
                      exploded_cell=None, mines=None, boost: float = 1.0) -> InlineKeyboardMarkup:
    """Прозрачность: показывает, где реально были мины и клетки-порталы, после
    того как раунд закончен. Кнопки декоративные (некликабельны, noop). Пройденный
    путь сохраняет цвета множителя — на финальном поле видно, как далеко зашёл игрок."""
    builder = InlineKeyboardBuilder()
    total = GRID_SIZE * GRID_SIZE
    for i in range(total):
        if i == exploded_cell:
            text = "💥"
        elif i in mine_positions:
            text = "💣"
        elif i in portal_positions:
            text = "🔝"
        elif i in opened_cells:
            text = _opened_emoji(opened_cells.index(i), mines, boost)
        else:
            text = "⬜"
        builder.button(text=text, callback_data="noop")
    builder.adjust(GRID_SIZE)
    return builder.as_markup()


def bet_kb(odds=None) -> InlineKeyboardMarkup:
    """Кнопки тотализатора под анонсом: «Ставка · x1,48» на каждую сторону (сумму человек
    пишет ответом на сообщение бота) и бесплатная ставка с ЯВНО указанной суммой."""
    builder = InlineKeyboardBuilder()
    for side in ("a", "b"):
        x = f" · {fmt_mult(odds[side])}" if odds and odds.get(side) else ""
        builder.button(text=f"{SIDE_EMOJI[side]} Ставка{x}", callback_data=f"bet:{side}:ask")
    free = f"{config.BET_COUPON_AMOUNT:g} Те"
    for side in ("a", "b"):
        builder.button(text=f"🎟 {free} бесплатно {SIDE_EMOJI[side]}", callback_data=f"bet:{side}:c")
    builder.adjust(2, 2)
    return builder.as_markup()


def clan_carousel_kb(index: int, total: int, clan_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️", callback_data=f"join:prev:{index}"),
        InlineKeyboardButton(text="Вступить ✅", callback_data=f"join:select:{clan_id}:{index}"),
        InlineKeyboardButton(text="➡️", callback_data=f"join:next:{index}"),
    )
    return builder.as_markup()


def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=yes_cb)
    builder.button(text="❌ Нет", callback_data=no_cb)
    builder.adjust(2)
    return builder.as_markup()


def skip_avatar_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data="createclan:skip_avatar")
    return builder.as_markup()
