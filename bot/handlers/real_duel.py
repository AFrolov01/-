# -*- coding: utf-8 -*-
"""
НАСТОЯЩАЯ ДУЭЛЬ — сравнение статистики двух игроков.

Как это работает
 - Раз в config.REAL_DUEL_INTERVAL_HOURS (48 ч) в каждой группе объявляется
   настоящая дуэль: два клана, по одному игроку от каждого.
 - Настоящая дуэль НЕ отменяет и не заменяет обычную (мины): они идут параллельно,
   у каждой свой слот (chat["real_duel"] против chat["pending_invite"] /
   chat["active_duels"]).
 - Кого звать от клана:
     * в клане 1 человек -> он играет и там и там;
     * в клане 2+ человека -> тот, у кого НЕТ активной обычной дуэли, и (правило
       «чтобы не было каши») с последнего его вызова на обычную дуэль прошло
       не меньше config.REAL_DUEL_GAP_AFTER_REGULAR_HOURS (12 ч). Если настоящая
       дуэль просрочена на config.REAL_DUEL_RELAX_AFTER_HOURS и всё ещё некого
       звать — правило 12 часов ослабляется (остаётся «нет активной обычной
       дуэли»), иначе дуэль могла бы не состояться никогда.
 - Достаточно, чтобы ОДИН из двух нажал «⚔️ Сравнить» — сразу показывается
   таблица из 7 критериев, подсчёт очков и перевод очков между кланами.
 - Если никто не нажал за config.REAL_DUEL_TIMEOUT_HOURS — дуэль отменяется
   без штрафов.

Критерии (за каждый выигранный критерий игрок получает 1 балл, при равенстве
баллов нет):
 1. Доля игрока в выигранных очках клана (% от суммы выигрышей всех участников
    клана за всё время)
 2. Текущая серия побед (раундов подряд)
 3. Максимальный множитель (x)
 4. Коэффициент побед в дуэлях (выиграно / сыграно)
 5. Активность в дуэлях (пришёл / был вызван)
 6. Активность в чате (сообщений с момента последней настоящей дуэли)
 7. Случайное число от 1 до 10

Итог: разница баллов -> процент очков проигравшего клана, который уходит
победителю (config.REAL_DUEL_TRANSFER_TABLE).
"""

import asyncio
import html
import logging
import random
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from bot import players
from bot.chat_state import all_chat_ids, get_chat
from bot.clan_utils import ensure_clan_fields
from bot.matchmaking import is_in_regular_duel
from bot.storage import Storage, now
from bot.texts import fmt_num

router = Router(name="real_duel")
logger = logging.getLogger("real_duel")


# ============================================================ счётчик чата ==

_msg_buffer: Dict[Tuple[int, int], int] = defaultdict(int)


class ChatActivityMiddleware(BaseMiddleware):
    """Считает сообщения в группах (в памяти) и НИКОГО не блокирует — просто
    пропускает дальше. Раз в цикл планировщика буфер переносится в хранилище."""

    async def __call__(self, handler, event, data):
        try:
            if (
                event.chat.type in ("group", "supergroup")
                and event.from_user
                and not event.from_user.is_bot
            ):
                _msg_buffer[(event.chat.id, event.from_user.id)] += 1
        except Exception:
            pass
        return await handler(event, data)


async def flush_message_counters() -> None:
    if not _msg_buffer:
        return
    snapshot = dict(_msg_buffer)
    _msg_buffer.clear()
    async with Storage() as db:
        for (chat_id, uid), cnt in snapshot.items():
            if str(chat_id) not in db.get("chats", {}):
                continue  # не заводим группу только из-за того, что в ней пишут
            chat = get_chat(db, chat_id)
            member = None
            for clan in chat.get("clans", {}).values():
                m = clan.get("members", {}).get(str(uid))
                if m:
                    member = m
                    break
            if not member:
                continue  # считаем только участников кланов
            pl = players.get_or_create_player(chat, uid, member.get("username", ""), member.get("first_name", "Игрок"))
            pl["chat_msgs"] = pl.get("chat_msgs", 0) + cnt


# ================================================================= helpers ==

def _member_name(member: dict) -> str:
    return f'@{member["username"]}' if member.get("username") else member.get("first_name", "Игрок")


def _mention(member: dict) -> str:
    return f'<a href="tg://user?id={member["user_id"]}">{html.escape(_member_name(member))}</a>'


def _real_duel_kb(rid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔️ Сравнить", callback_data=f"real:cmp:{rid}")
    ]])


def _transfer_percent(diff: int) -> int:
    for threshold, pct in config.REAL_DUEL_TRANSFER_TABLE:
        if diff >= threshold:
            return pct
    return 0


def _pick_real_player(chat: dict, clan: dict, relax: bool) -> Optional[dict]:
    members = list(clan.get("members", {}).values())
    if not members:
        return None
    if len(members) == 1:
        return members[0]  # одиночка играет и в обычной, и в настоящей

    free = [m for m in members if not is_in_regular_duel(chat, int(m["user_id"]))]
    if not relax:
        cutoff = now() - config.REAL_DUEL_GAP_AFTER_REGULAR_HOURS * 3600
        free = [m for m in free if m.get("last_regular_invite_at", 0) <= cutoff]
    if not free:
        return None
    free.sort(key=lambda m: m.get("last_real_duel_at", 0))
    return free[0]


# ================================================================ announce ==

async def announce_real_duel(bot: Bot, chat_id: int, relax: bool = False) -> Tuple[bool, str]:
    async with Storage() as db:
        chat = get_chat(db, chat_id)
        if chat.get("real_duel"):
            return False, "Настоящая дуэль уже объявлена — ждём, пока кто-то нажмёт «Сравнить»."

        clans = [c for c in chat["clans"].values() if c.get("members")]
        for c in clans:
            ensure_clan_fields(c)
        if len(clans) < 2:
            return False, "Для настоящей дуэли нужно минимум 2 клана с участниками."

        # кто дольше всех не участвовал в настоящих дуэлях — тот и первый
        clans.sort(key=lambda c: (c.get("last_real_duel_at", 0), c.get("points", 0)))
        clan_a, clan_b = clans[0], clans[1]

        member_a = _pick_real_player(chat, clan_a, relax)
        member_b = _pick_real_player(chat, clan_b, relax)
        if not member_a or not member_b:
            return False, "Пока некого звать: у игроков кланов недавно или прямо сейчас идёт обычная дуэль."

        rd = {
            "id": int(now()),
            "clan_a_id": clan_a["id"], "player_a_id": int(member_a["user_id"]),
            "clan_b_id": clan_b["id"], "player_b_id": int(member_b["user_id"]),
            "created_at": now(), "chat_id": chat_id, "message_id": None,
        }
        chat["real_duel"] = rd

        for clan, member in ((clan_a, member_a), (clan_b, member_b)):
            pl = players.get_or_create_player(
                chat, int(member["user_id"]), member.get("username", ""), member.get("first_name", "Игрок")
            )
            pl["duels_invited"] = pl.get("duels_invited", 0) + 1
            member["last_real_duel_at"] = now()
            clan["last_real_duel_at"] = now()

        text = (
            "⚔️ <b>НАСТОЯЩАЯ ДУЭЛЬ!</b> ⚔️\n\n"
            f"🛡 {_mention(member_a)} — клан «{html.escape(clan_a['name'])}»\n"
            "🗡 против\n"
            f"🛡 {_mention(member_b)} — клан «{html.escape(clan_b['name'])}»\n\n"
            "Достаточно, чтобы один из вас нажал «Сравнить»: бот сравнит статистику "
            "обоих по 7 критериям, а клан проигравшего отдаст часть очков.\n"
            "Обычные дуэли идут как обычно — настоящая им не мешает."
        )
        rid = rd["id"]

    try:
        sent = await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=_real_duel_kb(rid))
    except Exception as e:
        async with Storage() as db:
            chat = get_chat(db, chat_id)
            chat["real_duel"] = None
        return False, f"Не удалось отправить сообщение в группу: {e}"

    async with Storage() as db:
        chat = get_chat(db, chat_id)
        if chat.get("real_duel") and chat["real_duel"]["id"] == rid:
            chat["real_duel"]["message_id"] = sent.message_id

    return True, "Настоящая дуэль объявлена."


# ================================================================ сравнение ==

def _fmt_pct(v: float) -> str:
    return (f"{v:.1f}".rstrip("0").rstrip(".") or "0") + "%"


def _short(name: str, n: int = 6) -> str:
    name = name.lstrip("@")
    return name if len(name) <= n else name[: n - 1] + "…"


def _resolve(chat: dict, rd: dict) -> Optional[str]:
    """Считает сравнение, переводит очки, обновляет статистику. Возвращает текст
    результата (HTML) или None, если один из кланов уже не существует."""
    clan_a = chat["clans"].get(str(rd["clan_a_id"]))
    clan_b = chat["clans"].get(str(rd["clan_b_id"]))
    if not clan_a or not clan_b:
        return None
    ensure_clan_fields(clan_a)
    ensure_clan_fields(clan_b)

    member_a = clan_a.get("members", {}).get(str(rd["player_a_id"]), {"user_id": rd["player_a_id"]})
    member_b = clan_b.get("members", {}).get(str(rd["player_b_id"]), {"user_id": rd["player_b_id"]})
    pa = players.get_or_create_player(chat, rd["player_a_id"], member_a.get("username", ""), member_a.get("first_name", "Игрок"))
    pb = players.get_or_create_player(chat, rd["player_b_id"], member_b.get("username", ""), member_b.get("first_name", "Игрок"))

    def share(clan: dict, pl: dict) -> float:
        total = sum(
            chat.get("players", {}).get(uid, {}).get("points_won_total", 0.0)
            for uid in clan.get("members", {})
        )
        return round(pl.get("points_won_total", 0.0) / total * 100, 1) if total > 0 else 0.0

    def duel_wr(pl: dict) -> float:
        played = pl.get("duels_played", 0)
        return round(pl.get("duels_won", 0) / played * 100, 1) if played else 0.0

    def activity(pl: dict) -> Optional[float]:
        invited = pl.get("duels_invited", 0)
        return round(min(pl.get("duels_attended", 0), invited) / invited * 100, 1) if invited else None

    rand_a, rand_b = random.randint(1, 10), random.randint(1, 10)
    act_a, act_b = activity(pa), activity(pb)

    # (подпись, значение A, значение B, текст A, текст B)
    criteria: List[tuple] = [
        ("Очки клана %", share(clan_a, pa), share(clan_b, pb), None, None),
        ("Серия побед", pa.get("win_streak", 0), pb.get("win_streak", 0), None, None),
        ("Макс. икс", pa.get("best_multiplier", 0.0), pb.get("best_multiplier", 0.0),
         f"x{pa.get('best_multiplier', 0.0):.2f}", f"x{pb.get('best_multiplier', 0.0):.2f}"),
        ("Побед в дуэл.", duel_wr(pa), duel_wr(pb), None, None),
        ("Актив. дуэли", act_a or 0.0, act_b or 0.0,
         "—" if act_a is None else None, "—" if act_b is None else None),
        ("Актив. чат", pa.get("chat_msgs", 0), pb.get("chat_msgs", 0), None, None),
        ("Рандом 1-10", rand_a, rand_b, None, None),
    ]
    pct_labels = {"Очки клана %", "Побед в дуэл.", "Актив. дуэли"}

    score_a = score_b = 0
    lines = []
    name_a, name_b = _short(_member_name(member_a)), _short(_member_name(member_b))
    lines.append(f"{'':<14}{name_a:>6} {'':^5} {name_b:>6}")
    for label, va, vb, ta, tb in criteria:
        if va > vb:
            score_a += 1
        elif vb > va:
            score_b += 1
        if ta is None:
            ta = _fmt_pct(va) if label in pct_labels else str(int(va)) if float(va).is_integer() else str(va)
        if tb is None:
            tb = _fmt_pct(vb) if label in pct_labels else str(int(vb)) if float(vb).is_integer() else str(vb)
        sc = f"{score_a}:{score_b}"
        lines.append(f"{label:<14}{ta:>6} {sc:^5} {tb:>6}")
    table = "<pre>" + html.escape("\n".join(lines)) + "</pre>"

    header = (
        "📊 <b>Сравнение настоящей дуэли</b>\n"
        f"🛡 {html.escape(_member_name(member_a))} («{html.escape(clan_a['name'])}») vs "
        f"{html.escape(_member_name(member_b))} («{html.escape(clan_b['name'])}»)\n\n"
    )

    diff = abs(score_a - score_b)
    if diff == 0:
        verdict = f"\n🤝 <b>Итог: {score_a}:{score_b} — ничья.</b> Очки между кланами не переходят."
        winner_pl = loser_pl = None
    else:
        if score_a > score_b:
            win_clan, lose_clan, winner_pl, loser_pl = clan_a, clan_b, pa, pb
            ws, ls = score_a, score_b
        else:
            win_clan, lose_clan, winner_pl, loser_pl = clan_b, clan_a, pb, pa
            ws, ls = score_b, score_a
        pct = _transfer_percent(diff)
        loser_points = max(lose_clan.get("points", 0), 0)
        amount = round(loser_points * pct / 100, 2)
        lose_clan["points"] = round(lose_clan.get("points", 0) - amount, 2)
        win_clan["points"] = round(win_clan.get("points", 0) + amount, 2)
        verdict = (
            f"\n🏆 <b>Итог: {ws}:{ls}</b> — побеждает клан «{html.escape(win_clan['name'])}»!\n"
            f"Разница {ws}−{ls} = {diff} → {pct}% очков клана «{html.escape(lose_clan['name'])}» "
            f"переходит победителям.\n"
            f"💠 «{html.escape(lose_clan['name'])}»: −{fmt_num(amount)} → {fmt_num(lose_clan['points'])}\n"
            f"💠 «{html.escape(win_clan['name'])}»: +{fmt_num(amount)} → {fmt_num(win_clan['points'])}"
        )

    # статистика игроков + сброс счётчика чата «с момента последней настоящей дуэли»
    players.record_duel_result(pa, won=(winner_pl is pa))
    players.record_duel_result(pb, won=(winner_pl is pb))
    for pl in chat.get("players", {}).values():
        pl["chat_msgs"] = 0
    chat["last_real_duel_at"] = now()

    return header + table + verdict


@router.callback_query(F.data.startswith("real:cmp:"))
async def cb_compare(callback: CallbackQuery, bot: Bot) -> None:
    if callback.message.chat.type not in ("group", "supergroup"):
        await callback.answer()
        return
    try:
        rid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id

    await flush_message_counters()  # чтобы «активность в чате» была актуальной

    text: Optional[str] = None
    message_id: Optional[int] = None
    async with Storage() as db:
        chat = get_chat(db, chat_id)
        rd = chat.get("real_duel")
        if not rd or rd["id"] != rid:
            await callback.answer("Эта настоящая дуэль уже завершена.", show_alert=True)
            return
        if user_id not in (rd["player_a_id"], rd["player_b_id"]):
            await callback.answer("Это не ваша дуэль — сравнить могут только вызванные игроки.", show_alert=True)
            return

        # тот, кто нажал, «пришёл»
        side_clan_id = rd["clan_a_id"] if user_id == rd["player_a_id"] else rd["clan_b_id"]
        clan = chat["clans"].get(str(side_clan_id))
        member = (clan or {}).get("members", {}).get(str(user_id), {})
        presser = players.get_or_create_player(chat, user_id, member.get("username", ""), member.get("first_name", "Игрок"))
        presser["duels_attended"] = presser.get("duels_attended", 0) + 1

        message_id = rd.get("message_id")
        text = _resolve(chat, rd)
        chat["real_duel"] = None

    await callback.answer()
    if message_id:
        try:
            await bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        except Exception:
            pass
    if text is None:
        text = "⚠️ Настоящая дуэль отменена: один из кланов больше не существует."
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception:
        logger.exception("Не удалось отправить итог настоящей дуэли")


# ====================================================== админ: вручную ======

@router.message(Command("realduel"))
async def cmd_realduel(message: Message, bot: Bot) -> None:
    """Секретная команда владельца бота: объявить настоящую дуэль в этой группе
    прямо сейчас (для проверки). Не в меню команд; остальным — молчание."""
    if message.from_user.id != config.ADMIN_ID:
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Команду нужно вызвать в группе.")
        return
    ok, reason = await announce_real_duel(bot, message.chat.id, relax=True)
    if ok:
        async with Storage() as db:
            chat = get_chat(db, message.chat.id)
            chat["next_real_duel_due_at"] = now() + config.REAL_DUEL_INTERVAL_HOURS * 3600
    else:
        await message.reply(reason)


# ================================================================ планировщик

async def _expire_real_duels(bot: Bot) -> None:
    expired = []  # (chat_id, message_id)
    async with Storage() as db:
        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            rd = chat.get("real_duel")
            if not rd:
                continue
            if now() - rd.get("created_at", now()) < config.REAL_DUEL_TIMEOUT_HOURS * 3600:
                continue
            chat["real_duel"] = None
            expired.append((chat_id, rd.get("message_id")))

    for chat_id, message_id in expired:
        if message_id:
            try:
                await bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            except Exception:
                pass
        try:
            await bot.send_message(
                chat_id,
                "⌛ Настоящая дуэль не состоялась — никто из вызванных не нажал «Сравнить». "
                "Следующая будет по расписанию.",
            )
        except Exception:
            pass


async def _check_schedule(bot: Bot) -> None:
    due = []  # (chat_id, relax)
    async with Storage() as db:
        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            clans = [c for c in chat["clans"].values() if c.get("members")]
            if len(clans) < 2 or chat.get("real_duel"):
                continue
            due_at = chat.get("next_real_duel_due_at")
            if due_at is None:
                chat["next_real_duel_due_at"] = now() + config.REAL_DUEL_INTERVAL_HOURS * 3600
                continue
            if now() >= due_at:
                relax = now() - due_at >= config.REAL_DUEL_RELAX_AFTER_HOURS * 3600
                due.append((chat_id, relax))

    for chat_id, relax in due:
        ok, reason = await announce_real_duel(bot, chat_id, relax=relax)
        if ok:
            async with Storage() as db:
                chat = get_chat(db, chat_id)
                chat["next_real_duel_due_at"] = now() + config.REAL_DUEL_INTERVAL_HOURS * 3600
        else:
            logger.info("Настоящая дуэль в %s пока не объявлена: %s", chat_id, reason)


async def real_duel_scheduler_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(config.SCHEDULER_CHECK_INTERVAL_SECONDS)
        try:
            await flush_message_counters()
            await _expire_real_duels(bot)
            await _check_schedule(bot)
        except Exception:
            logger.exception("Ошибка планировщика настоящих дуэлей")
