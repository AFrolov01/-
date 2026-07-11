# -*- coding: utf-8 -*-
from aiogram import Router, Bot, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from bot.storage import Storage
from bot.matchmaking import announce_duel
from bot.clan_utils import ensure_clan_fields
from bot.leveling import clan_level_block, clan_prefix
from bot.reputation import reputation_block

router = Router(name="clan_info")


def _find_user_clan(db: dict, user_id: int):
    for clan in db["clans"].values():
        if str(user_id) in clan.get("members", {}):
            return clan
    return None


START_TEXT = (
    "⚔️ <b>Битвы кланов</b>\n\n"
    "Привет! Я слежу за войной кланов в этом чате: дуэли на поле 5×5, "
    "ставка — очки клана, риск — количество мин.\n\n"
    "<b>Команды:</b>\n"
    "🏰 /createclan — создать свой клан\n"
    "🤝 /join — вступить в существующий клан\n"
    "📊 /clan — карточка своего клана (очки, участники, серии побед)\n"
    "🚪 /leaveclan — покинуть свой клан\n"
    "👢 /kick — исключить участника (только для создателя клана)\n"
    "🗑 /deleteclan — расформировать клан (только для создателя)\n"
    "🏆 /top — топ кланов и топ игроков прямо сейчас\n"
    "📅 /season — сколько дней осталось до конца сезона\n"
    "🎯 /tactic — выбрать тактику клана на сезон (создатель клана)\n"
    "🛒 /shop — магазин привилегий за валюту Те\n"
    "👤 /iam (или просто слово «Б» в чат) — ваш профиль\n"
    "⚔️ /minduel — начать назначенную дуэль (доступно только вызванным игрокам)\n\n"
    "Совет: ответьте на чьё-то сообщение словами «твой б», чтобы увидеть "
    "профиль этого человека.\n\n"
    "Дуэли между кланами бот объявляет сам, примерно раз в 6 часов. Сезон "
    "войны длится 30 дней, по истечении которых бот сам подведёт итоги и "
    "объявит победителя, начислит опыт и репутацию кланам, после чего "
    "начнётся новый сезон.\n"
    "Если вы админ этого чата — вызовите /setgroup, чтобы назначить его "
    "основным чатом войны кланов."
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    text = START_TEXT
    if message.from_user.id == config.ADMIN_ID:
        text += (
            "\n\n👑 <b>Команды владельца:</b>\n"
            "/setgroup — назначить текущий чат боевым (вызывать в самой группе)\n"
            "/forceduel — объявить дуэль прямо сейчас, не дожидаясь расписания "
            "(можно вызвать даже здесь, в ЛС)"
        )
    text += "\n\n📖 Подробный разбор ВСЕХ механик — команда /help."
    await message.reply(text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


HELP_TITLES = ["🏰 Кланы", "⚔️ Дуэль", "🎯 Тактики и события", "📅 Сезон и уровни", "💰 Валюта и магазин"]

HELP_PAGES = [
    (
        "📖 <b>Битвы кланов — как всё устроено (1/5): кланы</b>\n\n"
        "🏰 <b>/createclan</b> — создать клан (название → аватар → девиз). "
        "Клан стартует с 1000 очков.\n"
        "🤝 <b>/join</b> — вступить в существующий клан (карусель кнопками).\n"
        "🚪 <b>/leaveclan</b> — покинуть клан (создатель так выйти не может, "
        "только расформировать).\n"
        "👢 <b>/kick</b> — исключить участника (только создатель; можно ответом "
        "на его сообщение).\n"
        "🗑 <b>/deleteclan</b> — расформировать клан насовсем (с подтверждением).\n"
        "📊 <b>/clan</b> — карточка своего клана: очки, уровень, репутация, "
        "тактика, история, участники."
    ),
    (
        "📖 <b>(2/5): как проходит дуэль</b>\n\n"
        "Раз в ~6 часов бот сам выбирает два клана и тегает по одному игроку "
        "от каждого (по скрытой очереди внутри клана — все играют по очереди, "
        "никто не сидит без дела).\n\n"
        "1️⃣ Любой из двух вызванных жмёт <b>/minduel</b> — открывается сразу "
        "для ОБОИХ, второй раз жать не нужно.\n"
        "2️⃣ Каждый выбирает СВОЁ количество мин (1–6) на поле 5×5 — чем больше "
        "мин, тем быстрее растёт множитель.\n"
        "3️⃣ Ставка — это ВСЕ текущие очки вашего клана на этот момент.\n"
        "4️⃣ Открываете клетки — множитель растёт. В любой момент можно нажать "
        "«✅ Забрать очки» и зафиксировать выигрыш.\n"
        "5️⃣ Если попали на мину — очки клана умножаются на штраф (обычно "
        "×0.75, но тактика может это менять) и раунд для вас окончен.\n\n"
        "🔁 <b>Попытки:</b> если предыдущий вызванный игрок вообще не сыграл, "
        "его попытка переходит следующему в очереди — у него будет 2 попытки "
        "подряд (и так далее, если пропускали несколько раз). Видно как "
        "«‼️У вас осталось X попыток‼️» после раунда."
    ),
    (
        "📖 <b>(3/5): тактики и еженедельные события</b>\n\n"
        "🎯 <b>/tactic</b> — создатель клана выбирает ОДНУ тактику на весь "
        "сезон (сменить нельзя до следующего сезона): смягчение штрафов, "
        "рост от побед подряд, удвоение валюты, случайные ежераундовые "
        "эффекты и т.д. — полный список прямо в самой команде.\n\n"
        "🧲 <b>Каждые 7 дней сезона происходят СРАЗУ ДВА события:</b>\n"
        "1) Очки каждого клана подтягиваются к среднему по всем кланам на "
        "10% — лидеры чуть проседают, аутсайдеры чуть подтягиваются (не даёт "
        "разрыву стать непреодолимым).\n"
        "2) ОТДЕЛЬНО — накопительный %-бонус или штраф к БУДУЩИМ очкам из "
        "раундов мин, по месту в рейтинге (топ −10%, аутсайдер +10%, "
        "остальные — между ними). Он складывается неделя к неделе и виден в "
        "сообщении о победе."
    ),
    (
        "📖 <b>(4/5): сезон, уровни, репутация</b>\n\n"
        "📅 Сезон длится 30 дней. В конце бот сам публикует итоги и "
        "победителя, дальше начинается новый сезон.\n"
        "📅 <b>/season</b> — сколько дней осталось.\n\n"
        "⭐ <b>Уровень клана</b> — в конце сезона клан получает опыт = "
        "(очки клана в конце сезона) × 0.01. Уровень, титул и значок "
        "показываются рядом с названием клана ВЕЗДЕ и НЕ сбрасываются между "
        "сезонами — это вечная история клана.\n\n"
        "👑 <b>Репутация клана</b> (0–1000) растёт за место в сезоне и за "
        "пройденные уровни. Обнуляется только раз в год (1 января), не между "
        "сезонами.\n\n"
        "🏆 <b>/top</b> — топ кланов и топ игроков прямо сейчас."
    ),
    (
        "📖 <b>(5/5): валюта, профиль, магазин, достижения</b>\n\n"
        "💰 <b>Валюта Те</b> — начисляется лично вам при победе в раунде, "
        "равна множителю, который вы выбили (x2.19 → +2.19 Те).\n\n"
        "👤 <b>/iam</b>, слово «<b>Б</b>» в чат — ваш профиль (лучший "
        "множитель, шанс победы, средние множители, баланс, достижения). "
        "«<b>твой б</b>» ответом на чьё-то сообщение — покажет ЕГО профиль.\n\n"
        "🛒 <b>/shop</b> — тратьте Те на привилегии: защита от исключения, "
        "х1.5 к следующей победе, досрочное завершение дуэли, голос за "
        "исключение из группы, смена названия клана/группы и другое.\n\n"
        "🏅 Достижения игрока и клана открываются автоматически по ходу игры "
        "(выбить x5/x7, первая победа, места в сезоне и т.д.) — видно в "
        "профиле и карточке клана."
    ),
]


def _help_menu_kb():
    builder = InlineKeyboardBuilder()
    for i, title in enumerate(HELP_TITLES):
        builder.button(text=title, callback_data=f"help:page:{i}")
    builder.adjust(1)
    return builder.as_markup()


def _help_page_kb(index: int):
    builder = InlineKeyboardBuilder()
    nav = []
    if index > 0:
        nav.append(("⬅️", f"help:page:{index - 1}"))
    nav.append(("🏠 Меню", "help:menu"))
    if index < len(HELP_PAGES) - 1:
        nav.append(("➡️", f"help:page:{index + 1}"))
    for text, cb in nav:
        builder.button(text=text, callback_data=cb)
    builder.adjust(len(nav))
    return builder.as_markup()


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "📖 <b>Битвы кланов — выберите раздел:</b>",
        parse_mode="HTML",
        reply_markup=_help_menu_kb(),
    )


@router.callback_query(F.data == "help:menu")
async def cb_help_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "📖 <b>Битвы кланов — выберите раздел:</b>",
        parse_mode="HTML",
        reply_markup=_help_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("help:page:"))
async def cb_help_page(callback: CallbackQuery) -> None:
    index = int(callback.data.split(":")[2])
    if not 0 <= index < len(HELP_PAGES):
        await callback.answer()
        return
    await callback.message.edit_text(
        HELP_PAGES[index], parse_mode="HTML", reply_markup=_help_page_kb(index)
    )
    await callback.answer()


def build_clan_text(clan: dict) -> str:
    members = list(clan.get("members", {}).values())
    members_lines = []
    for m in members:
        name = f'@{m["username"]}' if m.get("username") else m.get("first_name", "Игрок")
        crown = " 👑" if m["user_id"] == clan.get("creator_id") else ""
        members_lines.append(f"• {name}{crown}")

    best = clan.get("best_single_multiplier")
    best_line = "—"
    if best:
        best_name = f'@{best["username"]}' if best.get("username") else "игрок"
        best_line = f'x{best["value"]:.2f}'.replace(".", ",") + f" ({best_name})"

    medals = clan.get("medals", {"gold": 0, "silver": 0, "bronze": 0})
    tactic_key = clan.get("tactic")
    if tactic_key:
        tactic_name = config.SEASON_TACTICS.get(tactic_key, tactic_key)
        from bot.handlers.tactics import TACTIC_DESCRIPTIONS
        tactic_desc = TACTIC_DESCRIPTIONS.get(tactic_key, "")
        tactic_block = f"🎯 Тактика сезона:\n{tactic_desc}" if tactic_desc else f"🎯 Тактика сезона: {tactic_name}"
    else:
        tactic_block = "🎯 Тактика сезона: не выбрана (см. /tactic)"

    return (
        f"{clan_prefix(clan)}\n"
        f"🏰 <b>{clan['name']}</b>\n"
        f"📝 Девиз: {clan['motto']}\n\n"
        f"{clan_level_block(clan)}\n\n"
        f"{reputation_block(clan)}\n\n"
        f"🏆 Очки сезона: {clan.get('points', 0):g}\n"
        f"🔥 Текущая серия побед: {clan.get('current_win_streak', 0)}\n"
        f"⭐ Максимальная серия побед: {clan.get('max_win_streak', 0)}\n"
        f"🏅 Побед в войне кланов: {clan.get('wars_won', 0)}\n"
        f"💎 Лучший выигрышный множитель за бой: {best_line}\n"
        f"{tactic_block}\n\n"
        f"📜 <b>История:</b>\n"
        f"Сезонов сыграно: {clan.get('seasons_played', 0)}\n"
        f"🥇 x{medals.get('gold', 0)}  🥈 x{medals.get('silver', 0)}  🥉 x{medals.get('bronze', 0)}\n\n"
        f"👥 <b>Участники ({len(members)}):</b>\n" + "\n".join(members_lines)
    )


async def send_clan_card(message: Message, clan: dict) -> None:
    text = build_clan_text(clan)
    avatar = clan.get("avatar_file_id")
    if avatar:
        try:
            if len(text) <= 1024:
                await message.answer_photo(avatar, caption=text, parse_mode="HTML")
            else:
                # подпись к фото в Telegram ограничена 1024 символами — если карточка
                # длиннее (например, у клана очень много участников), шлём отдельно
                await message.answer_photo(avatar)
                await message.answer(text, parse_mode="HTML")
            return
        except Exception:
            pass  # если фото вдруг недоступно — отправим просто текстом ниже
    await message.answer(text, parse_mode="HTML")


@router.message(Command("clan"))
async def cmd_clan(message: Message) -> None:
    async with Storage() as db:
        clan = _find_user_clan(db, message.from_user.id)
        if clan:
            ensure_clan_fields(clan)

    if not clan:
        await message.reply("Вы не состоите ни в одном клане. Используйте /join или /createclan.")
        return

    await send_clan_card(message, clan)


@router.message(Command("setgroup"))
async def cmd_set_group(message: Message) -> None:
    if message.from_user.id != config.ADMIN_ID:
        await message.reply("Эта команда доступна только администратору бота.")
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.reply("Эту команду нужно вызывать в групповом чате войны кланов.")
        return
    async with Storage() as db:
        db["group_chat_id"] = message.chat.id
    await message.reply("✅ Этот чат назначен основным чатом войны кланов.")


@router.message(Command("forceduel"))
async def cmd_force_duel(message: Message, bot: Bot) -> None:
    """Владелец бота может вручную вызвать очередную дуэль — в том числе
    прямо из личных сообщений боту, не дожидаясь ежедневного расписания."""
    if message.from_user.id != config.ADMIN_ID:
        await message.reply("Эта команда доступна только владельцу бота.")
        return

    success, reason = await announce_duel(bot)
    if success:
        await message.reply(f"✅ {reason}")
    else:
        await message.reply(f"⚠️ Дуэль не объявлена: {reason}")
