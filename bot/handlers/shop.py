# -*- coding: utf-8 -*-
"""
Магазин привилегий за валюту Те.

Трактовка неочевидных пунктов ТЗ:
 - "вице" (ачивка «Это... Конец?») трактуется как покупка привилегии
   "проголосовать за исключение" (vote_kick) — это ближе всего по смыслу
   к «дать право вице-полномочий».
 - "проголосовать за исключение игрока/участника группы" реализовано как
   простое голосование прямо в чате (нужно 3 голоса «за» в течение 10 минут),
   а не полноценная система прав вице-президента.
 - "поменять название клана/группы" — запрашивает новое название следующим
   сообщением (FSM), устанавливает сразу после покупки.
"""

from aiogram import Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from bot.storage import Storage, now
from bot import players
from bot.clan_utils import ensure_clan_fields

router = Router(name="shop")

VOTE_KICK_THRESHOLD = 3
VOTE_KICK_WINDOW_SECONDS = 600


class ShopRename(StatesGroup):
    waiting_clan_name = State()
    waiting_group_name = State()


def _find_user_clan(db: dict, user_id: int):
    for clan in db["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return clan
    return None


@router.message(Command("shop"))
async def cmd_shop(message: Message) -> None:
    async with Storage() as db:
        player = players.get_or_create_player(
            db, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Игрок"
        )
        balance = player.get("currency", 0.0)

    builder = InlineKeyboardBuilder()
    lines = [f"🛒 <b>Магазин привилегий</b>\n💰 Ваш баланс: {balance:.2f} Те\n"]
    for key, (price, desc) in config.SHOP_ITEMS.items():
        lines.append(f"• {price} Те — {desc}")
        builder.button(text=f"{price} Те: {desc[:28]}...", callback_data=f"shop:buy:{key}")
    builder.adjust(1)

    await message.reply("\n".join(lines), parse_mode="HTML", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_shop_buy(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":")[2]
    item = config.SHOP_ITEMS.get(key)
    if not item:
        await callback.answer("Такой привилегии не существует.", show_alert=True)
        return
    price, desc = item

    async with Storage() as db:
        player = players.get_or_create_player(
            db, callback.from_user.id, callback.from_user.username or "", callback.from_user.first_name or "Игрок"
        )
        if player.get("currency", 0.0) < price:
            await callback.answer(
                f"Не хватает Те (нужно {price}, у вас {player.get('currency', 0):.2f}).", show_alert=True
            )
            return

        # для покупок, требующих активной дуэли, проверяем ДО списания валюты
        duel, side_key = None, None
        if key == "forfeit_duel_bonus":
            for d in db["active_duels"].values():
                for sk in ("a", "b"):
                    if d["sides"][sk]["player_id"] == callback.from_user.id and d["sides"][sk]["stage"] in (
                        "choose_mines", "playing"
                    ):
                        duel, side_key = d, sk
            if not duel:
                await callback.answer("У вас сейчас нет активной дуэли, которую можно завершить.", show_alert=True)
                return

        player["currency"] = round(player["currency"] - price, 2)
        player["purchases_count"] = player.get("purchases_count", 0) + 1
        new_achievements = []
        if player["purchases_count"] >= 2:
            if players.unlock_achievement(player, "pokupatel"):
                new_achievements.append("pokupatel")
        if key == "vote_kick" and players.unlock_achievement(player, "eto_konec"):
            new_achievements.append("eto_konec")

        effect_text = ""
        if key == "avoid_punishment":
            player["shop"]["avoid_punishment"] = player["shop"].get("avoid_punishment", 0) + 1
            effect_text = "Готово — сработает автоматически при следующем подрыве на мине."
        elif key == "next_win_boost":
            player["shop"]["next_win_boost"] = True
            effect_text = "Готово — ваша следующая победа принесёт x1.5."
        elif key == "next_loss_forgiven":
            player["shop"]["next_loss_forgiven"] = True
            effect_text = "Готово — следующее поражение не будет засчитано."
        elif key == "vote_kick":
            player.setdefault("shop", {})["vote_kick_tokens"] = player["shop"].get("vote_kick_tokens", 0) + 1
            effect_text = "Готово — используйте /votekick ответом на сообщение нужного участника."
        elif key == "forfeit_duel_bonus":
            duel["sides"][side_key]["stage"] = "done"
            duel["sides"][side_key]["result"] = "win"
            clan = db["clans"].get(str(duel["sides"][side_key]["clan_id"]))
            if clan:
                ensure_clan_fields(clan)
                clan["points"] = round(clan.get("points", 0) * 1.1, 2)
            effect_text = "Готово — ваша дуэль завершена, очки клана x1.1."
        elif key in ("rename_clan", "rename_group"):
            effect_text = "Напишите новое название следующим сообщением."

    if key == "rename_clan":
        await state.set_state(ShopRename.waiting_clan_name)
    elif key == "rename_group":
        await state.set_state(ShopRename.waiting_group_name)

    await callback.message.edit_text(f"✅ Куплено: {desc}\n{effect_text}", parse_mode="HTML")
    await callback.answer()

    if new_achievements:
        from bot import texts
        try:
            await callback.message.answer(texts.new_achievements_text(new_achievements), parse_mode="HTML")
        except Exception:
            pass


@router.message(StateFilter(ShopRename.waiting_clan_name))
async def process_rename_clan(message: Message, state: FSMContext) -> None:
    new_name = (message.text or "").strip()
    if not new_name or len(new_name) > 40:
        await message.reply("Название должно быть от 1 до 40 символов. Попробуйте ещё раз:")
        return
    async with Storage() as db:
        clan = _find_user_clan(db, message.from_user.id)
        if not clan:
            await state.clear()
            await message.reply("Вы больше не состоите в клане.")
            return
        old_name = clan["name"]
        clan["name"] = new_name
    await state.clear()
    await message.reply(f"✅ Клан «{old_name}» переименован в «{new_name}».")


@router.message(StateFilter(ShopRename.waiting_group_name))
async def process_rename_group(message: Message, state: FSMContext, bot: Bot) -> None:
    new_name = (message.text or "").strip()
    if not new_name or len(new_name) > 128:
        await message.reply("Название должно быть от 1 до 128 символов. Попробуйте ещё раз:")
        return
    await state.clear()
    try:
        await bot.set_chat_title(message.chat.id, new_name)
        await message.reply(f"✅ Группа переименована в «{new_name}».")
    except Exception:
        await message.reply(
            "⚠️ Не получилось переименовать группу — убедитесь, что бот является "
            "администратором с правом менять информацию о группе."
        )


# ------------------------------------------------------------- /votekick ---

@router.message(Command("votekick"))
async def cmd_votekick(message: Message) -> None:
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Ответьте этой командой на сообщение участника, которого хотите исключить из группы.")
        return

    target = message.reply_to_message.from_user
    async with Storage() as db:
        player = players.find_player(db, message.from_user.id)
        tokens = player.get("shop", {}).get("vote_kick_tokens", 0) if player else 0
        if tokens <= 0:
            await message.reply("У вас нет купленного голоса за исключение (см. /shop).")
            return
        player["shop"]["vote_kick_tokens"] -= 1

        vote_id = str(int(now() * 1000))
        db.setdefault("active_votes", {})[vote_id] = {
            "target_id": target.id,
            "target_name": target.username or target.first_name,
            "chat_id": message.chat.id,
            "voters": [message.from_user.id],
            "created_at": now(),
        }

    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Голосовать за исключение (1/{VOTE_KICK_THRESHOLD})", callback_data=f"votekick:{vote_id}")
    await message.reply(
        f"🗳 Голосование: исключить @{target.username or target.first_name} из группы?\n"
        f"Нужно {VOTE_KICK_THRESHOLD} голосов «за» в течение {VOTE_KICK_WINDOW_SECONDS // 60} минут.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("votekick:"))
async def cb_votekick(callback: CallbackQuery, bot: Bot) -> None:
    vote_id = callback.data.split(":")[1]

    kick_target = None
    count = 0
    async with Storage() as db:
        vote = db.get("active_votes", {}).get(vote_id)
        if not vote:
            await callback.answer("Голосование уже завершено.", show_alert=True)
            return
        if now() - vote["created_at"] > VOTE_KICK_WINDOW_SECONDS:
            del db["active_votes"][vote_id]
            await callback.answer("Время голосования истекло.", show_alert=True)
            return
        if callback.from_user.id in vote["voters"]:
            await callback.answer("Вы уже проголосовали.")
            return
        vote["voters"].append(callback.from_user.id)
        count = len(vote["voters"])

        if count >= VOTE_KICK_THRESHOLD:
            kick_target = (vote["chat_id"], vote["target_id"], vote["target_name"])
            del db["active_votes"][vote_id]

    if kick_target:
        chat_id, target_id, target_name = kick_target
        try:
            await bot.ban_chat_member(chat_id, target_id)
            await bot.unban_chat_member(chat_id, target_id)  # кик, а не бан навсегда
            await callback.message.edit_text(f"👢 @{target_name} исключён(а) из группы по голосованию.")
        except Exception:
            await callback.message.edit_text(
                "⚠️ Не удалось исключить участника — проверьте, что бот администратор с правом банить."
            )
    else:
        await callback.answer(f"Голос учтён ({count}/{VOTE_KICK_THRESHOLD}).")
        try:
            builder = InlineKeyboardBuilder()
            builder.button(
                text=f"✅ Голосовать за исключение ({count}/{VOTE_KICK_THRESHOLD})",
                callback_data=f"votekick:{vote_id}",
            )
            await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
        except Exception:
            pass
