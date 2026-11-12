"""Закрепы: бот обязан открепить сообщения, когда дуэль сыграна обоими, когда кто-то не пришёл и
началась новая, когда вызов не принят, когда сезон сброшен, — и не терять закреп при сбое сети."""
import asyncio, os, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pins_")
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import aiogram  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.join(HERE, ".."))

import config
from bot.storage import Storage
from bot.chat_state import get_chat
from bot.clan_utils import ensure_clan_fields
from bot import players, matchmaking, turns, season, pins
from bot.handlers import duel

CHAT = -1001
BOT_ID = 999


class NotFound(pins.TelegramBadRequest):
    def __init__(self, msg): Exception.__init__(self, msg)
    def __str__(self): return self.args[0]


class Chat:
    def __init__(self, id): self.id = id

class User:
    def __init__(self, uid, username="", first="X"): self.id, self.username, self.first_name, self.is_bot = uid, username, first, False

class FakeMsg:
    def __init__(self, bot, chat_id, mid, text="", author=BOT_ID):
        self.bot, self.chat, self.message_id, self.text, self.caption = bot, Chat(chat_id), mid, text, None
        self.from_user = User(author)
    async def edit_text(self, text, **kw): self.bot.log.append(("edit_text", self.message_id, text))
    async def answer(self, text, **kw): return await self.bot.send_message(self.chat.id, text, **kw)

class FakeBot:
    """Модель Telegram: закреплённые — стек (последний = вершина); удаление сообщения снимает закреп;
    открепить не закреплённое — ошибка «not found»; fail_unpin имитирует сбой сети."""
    def __init__(self):
        self.log, self._mid, self.msgs, self.pinned = [], 1000, {}, []
        self.fail_unpin = False; self.fail_delete = False; self.fail_pin = False
    async def send_message(self, chat_id, text, **kw):
        self._mid += 1; self.log.append(("send", chat_id, text))
        m = FakeMsg(self, chat_id, self._mid, text); self.msgs[self._mid] = m; return m
    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kw):
        self.log.append(("edit", message_id, text))
        if message_id in self.msgs: self.msgs[message_id].text = text
    async def edit_message_reply_markup(self, *a, **k): pass
    async def delete_message(self, chat_id, message_id):
        if self.fail_delete: raise RuntimeError("cannot delete")
        self.log.append(("delete", message_id)); self.msgs.pop(message_id, None)
        if message_id in self.pinned: self.pinned.remove(message_id)
    async def pin_chat_message(self, chat_id, message_id, disable_notification=None):
        if self.fail_pin: raise NotFound("not enough rights to pin a message")
        self.log.append(("pin", message_id)); self.pinned.append(message_id)
    async def unpin_chat_message(self, chat_id, message_id=None):
        if self.fail_unpin: raise RuntimeError("network down")
        if message_id not in self.pinned: raise NotFound("Bad Request: message to unpin not found")
        self.log.append(("unpin", message_id)); self.pinned.remove(message_id)
    async def me(self): return User(BOT_ID, "duelbot")
    async def get_chat(self, chat_id):
        top = self.msgs.get(self.pinned[-1]) if self.pinned else None
        class Info: pass
        i = Info(); i.pinned_message = top; return i
    def add_old_pin(self, mid, text, author=BOT_ID):  # закреп, оставшийся от прошлой версии (id нигде не записан)
        self.msgs[mid] = FakeMsg(self, CHAT, mid, text, author); self.pinned.append(mid)

class Cb:
    def __init__(self, bot, msg, data, user): self.bot, self.message, self.data, self.from_user, self.answers = bot, msg, data, user, []
    async def answer(self, text=None, show_alert=False, **kw): self.answers.append((text, show_alert))

U = {n: User(i, n) for i, n in ((100, "alice"), (101, "alan"), (200, "bob"), (300, "carl"))}

def clan(cid, name, pts, members):
    return ensure_clan_fields({
        "id": cid, "name": name, "motto": "m", "creator_id": members[0][0], "points": float(pts),
        "members": {str(u): {"user_id": u, "username": un, "first_name": un.title()} for u, un in members},
    })

async def seed():
    async with Storage() as db:
        chat = get_chat(db, CHAT)
        chat["players"] = {}; chat["active_duels"] = {}; chat["pending_invite"] = None; chat["bot_pins"] = []; chat["pending_skip_notes"] = []
        chat["clans"]["1"] = clan(1, "Альфа", 1000, [(100, "alice"), (101, "alan")])
        chat["clans"]["2"] = clan(2, "Бета", 800, [(200, "bob")])
        for uid, un in ((100, "alice"), (101, "alan"), (200, "bob"), (300, "carl")):
            players.get_or_create_player(chat, uid, un, un.title())
        db["season_started_at"] = time.time()
        return chat["clans"]["1"], chat["clans"]["1"]["members"]["100"], chat["clans"]["2"], chat["clans"]["2"]["members"]["200"]

async def state():
    async with Storage() as db:
        c = get_chat(db, CHAT); return list(c["bot_pins"]), c.get("pending_invite"), dict(c["active_duels"])

async def announce(bot):
    async with Storage() as db:
        chat = get_chat(db, CHAT); a, b = chat["clans"]["1"], chat["clans"]["2"]
        pair = (a, a["members"]["100"], b, b["members"]["200"])
    matchmaking.pick_duel_pair = lambda chat: pair
    ok, info = await matchmaking.announce_duel(bot, CHAT); assert ok, info
    pins_, inv, _ = await state()
    return inv["message_id"]

async def start(bot, mines=3, both=True):
    ok, msg = await duel._start_duel_core(bot, CHAT, 100); assert ok, msg
    rules = bot.msgs[max(bot.msgs)]
    _, _, act = await state(); did = list(act)[0]
    await duel.cb_choose_mines(Cb(bot, rules, f"duel:mines:{did}:{mines}", U["alice"]))
    if both: await duel.cb_choose_mines(Cb(bot, rules, f"duel:mines:{did}:{mines}", U["bob"]))
    async with Storage() as db:
        d = get_chat(db, CHAT)["active_duels"][did]
        for k in "ab":
            d["sides"][k]["mine_positions"] = [0, 1, 2]; d["sides"][k]["portal_positions"] = []
        mA = bot.msgs[d["sides"]["a"]["message_id"]]; mB = bot.msgs[d["sides"]["b"]["message_id"]] if both else None
    return did, rules, mA, mB

async def boom(bot, msg, did, who): await duel.cb_cell(Cb(bot, msg, f"duel:cell:{did}:0", U[who]), bot)


async def t_normal_cycle():
    """Вызов закреплён → /minduel: вызов удалён, закреплено меню выбора мин → оба сыграли → ничего не закреплено."""
    bot = FakeBot(); await seed()
    inv = await announce(bot)
    assert bot.pinned == [inv] and (await state())[0] == [inv], bot.pinned
    did, rules, mA, mB = await start(bot)
    assert ("delete", inv) in bot.log and inv not in bot.msgs                 # вызов удалён
    assert bot.pinned == [rules.message_id], bot.pinned                        # закреплено только меню выбора мин
    assert (await state())[0] == [rules.message_id]
    await boom(bot, mA, did, "alice")
    assert bot.pinned == [rules.message_id], "пока доиграл один — меню ещё закреплено"
    await boom(bot, mB, did, "bob")
    assert bot.pinned == [] and (await state())[0] == [], (bot.pinned, await state())
    print("обычный цикл: ОК")


async def t_no_show_then_new_duel():
    """Один не пришёл. Дуэль закрывается по таймауту — закреп снимается, новая дуэль закрепляется одна."""
    bot = FakeBot(); await seed()
    await announce(bot)
    did, rules, mA, _ = await start(bot, both=False)
    await boom(bot, mA, did, "alice")                                           # сыграл только alice
    assert bot.pinned == [rules.message_id]
    async with Storage() as db:                                                  # bob так и не выбрал мины — время вышло
        get_chat(db, CHAT)["active_duels"][did]["sides"]["b"]["last_action_at"] -= config.TURN_TIMEOUT_HOURS * 3600 + 60
    await turns._expire_stuck_duel_sides(bot)
    assert bot.pinned == [] and (await state())[2] == {}, bot.pinned
    inv2 = await announce(bot)                                                   # началась новая дуэль
    assert bot.pinned == [inv2] and (await state())[0] == [inv2]
    print("не пришёл → новая дуэль: ОК")


async def t_invite_expired():
    bot = FakeBot(); await seed()
    inv = await announce(bot); assert bot.pinned == [inv]
    async with Storage() as db:
        get_chat(db, CHAT)["pending_invite"]["created_at"] -= config.TURN_TIMEOUT_HOURS * 3600 + 60
    await turns._expire_pending_invites(bot)
    assert bot.pinned == [] and (await state())[0] == []
    print("вызов не принят: ОК")


async def t_network_failure_is_retried():
    """Сбой сети при откреплении не теряет закреп: он остаётся в списке и снимается фоновой зачисткой."""
    bot = FakeBot(); await seed()
    await announce(bot); did, rules, mA, mB = await start(bot)
    bot.fail_unpin = True
    await boom(bot, mA, did, "alice"); await boom(bot, mB, did, "bob")
    assert bot.pinned == [rules.message_id] and (await state())[0] == [rules.message_id], "id не потерян"
    bot.fail_unpin = False
    await pins.sweep_all(bot)                                                    # фоновая страховка (раз в 5 минут)
    assert bot.pinned == [] and (await state())[0] == []
    print("сбой сети → повтор: ОК")


async def t_deleted_message_is_forgotten():
    """Сообщение удалили вручную — запись о закрепе не копится вечно."""
    bot = FakeBot(); await seed()
    inv = await announce(bot)
    bot.pinned.remove(inv); bot.msgs.pop(inv)                                    # админ снял закреп и удалил сообщение
    async with Storage() as db:
        c = get_chat(db, CHAT); c["pending_invite"] = None
    await pins.sweep(bot, CHAT)
    assert (await state())[0] == []
    print("удалённое сообщение: ОК")


async def t_season_reset_unpins():
    """Конец сезона сбрасывает вызов/дуэли в состоянии — раньше их закреп висел навсегда."""
    bot = FakeBot(); await seed()
    inv = await announce(bot); assert bot.pinned == [inv]
    async with Storage() as db:
        db["season_started_at"] = time.time() - (config.SEASON_LENGTH_DAYS + 1) * 86400
    await season._check_and_finalize(bot)
    assert bot.pinned == [] and (await state())[1] is None, bot.pinned
    print("конец сезона: ОК")


async def t_delete_fails_fallback():
    """Удалить вызов не получилось — кнопки убраны, текст «ставки закрыты», закреп снят."""
    bot = FakeBot(); await seed()
    inv = await announce(bot); bot.fail_delete = True
    did, rules, mA, mB = await start(bot)
    edits = [l for l in bot.log if l[0] == "edit" and l[1] == inv]
    assert edits and bot.pinned == [rules.message_id], (edits, bot.pinned)
    print("не удалось удалить вызов: ОК")


async def t_legacy_cleanup():
    """Старые закрепы бота (id не записаны) снимаются с вершины; чужие и недуэльные — нет."""
    bot = FakeBot(); await seed()
    bot.add_old_pin(50, "Правила чата от админа", author=1)                      # человек
    bot.add_old_pin(51, "⚔️ «Альфа» vs «Бета»\n💰 Ставка", author=BOT_ID)       # старое меню выбора мин
    bot.add_old_pin(52, "⚔️ Дуэль чести!\n🔵 «Альфа»", author=BOT_ID)             # старый вызов
    inv = await announce(bot)
    assert bot.pinned == [50, inv], bot.pinned                                   # 52 и 51 сняты, человеческий 50 остался
    bot2 = FakeBot(); await seed()
    bot2.add_old_pin(60, "🧾 Справка бота: /help", author=BOT_ID)                 # бот, но не дуэль
    await announce(bot2); assert 60 in bot2.pinned
    print("наследие старых версий: ОК")


async def t_no_admin_rights():
    """Нет права закреплять — ничего не падает, список не копится."""
    bot = FakeBot(); await seed(); bot.fail_pin = True
    inv = await announce(bot); did, rules, mA, mB = await start(bot)
    await boom(bot, mA, did, "alice"); await boom(bot, mB, did, "bob")
    assert bot.pinned == [] and (await state())[0] == []
    print("нет прав админа: ОК")


async def main():
    for t in (t_normal_cycle, t_no_show_then_new_duel, t_invite_expired, t_network_failure_is_retried,
              t_deleted_message_is_forgotten, t_season_reset_unpins, t_delete_fails_fallback, t_legacy_cleanup, t_no_admin_rights):
        await t()

asyncio.run(main())
print("PINS TESTS PASSED")
