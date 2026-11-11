# -*- coding: utf-8 -*-
"""
Тотализатор: кнопки ставок под анонсом дуэли, ввод суммы и команда /bets.
Вся математика — в bot/betting.py, здесь только Telegram-обвязка.

ПОТОК СТАВКИ
 1. Под анонсом дуэли человек жмёт «🔵 Ставка · x1,48» (или «🔴 …»).
 2. Бот присылает сообщение «сколько Те ставите?» с полем ответа (ForceReply) — ответ на
    сообщение бота доходит до него даже в группе с включённым режимом приватности.
 3. Человек отвечает суммой — любой («25», «2,5», «1 000», «всё»). Ставка принимается,
    анонс обновляется (видно, сколько поставлено), приглашение превращается в подтверждение.
 4. Не поняли сумму или не хватает Те — бот переспрашивает (до 3 раз).
 «🎟 1 Те бесплатно» — бесплатная ставка раз в сутки, принимается сразу, без ввода суммы.
"""

import asyncio
import html
import time
from typing import Optional

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, ForceReply

from bot import betting, players, texts
from bot.chat_state import get_chat, resolve_chat_for_message
from bot.invite_view import invite_view
from bot.storage import Storage

router = Router(name="betting")

# Ставки приходят пачками (все жмут сразу после анонса). Чтобы правки одного и того же
# сообщения не обгоняли друг друга и на экране всегда оставалась ПОСЛЕДНЯЯ версия,
# состояние читаем и сообщение обновляем под одним замком.
_edit_lock = asyncio.Lock()

# «Приглашения ввести сумму», которые бот сейчас ждёт: (chat_id, id_сообщения_бота) -> данные.
# Живут в памяти: после перезапуска бота старые приглашения просто перестают работать
# (человек нажмёт кнопку ещё раз).
_PROMPTS: dict = {}
_PROMPT_TTL = 3600
_MAX_TRIES = 3
_PLACEHOLDER = "Сумма в Те, например 25"


def _prune_prompts() -> None:
    limit = time.time() - _PROMPT_TTL
    for key in [k for k, v in _PROMPTS.items() if v["ts"] < limit]:
        _PROMPTS.pop(key, None)


def _mention(user) -> str:
    """Кликабельное упоминание: оно же нужно для selective ForceReply (поле ответа открывается
    только у этого человека)."""
    name = user.first_name or user.username or "Игрок"
    return f'<a href="tg://user?id={user.id}">{html.escape(name)}</a>'


def _invite_open(invite: Optional[dict], invite_mid: int) -> bool:
    return bool(
        invite and invite.get("message_id") == invite_mid
        and invite.get("bets") and invite["bets"].get("open")
    )


@router.callback_query(F.data.startswith("bet:"))
async def cb_bet(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3 or callback.message is None:
        await callback.answer()
        return
    _, side, kind = parts
    chat_id = callback.message.chat.id
    user = callback.from_user

    async with _edit_lock:
        async with Storage() as db:
            chat = get_chat(db, chat_id)
            invite = chat.get("pending_invite")
            # кнопки живут только на сообщении-анонсе ТЕКУЩЕГО вызова и только пока ставки открыты
            if not _invite_open(invite, callback.message.message_id):
                await callback.answer("Ставки на эту дуэль уже закрыты.", show_alert=True)
                return

            if kind == "c":  # бесплатная ставка — сразу
                ok, text = betting.place_bet(chat, invite, user, side, "c")
                view = invite_view(chat, invite) if ok else None
                prompt_text = None
            else:            # «Ставка» — спрашиваем сумму
                ok, text = betting.check_can_bet(chat, invite, user, side)
                view = None
                prompt_text = None
                if ok:
                    bets = invite["bets"]
                    pl = players.find_player(chat, user.id)
                    balance = pl.get("currency", 0.0) if pl else 0.0
                    prompt_text = (
                        f"💬 {_mention(user)}, сколько Те ставите на {betting.SIDE_EMOJI[side]} "
                        f"{texts._e(bets['info'][side]['name'])}? Множитель <b>{texts.fmt_mult(bets['odds'][side])}</b> "
                        f"зафиксирован. Ответьте на это сообщение суммой — например <code>25</code> — "
                        f"или словом «всё». На балансе: {texts.fmt_num(balance)} Те."
                    )

        if kind == "c":
            if ok and view:
                try:
                    await callback.message.edit_text(view[0], parse_mode="HTML", reply_markup=view[1])
                except Exception:
                    pass  # «message is not modified» и т.п. — не страшно
            await callback.answer(text, show_alert=not ok)
            return

    if not ok:
        await callback.answer(text, show_alert=True)
        return

    # у человека не может быть двух «висящих» приглашений: старое убираем
    _prune_prompts()
    for key, p in list(_PROMPTS.items()):
        if key[0] == chat_id and p["user_id"] == user.id:
            _PROMPTS.pop(key, None)
            try:
                await bot.delete_message(chat_id, key[1])
            except Exception:
                pass

    sent = await bot.send_message(
        chat_id, prompt_text, parse_mode="HTML",
        reply_markup=ForceReply(selective=True, input_field_placeholder=_PLACEHOLDER),
    )
    _PROMPTS[(chat_id, sent.message_id)] = {
        "user_id": user.id, "side": side, "invite_mid": callback.message.message_id,
        "ts": time.time(), "tries": 0,
    }
    await callback.answer("Ответьте на сообщение бота суммой ставки 👇")


async def _is_prompt_reply(message: Message) -> bool:
    """Это ответ того же человека на наше приглашение ввести сумму?"""
    r = message.reply_to_message
    if r is None or message.from_user is None or not message.text:
        return False
    p = _PROMPTS.get((message.chat.id, r.message_id))
    return bool(p and p["user_id"] == message.from_user.id)


@router.message(F.reply_to_message, _is_prompt_reply)
async def on_bet_amount(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    key = (chat_id, message.reply_to_message.message_id)
    prompt = _PROMPTS.get(key)
    if not prompt:
        return
    user = message.from_user

    outcome, text, view = "retry", "", None
    async with _edit_lock:
        async with Storage() as db:
            chat = get_chat(db, chat_id)
            invite = chat.get("pending_invite")
            if not _invite_open(invite, prompt["invite_mid"]):
                outcome, text = "drop", "Ставки на эту дуэль уже закрыты."
            else:
                ok, why = betting.check_can_bet(chat, invite, user, prompt["side"])
                if not ok:
                    outcome, text = "drop", why
                else:
                    pl = players.find_player(chat, user.id)
                    balance = pl.get("currency", 0.0) if pl else 0.0
                    amount, err = betting.parse_amount(message.text, balance)
                    if err:
                        outcome, text = "retry", err
                    else:
                        ok, text = betting.place_bet(chat, invite, user, prompt["side"], amount)
                        if ok:
                            outcome, view = "placed", invite_view(chat, invite)
                        else:
                            outcome = "retry"

        if outcome == "placed":
            _PROMPTS.pop(key, None)
            if view:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=prompt["invite_mid"], text=view[0],
                        parse_mode="HTML", reply_markup=view[1],
                    )
                except Exception:
                    pass

    if outcome == "placed":
        confirmation = f"✅ {_mention(user)}: {text}"
        try:  # приглашение превращаем в подтверждение — чат не засоряется лишними сообщениями
            await bot.edit_message_text(
                chat_id=chat_id, message_id=key[1], text=confirmation, parse_mode="HTML",
            )
        except Exception:
            await message.reply(confirmation, parse_mode="HTML")
        return

    if outcome == "drop":
        _PROMPTS.pop(key, None)
        await message.reply(text)
        return

    # retry: не поняли сумму / не хватает Те — переспрашиваем, но не бесконечно
    _PROMPTS.pop(key, None)
    tries = prompt["tries"] + 1
    if tries >= _MAX_TRIES:
        await message.reply(f"{text} Ввод отменён — нажмите «Ставка» ещё раз.")
        return
    again = await message.reply(
        f"{text} Напишите сумму ещё раз — например <code>25</code> — или «всё».",
        parse_mode="HTML",
        reply_markup=ForceReply(selective=True, input_field_placeholder=_PLACEHOLDER),
    )
    _PROMPTS[(chat_id, again.message_id)] = {**prompt, "tries": tries, "ts": time.time()}


@router.message(Command("bets"))
async def cmd_bets(message: Message) -> None:
    text = texts.bets_help_text()
    async with Storage() as db:
        chat_id, error = await resolve_chat_for_message(message, db)
        if not error:
            chat = get_chat(db, chat_id)
            pl = players.find_player(chat, message.from_user.id)
            if pl:
                coupon = (
                    "уже использована сегодня"
                    if pl.get("free_bet_day") == betting.today_key() else "доступна"
                )
                text += f"\n\n👤 Ваш баланс: {pl.get('currency', 0):.2f} Те · 🎟 бесплатная ставка: {coupon}"
                won, lost = pl.get("bets_won", 0), pl.get("bets_lost", 0)
                if won + lost > 0:
                    text += (
                        f"\n🎰 Угадано {won} из {won + lost} · "
                        f"итог {texts.fmt_signed_num(pl.get('bet_profit_total', 0))} Те"
                    )
    await message.reply(text, parse_mode="HTML")
