# -*- coding: utf-8 -*-
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import GRID_SIZE, MIN_MINES, MAX_MINES

MINE_EMOJIS = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}


def mine_count_kb(duel_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in range(MIN_MINES, MAX_MINES + 1):
        builder.button(text=MINE_EMOJIS[m], callback_data=f"duel:mines:{duel_id}:{m}")
    builder.adjust(6)
    return builder.as_markup()


def board_kb(duel_id: int, opened_cells: list, exploded: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = GRID_SIZE * GRID_SIZE
    for i in range(total):
        if i in opened_cells:
            text = "💥" if (exploded and i == opened_cells[-1]) else "✅"
        else:
            text = "❓"
        builder.button(text=text, callback_data=f"duel:cell:{duel_id}:{i}")
    builder.adjust(GRID_SIZE)
    if not exploded:
        builder.row(InlineKeyboardButton(text="✅ Забрать очки", callback_data=f"duel:cashout:{duel_id}"))
    return builder.as_markup()


def board_kb(duel_id: int, opened_cells: list, exploded: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = GRID_SIZE * GRID_SIZE
    for i in range(total):
        if i in opened_cells:
            text = "💥" if (exploded and i == opened_cells[-1]) else "✅"
        else:
            text = "❓"
        builder.button(text=text, callback_data=f"duel:cell:{duel_id}:{i}")
    builder.adjust(GRID_SIZE)
    if not exploded:
        builder.row(InlineKeyboardButton(text="✅ Забрать очки", callback_data=f"duel:cashout:{duel_id}"))
    return builder.as_markup()


def board_revealed_kb(opened_cells: list, mine_positions: list, portal_positions: list, exploded_cell=None) -> InlineKeyboardMarkup:
    """Прозрачность: показывает, где реально были мины и клетки-порталы, после
    того как раунд закончен. Кнопки декоративные (некликабельны, noop)."""
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
            text = "✅"
        else:
            text = "⬜"
        builder.button(text=text, callback_data="noop")
    builder.adjust(GRID_SIZE)
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
