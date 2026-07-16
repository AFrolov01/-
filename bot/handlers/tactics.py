# -*- coding: utf-8 -*-
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from bot.storage import Storage
from bot.chat_state import get_chat, resolve_chat_for_message
from bot.clan_utils import ensure_clan_fields

router = Router(name="tactics")

TACTIC_DESCRIPTIONS = {
    "quiet": (
        "🐢 <b>Тихо не спеша мальчик ты куда</b>\n"
        "Раз в 7 дней сезона, когда очки кланов могут получить бафф или дебафф, "
        "для вашего клана эффект мягче: если вы в топе — теряете всего −6% ко "
        "всем выигранным очкам в минах, если в аутсайдерах — получаете всего "
        "+6% к выигранным очкам в минах."
    ),
    "red": (
        "🩸 <b>Мы уже красные</b>\n"
        "Обычный штраф за подрыв на мине x0.75. С этой тактикой за каждое "
        "поражение ПОДРЯД штраф смягчается на 0.05 (то есть теряете меньше), "
        "максимум до x0.95. После победы счётчик обнуляется."
    ),
    "streak": (
        "💡 <b>Да да нет нет да будет свет</b>\n"
        "Каждая победа подряд даёт +10% к очкам за раунд (максимум +50%), "
        "поражение обнуляет серию.\nНо при поражении штраф жёстче: x0.70 "
        "(первое подряд), затем x0.80 (второе и далее подряд), пока не выиграете."
    ),
    "hamster": (
        "🐹 <b>Хамстер комбат 👆</b>\n"
        "Вся валюта Те за раунд удваивается. Дополнительно множитель очков "
        "растёт на (ваш баланс Те × 0.0001), максимум +15%."
    ),
    "gamble": (
        "🎲 <b>Азарт</b>\n"
        "Перед каждым раундом выпадает случайный эффект (виден и его описание "
        "прямо в игре): 🍀 Фортуна (+20% очков), 🛡 Броня (штраф x0.85), "
        "💰 Золотая жила (Те x3), 💣 Безумие (+20% очков, но штраф x0.65), "
        "☠ Неудача (−20% очков)."
    ),
    "barrel": (
        "🌀 <b>Вращайте барабан</b>\n"
        "На поле появляется клетка-портал. Клик по ней полностью сбрасывает "
        "текущий раунд (множитель сгорает), поле создаётся заново с тем же "
        "числом мин, а ВСЕ множители умножаются на x1.5 (накопительно: "
        "1.5 → 2.25 → 3.375 → ...). Переходов не ограничено, попытка при этом "
        "не тратится."
    ),
}


def _find_user_clan(chat: dict, user_id: int):
    for clan in chat["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return clan
    return None


@router.message(Command("tactic"))
async def cmd_tactic(message: Message) -> None:
    async with Storage() as db:
        chat_id, error = await resolve_chat_for_message(message, db)
        if error:
            await message.reply(error)
            return
        chat = get_chat(db, chat_id)
        clan = _find_user_clan(chat, message.from_user.id)
        if not clan:
            await message.reply("Вы не состоите ни в одном клане.")
            return
        if clan["creator_id"] != message.from_user.id:
            await message.reply("Тактику сезона выбирает только создатель клана.")
            return
        ensure_clan_fields(clan)
        current = clan.get("tactic")
        clan_id = clan["id"]

        if clan.get("tactic_locked"):
            name = config.SEASON_TACTICS.get(current, current)
            await message.reply(
                f"Тактика на этот сезон уже выбрана: <b>{name}</b>.\n"
                "Сменить её можно будет только в начале следующего сезона.",
                parse_mode="HTML",
            )
            return

    current_text = "\n\nТактика пока не выбрана."

    builder = InlineKeyboardBuilder()
    for key, name in config.SEASON_TACTICS.items():
        builder.button(text=name, callback_data=f"tactic:set:{chat_id}:{clan_id}:{key}")
    builder.adjust(1)

    descriptions = "\n\n".join(TACTIC_DESCRIPTIONS.values())
    await message.reply(
        f"⚔️ Выберите тактику клана на этот сезон:{current_text}\n\n{descriptions}",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("tactic:set:"))
async def cb_tactic_set(callback: CallbackQuery) -> None:
    _, _, chat_id_s, clan_id_s, key = callback.data.split(":")
    chat_id, clan_id = int(chat_id_s), int(clan_id_s)

    async with Storage() as db:
        chat = get_chat(db, chat_id)
        clan = chat["clans"].get(str(clan_id))
        if not clan:
            await callback.answer("Клан не найден.", show_alert=True)
            return
        if clan["creator_id"] != callback.from_user.id:
            await callback.answer("Только создатель клана может это менять.", show_alert=True)
            return
        ensure_clan_fields(clan)
        if clan.get("tactic_locked"):
            await callback.answer("Тактика на этот сезон уже выбрана и заблокирована.", show_alert=True)
            return
        clan["tactic"] = key
        clan["tactic_locked"] = True
        clan["consecutive_losses"] = 0
        clan["tactic_consecutive_wins"] = 0
        name = config.SEASON_TACTICS.get(key, key)

    await callback.message.edit_text(
        f"✅ Тактика клана на этот сезон: <b>{name}</b>\n(сменить можно будет только в следующем сезоне)",
        parse_mode="HTML",
    )
    await callback.answer()
