# -*- coding: utf-8 -*-
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

import config
from bot.handlers import clan_create, clan_join, clan_info, clan_manage, tactics, shop, bank, ads, duel, player_profile
from bot.matchmaking import scheduler_loop
from bot.handlers.duel import afk_watcher_loop
from bot.season import season_watcher_loop
from bot.turns import turn_watcher_loop
from bot.handlers.ads import ad_watcher_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")


async def _set_commands(bot: Bot) -> None:
    # ВАЖНО: /creatadd сюда НЕ добавляется — это секретная команда владельца,
    # она не должна светиться в меню команд ни у кого.
    await bot.set_my_commands([
        BotCommand(command="start", description="Как пользоваться ботом"),
        BotCommand(command="help", description="Подробный гайд по всем механикам"),
        BotCommand(command="createclan", description="Создать клан"),
        BotCommand(command="join", description="Вступить в клан"),
        BotCommand(command="clan", description="Информация о моём клане"),
        BotCommand(command="settingclan", description="Управление кланом: покинуть/кикнуть/расформировать/тактика"),
        BotCommand(command="shop", description="Магазин привилегий за Те"),
        BotCommand(command="bank", description="Банк — внести/снять Те под процент"),
        BotCommand(command="iam", description="Мой профиль"),
        BotCommand(command="top", description="Топ кланов этой группы"),
        BotCommand(command="globaltop", description="Топ кланов среди всех групп"),
        BotCommand(command="season", description="Сколько дней осталось до конца сезона"),
        BotCommand(command="online", description="Сколько групп и игроков используют бота"),
        BotCommand(command="minduel", description="Начать назначенную дуэль"),
        BotCommand(command="tixa", description="Тихий режим дуэлей (только владелец группы)"),
    ])


async def main() -> None:
    logger.info("=" * 60)
    logger.info("Файл базы данных: %s", config.DATA_FILE)
    logger.info(
        "Если после деплоя кланы/очки пропадают — проверьте, что этот путь "
        "совпадает с Mount Path подключённого Railway Volume."
    )
    try:
        from bot.storage import read_only
        db = await read_only()
        chats = db.get("chats", {})
        total_clans = sum(len(c.get("clans", {})) for c in chats.values())
        total_players = sum(len(c.get("players", {})) for c in chats.values())
        logger.info(
            "При старте загружено групп: %d, кланов: %d, игроков: %d",
            len(chats), total_clans, total_players
        )
    except Exception as e:
        logger.error("НЕ УДАЛОСЬ прочитать файл базы данных при старте: %s", e)
    logger.info("=" * 60)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.include_router(clan_create.router)
    dp.include_router(clan_join.router)
    dp.include_router(clan_info.router)
    dp.include_router(clan_manage.router)
    dp.include_router(tactics.router)
    dp.include_router(shop.router)
    dp.include_router(bank.router)
    dp.include_router(ads.router)
    dp.include_router(duel.router)
    dp.include_router(player_profile.router)  # последним: ловит свободные текстовые триггеры

    await _set_commands(bot)

    asyncio.create_task(scheduler_loop(bot))
    asyncio.create_task(afk_watcher_loop(bot))
    asyncio.create_task(season_watcher_loop(bot))
    asyncio.create_task(turn_watcher_loop(bot))
    asyncio.create_task(ad_watcher_loop(bot))

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
