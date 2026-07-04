# -*- coding: utf-8 -*-
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

import config
from bot.handlers import clan_create, clan_join, clan_info, duel
from bot.matchmaking import scheduler_loop
from bot.handlers.duel import afk_watcher_loop

logging.basicConfig(level=logging.INFO)


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Как пользоваться ботом"),
        BotCommand(command="createclan", description="Создать клан"),
        BotCommand(command="join", description="Вступить в клан"),
        BotCommand(command="clan", description="Информация о моём клане"),
        BotCommand(command="minduel", description="Начать назначенную дуэль"),
    ])


async def main() -> None:
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(clan_create.router)
    dp.include_router(clan_join.router)
    dp.include_router(clan_info.router)
    dp.include_router(duel.router)

    await _set_commands(bot)

    asyncio.create_task(scheduler_loop(bot))
    asyncio.create_task(afk_watcher_loop(bot))

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
