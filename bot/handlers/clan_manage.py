# -*- coding: utf-8 -*-
"""
Управление кланом и таблица лидеров.

Добавлено по просьбе владельца:
 - /deleteclan — создатель может расформировать свой клан
 - /kick        — создатель может исключить участника
 - /leaveclan   — рядовой участник может сам покинуть клан
 - /top         — топ кланов и топ игроков ПРЯМО СЕЙЧАС (текущее состояние войны)
 - /resetwar    — (только владелец бота) подводит итоги текущей войны (тот же
                  текст, что и /top) и начинает новый сезон: очки кланов
                  сбрасываются к стартовым, серии и счётчики побед обнуляются.

ВАЖНО: сезон войны длится config.SEASON_LENGTH_DAYS (30) дней и завершается
АВТОМАТИЧЕСКИ фоновой задачей (см. bot/season.py) — итоги публикуются в
боевой чат сами, вручную ничего нажимать не нужно. Команда /resetwar ниже
нужна лишь для ручного досрочного завершения (например, если нужно
перезапустить сезон раньше срока).
"""

from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from bot.storage import Storage, now
from bot.keyboards import confirm_kb
from bot.leaderboard import build_top_text
from bot.season import finalize_season_locked

router = Router(name="clan_manage")


def _find_user_clan(db: dict, user_id: int) -> Optional[dict]:
    for clan in db["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return clan
    return None


def _display_name(member: dict) -> str:
    return f'@{member["username"]}' if member.get("username") else member.get("first_name", "Игрок")


# ------------------------------------------------------------- /deleteclan -

@router.message(Command("deleteclan"))
async def cmd_delete_clan(message: Message) -> None:
    async with Storage() as db:
        clan = _find_user_clan(db, message.from_user.id)
        if not clan:
            await message.reply("Вы не состоите ни в одном клане.")
            return
        if clan["creator_id"] != message.from_user.id:
            await message.reply("Расформировать клан может только его создатель.")
            return
        clan_id = clan["id"]
        clan_name = clan["name"]

    await message.reply(
        f"⚠️ Вы уверены, что хотите расформировать клан «{clan_name}»?\n"
        "Это действие необратимо: клан и вся его статистика будут удалены, "
        "все участники освободятся и смогут вступить в другой клан.",
        reply_markup=confirm_kb(
            yes_cb=f"deleteclan:confirm:{clan_id}",
            no_cb="deleteclan:cancel",
        ),
    )


@router.callback_query(F.data.startswith("deleteclan:confirm:"))
async def cb_delete_clan_confirm(callback: CallbackQuery) -> None:
    clan_id = int(callback.data.split(":")[2])
    async with Storage() as db:
        clan = db["clans"].get(str(clan_id))
        if not clan:
            await callback.answer("Клан уже удалён.", show_alert=True)
            await callback.message.edit_text("Клан уже был удалён ранее.")
            return
        if clan["creator_id"] != callback.from_user.id:
            await callback.answer("Только создатель может это подтвердить.", show_alert=True)
            return

        clan_name = clan["name"]
        del db["clans"][str(clan_id)]

        # подчищаем связанные состояния, чтобы не остались "битые" ссылки
        invite = db.get("pending_invite")
        if invite and (invite["clan_a_id"] == clan_id or invite["clan_b_id"] == clan_id):
            db["pending_invite"] = None
        for duel_id in list(db["active_duels"].keys()):
            duel = db["active_duels"][duel_id]
            if duel["sides"]["a"]["clan_id"] == clan_id or duel["sides"]["b"]["clan_id"] == clan_id:
                del db["active_duels"][duel_id]

    await callback.message.edit_text(f"🗑 Клан «{clan_name}» расформирован.")
    await callback.answer()


@router.callback_query(F.data == "deleteclan:cancel")
async def cb_delete_clan_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено — клан не тронут.")
    await callback.answer()


# ------------------------------------------------------------------ /kick --

@router.message(Command("kick"))
async def cmd_kick(message: Message) -> None:
    async with Storage() as db:
        clan = _find_user_clan(db, message.from_user.id)
        if not clan:
            await message.reply("Вы не состоите ни в одном клане.")
            return
        if clan["creator_id"] != message.from_user.id:
            await message.reply("Исключать участников может только создатель клана.")
            return

        # /kick ответом на сообщение участника — кикаем сразу его
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
            target = clan["members"].get(str(target_id))
            if not target:
                await message.reply("Этот пользователь не состоит в вашем клане.")
                return
            if target_id == clan["creator_id"]:
                await message.reply("Нельзя исключить самого себя (создателя). Используйте /deleteclan.")
                return
            del clan["members"][str(target_id)]
            name = _display_name(target)
            await message.reply(f"👢 {name} исключён(а) из клана «{clan['name']}».")
            return

        others = [m for m in clan["members"].values() if m["user_id"] != clan["creator_id"]]
        if not others:
            await message.reply("В клане нет участников, кроме вас.")
            return

        builder = InlineKeyboardBuilder()
        for m in others:
            builder.button(
                text=f"👢 {_display_name(m)}",
                callback_data=f"kick:select:{clan['id']}:{m['user_id']}",
            )
        builder.button(text="Отмена", callback_data="kick:cancel")
        builder.adjust(1)

    await message.reply(
        "Кого исключить из клана?\n"
        "(Совет: можно также ответить командой /kick на сообщение нужного участника.)",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("kick:select:"))
async def cb_kick_select(callback: CallbackQuery) -> None:
    _, _, clan_id_s, user_id_s = callback.data.split(":")
    clan_id, target_id = int(clan_id_s), int(user_id_s)

    async with Storage() as db:
        clan = db["clans"].get(str(clan_id))
        if not clan:
            await callback.answer("Клан не найден.", show_alert=True)
            return
        if clan["creator_id"] != callback.from_user.id:
            await callback.answer("Только создатель может исключать участников.", show_alert=True)
            return
        target = clan["members"].get(str(target_id))
        if not target:
            await callback.answer("Этот участник уже не в клане.", show_alert=True)
            return
        del clan["members"][str(target_id)]
        name = _display_name(target)
        clan_name = clan["name"]

    await callback.message.edit_text(f"👢 {name} исключён(а) из клана «{clan_name}».")
    await callback.answer()


@router.callback_query(F.data == "kick:cancel")
async def cb_kick_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено.")
    await callback.answer()


# -------------------------------------------------------------- /leaveclan -

@router.message(Command("leaveclan"))
async def cmd_leave_clan(message: Message) -> None:
    async with Storage() as db:
        clan = _find_user_clan(db, message.from_user.id)
        if not clan:
            await message.reply("Вы не состоите ни в одном клане.")
            return
        if clan["creator_id"] == message.from_user.id:
            await message.reply(
                "Вы создатель клана и не можете просто выйти — используйте /deleteclan, "
                "чтобы расформировать клан целиком."
            )
            return
        del clan["members"][str(message.from_user.id)]
        clan_name = clan["name"]

    await message.reply(f"Вы покинули клан «{clan_name}». Можете вступить в другой командой /join.")


# ------------------------------------------------------------------- /top --

@router.message(Command("top"))
async def cmd_top(message: Message) -> None:
    async with Storage() as db:
        text = build_top_text(db, "📊 <b>Текущее положение войны кланов</b>")
    await message.reply(text, parse_mode="HTML")


# ---------------------------------------------------------------- /season --

@router.message(Command("season"))
async def cmd_season(message: Message) -> None:
    async with Storage() as db:
        started = db.get("season_started_at")
    if not started:
        await message.reply("Сезон ещё не начался (запустите бота — отсчёт стартует автоматически).")
        return
    elapsed_days = (now() - started) / 86400
    remaining_days = max(0, config.SEASON_LENGTH_DAYS - elapsed_days)
    await message.reply(
        f"📅 Текущий сезон идёт {elapsed_days:.1f} из {config.SEASON_LENGTH_DAYS} дней.\n"
        f"⏳ Осталось примерно {remaining_days:.1f} дн. до автоматического подведения итогов."
    )


# --------------------------------------------------------------- /resetwar -

@router.message(Command("resetwar"))
async def cmd_reset_war(message: Message) -> None:
    if message.from_user.id != config.ADMIN_ID:
        await message.reply("Эта команда доступна только владельцу бота.")
        return

    async with Storage() as db:
        if not db["clans"]:
            await message.reply("Кланов пока нет — сбрасывать нечего.")
            return

    await message.reply(
        "⚠️ Это досрочно подведёт итоги текущего сезона и обнулит очки/серии/"
        "победы всех кланов (новый сезон начнётся заново). Участники и сами "
        "кланы сохранятся.\nПродолжить?",
        reply_markup=confirm_kb(yes_cb="resetwar:confirm", no_cb="resetwar:cancel"),
    )


@router.callback_query(F.data == "resetwar:confirm")
async def cb_reset_war_confirm(callback: CallbackQuery) -> None:
    if callback.from_user.id != config.ADMIN_ID:
        await callback.answer("Только владелец бота может это подтвердить.", show_alert=True)
        return

    async with Storage() as db:
        final_text = finalize_season_locked(db)

    await callback.message.edit_text(final_text + "\n\n🔄 Новый сезон начат!", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "resetwar:cancel")
async def cb_reset_war_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено — сброса не будет.")
    await callback.answer()
