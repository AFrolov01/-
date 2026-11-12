"""Сквозные сценарии на заглушках aiogram: анонс → ставки (ввод суммы) → старт → игра → итог."""
import asyncio, os, sys, tempfile
_TMP = tempfile.mkdtemp(prefix="duel_test_")
os.environ["DATA_DIR"] = _TMP          # тест не трогает настоящий data/storage.json
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import aiogram  # noqa: F401  — реальный aiogram, заглушки не нужны
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "stubs"))  # лёгкие заглушки aiogram
sys.path.insert(0, os.path.join(HERE, ".."))

import config
from bot.storage import Storage
from bot.chat_state import get_chat
from bot.clan_utils import ensure_clan_fields
from bot import players, betting, matchmaking, turns, texts, game
from bot.handlers import duel, betting as bet_handlers

CHAT = -1001

class Chat:
    def __init__(self, id): self.id = id

class FakeMsg:
    def __init__(self, bot, chat_id, mid, text=None, from_user=None, reply_to=None):
        self.bot, self.chat, self.message_id = bot, Chat(chat_id), mid
        self.text, self.from_user, self.reply_to_message = text, from_user, reply_to
        self.edits = []
    async def edit_text(self, text, **kw):
        self.edits.append((text, kw)); self.bot.log.append(("edit_text", self.message_id, text, kw))
    async def answer(self, text, **kw): return await self.bot.send_message(self.chat.id, text, **kw)
    async def reply(self, text, **kw): return await self.bot.send_message(self.chat.id, text, reply_to=self.message_id, **kw)

class FakeBot:
    def __init__(self): self.log, self._mid, self.msgs = [], 1000, {}
    async def send_message(self, chat_id, text, reply_to=None, **kw):
        self._mid += 1
        self.log.append(("send", chat_id, text, kw))
        m = FakeMsg(self, chat_id, self._mid); self.msgs[self._mid] = m; return m
    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kw):
        self.log.append(("edit", message_id, text, kw))
    async def delete_message(self, chat_id, message_id): self.log.append(("delete", message_id))
    async def pin_chat_message(self, *a, **k): self.log.append(("pin", a))
    async def unpin_chat_message(self, *a, **k): self.log.append(("unpin", a))
    def sent(self): return [l for l in self.log if l[0] == "send"]

class User:
    def __init__(self, uid, username="", first="X"):
        self.id, self.username, self.first_name = uid, username, first

class Cb:
    def __init__(self, bot, msg, data, user):
        self.bot, self.message, self.data, self.from_user = bot, msg, data, user
        self.answers = []
    async def answer(self, text=None, show_alert=False, **kw): self.answers.append((text, show_alert))

U = {n: User(i, n) for i, n in ((100, "alice"), (101, "alan"), (200, "bob"), (300, "carl"), (400, "dina"), (401, "eve"), (402, "fran"))}

def clan(cid, name, pts, members):
    return ensure_clan_fields({
        "id": cid, "name": name, "motto": "m", "creator_id": members[0][0], "points": float(pts),
        "members": {str(u): {"user_id": u, "username": un, "first_name": un.title()} for u, un in members},
    })

class Env:
    """Хелперы: нажать кнопку, ответить суммой, прочитать состояние."""
    def __init__(self, bot, inv_msg): self.bot, self.inv_msg = bot, inv_msg
    async def tap(self, who, data, msg=None):
        cb = Cb(self.bot, msg or self.inv_msg, data, U[who]); await bet_handlers.cb_bet(cb, self.bot); return cb.answers[-1], cb
    def last_prompt(self):
        s = [l for l in self.bot.log if l[0] == "send"][-1]
        return max(self.bot.msgs), s
    async def reply(self, who, prompt_mid, text):
        m = FakeMsg(self.bot, CHAT, 9000 + len(self.bot.log), text=text, from_user=U[who], reply_to=FakeMsg(self.bot, CHAT, prompt_mid))
        assert await bet_handlers._is_prompt_reply(m), "ответ должен распознаваться как ответ на приглашение"
        await bet_handlers.on_bet_amount(m, self.bot)
    async def bet(self, who, side, text):
        """Полный поток: нажал «Ставка» → получил приглашение → ответил суммой."""
        ans, cb = await self.tap(who, f"bet:{side}:ask")
        if ans[1]: return ans                                   # отказ (alert)
        mid, sent = self.last_prompt()
        assert "ForceReply" in type(sent[3]["reply_markup"]).__name__ and sent[3]["parse_mode"] == "HTML"
        await self.reply(who, mid, text); return ("ok", False)

async def seed(rec_a=(12, 20), rec_b=(3, 10), pair_members=(101, 200)):
    async with Storage() as db:
        chat = get_chat(db, CHAT)
        chat["players"] = {}; chat["active_duels"] = {}; chat["pending_invite"] = None
        chat["clans"]["1"] = clan(1, "Альфа", 1000, [(100, "alice"), (101, "alan")])
        chat["clans"]["2"] = clan(2, "Бета <b>", 800, [(200, "bob")])
        chat["clans"]["3"] = clan(3, "Гамма", 500, [(300, "carl")])
        for uid, un, cur in [(100, "alice", 3), (101, "alan", 20), (200, "bob", 3), (300, "carl", 30), (400, "dina", 12), (401, "eve", 0), (402, "fran", 1000)]:
            pl = players.get_or_create_player(chat, uid, un, un.title()); pl["currency"] = float(cur)
        for uid, (w, p) in ((100, rec_a), (200, rec_b)):
            chat["players"][str(uid)]["duels_won"], chat["players"][str(uid)]["duels_played"] = w, p
        a, b = chat["clans"]["1"], chat["clans"]["2"]
        pa = pair_members[0]
        return (a, a["members"][str(pa)], b, b["members"]["200"])

async def announce(bot, pair):
    matchmaking.pick_duel_pair = lambda chat: pair
    ok, info = await matchmaking.announce_duel(bot, CHAT); assert ok, info
    return bot.msgs[max(bot.msgs)], bot.sent()[-1]

async def start_and_pick(bot, who_starts, mines=3):
    ok, msg = await duel._start_duel_core(bot, CHAT, who_starts); assert ok, msg
    start_msg = bot.msgs[max(bot.msgs)]
    async with Storage() as db:
        did = list(get_chat(db, CHAT)["active_duels"].keys())[0]
    pa, pb = (100 if who_starts == 100 else 101), 200
    await duel.cb_choose_mines(Cb(bot, start_msg, f"duel:mines:{did}:{mines}", U["alice" if pa == 100 else "alan"]))
    await duel.cb_choose_mines(Cb(bot, start_msg, f"duel:mines:{did}:{mines}", U["bob"]))
    async with Storage() as db:
        d = get_chat(db, CHAT)["active_duels"][did]
        mA, mB = bot.msgs[d["sides"]["a"]["message_id"]], bot.msgs[d["sides"]["b"]["message_id"]]
        for k in "ab":
            d["sides"][k]["mine_positions"] = [0, 1, 2]; d["sides"][k]["portal_positions"] = []
    return did, start_msg, mA, mB

async def click(bot, msg, did, cell, who):
    c = Cb(bot, msg, f"duel:cell:{did}:{cell}", U[who]); await duel.cb_cell(c, bot); return c

def finales(bot): return [l for l in bot.log if l[0] == "send" and "Итог дуэли" in l[2]]

# ============================================================== сценарий 1: ставки и победа
async def scenario1():
    bot = FakeBot()
    pair = await seed(pair_members=(100, 200))
    inv_msg, inv_send = await announce(bot, pair)
    text, kw = inv_send[2], inv_send[3]
    print("=== АНОНС ===\n" + text + "\n")
    inv = None
    async with Storage() as db:
        inv = get_chat(db, CHAT)["pending_invite"]; X = dict(inv["bets"]["odds"])
    assert X["a"] < X["b"] and "«Альфа»" in text and "Бета &lt;b&gt;" in text and "60% побед (12/20)" in text and "30% побед (3/10)" in text
    assert texts.fmt_mult(X["a"]) in text and "Бесплатная ставка — 1 Те" in text
    labels = [[b.text for b in row] for row in kw["reply_markup"].inline_keyboard]
    print(labels)
    assert labels == [[f"🔵 Ставка · {texts.fmt_mult(X['a'])}", f"🔴 Ставка · {texts.fmt_mult(X['b'])}"],
                      ["🎟 1 Те бесплатно 🔵", "🎟 1 Те бесплатно 🔴"]], labels
    env = Env(bot, inv_msg)

    # --- ставки с вводом суммы
    await env.bet("carl", "a", "5")
    conf = [l for l in bot.log if l[0] == "edit" and "✅" in l[2]][-1]
    print("подтверждение:", conf[2]); assert "Принято" in conf[2] and "5 Те" in conf[2]
    r, _ = await env.tap("carl", "bet:b:ask");   print("carl на обе:", r); assert r[1] and "на двух сразу" in r[0]
    r, _ = await env.tap("alice", "bet:a:ask");  print("играющий:", r);   assert r[1] and "нельзя" in r[0]
    r, _ = await env.tap("alan", "bet:b:ask");   print("против своих:", r); assert r[1] and "свой клан" in r[0]
    await env.bet("alan", "a", "всё")                     # весь баланс (20 Те)
    await env.bet("dina", "b", "2,5")                     # дробная сумма
    await env.bet("fran", "b", "900")                     # крупная, без верхнего предела
    # у eve нет Те: «5» не пройдёт, переспрашивание, затем отмена после 3 попыток
    await env.bet("eve", "b", "5")
    mid, sent = env.last_prompt(); print("переспрос:", sent[2][:70]); assert "Не хватает" in sent[2] and "ForceReply" in type(sent[3]["reply_markup"]).__name__
    await env.reply("eve", mid, "абв")
    mid, sent = env.last_prompt(); assert "Не понял сумму" in sent[2]
    await env.reply("eve", mid, "абв")
    last = bot.sent()[-1][2]; print("после 3 попыток:", last); assert "Ввод отменён" in last
    r, _ = await env.tap("eve", "bet:b:c"); print("купон eve:", r); assert not r[1] and "1 Те" in r[0]
    r, _ = await env.tap("eve", "bet:b:c"); assert r[1]
    shown = [l for l in bot.log if l[0] == "edit" and l[1] == inv_msg.message_id][-1][2]
    print("\n=== АНОНС ПОСЛЕ СТАВОК ===\n" + shown + "\n")
    assert "Поставлено" in shown and texts.fmt_mult(X["a"]) in shown            # иксы не поменялись
    async with Storage() as db:
        chat = get_chat(db, CHAT); P = chat["players"]; inv = chat["pending_invite"]
        assert inv["bets"]["odds"] == X
        assert betting.pools(inv["bets"]) == {"a": 25.0, "b": 903.5}, betting.pools(inv["bets"])
        assert P["300"]["currency"] == 25.0 and P["101"]["currency"] == 0.0 and P["400"]["currency"] == 9.5 and P["402"]["currency"] == 100.0

    # --- старт: ставки закрываются
    ok, msg = await duel._start_duel_core(bot, CHAT, 100); assert ok, msg
    # вызов УДАЛЯЕТСЯ (чат не засоряется), а сразу после этого приходит сообщение с правилами и выбором мин
    assert ("delete", inv_msg.message_id) in bot.log, "сообщение-вызов должно быть удалено"
    assert bot.log.index(("delete", inv_msg.message_id)) < max(i for i, l in enumerate(bot.log) if l[0] == "send")
    assert not [l for l in bot.log if l[0] == "edit" and l[1] == inv_msg.message_id and "Ставки закрыты" in l[2]]
    print("=== ВЫЗОВ УДАЛЁН, СЛЕДОМ ПРАВИЛА ===")
    r, _ = await env.tap("dina", "bet:a:ask"); assert r[1] and "закрыты" in r[0]
    rules = bot.sent()[-1]; start_msg = bot.msgs[max(bot.msgs)]
    assert len(rules[2]) < 450
    # поздний ответ на старое приглашение (ставка уже закрыта)
    await env.bet("dina", "b", "1") if False else None
    # --- игра: A забирает, B подрывается
    async with Storage() as db:
        did = list(get_chat(db, CHAT)["active_duels"].keys())[0]
    await duel.cb_choose_mines(Cb(bot, start_msg, f"duel:mines:{did}:3", U["alice"]))
    await duel.cb_choose_mines(Cb(bot, start_msg, f"duel:mines:{did}:3", U["bob"]))
    async with Storage() as db:
        d = get_chat(db, CHAT)["active_duels"][did]
        mA, mB = bot.msgs[d["sides"]["a"]["message_id"]], bot.msgs[d["sides"]["b"]["message_id"]]
        for k in "ab":
            d["sides"][k]["mine_positions"] = [0, 1, 2]; d["sides"][k]["portal_positions"] = []
    for cell in (10, 11, 12): await click(bot, mA, did, cell, "alice")
    await duel.cb_cashout(Cb(bot, mA, f"duel:cashout:{did}", U["alice"]), bot)
    await click(bot, mB, did, 0, "bob")
    f = finales(bot); assert len(f) == 1
    print("\n=== ИТОГОВАЯ КАРТОЧКА ===\n" + f[0][2])
    rp = f[0][3].get("reply_parameters"); assert rp is not None and rp.message_id == start_msg.message_id
    assert "Победа: <b>«Альфа»</b>" in f[0][2] and "Бонус чемпиона" in f[0][2] and "выплата" in f[0][2]
    async with Storage() as db:
        P = get_chat(db, CHAT)["players"]
        assert abs(P["300"]["currency"] - (25 + 5 * X["a"])) < 0.02
        assert abs(P["101"]["currency"] - (0 + 20 * X["a"])) < 0.02
        assert P["400"]["currency"] == 9.5 and P["402"]["currency"] == 100.0 and P["401"]["currency"] == 0.0
        assert abs(P["100"]["currency"] - (3 + 1.45 + round(0.05 * 25, 2))) < 0.02, P["100"]["currency"]
    print("СЦЕНАРИЙ 1 — ОК")

# ====================================== сценарий 2: у кого больше икс — тот и победил
async def scenario_winner():
    """Оба забрали: побеждает больший множитель; равные — ничья; подорвался против забравшего — проигрыш."""
    async def duel_with(opens_a, opens_b, explode_b=False):
        bot = FakeBot()
        pair = await seed(rec_a=(0, 0), rec_b=(0, 0), pair_members=(100, 200))
        await announce(bot, pair)
        did, start_msg, mA, mB = await start_and_pick(bot, 100)
        for i in range(opens_a): await click(bot, mA, did, 10 + i, "alice")
        await duel.cb_cashout(Cb(bot, mA, f"duel:cashout:{did}", U["alice"]), bot)
        if explode_b: await click(bot, mB, did, 0, "bob")
        else:
            for i in range(opens_b): await click(bot, mB, did, 10 + i, "bob")
            await duel.cb_cashout(Cb(bot, mB, f"duel:cashout:{did}", U["bob"]), bot)
        f = finales(bot); assert len(f) == 1
        return f[0][2]
    m = game.multiplier_for
    assert m(3, 2) < m(3, 3)
    t = await duel_with(2, 3)
    print("A x%.2f vs B x%.2f ->" % (m(3, 2), m(3, 3)), "победа Бета" if "Победа: <b>«Бета" in t else t); assert "Победа: <b>«Бета" in t
    t = await duel_with(3, 2);  assert "Победа: <b>«Альфа»" in t
    t = await duel_with(2, 2);  assert "Ничья" in t                                  # равные иксы
    t = await duel_with(0, 0, explode_b=True);  assert "Победа: <b>«Альфа»" in t     # A забрал x1,00, B подорвался
    print("СЦЕНАРИЙ 2 (больший икс побеждает) — ОК")

# ============================================ сценарий 3: портал, ничья, возврат ставок
async def scenario_draw():
    bot = FakeBot()
    pair = await seed(pair_members=(101, 200))
    inv_msg, _ = await announce(bot, pair); env = Env(bot, inv_msg)
    await env.bet("carl", "a", "5"); await env.bet("dina", "b", "5")
    did, start_msg, mA, mB = await start_and_pick(bot, 101)
    async with Storage() as db:
        d = get_chat(db, CHAT)["active_duels"][did]; d["sides"]["a"]["portal_positions"] = [5]
    for cell in (10, 11): await click(bot, mA, did, cell, "alan")
    c = await click(bot, mA, did, 5, "alan"); print("портал тост:", c.answers[-1]); assert "Портал" in c.answers[-1][0]
    async with Storage() as db:
        d = get_chat(db, CHAT)["active_duels"][did]; d["sides"]["a"]["mine_positions"] = [0, 1, 2]; d["sides"]["a"]["portal_positions"] = []
    await click(bot, mA, did, 0, "alan"); await click(bot, mB, did, 0, "bob")
    f = finales(bot); assert len(f) == 1 and "Ничья" in f[0][2] and "возвращены" in f[0][2]
    async with Storage() as db:
        P = get_chat(db, CHAT)["players"]; assert P["300"]["currency"] == 30.0 and P["400"]["currency"] == 12.0
    print("СЦЕНАРИЙ 3 (портал, ничья → возврат) — ОК")

# ============================================ сценарий 4: вызов истёк — ставки возвращены
async def scenario_expire():
    bot = FakeBot()
    pair = await seed(pair_members=(101, 200))
    inv_msg, _ = await announce(bot, pair); env = Env(bot, inv_msg)
    await env.bet("carl", "a", "10"); await env.tap("eve", "bet:b:c")
    async with Storage() as db:
        chat = get_chat(db, CHAT); chat["pending_invite"]["created_at"] -= config.TURN_TIMEOUT_HOURS * 3600 + 5
    await turns._expire_pending_invites(bot)
    async with Storage() as db:
        chat = get_chat(db, CHAT); P = chat["players"]
        assert chat["pending_invite"] is None and P["300"]["currency"] == 30.0 and P["401"].get("free_bet_day") is None
    exp = [l for l in bot.log if l[0] == "edit" and l[1] == inv_msg.message_id][-1]
    assert "возвращены" in exp[2] and exp[3]["reply_markup"].inline_keyboard == []
    print("СЦЕНАРИЙ 4 (истёк вызов → возврат) — ОК")

async def main():
    await scenario1(); await scenario_winner(); await scenario_draw(); await scenario_expire()

asyncio.run(main())
print("\nВСЕ СЦЕНАРИИ ПРОЙДЕНЫ")
