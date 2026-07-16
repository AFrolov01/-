# -*- coding: utf-8 -*-
"""
Матчмейкинг для войны кланов.

Дуэли происходят ТОЛЬКО внутри своей группы (кланы разных групп никогда не
встречаются друг с другом). У каждой группы свой независимый таймер вызова
дуэли (chat["next_duel_due_at"]) — фоновый цикл проверяет все группы раз в
несколько минут и объявляет дуэль в тех, где подошло время.

Правила подбора пары внутри группы (реализованы приближённо, но осмысленно):
 - если кланов всего 2 — играют всегда друг с другом;
 - если кланов больше 2:
     * первым выбирается клан, который дольше всех не играл (или ещё не играл
       вообще) и/или у которого меньше всего очков ("аутсайдеры" в приоритете);
     * его соперник должен чередоваться: то другой аутсайдер, то лидер по
       очкам — чтобы у лидера тоже не было простоя, а у аутсайдеров был шанс
       "выстрелить";
 - внутри клана на бой отправляется участник СТРОГО ПО СКРЫТОЙ ОЧЕРЕДИ
   (игрок1 -> игрок2 -> игрок3 -> игрок1 -> ...), очередь не видна пользователям,
   хранится в clan["queue"];
 - если предыдущий вызов игрока клана остался неотыгранным, его попытка
   переходит следующему в очереди с накоплением (см. bot/turns.py);
 - игрок, который прямо СЕЙЧАС находится в незавершённой дуэли (выбирает мины
   или уже играет), никогда не выбирается для НОВОГО вызова — очередь просто
   пропускает его и берёт следующего.

Состояние чередования (кого позвать соперником — другого аутсайдера или
лидера) хранится в самом chat как chat["matchmaking_alternate_leader"] (bool).
"""

import asyncio
import random
from typing import Optional, Tuple

from aiogram import Bot

import config
from bot.storage import Storage, now
from bot import texts
from bot.chat_state import get_chat, all_chat_ids
from bot.clan_utils import ensure_clan_fields


def _is_member_busy(chat: dict, user_id: int) -> bool:
    """True, если игрок сейчас участвует в невыгранной дуэли (приглашён, но ещё
    не сыграл, либо уже играет) — таких нельзя вызывать на новую дуэль."""
    invite = chat.get("pending_invite")
    if invite and user_id in (invite.get("player_a_id"), invite.get("player_b_id")):
        return True
    for duel in chat.get("active_duels", {}).values():
        for side_key in ("a", "b"):
            side = duel["sides"][side_key]
            if side["player_id"] == user_id and side["stage"] in ("choose_mines", "playing"):
                return True
    return False


def _pick_member(clan: dict, chat: dict) -> Optional[dict]:
    """Выбирает следующего СВОБОДНОГО игрока клана по скрытой очереди (round-robin),
    пропуская тех, кто прямо сейчас занят в другой незавершённой дуэли."""
    members = clan.get("members", {})
    if not members:
        return None
    member_ids = list(members.keys())
    queue = [uid for uid in clan.get("queue", []) if uid in member_ids]
    for uid in member_ids:
        if uid not in queue:
            queue.append(uid)
    if not queue:
        return None

    for i, uid in enumerate(queue):
        if _is_member_busy(chat, int(uid)):
            continue
        queue.pop(i)
        queue.append(uid)
        clan["queue"] = queue
        return members[uid]

    return None  # все участники клана сейчас заняты


def pick_duel_pair(chat: dict) -> Optional[Tuple[dict, dict, dict, dict]]:
    """Возвращает (clan_a, member_a, clan_b, member_b) либо None, если играть некому."""
    clans = [c for c in chat["clans"].values() if c.get("members")]
    for c in clans:
        ensure_clan_fields(c)
    if len(clans) < 2:
        return None

    if len(clans) == 2:
        clan_a, clan_b = clans[0], clans[1]
    else:
        clans_sorted_outsider = sorted(
            clans, key=lambda c: (c.get("points", 0), c.get("last_played_at", 0))
        )
        clan_a = clans_sorted_outsider[0]

        alternate_leader = chat.get("matchmaking_alternate_leader", False)
        rest = [c for c in clans if c["id"] != clan_a["id"]]
        if alternate_leader:
            rest_sorted = sorted(rest, key=lambda c: -c.get("points", 0))
        else:
            rest_sorted = sorted(rest, key=lambda c: (c.get("points", 0), c.get("last_played_at", 0)))
        clan_b = rest_sorted[0]
        chat["matchmaking_alternate_leader"] = not alternate_leader

    member_a = _pick_member(clan_a, chat)
    member_b = _pick_member(clan_b, chat)
    if not member_a or not member_b:
        return None
    return clan_a, member_a, clan_b, member_b


async def announce_duel(bot: Bot, chat_id: int) -> Tuple[bool, str]:
    """Объявляет дуэль в КОНКРЕТНОЙ группе. Возвращает (успех, причина/описание)."""
    async with Storage() as db:
        chat = get_chat(db, chat_id)
        if chat.get("pending_invite"):
            return False, "Предыдущий вызов на дуэль ещё не сыгран (ждём /minduel от вызванных игроков)."
        pair = pick_duel_pair(chat)
        if not pair:
            return False, (
                "Недостаточно кланов со свободными участниками для дуэли прямо сейчас "
                "(нужно минимум 2 клана, у которых есть игрок, не занятый в другой дуэли)."
            )
        clan_a, member_a, clan_b, member_b = pair

        attempts_a = clan_a.get("carried_attempts", 1)
        attempts_b = clan_b.get("carried_attempts", 1)
        clan_a["carried_attempts"] = 1
        clan_b["carried_attempts"] = 1

        chat["pending_invite"] = {
            "clan_a_id": clan_a["id"],
            "player_a_id": member_a["user_id"],
            "clan_b_id": clan_b["id"],
            "player_b_id": member_b["user_id"],
            "attempts_a": attempts_a,
            "attempts_b": attempts_b,
            "created_at": now(),
            "message_id": None,
        }
        name_a = f'@{member_a["username"]}' if member_a.get("username") else member_a.get("first_name", "Игрок")
        name_b = f'@{member_b["username"]}' if member_b.get("username") else member_b.get("first_name", "Игрок")
        text = texts.duel_invite_text(name_a, name_b)

        skip_notes = chat.pop("pending_skip_notes", [])
        if skip_notes:
            text = "\n".join(skip_notes) + "\n\n" + text
            chat["pending_skip_notes"] = []

    try:
        sent = await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        async with Storage() as db:
            chat = get_chat(db, chat_id)
            chat["pending_invite"] = None
        return False, f"Не удалось отправить сообщение в группу: {e}"

    async with Storage() as db:
        chat = get_chat(db, chat_id)
        if chat.get("pending_invite"):
            chat["pending_invite"]["message_id"] = sent.message_id
            chat["pending_invite"]["chat_id"] = sent.chat.id
    try:
        await bot.pin_chat_message(sent.chat.id, sent.message_id, disable_notification=True)
    except Exception:
        pass  # бот может быть не админом — не критично, просто без закрепления

    return True, f"Дуэль объявлена: «{clan_a['name']}» vs «{clan_b['name']}»."


def _next_interval_seconds() -> float:
    hours = random.uniform(config.DUEL_INTERVAL_MIN_HOURS, config.DUEL_INTERVAL_MAX_HOURS)
    return hours * 3600


async def scheduler_loop(bot: Bot) -> None:
    """Фоновая задача: у каждой группы свой независимый таймер (~4ч ±40мин).
    Проверяем все известные группы раз в несколько минут; если для группы
    подошло время — объявляем в ней дуэль и назначаем следующий момент."""
    while True:
        await asyncio.sleep(config.SCHEDULER_CHECK_INTERVAL_SECONDS)
        try:
            async with Storage() as db:
                due_chat_ids = []
                for chat_id in all_chat_ids(db):
                    chat = get_chat(db, chat_id)
                    if not chat["clans"]:
                        continue
                    due_at = chat.get("next_duel_due_at")
                    if due_at is None:
                        chat["next_duel_due_at"] = now() + _next_interval_seconds()
                        continue
                    if now() >= due_at:
                        due_chat_ids.append(chat_id)

            for chat_id in due_chat_ids:
                success, _reason = await announce_duel(bot, chat_id)
                async with Storage() as db:
                    chat = get_chat(db, chat_id)
                    # даже если не получилось (например, все заняты) — не долбим
                    # каждую минуту, пробуем снова через обычный интервал
                    chat["next_duel_due_at"] = now() + _next_interval_seconds()
        except Exception:
            pass
