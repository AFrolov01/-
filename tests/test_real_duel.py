"""Настоящая дуэль: ровно раз в 48 часов по жёсткой сетке, без ожидания «окна после обычной дуэли»."""
import asyncio, os, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="real_")
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
from bot import players
from bot.handlers import real_duel

CHAT = -2002
H = 3600
STEP = config.REAL_DUEL_INTERVAL_HOURS * H


class Sent:
    def __init__(self, mid): self.message_id = mid

class FakeBot:
    def __init__(self): self.sent, self._mid = [], 100
    async def send_message(self, chat_id, text, **kw):
        self._mid += 1; self.sent.append((chat_id, text)); return Sent(self._mid)
    async def edit_message_reply_markup(self, *a, **k): pass


def clan(cid, name, members):
    return ensure_clan_fields({
        "id": cid, "name": name, "motto": "m", "creator_id": members[0][0], "points": 1000.0,
        "members": {str(u): {"user_id": u, "username": un, "first_name": un.title()} for u, un in members},
    })

async def seed(due_at, recent_regular=True):
    """Группа в том же положении, что и у пользователя: обычные дуэли идут так часто, что у ВСЕХ игроков
    последний вызов был меньше 12 часов назад, а один из них прямо сейчас в обычной дуэли."""
    async with Storage() as db:
        chat = get_chat(db, CHAT)
        chat["clans"] = {"1": clan(1, "Альфа", [(100, "alice"), (101, "alan")]), "2": clan(2, "Бета", [(200, "bob"), (201, "bill")])}
        chat["players"] = {}; chat["real_duel"] = None; chat["pending_invite"] = None; chat["active_duels"] = {}
        for c in chat["clans"].values():
            for m in c["members"].values():
                players.get_or_create_player(chat, m["user_id"], m["username"], m["first_name"])
                if recent_regular: m["last_regular_invite_at"] = time.time() - 1 * H
        chat["pending_invite"] = {"player_a_id": 100, "player_b_id": 200, "clan_a_id": 1, "clan_b_id": 2, "message_id": 1}
        chat["next_real_duel_due_at"] = due_at

async def chat_state():
    async with Storage() as db:
        c = get_chat(db, CHAT); return c.get("real_duel"), c.get("next_real_duel_due_at")


async def t_slots():
    ns = real_duel._next_slot
    T = 1_000_000.0
    assert ns(T, T - 1) == T                               # срок ещё впереди — не трогаем
    assert ns(T, T + 1 * H) == T + STEP                    # чуть опоздали
    assert ns(T, T + STEP + 1) == T + 2 * STEP             # опоздали больше чем на 2 дня — следующий слот на сетке в будущем
    assert ns(T, T + 3 * STEP) == T + 4 * STEP
    assert (ns(T, T + 100 * H) - T) % STEP == 0            # всегда кратно 48 часам от исходного срока
    print("сетка слотов: ОК")


async def t_pick_never_waits():
    """Раньше при «вызывали на обычную дуэль <12 ч назад» игрока не находили — и дуэль не объявлялась."""
    await seed(time.time())
    async with Storage() as db:
        chat = get_chat(db, CHAT)
        a = chat["clans"]["1"]; b = chat["clans"]["2"]
        pa, pb = real_duel._pick_real_player(chat, a), real_duel._pick_real_player(chat, b)
        assert pa and pb, "игрок должен находиться, даже если всех недавно вызывали на обычную"
        assert pa["user_id"] == 101 and pb["user_id"] == 201, "в приоритете те, кто не в обычной дуэли"
        # заняты ВСЕ члены клана — всё равно берём кого-то (настоящая дуэль обычной не мешает)
        chat["pending_invite"] = {"player_a_id": 100, "player_b_id": 101, "clan_a_id": 1, "clan_b_id": 1, "message_id": 1}
        assert real_duel._pick_real_player(chat, a)
        solo = clan(3, "Соло", [(300, "carl")]); assert real_duel._pick_real_player(chat, solo)["user_id"] == 300
        assert real_duel._pick_real_player(chat, clan(4, "Пусто", [(1, "x")]) | {"members": {}}) is None
    print("выбор игрока без ожидания: ОК")


async def t_overdue_fires_now_then_grid():
    """«3 дня прошло, а дуэли нет»: срок давно прошёл → объявляется сразу; следующий срок — на сетке в будущем."""
    now = time.time(); due = now - 3 * 86400
    await seed(due); bot = FakeBot()
    await real_duel._check_schedule(bot)
    rd, nxt = await chat_state()
    assert rd is not None and len(bot.sent) == 1 and "НАСТОЯЩАЯ ДУЭЛЬ" in bot.sent[0][1], bot.sent
    assert nxt == due + 2 * STEP and nxt > time.time() and (nxt - due) % STEP == 0, (nxt - now) / H
    await real_duel._check_schedule(bot); assert len(bot.sent) == 1, "повторно сразу не объявляется"
    print("просроченная дуэль объявляется сразу: ОК")


async def t_no_drift():
    """Слегка опоздали — следующий срок ровно +48 ч от ПРОШЛОГО срока, а не от момента объявления."""
    due = time.time() - 5 * 60
    await seed(due); bot = FakeBot()
    await real_duel._check_schedule(bot)
    _, nxt = await chat_state(); assert nxt == due + STEP, (nxt - due) / H
    # до срока ничего не происходит
    await seed(time.time() + 1 * H); bot2 = FakeBot()
    await real_duel._check_schedule(bot2); assert not bot2.sent and (await chat_state())[0] is None
    print("без дрейфа: ОК")


async def t_first_sight_and_active():
    await seed(None); bot = FakeBot()
    t0 = time.time(); await real_duel._check_schedule(bot)
    rd, nxt = await chat_state(); assert rd is None and not bot.sent and abs(nxt - (t0 + STEP)) < 5   # первый раз: через 48 ч
    # пока предыдущая настоящая дуэль висит — срок не «съедается»
    due = time.time() - 60; await seed(due)
    async with Storage() as db: get_chat(db, CHAT)["real_duel"] = {"id": 1}
    bot3 = FakeBot(); await real_duel._check_schedule(bot3)
    assert not bot3.sent and (await chat_state())[1] == due
    print("первый запуск / активная дуэль: ОК")


async def main():
    for t in (t_slots, t_pick_never_waits, t_overdue_fires_now_then_grid, t_no_drift, t_first_sight_and_active):
        await t()

asyncio.run(main())
print("REAL DUEL TESTS PASSED")
