# -*- coding: utf-8 -*-
"""
Логика мин-дуэли.

ВАЖНО про экономику очков:
 - ставка дуэли = ВСЕ текущие очки клана на момент выбора количества мин
   (снимок сохраняется в side["stake"], дальше игра считается по нему,
   даже если очки клана вдруг изменятся извне за это время);
 - при подрыве на мине: очки ВСЕГО клана умножаются на LOSS_MULTIPLIER (0.75);
 - при "заборе" выигрыша: очки клана НЕ складываются "поверх" ставки (иначе
   она задвоилась бы), а обновляются:
       clan.points = clan.points - stake + (stake * multiplier)
   Так как stake обычно равен всем очкам клана на старте, для типичного
   случая это эквивалентно простому clan.points = stake * multiplier —
   но формула через вычитание остаётся корректной и в редких случаях,
   когда очки клана успели измениться (например, другая параллельная
   дуэль) между стартом и завершением этой игры.

Оба игрока дуэли играют НА СВОИХ ОТДЕЛЬНЫХ ПОЛЯХ параллельно (независимо друг
от друга) — так задано в ТЗ ("у двух игроков из разных кланов будет разное
поле"). Понятие "AFK / переход хода" реализовано как таймаут бездействия на
поле конкретного игрока: если игрок долго не нажимает клетку, его текущий
прогресс автоматически фиксируется через "забрать очки" (безопасный дефолт).
"""

import time
from typing import Optional, Tuple

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import config
from bot.storage import Storage, now
from bot import texts, game
from bot.keyboards import mine_count_kb, board_kb

router = Router(name="duel")


# ---------------------------------------------------------------- helpers --

def _find_clan(db: dict, clan_id: int) -> Optional[dict]:
    return db["clans"].get(str(clan_id))


def _side_key_for_user(duel: dict, user_id: int) -> Optional[str]:
    if duel["sides"]["a"]["player_id"] == user_id:
        return "a"
    if duel["sides"]["b"]["player_id"] == user_id:
        return "b"
    return None


def _other_side(side_key: str) -> str:
    return "b" if side_key == "a" else "a"


def _format_progression(values) -> str:
    from bot.game import format_progression
    return format_progression(values)


def _mark_member_played(clan: dict, user_id: int) -> None:
    member = clan.get("members", {}).get(str(user_id))
    if member:
        member["matches_played"] = member.get("matches_played", 0) + 1
        member["last_played_at"] = now()
    clan["last_played_at"] = now()


def _apply_loss(clan: dict) -> float:
    new_points = round(clan.get("points", 0) * config.LOSS_MULTIPLIER, 2)
    clan["points"] = new_points
    clan["current_win_streak"] = 0
    return new_points


def _apply_cashout(clan: dict, multiplier: float, stake: int, user_id: int, username: str) -> Tuple[float, int]:
    won_al = round(stake * multiplier)
    new_points = round(clan.get("points", 0) - stake + won_al, 2)
    clan["points"] = new_points
    best = clan.get("best_single_multiplier")
    if not best or multiplier > best.get("value", 0):
        clan["best_single_multiplier"] = {
            "value": multiplier, "user_id": user_id, "username": username or ""
        }
    return new_points, won_al


def _finalize_duel_result(db: dict, duel: dict) -> str:
    """Вызывается, когда ОБЕ стороны завершили игру. Возвращает текст итога."""
    side_a, side_b = duel["sides"]["a"], duel["sides"]["b"]
    clan_a = _find_clan(db, side_a["clan_id"])
    clan_b = _find_clan(db, side_b["clan_id"])
    if not clan_a or not clan_b:
        return ""

    _mark_member_played(clan_a, side_a["player_id"])
    _mark_member_played(clan_b, side_b["player_id"])

    a_won = side_a["result"] == "win"
    b_won = side_b["result"] == "win"

    winner_clan = None
    loser_clan = None

    if a_won and not b_won:
        winner_clan, loser_clan = clan_a, clan_b
    elif b_won and not a_won:
        winner_clan, loser_clan = clan_b, clan_a
    elif a_won and b_won:
        mult_a = side_a.get("multiplier", 0)
        mult_b = side_b.get("multiplier", 0)
        if mult_a > mult_b:
            winner_clan, loser_clan = clan_a, clan_b
        elif mult_b > mult_a:
            winner_clan, loser_clan = clan_b, clan_a
        # при равенстве — ничья, стрики никого не трогаем

    if winner_clan is not None:
        winner_clan["current_win_streak"] = winner_clan.get("current_win_streak", 0) + 1
        winner_clan["max_win_streak"] = max(
            winner_clan.get("max_win_streak", 0), winner_clan["current_win_streak"]
        )
        winner_clan["wars_won"] = winner_clan.get("wars_won", 0) + 1
        loser_clan["current_win_streak"] = 0
        return (
            f"🏆 По итогам дуэли побеждает клан «{winner_clan['name']}»!\n"
            f"Очки «{clan_a['name']}»: {clan_a.get('points', 0):g} | "
            f"Очки «{clan_b['name']}»: {clan_b.get('points', 0):g}"
        )
    return (
        f"🤝 Дуэль завершилась вничью.\n"
        f"Очки «{clan_a['name']}»: {clan_a.get('points', 0):g} | "
        f"Очки «{clan_b['name']}»: {clan_b.get('points', 0):g}"
    )


# ------------------------------------------------------------- /minduel ----

@router.message(Command("minduel"))
async def cmd_minduel(message: Message) -> None:
    async with Storage() as db:
        invite = db.get("pending_invite")
        if not invite:
            await message.reply("Сейчас нет активного вызова на дуэль.")
            return
        if message.from_user.id not in (invite["player_a_id"], invite["player_b_id"]):
            await message.reply("Эта дуэль вызвана не для вас.")
            return

        clan_a = _find_clan(db, invite["clan_a_id"])
        clan_b = _find_clan(db, invite["clan_b_id"])
        if not clan_a or not clan_b:
            db["pending_invite"] = None
            await message.reply("Один из кланов-участников больше не существует. Дуэль отменена.")
            return

        duel_id = db["next_duel_id"]
        db["next_duel_id"] += 1

        duel = {
            "id": duel_id,
            "sides": {
                "a": {
                    "clan_id": clan_a["id"], "player_id": invite["player_a_id"],
                    "stage": "choose_mines", "mines_count": None, "mine_positions": [],
                    "opened_cells": [], "current_multiplier": 1.0, "result": None,
                    "multiplier": 0, "chat_id": None, "message_id": None,
                    "stake": max(round(clan_a.get("points", 0)), config.MIN_STAKE_AL),
                    "last_action_at": now(),
                },
                "b": {
                    "clan_id": clan_b["id"], "player_id": invite["player_b_id"],
                    "stage": "choose_mines", "mines_count": None, "mine_positions": [],
                    "opened_cells": [], "current_multiplier": 1.0, "result": None,
                    "multiplier": 0, "chat_id": None, "message_id": None,
                    "stake": max(round(clan_b.get("points", 0)), config.MIN_STAKE_AL),
                    "last_action_at": now(),
                },
            },
        }
        db["active_duels"][str(duel_id)] = duel
        db["pending_invite"] = None

        rules = texts.duel_rules_text(
            clan_a["name"], clan_a.get("points", 0), clan_b["name"], clan_b.get("points", 0)
        )
        progressions = {
            m: _format_progression(game.progression_list(m, steps=5, start_from=0))
            for m in range(config.MIN_MINES, config.MAX_MINES + 1)
        }
        prog_block = texts.mines_progressions_block(progressions)

    text = rules + "\n" + prog_block + texts.choose_mines_prompt()
    await message.answer(text, parse_mode="HTML", reply_markup=mine_count_kb(duel_id))


# ------------------------------------------------------- выбор кол-ва мин --

@router.callback_query(F.data.startswith("duel:mines:"))
async def cb_choose_mines(callback: CallbackQuery) -> None:
    _, _, duel_id_s, mines_s = callback.data.split(":")
    duel_id, mines = int(duel_id_s), int(mines_s)

    async with Storage() as db:
        duel = db["active_duels"].get(str(duel_id))
        if not duel:
            await callback.answer("Эта дуэль уже завершена.", show_alert=True)
            return
        side_key = _side_key_for_user(duel, callback.from_user.id)
        if side_key is None:
            await callback.answer("Это не ваша дуэль.", show_alert=True)
            return
        side = duel["sides"][side_key]
        if side["stage"] != "choose_mines":
            await callback.answer("Вы уже выбрали количество мин.", show_alert=True)
            return

        side["mines_count"] = mines
        side["mine_positions"] = game.generate_mines(mines)
        side["opened_cells"] = []
        side["current_multiplier"] = 1.0
        side["stage"] = "playing"
        side["last_action_at"] = now()

        next_prog = _format_progression(game.progression_list(mines, steps=5, start_from=0))
        header = texts.board_header(mines, 0, 1.0, next_prog, side["stake"])

    user = callback.from_user
    name = f"@{user.username}" if user.username else user.first_name
    sent = await callback.message.answer(
        f"🎯 {name}, ваше поле готово!\n\n" + header,
        parse_mode="HTML",
        reply_markup=board_kb(duel_id, []),
    )

    async with Storage() as db:
        duel = db["active_duels"].get(str(duel_id))
        if duel:
            duel["sides"][side_key]["chat_id"] = sent.chat.id
            duel["sides"][side_key]["message_id"] = sent.message_id

    await callback.answer("Мины расставлены, удачи! 🍀")


# ------------------------------------------------------------- клик поля ---

@router.callback_query(F.data.startswith("duel:cell:"))
async def cb_cell(callback: CallbackQuery) -> None:
    _, _, duel_id_s, idx_s = callback.data.split(":")
    duel_id, idx = int(duel_id_s), int(idx_s)

    finalize_text = None
    async with Storage() as db:
        duel = db["active_duels"].get(str(duel_id))
        if not duel:
            await callback.answer("Эта дуэль уже завершена.", show_alert=True)
            return
        side_key = _side_key_for_user(duel, callback.from_user.id)
        if side_key is None:
            await callback.answer("Это не ваша дуэль.", show_alert=True)
            return
        side = duel["sides"][side_key]
        if side["stage"] != "playing":
            await callback.answer("Ваша игра уже завершена.", show_alert=True)
            return
        if idx in side["opened_cells"]:
            await callback.answer("Эта клетка уже открыта.")
            return

        side["last_action_at"] = now()
        mines = side["mines_count"]
        user = callback.from_user
        clan = _find_clan(db, side["clan_id"])

        if idx in side["mine_positions"]:
            side["opened_cells"].append(idx)
            side["stage"] = "done"
            side["result"] = "loss"
            new_points = _apply_loss(clan)
            text = texts.lose_text(clan["name"], new_points)
            kb = board_kb(duel_id, side["opened_cells"], exploded=True)
            other = duel["sides"][_other_side(side_key)]
            if other["stage"] == "done":
                finalize_text = _finalize_duel_result(db, duel)
                del db["active_duels"][str(duel_id)]
        else:
            side["opened_cells"].append(idx)
            opened_count = len(side["opened_cells"])
            side["current_multiplier"] = game.multiplier_for(mines, opened_count)
            side["multiplier"] = side["current_multiplier"]

            max_possible = config.TOTAL_CELLS - mines
            if opened_count >= max_possible:
                # все безопасные клетки открыты — авто-забор выигрыша
                new_points, won_al = _apply_cashout(
                    clan, side["current_multiplier"], side["stake"], user.id, user.username or user.first_name
                )
                side["stage"] = "done"
                side["result"] = "win"
                text = (
                    "🌟 Все безопасные клетки открыты! Автоматически забираем выигрыш.\n\n"
                    + texts.cashout_text(clan["name"], side["current_multiplier"], won_al, new_points)
                )
                kb = None
                other = duel["sides"][_other_side(side_key)]
                if other["stage"] == "done":
                    finalize_text = _finalize_duel_result(db, duel)
                    del db["active_duels"][str(duel_id)]
            else:
                next_prog = _format_progression(
                    game.progression_list(mines, steps=5, start_from=opened_count)
                )
                text = texts.board_header(
                    mines, opened_count, side["current_multiplier"], next_prog, side["stake"]
                )
                kb = board_kb(duel_id, side["opened_cells"])

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await callback.answer()

    if finalize_text:
        try:
            await callback.message.answer(finalize_text, parse_mode="HTML")
        except Exception:
            pass


# --------------------------------------------------------------- cashout ---

@router.callback_query(F.data.startswith("duel:cashout:"))
async def cb_cashout(callback: CallbackQuery) -> None:
    duel_id = int(callback.data.split(":")[2])

    finalize_text = None
    async with Storage() as db:
        duel = db["active_duels"].get(str(duel_id))
        if not duel:
            await callback.answer("Эта дуэль уже завершена.", show_alert=True)
            return
        side_key = _side_key_for_user(duel, callback.from_user.id)
        if side_key is None:
            await callback.answer("Это не ваша дуэль.", show_alert=True)
            return
        side = duel["sides"][side_key]
        if side["stage"] != "playing":
            await callback.answer("Ваша игра уже завершена.", show_alert=True)
            return

        clan = _find_clan(db, side["clan_id"])
        user = callback.from_user
        multiplier = side["current_multiplier"] if side["opened_cells"] else 1.0
        new_points, won_al = _apply_cashout(
            clan, multiplier, side["stake"], user.id, user.username or user.first_name
        )
        side["stage"] = "done"
        side["result"] = "win"
        side["multiplier"] = multiplier

        text = texts.cashout_text(clan["name"], multiplier, won_al, new_points)

        other = duel["sides"][_other_side(side_key)]
        if other["stage"] == "done":
            finalize_text = _finalize_duel_result(db, duel)
            del db["active_duels"][str(duel_id)]

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=None)
    except Exception:
        pass
    await callback.answer("Очки зафиксированы!")

    if finalize_text:
        try:
            await callback.message.answer(finalize_text, parse_mode="HTML")
        except Exception:
            pass


# ------------------------------------------------------------ AFK-хранитель

async def afk_watcher_loop(bot: Bot) -> None:
    """Фоновая задача: следит за неактивными игроками и автофиксирует их выигрыш."""
    import asyncio

    while True:
        await asyncio.sleep(config.AFK_CHECK_INTERVAL_SECONDS)
        try:
            await _check_afk_once(bot)
        except Exception:
            pass


async def _check_afk_once(bot: Bot) -> None:
    to_notify = []  # (chat_id, message_id, text, kb, finalize_text)

    async with Storage() as db:
        for duel_id_s, duel in list(db["active_duels"].items()):
            for side_key in ("a", "b"):
                side = duel["sides"][side_key]
                if side["stage"] != "playing":
                    continue
                if now() - side.get("last_action_at", 0) < config.AFK_TIMEOUT_SECONDS:
                    continue
                if not side.get("chat_id") or not side.get("message_id"):
                    continue

                clan = _find_clan(db, side["clan_id"])
                if not clan:
                    continue

                multiplier = side["current_multiplier"] if side["opened_cells"] else 1.0
                member = clan.get("members", {}).get(str(side["player_id"]), {})
                username = member.get("username") or member.get("first_name", "Игрок")

                new_points, won_al = _apply_cashout(clan, multiplier, side["stake"], side["player_id"], username)
                side["stage"] = "done"
                side["result"] = "win"
                side["multiplier"] = multiplier

                text = (
                    texts.afk_autocashout_text(f"@{username}" if member.get("username") else username)
                    + "\n\n"
                    + texts.cashout_text(clan["name"], multiplier, won_al, new_points)
                )

                finalize_text = None
                other = duel["sides"][_other_side(side_key)]
                if other["stage"] == "done":
                    finalize_text = _finalize_duel_result(db, duel)
                    del db["active_duels"][str(duel_id_s)]

                to_notify.append((side["chat_id"], side["message_id"], text, finalize_text))

    for chat_id, message_id, text, finalize_text in to_notify:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML"
            )
        except Exception:
            pass
        if finalize_text:
            try:
                await bot.send_message(chat_id, finalize_text, parse_mode="HTML")
            except Exception:
                pass
