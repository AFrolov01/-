# -*- coding: utf-8 -*-
"""
Логика мин-дуэли.

ПОРЯДОК ПРИМЕНЕНИЯ БОНУСОВ К ВЫИГРЫШУ (важно, фиксированный порядок):
 1. Базовое поле (x из прогрессии мин)
 2. Усиление портала (тактика/предмет "Вращайте барабан", x1.5 за каждый портал,
    накопительно) — шаги 1+2 вместе дают side["current_multiplier"]
 3. % тактики клана (например тактика "Да да нет нет" +10% за серию побед) —
    ТОЛЬКО к выигранным очкам этого раунда, не трогает баланс клана напрямую
 4. % недельного модификатора (накопительный ранговый баф/дебафф)
 5. Личные предметы игрока (например "Сливы, виноград") — применяются САМЫМИ
    ПОСЛЕДНИМИ, после того как уже посчитаны шаги 1-4

ВАЖНО про экономику очков:
 - ставка каждой ПОПЫТКИ = ВСЕ текущие очки клана на момент выбора количества
   мин для этой попытки (снимок берётся заново для каждой попытки);
 - при подрыве на мине: очки клана умножаются на эффективный штраф (обычно
   LOSS_MULTIPLIER=0.75, тактики могут его менять).

ПОРТАЛ: если у клана тактика "Вращайте барабан" и/или у игрока активен купленный
"Кубик-нубика" — на поле есть 1 (или 2, если активны оба) клетка-портал. Клик по
ней полностью сбрасывает текущий раунд (множитель сгорает), поле генерируется
заново с тем же числом мин, а ВСЕ множители умножаются на PORTAL_MULTIPLIER_STEP
(накопительно). Переходов не ограничено, попытка при этом не расходуется.

ПРОЗРАЧНОСТЬ: после того как раунд завершён (победа или поражение), бот
присылает отдельным сообщением поле с открытыми настоящими позициями мин (🔴)
и порталов (🌀), чтобы было видно, что реально было на поле.

ПОПЫТКИ: если игрок клана не сыграл свой вызов вовсе, его попытка переходит
следующему в очереди с накоплением (bot/turns.py). Столько попыток, сколько
накопилось, доступны в рамках ОДНОГО вызова подряд.

ЗАКРЕПЛЕНИЕ: сообщение-вызов на дуэль закрепляется ботом; как только кто-то
открывает выбор количества мин, вызов открепляется и закрепляется само меню
выбора мин; когда дуэль (все попытки обеих сторон) завершена — открепляется.
"""

import random
from typing import Optional, Tuple

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import config
from bot.storage import Storage, now
from bot import texts, game, players, tactics
from bot.clan_utils import ensure_clan_fields
from bot.keyboards import mine_count_kb, board_kb, board_revealed_kb

router = Router(name="duel")


# ---------------------------------------------------------------- helpers --

def _find_clan(db: dict, clan_id: int) -> Optional[dict]:
    clan = db["clans"].get(str(clan_id))
    if clan:
        ensure_clan_fields(clan)
    return clan


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


async def _unpin_safe(bot: Bot, chat_id, message_id) -> None:
    if not chat_id or not message_id:
        return
    try:
        await bot.unpin_chat_message(chat_id, message_id)
    except Exception:
        pass


def _default_shop() -> dict:
    return {
        "avoid_punishment": 0, "next_win_boost": False, "next_loss_forgiven": False,
        "noob_dice_rounds": 0, "grapes_rounds": 0,
    }


def _apply_loss(clan: dict, side: dict, player: dict) -> Tuple[float, str, bool, list, float]:
    """Возвращает (новые_очки, текст_эффекта_привилегии, конвертировано_в_победу, новые_ачивки, применённый_множитель)."""
    shop = player.setdefault("shop", _default_shop())

    if shop.get("next_loss_forgiven"):
        shop["next_loss_forgiven"] = False
        new_points, won_al, base_al, new_ach, _grapes_note, _tactic_note = _apply_cashout(
            clan, side, player, 1.0, side["stake"], player["user_id"],
            player.get("username") or player.get("first_name")
        )
        note = "🔵 Привилегия «Поражение не засчитывается» сработала — раунд завершён на x1, без потерь."
        return new_points, note, True, new_ach, 1.0

    mult = tactics.effective_loss_multiplier(clan, side)
    new_points = round(clan.get("points", 0) * mult, 2)
    clan["points"] = new_points
    clan["current_win_streak"] = 0
    tactics.register_round_result(clan, won=False)
    new_ach = players.record_round_result(player, won=False, multiplier=0)
    return new_points, "", False, new_ach, mult


def _apply_cashout(
    clan: dict, side: dict, player: dict, multiplier: float, stake: int, user_id: int, username: str
) -> Tuple[float, int, int, list, str, str]:
    """Возвращает (новые_очки, выигрыш_с_модификаторами, выигрыш_до_недельного_модификатора,
    новые_ачивки, заметка_о_личном_предмете, заметка_о_бонусе_тактики).
    `multiplier` уже включает шаги 1+2 (поле + усиление портала)."""
    shop = player.setdefault("shop", _default_shop())

    # шаг 3: % тактики клана (+ next_win_boost как временный аналогичный бонус)
    pure_tactic_mult = tactics.win_points_multiplier(clan, side, player)
    tactic_mult = pure_tactic_mult
    if shop.get("next_win_boost"):
        shop["next_win_boost"] = False
        tactic_mult *= 1.5
    after_tactic = stake * multiplier * tactic_mult
    base_al = round(after_tactic)

    # шаг 4: недельный модификатор
    weekly_mult = tactics.weekly_modifier_multiplier(clan)
    after_weekly = after_tactic * weekly_mult

    # шаг 5: личные предметы ("Сливы, виноград") — применяются последними
    grapes_note = ""
    final_amount = after_weekly
    if shop.get("grapes_rounds", 0) > 0:
        eff_x = (after_weekly / stake) if stake else 0
        if eff_x < config.GRAPES_MIN_MULTIPLIER:
            if random.random() < config.GRAPES_SUCCESS_CHANCE:
                final_amount = stake * config.GRAPES_MIN_MULTIPLIER
                grapes_note = (
                    f"🍇 «Сливы, виноград» сработали! Выигрыш поднят до "
                    f"x{config.GRAPES_MIN_MULTIPLIER} ({round(final_amount)} Al)."
                )
            else:
                final_amount = after_weekly * (1 - config.GRAPES_FAIL_PENALTY)
                grapes_note = (
                    f"🍇 «Сливы, виноград» — не повезло: "
                    f"-{int(config.GRAPES_FAIL_PENALTY * 100)}% от выигрыша "
                    f"({round(final_amount)} Al вместо {round(after_weekly)})."
                )

    won_al = round(final_amount)
    new_points = round(clan.get("points", 0) - stake + won_al, 2)
    clan["points"] = new_points

    best = clan.get("best_single_multiplier")
    if not best or multiplier > best.get("value", 0):
        clan["best_single_multiplier"] = {
            "value": multiplier, "user_id": user_id, "username": username or ""
        }

    tactics.register_round_result(clan, won=True)
    tactic_note = tactics.describe_tactic_bonus(clan, side, player, pure_tactic_mult)
    te_gain = round(multiplier * tactics.currency_multiplier(clan, side), 2)
    new_ach = players.record_round_result(player, won=True, multiplier=multiplier, currency_gain=te_gain)

    return new_points, won_al, base_al, new_ach, grapes_note, tactic_note


def _cashout_text_for(clan: dict, multiplier: float, won_al: int, base_al: int, new_points: float, grapes_note: str = "", tactic_note: str = "") -> str:
    weekly_pct = clan.get("weekly_percent_modifier", 0)
    return texts.cashout_text(clan["name"], multiplier, won_al, new_points, weekly_pct, base_al, grapes_note, tactic_note)


def _finalize_duel_result(db: dict, duel: dict) -> str:
    """Вызывается, когда ОБЕ стороны исчерпали свои попытки. Возвращает текст итога."""
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


def _revealed_kb_for(side: dict, exploded_cell=None):
    return board_revealed_kb(
        side.get("opened_cells", []), side.get("mine_positions", []),
        side.get("portal_positions", []), exploded_cell,
    )


# ------------------------------------------------------------- /minduel ----

def _find_active_side_for_user(db: dict, user_id: int):
    for duel in db["active_duels"].values():
        side_key = _side_key_for_user(duel, user_id)
        if side_key:
            return duel, side_key
    return None, None


def _new_side(player_id: int, attempts_total: int) -> dict:
    return {
        "player_id": player_id,
        "stage": "choose_mines", "mines_count": None,
        "mine_positions": [], "portal_positions": [], "portals_count": 0,
        "field_boost": 1.0, "portal_triggered_this_round": False,
        "opened_cells": [], "current_multiplier": 1.0, "result": None,
        "multiplier": 0, "chat_id": None, "message_id": None,
        "stake": None,
        "attempts_total": attempts_total, "attempts_used": 0,
        "last_action_at": now(),
    }


@router.message(Command("minduel"))
async def cmd_minduel(message: Message, bot: Bot) -> None:
    invite_to_unpin = None
    async with Storage() as db:
        invite = db.get("pending_invite")
        user_is_invited = bool(invite) and message.from_user.id in (
            invite["player_a_id"], invite["player_b_id"]
        )

        if not user_is_invited:
            duel, side_key = _find_active_side_for_user(db, message.from_user.id)
            if duel:
                side = duel["sides"][side_key]
                if side["stage"] == "choose_mines":
                    await message.reply(
                        "Ваша дуэль уже начата напарником по вызову — команда /minduel "
                        "второй раз не нужна. Найдите сообщение с правилами и кнопками "
                        "1️⃣–6️⃣ (выше в чате) и выберите там количество мин."
                    )
                elif side["stage"] == "playing":
                    await message.reply(
                        "Ваша игра уже идёт! Прокрутите чат немного выше и нажимайте "
                        "на клетки своего поля 5×5 (или «Забрать очки»)."
                    )
                else:
                    await message.reply("Ваша часть этой дуэли уже завершена.")
                return

            if not invite:
                await message.reply("Сейчас нет активного вызова на дуэль.")
            else:
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
                "a": {**_new_side(invite["player_a_id"], invite.get("attempts_a", 1)), "clan_id": clan_a["id"]},
                "b": {**_new_side(invite["player_b_id"], invite.get("attempts_b", 1)), "clan_id": clan_b["id"]},
            },
            "pinned_chat_id": None,
            "pinned_message_id": None,
        }
        db["active_duels"][str(duel_id)] = duel
        db["pending_invite"] = None

        if invite.get("chat_id") and invite.get("message_id"):
            invite_to_unpin = (invite["chat_id"], invite["message_id"])

        rules = texts.duel_rules_text(
            clan_a["name"], clan_a.get("points", 0), clan_b["name"], clan_b.get("points", 0)
        )
        progressions = {
            m: _format_progression(game.progression_list(m, steps=5, start_from=0))
            for m in range(config.MIN_MINES, config.MAX_MINES + 1)
        }
        prog_block = texts.mines_progressions_block(progressions)

        extra_note = ""
        max_attempts = max(invite.get("attempts_a", 1), invite.get("attempts_b", 1))
        if max_attempts > 1:
            extra_note = (
                f"\n\n🔁 У кого-то из вызванных накопились пропущенные ходы — "
                f"в этом вызове доступно до {max_attempts} попыток подряд."
            )

    if invite_to_unpin:
        await _unpin_safe(bot, invite_to_unpin[0], invite_to_unpin[1])

    text = rules + "\n" + prog_block + texts.choose_mines_prompt() + extra_note
    sent = await message.answer(text, parse_mode="HTML", reply_markup=mine_count_kb(duel_id))

    try:
        await bot.pin_chat_message(sent.chat.id, sent.message_id, disable_notification=True)
        async with Storage() as db:
            d = db["active_duels"].get(str(duel_id))
            if d:
                d["pinned_chat_id"] = sent.chat.id
                d["pinned_message_id"] = sent.message_id
    except Exception:
        pass


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

        clan = _find_clan(db, side["clan_id"])
        user = callback.from_user
        player = players.get_or_create_player(db, user.id, user.username or "", user.first_name or "Игрок")

        portals_count = players.portals_count_for(clan, player)
        mine_positions, portal_positions = game.generate_field(mines, portals_count)

        side["mines_count"] = mines
        side["mine_positions"] = mine_positions
        side["portal_positions"] = portal_positions
        side["portals_count"] = portals_count
        side["field_boost"] = 1.0
        side["portal_triggered_this_round"] = False
        side["opened_cells"] = []
        side["current_multiplier"] = 1.0
        side["stage"] = "playing"
        side["last_action_at"] = now()
        # ставка снимается заново на КАЖДУЮ попытку — текущие очки клана на этот момент
        side["stake"] = max(round(clan.get("points", 0)) if clan else 0, config.MIN_STAKE_AL)

        gamble_note = ""
        if clan and clan.get("tactic") == "gamble":
            effect_key = tactics.roll_gamble_effect()
            side["gamble_effect"] = effect_key
            effect = tactics.GAMBLE_EFFECTS[effect_key]
            gamble_note = (
                f'\n🎲 Тактика «Азарт»: выпал эффект {effect["emoji"]} <b>{effect["name"]}</b> '
                f'— {effect["desc"]}!'
            )

        portal_note = ""
        if portals_count > 0:
            portal_note = f"\n🌀 На поле спрятан{'ы' if portals_count > 1 else ''} {portals_count} портал{'а' if portals_count > 1 else ''}!"

        attempts_note = ""
        total = side.get("attempts_total", 1)
        if total > 1 and side.get("attempts_used", 0) == 0:
            attempts_note = f"\n🔁 Доступно попыток в этом вызове: {total}"

        next_prog = _format_progression(game.progression_list(mines, steps=5, start_from=0))
        header = texts.board_header(mines, 0, 1.0, next_prog, side["stake"]) + gamble_note + portal_note + attempts_note

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


# -------------------------------------------------- завершение попытки -----

def _finish_attempt(duel: dict, side: dict, side_key: str, db: dict):
    """После розыгрыша одной попытки: если остались ещё — сбрасывает сторону
    обратно в 'choose_mines' и возвращает (True, доп_текст). Если попытки
    исчерпаны — помечает сторону 'done' и возвращает (False, финализация_дуэли)."""
    side["attempts_used"] = side.get("attempts_used", 0) + 1
    remaining = side.get("attempts_total", 1) - side["attempts_used"]

    if remaining > 0:
        side["stage"] = "choose_mines"
        return True, texts.attempts_remaining_text(remaining)

    side["stage"] = "done"
    finalize_text = None
    pinned = None
    other = duel["sides"][_other_side(side_key)]
    if other["stage"] == "done":
        finalize_text = _finalize_duel_result(db, duel)
        pinned = (duel.get("pinned_chat_id"), duel.get("pinned_message_id"))
        del db["active_duels"][str(duel["id"])]
    return False, (finalize_text, pinned)


# ------------------------------------------------------------- клик поля ---

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("duel:cell:"))
async def cb_cell(callback: CallbackQuery, bot: Bot) -> None:
    _, _, duel_id_s, idx_s = callback.data.split(":")
    duel_id, idx = int(duel_id_s), int(idx_s)

    finalize_text = None
    pinned_to_unpin = None
    reveal_kb = None
    item_notes = []
    next_attempt_note = None
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
        player = players.get_or_create_player(db, user.id, user.username or "", user.first_name or "Игрок")

        new_achievements = []

        if idx in side.get("portal_positions", []):
            # --- ПОРТАЛ: сброс поля, множитель сгорает, усиление растёт ---
            old_multiplier = side["current_multiplier"] if side["opened_cells"] else 1.0
            side["field_boost"] = round(side.get("field_boost", 1.0) * config.PORTAL_MULTIPLIER_STEP, 4)
            side["portal_triggered_this_round"] = True

            mine_positions, portal_positions = game.generate_field(mines, side.get("portals_count", 0))
            side["mine_positions"] = mine_positions
            side["portal_positions"] = portal_positions
            side["opened_cells"] = []
            side["current_multiplier"] = game.multiplier_for(mines, 0, side["field_boost"])
            side["multiplier"] = side["current_multiplier"]

            next_prog = _format_progression(
                game.progression_list(mines, steps=5, start_from=0, boost=side["field_boost"])
            )
            text = (
                texts.portal_text(old_multiplier, side["field_boost"]) + "\n\n"
                + texts.board_header(mines, 0, side["current_multiplier"], next_prog, side["stake"], side["field_boost"])
            )
            kb = board_kb(duel_id, [])
            # попытка НЕ расходуется — раунд продолжается на новом поле

        elif idx in side["mine_positions"]:
            old_points = clan.get("points", 0)
            possible_multiplier = side["current_multiplier"] if side["opened_cells"] else 1.0
            possible_al = round(side["stake"] * possible_multiplier)

            side["opened_cells"].append(idx)
            new_points, effect_note, converted_to_win, new_achievements, applied_mult = _apply_loss(clan, side, player)
            if converted_to_win:
                side["result"] = "win"
                side["multiplier"] = 1.0
                text = effect_note + "\n\n" + _cashout_text_for(clan, 1.0, round(side["stake"]), round(side["stake"]), new_points)
            else:
                side["result"] = "loss"
                text = texts.lose_text(clan["name"], old_points, new_points, possible_multiplier, possible_al, applied_mult)
                if effect_note:
                    text = effect_note + "\n\n" + text

            reveal_kb = _revealed_kb_for(side, exploded_cell=idx)
            kb = reveal_kb
            item_notes = players.tick_temporary_items(player, side.get("portal_triggered_this_round", False))

            has_more, extra = _finish_attempt(duel, side, side_key, db)
            if has_more:
                next_attempt_note = extra
            else:
                finalize_text, pinned_to_unpin = extra
        else:
            side["opened_cells"].append(idx)
            opened_count = len(side["opened_cells"])
            side["current_multiplier"] = game.multiplier_for(mines, opened_count, side.get("field_boost", 1.0))
            side["multiplier"] = side["current_multiplier"]

            max_possible = config.TOTAL_CELLS - mines - len(side.get("portal_positions", []))
            if opened_count >= max_possible:
                new_points, won_al, base_al, new_achievements, grapes_note, tactic_note = _apply_cashout(
                    clan, side, player, side["current_multiplier"], side["stake"],
                    user.id, user.username or user.first_name
                )
                side["result"] = "win"
                text = (
                    "🌟 Все безопасные клетки открыты! Автоматически забираем выигрыш.\n\n"
                    + _cashout_text_for(clan, side["current_multiplier"], won_al, base_al, new_points, grapes_note, tactic_note)
                )

                reveal_kb = _revealed_kb_for(side)
                kb = reveal_kb
                item_notes = players.tick_temporary_items(player, side.get("portal_triggered_this_round", False))

                has_more, extra = _finish_attempt(duel, side, side_key, db)
                if has_more:
                    next_attempt_note = extra
                else:
                    finalize_text, pinned_to_unpin = extra
            else:
                next_prog = _format_progression(
                    game.progression_list(mines, steps=5, start_from=opened_count, boost=side.get("field_boost", 1.0))
                )
                text = texts.board_header(
                    mines, opened_count, side["current_multiplier"], next_prog, side["stake"], side.get("field_boost", 1.0)
                )
                kb = board_kb(duel_id, side["opened_cells"])

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await callback.answer()

    if item_notes:
        try:
            await callback.message.answer("\n".join(item_notes))
        except Exception:
            pass
    if new_achievements:
        try:
            await callback.message.answer(texts.new_achievements_text(new_achievements), parse_mode="HTML")
        except Exception:
            pass
    if next_attempt_note:
        try:
            await callback.message.answer(next_attempt_note, reply_markup=mine_count_kb(duel_id))
        except Exception:
            pass
    if finalize_text:
        try:
            await callback.message.answer(finalize_text, parse_mode="HTML")
        except Exception:
            pass
    if pinned_to_unpin:
        await _unpin_safe(bot, pinned_to_unpin[0], pinned_to_unpin[1])


# --------------------------------------------------------------- cashout ---

@router.callback_query(F.data.startswith("duel:cashout:"))
async def cb_cashout(callback: CallbackQuery, bot: Bot) -> None:
    duel_id = int(callback.data.split(":")[2])

    finalize_text = None
    pinned_to_unpin = None
    next_attempt_note = None
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
        player = players.get_or_create_player(db, user.id, user.username or "", user.first_name or "Игрок")
        multiplier = side["current_multiplier"] if side["opened_cells"] else 1.0
        new_points, won_al, base_al, new_achievements, grapes_note, tactic_note = _apply_cashout(
            clan, side, player, multiplier, side["stake"], user.id, user.username or user.first_name
        )
        side["result"] = "win"
        side["multiplier"] = multiplier

        text = _cashout_text_for(clan, multiplier, won_al, base_al, new_points, grapes_note, tactic_note)
        kb = _revealed_kb_for(side)
        item_notes = players.tick_temporary_items(player, side.get("portal_triggered_this_round", False))

        has_more, extra = _finish_attempt(duel, side, side_key, db)
        if has_more:
            next_attempt_note = extra
        else:
            finalize_text, pinned_to_unpin = extra

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Очки зафиксированы!")

    if item_notes:
        try:
            await callback.message.answer("\n".join(item_notes))
        except Exception:
            pass
    if new_achievements:
        try:
            await callback.message.answer(texts.new_achievements_text(new_achievements), parse_mode="HTML")
        except Exception:
            pass
    if next_attempt_note:
        try:
            await callback.message.answer(next_attempt_note, reply_markup=mine_count_kb(duel_id))
        except Exception:
            pass
    if finalize_text:
        try:
            await callback.message.answer(finalize_text, parse_mode="HTML")
        except Exception:
            pass
    if pinned_to_unpin:
        await _unpin_safe(bot, pinned_to_unpin[0], pinned_to_unpin[1])


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
    to_notify = []  # (chat_id, message_id, text, kb, finalize_text, pinned_to_unpin, next_attempt)

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
                player = players.get_or_create_player(
                    db, side["player_id"], member.get("username", ""), member.get("first_name", "Игрок")
                )

                new_points, won_al, base_al, new_achievements, grapes_note, tactic_note = _apply_cashout(
                    clan, side, player, multiplier, side["stake"], side["player_id"], username
                )
                side["result"] = "win"
                side["multiplier"] = multiplier

                text = (
                    texts.afk_autocashout_text(f"@{username}" if member.get("username") else username)
                    + "\n\n"
                    + _cashout_text_for(clan, multiplier, won_al, base_al, new_points, grapes_note, tactic_note)
                )
                if new_achievements:
                    text += "\n\n" + texts.new_achievements_text(new_achievements)

                item_notes = players.tick_temporary_items(player, side.get("portal_triggered_this_round", False))
                if item_notes:
                    text += "\n\n" + "\n".join(item_notes)

                kb = _revealed_kb_for(side)

                has_more, extra = _finish_attempt(duel, side, side_key, db)
                finalize_text = None
                pinned_to_unpin = None
                next_attempt = extra if has_more else None
                if not has_more:
                    finalize_text, pinned_to_unpin = extra

                to_notify.append((side["chat_id"], side["message_id"], text, kb, finalize_text, pinned_to_unpin, next_attempt, int(duel_id_s)))

    for chat_id, message_id, text, kb, finalize_text, pinned_to_unpin, next_attempt, duel_id in to_notify:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML", reply_markup=kb
            )
        except Exception:
            pass
        if next_attempt:
            try:
                await bot.send_message(chat_id, next_attempt, reply_markup=mine_count_kb(duel_id))
            except Exception:
                pass
        if finalize_text:
            try:
                await bot.send_message(chat_id, finalize_text, parse_mode="HTML")
            except Exception:
                pass
        if pinned_to_unpin:
            await _unpin_safe(bot, pinned_to_unpin[0], pinned_to_unpin[1])
