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
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

import config
from bot.storage import Storage, now
from bot.chat_state import get_chat, all_chat_ids
from bot.clan_utils import ensure_clan_fields
from bot import betting, pins
from bot.invite_view import invite_view

logger = logging.getLogger("turns")


def _skip_note_for(clan: dict, player_id: int) -> str:
    member = clan.get("members", {}).get(str(player_id), {}) if clan else {}
    name = f'@{member["username"]}' if member.get("username") else member.get("first_name", "Игрок")
    return f"{name} не пришёл(а) на дуэль⛔ Переход хода следующему."


async def _expire_pending_invites(bot: Bot) -> None:
    to_sweep = []  # chat_id групп, где вызов сняли — там открепляем (bot/pins.py)
    to_edit = []   # (chat_id, message_id, text) — анонс без кнопок ставок

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

            to_sweep.append(chat_id)

            # вызов не принят — ставки зрителей возвращаются полностью (и купоны тоже)
            if invite.get("bets") is not None:
                betting.refund(chat, invite["bets"])
                if invite.get("chat_id") and invite.get("message_id"):
                    view = invite_view(chat, invite, closed=True)
                    if view:
                        to_edit.append((
                            invite["chat_id"], invite["message_id"],
                            view[0] + "\n\n⌛ Вызов не принят вовремя — ставки (если были) возвращены.",
                        ))
            chat["pending_invite"] = None

    for chat_id, message_id, text in to_edit:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),  # убираем кнопки ставок
            )
        except Exception:
            pass
    for chat_id in to_sweep:  # вызов уже не «живой» (pending_invite сброшен) — его закреп снимается
        await pins.sweep(bot, chat_id)


async def _expire_stuck_duel_sides(bot: Bot) -> None:
    notifications = []  # (chat_id, text, reply_to_message_id)
    to_sweep = []       # группы, где дуэль закрыта из-за «не пришёл» — там открепляем

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
                        notifications.append((chat_id, text, duel.get("start_message_id")))
                    to_sweep.append(chat_id)
                    del chat["active_duels"][duel_id]

    if notifications:
        from bot.handlers.duel import _send_finale
        for chat_id, text, reply_to in notifications:
            await _send_finale(bot, chat_id, text, reply_to)
    for chat_id in to_sweep:  # дуэль закрыта — её меню выбора мин больше не «живое», открепляем
        await pins.sweep(bot, chat_id)


async def turn_watcher_loop(bot: Bot) -> None:
    """Раз в 5 минут: снимает просроченные вызовы, закрывает дуэли, где кто-то не пришёл,
    и подметает закрепы. Каждый шаг в своём try/except и с записью ошибки в лог — раньше
    любой сбой молча глотался, и один упавший шаг блокировал все остальные."""
    startup = True
    while True:
        if not startup:
            await asyncio.sleep(config.TURN_CHECK_INTERVAL_SECONDS)
        for name, step in (
            ("снятие просроченных вызовов", _expire_pending_invites),
            ("закрытие дуэлей с неявкой", _expire_stuck_duel_sides),
        ):
            try:
                await step(bot)
            except Exception:
                logger.exception("Ошибка фоновой задачи: %s", name)
        try:
            # страховка: открепляет всё, что бот закреплял и что уже не нужно. При старте бота
            # (startup) — ещё и разовая зачистка закрепов, оставшихся от старых версий.
            await pins.sweep_all(bot, legacy=startup)
        except Exception:
            logger.exception("Ошибка фоновой зачистки закрепов")
        startup = False
