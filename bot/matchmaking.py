# -*- coding: utf-8 -*-
"""
Матчмейкинг для войны кланов.

Правила из ТЗ (реализованы приближённо, но осмысленно):
 - если кланов всего 2 — играют всегда друг с другом;
 - если кланов больше 2:
     * первым выбирается клан, который дольше всех не играл (или ещё не играл вообще)
       и/или у которого меньше всего очков ("аутсайдеры" в приоритете);
     * его соперник должен чередоваться: то другой аутсайдер, то лидер по очкам —
       чтобы у лидера тоже не было простоя, а у аутсайдеров был шанс "выстрелить";
 - внутри клана на бой отправляется участник СТРОГО ПО СКРЫТОЙ ОЧЕРЕДИ
   (игрок1 -> игрок2 -> игрок3 -> игрок1 -> ...), очередь не видна пользователям,
   хранится в clan["queue"];
 - если предыдущий вызов игрока клана остался неотыгранным, его попытка
   переходит следующему в очереди с накоплением (см. bot/turns.py).

ВАЖНО (исправление критичного бага): игрок, который прямо СЕЙЧАС находится в
незавершённой дуэли (выбирает мины или уже играет), НИКОГДА не выбирается для
НОВОГО вызова — очередь просто пропускает его и берёт следующего. Раньше здесь
была принудительная "зачистка" старых дуэлей перед каждым новым вызовом, но
она по ошибке могла преждевременно завершать ЧУЖУЮ сторону дуэли, пока
соперник ещё реально играл — это и вызывало "дуэль уже завершена" посреди игры.

Состояние чередования (кого позвать соперником — другого аутсайдера или лидера)
хранится в самом db как db["matchmaking_alternate_leader"] (bool).
"""

import asyncio
import random
from typing import Optional, Tuple

from aiogram import Bot

import config
from bot.storage import Storage, now
from bot import texts
from bot.clan_utils import ensure_clan_fields


def _is_member_busy(db: dict, user_id: int) -> bool:
    """True, если игрок сейчас участвует в невыгранной дуэли (приглашён, но ещё
    не сыграл, либо уже играет) — таких нельзя вызывать на новую дуэль."""
    invite = db.get("pending_invite")
    if invite and user_id in (invite.get("player_a_id"), invite.get("player_b_id")):
        return True
    for duel in db.get("active_duels", {}).values():
        for side_key in ("a", "b"):
            side = duel["sides"][side_key]
            if side["player_id"] == user_id and side["stage"] in ("choose_mines", "playing"):
                return True
    return False


def _pick_member(clan: dict, db: dict) -> Optional[dict]:
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
        if _is_member_busy(db, int(uid)):
            continue
        # переносим выбранного в конец очереди (обычная ротация), остальных не трогаем
        queue.pop(i)
        queue.append(uid)
        clan["queue"] = queue
        return members[uid]

    return None  # все участники клана сейчас заняты в других дуэлях


def pick_duel_pair(db: dict) -> Optional[Tuple[dict, dict, dict, dict]]:
    """Возвращает (clan_a, member_a, clan_b, member_b) либо None, если играть некому."""
    clans = [c for c in db["clans"].values() if c.get("members")]
    for c in clans:
        ensure_clan_fields(c)
    if len(clans) < 2:
        return None

    if len(clans) == 2:
        clan_a, clan_b = clans[0], clans[1]
    else:
        # аутсайдер: меньше очков + дольше не играл
        clans_sorted_outsider = sorted(
            clans, key=lambda c: (c.get("points", 0), c.get("last_played_at", 0))
        )
        clan_a = clans_sorted_outsider[0]

        alternate_leader = db.get("matchmaking_alternate_leader", False)
        rest = [c for c in clans if c["id"] != clan_a["id"]]
        if alternate_leader:
            # соперник — лидер по очкам
            rest_sorted = sorted(rest, key=lambda c: -c.get("points", 0))
        else:
            # соперник — следующий по "аутсайдерству"
            rest_sorted = sorted(rest, key=lambda c: (c.get("points", 0), c.get("last_played_at", 0)))
        clan_b = rest_sorted[0]
        db["matchmaking_alternate_leader"] = not alternate_leader

    member_a = _pick_member(clan_a, db)
    member_b = _pick_member(clan_b, db)
    if not member_a or not member_b:
        return None
    return clan_a, member_a, clan_b, member_b


async def announce_duel(bot: Bot) -> Tuple[bool, str]:
    """Возвращает (успех, причина/описание) — удобно и для планировщика, и для
    ручного вызова админом командой /forceduel из ЛС бота."""
    async with Storage() as db:
        group_id = db.get("group_chat_id")
        if not group_id:
            return False, "Боевой чат ещё не назначен. Вызовите /setgroup в нужной группе."
        if db.get("pending_invite"):
            return False, "Предыдущий вызов на дуэль ещё не сыгран (ждём /minduel от вызванных игроков)."
        pair = pick_duel_pair(db)
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

        db["pending_invite"] = {
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

        skip_notes = db.pop("pending_skip_notes", [])
        if skip_notes:
            text = "\n".join(skip_notes) + "\n\n" + text

    try:
        sent = await bot.send_message(group_id, text, parse_mode="HTML")
    except Exception as e:
        async with Storage() as db:
            db["pending_invite"] = None
        return False, f"Не удалось отправить сообщение в боевой чат: {e}"

    async with Storage() as db:
        if db.get("pending_invite"):
            db["pending_invite"]["message_id"] = sent.message_id
            db["pending_invite"]["chat_id"] = sent.chat.id
    try:
        await bot.pin_chat_message(sent.chat.id, sent.message_id, disable_notification=True)
    except Exception:
        pass  # бот может быть не админом — не критично, просто без закрепления

    return True, f"Дуэль объявлена: «{clan_a['name']}» vs «{clan_b['name']}»."


async def scheduler_loop(bot: Bot) -> None:
    """Фоновая задача: раз в ~6 часов (со случайным разбросом) объявляет новую дуэль."""
    while True:
        interval_hours = random.uniform(
            config.DUEL_INTERVAL_MIN_HOURS, config.DUEL_INTERVAL_MAX_HOURS
        )
        await asyncio.sleep(interval_hours * 3600)
        try:
            await announce_duel(bot)
        except Exception:
            # планировщик не должен падать из-за единичной ошибки
            pass
