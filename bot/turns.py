# -*- coding: utf-8 -*-
"""
Передача хода при пропуске.

Если вызванный игрок клана вообще не сыграл (ни разу не выбрал количество
мин) в течение config.TURN_TIMEOUT_HOURS — его попытка "сгорает" для него, но
НЕ теряется для клана: clan["carried_attempts"] увеличивается на 1, и при
следующем вызове следующий игрок в очереди получит на 1 попытку больше.

Проверяется ОТДЕЛЬНО в каждой группе (дуэли/приглашения группы никак не
пересекаются с другими группами).

Два случая протухания:
 1. Приглашение на дуэль (chat["pending_invite"]) — НИКТО из двоих не нажал
    /minduel вовсе.
 2. Сторона уже созданной дуэли осталась в стадии "choose_mines" (второй
    игрок нажал /minduel, но этот конкретный участник так и не выбрал мины).
"""

import asyncio

from aiogram import Bot

import config
from bot.storage import Storage, now
from bot.chat_state import get_chat, all_chat_ids
from bot.clan_utils import ensure_clan_fields


def _skip_note_for(clan: dict, player_id: int) -> str:
    member = clan.get("members", {}).get(str(player_id), {}) if clan else {}
    name = f'@{member["username"]}' if member.get("username") else member.get("first_name", "Игрок")
    return f"{name} не пришёл(а) на дуэль⛔ Переход хода следующему."


async def _expire_pending_invites(bot: Bot) -> None:
    to_unpin = []  # (chat_id, message_id)

    async with Storage() as db:
        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            invite = chat.get("pending_invite")
            if not invite:
                continue
            if now() - invite.get("created_at", now()) < config.TURN_TIMEOUT_HOURS * 3600:
                continue

            notes = chat.setdefault("pending_skip_notes", [])
            for clan_key, player_key in (("clan_a_id", "player_a_id"), ("clan_b_id", "player_b_id")):
                clan = chat["clans"].get(str(invite[clan_key]))
                if clan:
                    ensure_clan_fields(clan)
                    clan["carried_attempts"] = clan.get("carried_attempts", 1) + 1
                    notes.append(_skip_note_for(clan, invite[player_key]))

            if invite.get("chat_id") and invite.get("message_id"):
                to_unpin.append((invite["chat_id"], invite["message_id"]))
            chat["pending_invite"] = None

    for chat_id, message_id in to_unpin:
        try:
            await bot.unpin_chat_message(chat_id, message_id)
        except Exception:
            pass


async def _expire_stuck_duel_sides(bot: Bot) -> None:
    notifications = []  # (chat_id, text)
    to_unpin = []

    async with Storage() as db:
        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            for duel_id, duel in list(chat["active_duels"].items()):
                touched = False
                for side_key in ("a", "b"):
                    side = duel["sides"][side_key]
                    if side["stage"] != "choose_mines":
                        continue
                    if now() - side.get("last_action_at", 0) < config.TURN_TIMEOUT_HOURS * 3600:
                        continue

                    clan = chat["clans"].get(str(side["clan_id"]))
                    if clan:
                        ensure_clan_fields(clan)
                        clan["carried_attempts"] = clan.get("carried_attempts", 1) + 1
                        chat.setdefault("pending_skip_notes", []).append(_skip_note_for(clan, side["player_id"]))

                    side["stage"] = "done"
                    side["result"] = None  # не сыграл — не победа и не поражение
                    touched = True

                if not touched:
                    continue

                other_a, other_b = duel["sides"]["a"], duel["sides"]["b"]
                if other_a["stage"] == "done" and other_b["stage"] == "done":
                    from bot.handlers.duel import _finalize_duel_result
                    text = _finalize_duel_result(chat, duel)
                    if text:
                        notifications.append((chat_id, text))
                    if duel.get("pinned_chat_id") and duel.get("pinned_message_id"):
                        to_unpin.append((duel["pinned_chat_id"], duel["pinned_message_id"]))
                    del chat["active_duels"][duel_id]

    for chat_id, text in notifications:
        try:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception:
            pass
    for chat_id, message_id in to_unpin:
        try:
            await bot.unpin_chat_message(chat_id, message_id)
        except Exception:
            pass


async def turn_watcher_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(config.TURN_CHECK_INTERVAL_SECONDS)
        try:
            await _expire_pending_invites(bot)
            await _expire_stuck_duel_sides(bot)
        except Exception:
            pass
