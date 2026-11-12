# -*- coding: utf-8 -*-
"""
Закрепление и открепление сообщений бота (вызов на дуэль, меню выбора мин).

ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ МОДУЛЬ. Раньше каждое место, где дуэль заканчивалась, само
хранило «какое сообщение открепить» и глотало любые ошибки. Стоило хоть одному пути
(конец сезона, роспуск клана, исключение из дуэли, сбой в фоновой задаче) пропустить
открепление — сообщение висело закреплённым навсегда, потому что его номер нигде
больше не хранился.

КАК РАБОТАЕТ СЕЙЧАС
 - Каждое закрепление бота записывается в chat["bot_pins"] (список id сообщений).
 - «Живые» закрепы — те, что должны висеть прямо сейчас: сообщение текущего вызова
   (chat["pending_invite"]) и меню выбора мин идущих дуэлей (duel["pinned_message_id"]).
 - sweep() откpепляет ВСЁ, что бот закреплял, но чего нет среди живых. Его вызывают:
     * сразу после завершения дуэли (оба сыграли / не пришёл и дуэль закрыта);
     * при старте дуэли (вызов → меню выбора мин) и перед закреплением нового вызова
       (так «не пришёл кто-то, началась новая дуэль» тоже всё чистит);
     * при конце сезона и роспуске клана (там состояние дуэлей сбрасывается);
     * фоновой задачей раз в несколько минут — на случай любого пропуска или сбоя сети.
 - Ошибка открепления не теряется: id остаётся в списке и открепляется при
   следующей зачистке. Исключение — «сообщение уже удалено / не закреплено»: тогда
   записи просто нечего открепять.
 - Разовая «зачистка наследия»: закрепы, повисшие со старых версий бота (их id не
   записаны), снимаются с вершины списка закреплённых — только сообщения самого бота,
   похожие на дуэльные, и только когда в группе нет живой дуэли.

Для работы боту нужны права администратора с правом закреплять сообщения.
"""

import logging
from typing import List, Set

try:
    from aiogram.exceptions import TelegramBadRequest
except ImportError:  # лёгкие заглушки aiogram в тестах
    class TelegramBadRequest(Exception):
        pass

from bot.chat_state import all_chat_ids, get_chat
from bot.storage import Storage

logger = logging.getLogger("pins")

_MAX_TRACKED = 50        # сколько последних закрепов помним на группу (защита от разрастания)
_LEGACY_MAX_STEPS = 10   # сколько старых закрепов максимум снимаем за одну зачистку наследия


# ------------------------------------------------------------ состояние ------

def live_pins(chat: dict) -> Set[int]:
    """id сообщений, которые должны оставаться закреплёнными прямо сейчас."""
    live: Set[int] = set()
    invite = chat.get("pending_invite")
    if invite and invite.get("message_id"):
        live.add(invite["message_id"])
    for duel in chat.get("active_duels", {}).values():
        if duel.get("pinned_message_id"):
            live.add(duel["pinned_message_id"])
    return live


def register(chat: dict, message_id: int) -> None:
    """Запоминает, что бот закрепил это сообщение. Вызывать внутри `async with Storage()`
    — в ТОМ ЖЕ блоке, где сообщение становится «живым» (так фоновая зачистка не успеет
    открепить только что закреплённое)."""
    pins: List[int] = chat.setdefault("bot_pins", [])
    if message_id not in pins:
        pins.append(message_id)
    del pins[:-_MAX_TRACKED]


async def try_pin(bot, chat_id: int, message_id: int) -> bool:
    """Закрепляет сообщение без уведомления. True — получилось (тогда вызывающий должен
    сразу вызвать register() в своём блоке Storage)."""
    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=message_id, disable_notification=True)
        return True
    except Exception as e:  # бот не админ / нет права закреплять — не критично
        logger.warning("Не удалось закрепить сообщение %s в %s: %s", message_id, chat_id, e)
        return False


async def _unpin(bot, chat_id: int, message_id: int) -> bool:
    """True — сообщение больше не закреплено (открепили, либо оно уже удалено/не закреплено).
    False — временный сбой (сеть, нет прав): попробуем позже."""
    try:
        await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramBadRequest as e:
        low = str(e).lower()
        if "rights" in low or "admin" in low:
            logger.warning("Нет прав открепить сообщение %s в %s: %s", message_id, chat_id, e)
            return False
        return True  # «message to unpin not found» и т.п.: откреплять нечего
    except Exception as e:
        logger.warning("Не удалось открепить сообщение %s в %s: %s", message_id, chat_id, e)
        return False


# ------------------------------------------------------------- зачистка ------

def _looks_like_duel_message(text: str) -> bool:
    """Старое дуэльное сообщение бота: вызов («⚔️ Дуэль чести!»), меню выбора мин
    («⚔️ «А» vs «Б»») или раскрытые правила. «Настоящую дуэль» бот не закрепляет."""
    text = (text or "").lstrip()
    if "НАСТОЯЩАЯ ДУЭЛЬ" in text[:60]:
        return False
    return text.startswith("⚔") or "Правила дуэли" in text[:60]


async def _sweep_legacy(bot, chat_id: int, live: Set[int]) -> int:
    """Снимает с вершины списка закреплённых старые дуэльные сообщения самого бота
    (id которых не записаны, потому что они закреплены старой версией). Останавливается
    на первом чужом/не дуэльном/живом закрепе."""
    try:
        me = await bot.me()
    except Exception:
        return 0
    removed, last = 0, None
    for _ in range(_LEGACY_MAX_STEPS):
        try:
            info = await bot.get_chat(chat_id)
        except Exception:
            break
        pinned = getattr(info, "pinned_message", None)
        author = getattr(pinned, "from_user", None) if pinned else None
        if not pinned or not author or author.id != me.id:
            break
        mid = pinned.message_id
        if mid in live or mid == last:
            break
        if not _looks_like_duel_message(getattr(pinned, "text", None) or getattr(pinned, "caption", None) or ""):
            break
        if not await _unpin(bot, chat_id, mid):
            break
        removed += 1
        last = mid
    return removed


async def sweep(bot, chat_id: int, legacy: bool = False) -> int:
    """Открепляет все закрепы бота в группе, которых нет среди живых. Возвращает, сколько
    снято. legacy=True — дополнительно зачищает закрепы старых версий (см. модуль)."""
    async with Storage() as db:
        chat = get_chat(db, chat_id)
        live = live_pins(chat)
        stale = [m for m in chat.get("bot_pins", []) if m not in live]

    done = []
    for mid in stale:
        if await _unpin(bot, chat_id, mid):
            done.append(mid)
    if done:
        async with Storage() as db:
            chat = get_chat(db, chat_id)
            chat["bot_pins"] = [m for m in chat.get("bot_pins", []) if m not in done]

    legacy_removed = await _sweep_legacy(bot, chat_id, live) if legacy else 0
    return len(done) + legacy_removed


async def sweep_all(bot, legacy: bool = False) -> None:
    """Зачистка по всем группам: фоновая страховка. legacy=True (при старте бота) — ещё и
    наследие старых версий, но только в группах, где сейчас нет живой дуэли."""
    todo = []
    async with Storage() as db:
        for chat_id in all_chat_ids(db):
            chat = get_chat(db, chat_id)
            idle = not live_pins(chat)
            if chat.get("bot_pins") or (legacy and idle):
                todo.append((chat_id, legacy and idle))
    for chat_id, leg in todo:
        try:
            await sweep(bot, chat_id, legacy=leg)
        except Exception:
            logger.exception("Ошибка зачистки закрепов в %s", chat_id)
