# -*- coding: utf-8 -*-
"""
/bank — банк для валюты Те.

Условия (всегда показываются при вызове /bank):
 - ставка: 2% в день сложным процентом (config.BANK_DAILY_RATE)
 - снять можно не раньше, чем через 2 дня после внесения (config.BANK_MIN_HOLD_DAYS)
 - одновременно на счету может быть только один "вклад" (новое пополнение
   добавляется к уже лежащей сумме, а таймер удержания сдвигается на текущий
   момент — иначе пришлось бы вести историю нескольких вкладов с разными
   датами, что сильно усложнило бы механику без явного запроса на это)
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import config
from bot.storage import Storage, now
from bot import players

router = Router(name="bank")

RULES_TEXT = (
    "🏦 <b>Банк</b>\n\n"
    f"💹 Ставка: {config.BANK_DAILY_RATE * 100:g}% в день (сложный процент — "
    "проценты сами капают на проценты).\n"
    f"⏳ Снять можно не раньше, чем через {config.BANK_MIN_HOLD_DAYS} дня(ей) "
    "после последнего пополнения.\n"
    "➕ Новое пополнение добавляется к вкладу, но отсчёт срока удержания "
    "начинается заново с момента пополнения.\n\n"
    "<b>Команды:</b>\n"
    "/bank — эти правила + текущий баланс\n"
    "/bank депозит СУММА — положить Те на вклад\n"
    "/bank вывести СУММА — снять Те (если срок удержания уже прошёл)"
)


@router.message(Command("bank"))
async def cmd_bank(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=2)

    async with Storage() as db:
        player = players.get_or_create_player(
            db, message.from_user.id, message.from_user.username or "", message.from_user.first_name or "Игрок"
        )

        if len(parts) < 2:
            balance = players.bank_current_balance(player)
            days_left = players.bank_days_left_to_withdraw(player)
            text = (
                RULES_TEXT
                + f"\n\n💰 На руках: {player.get('currency', 0):.2f} Те\n"
                + f"🏦 В банке (с процентами): {balance:.2f} Те"
            )
            if balance > 0:
                text += (
                    f"\n⏳ До снятия: {days_left:.1f} дн." if days_left > 0 else "\n✅ Снять можно прямо сейчас."
                )
            await message.reply(text, parse_mode="HTML")
            return

        action = parts[1].lower()
        if action not in ("ltgjpbn", "вывести") or len(parts) < 3:
            await message.reply("Используйте: /bank deposit СУММА или /bank withdraw СУММА")
            return

        try:
            amount = float(parts[2].replace(",", "."))
        except ValueError:
            await message.reply("Сумма должна быть числом.")
            return
        if amount <= 0:
            await message.reply("Сумма должна быть положительной.")
            return

        bank = player.setdefault("bank", {"balance": 0.0, "deposited_at": None})

        if action == "депозит":
            if player.get("currency", 0.0) < amount:
                await message.reply(f"Недостаточно Те на руках (есть {player.get('currency', 0):.2f}).")
                return
            current_balance = players.bank_current_balance(player)
            player["currency"] = round(player["currency"] - amount, 2)
            bank["balance"] = round(current_balance + amount, 2)
            bank["deposited_at"] = now()
            await message.reply(
                f"✅ Внесено {amount:.2f} Те. Баланс вклада: {bank['balance']:.2f} Те.\n"
                f"⏳ Снять можно будет через {config.BANK_MIN_HOLD_DAYS} дня(ей)."
            )
            return

        # withdraw
        days_left = players.bank_days_left_to_withdraw(player)
        if days_left > 0:
            await message.reply(f"⏳ Ещё рано снимать — подождите {days_left:.1f} дн.")
            return
        current_balance = players.bank_current_balance(player)
        if amount > current_balance:
            await message.reply(f"На вкладе только {current_balance:.2f} Те.")
            return
        remaining = round(current_balance - amount, 2)
        bank["balance"] = remaining
        bank["deposited_at"] = now() if remaining > 0 else None
        player["currency"] = round(player.get("currency", 0.0) + amount, 2)
        await message.reply(
            f"✅ Снято {amount:.2f} Те. На руках теперь: {player['currency']:.2f} Те.\n"
            f"🏦 Осталось на вкладе: {remaining:.2f} Те."
        )
