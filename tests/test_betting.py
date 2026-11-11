"""Математика тотализатора (без aiogram): фиксированные иксы по проценту побед, любые суммы."""
import os, sys, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config
from bot import betting, players

class U:
    def __init__(self, uid, username="", first="X"):
        self.id, self.username, self.first_name = uid, username, first

def make(a_rec=(0, 0), b_rec=(0, 0)):
    """Группа: кланы 1 (игрок 100, ещё член 101) и 2 (игрок 200), третий клан 3 (член 300)."""
    chat = {"players": {}, "clans": {
        "1": {"id": 1, "name": "A", "members": {"100": {}, "101": {}}},
        "2": {"id": 2, "name": "B", "members": {"200": {}}},
        "3": {"id": 3, "name": "C", "members": {"300": {}}},
    }}
    for uid, (won, played) in ((100, a_rec), (200, b_rec)):
        pl = players.get_or_create_player(chat, uid, f"p{uid}", f"P{uid}")
        pl["duels_won"], pl["duels_played"] = won, played
    invite = {"clan_a_id": 1, "player_a_id": 100, "clan_b_id": 2, "player_b_id": 200,
              "name_a": "@alice", "name_b": "@bob"}
    invite["bets"] = betting.new_bets(chat, invite)
    return chat, invite

def fund(chat, uid, amount):
    pl = players.get_or_create_player(chat, uid, f"u{uid}", f"n{uid}")
    pl["currency"] = float(amount)
    return pl

def cash(chat, uid): return chat["players"][str(uid)]["currency"]

# ---------- 1. иксы: чем меньше побед, тем выше икс; новички — поровну
chat, inv = make()
assert inv["bets"]["odds"] == {"a": 1.84, "b": 1.84}, inv["bets"]["odds"]         # 0.92 / 0.5
chat, inv = make((12, 20), (3, 10))
oa, ob = inv["bets"]["odds"]["a"], inv["bets"]["odds"]["b"]
print("12/20 vs 3/10 ->", oa, ob)
assert oa < ob, "у кого меньше процент побед — выше икс"
assert inv["bets"]["info"]["a"] == {"name": "@alice", "won": 12, "played": 20}
chat, inv = make((30, 40), (2, 40))
assert inv["bets"]["odds"]["a"] == config.BET_MIN_ODDS and inv["bets"]["odds"]["b"] <= config.BET_MAX_ODDS
random.seed(3)
for _ in range(2000):   # арбитража нет: 1/x_a + 1/x_b > 1, границы соблюдены
    ra, rb = random.random() * 0.98 + 0.01, random.random() * 0.98 + 0.01
    xa, xb = betting.odds_from_rates(ra, rb)
    assert config.BET_MIN_ODDS <= xa <= config.BET_MAX_ODDS and config.BET_MIN_ODDS <= xb <= config.BET_MAX_ODDS
    assert 1 / xa + 1 / xb > 1.0, (ra, rb, xa, xb)
# новичок с 1 победой из 1 не получает «100%»: сглаживание
assert betting.smoothed_rate(1, 1) == (1 + 2) / (1 + 4) and betting.smoothed_rate(0, 0) == 0.5

# ---------- 2. иксы фиксированы: ставки их НЕ двигают
chat, inv = make((12, 20), (3, 10))
before = dict(inv["bets"]["odds"])
for uid, side, amt in ((300, "a", 50), (400, "b", 5), (401, "b", 7)):
    fund(chat, uid, 1000)
    ok, msg = betting.place_bet(chat, inv, U(uid), side, amt); assert ok, msg
assert inv["bets"]["odds"] == before
assert betting.ensure_odds(chat, inv)["odds"] == before                    # повторный вызов не пересчитывает
chat["players"]["100"]["duels_won"] = 0                                     # даже если статистика игрока изменилась
assert betting.ensure_odds(chat, inv)["odds"] == before

# ---------- 3. любые суммы: дробные, крупные, «всё», минимум
chat, inv = make(); fund(chat, 300, 5000.5)
ok, msg = betting.place_bet(chat, inv, U(300), "a", 2.5); assert ok, msg; print(msg)
assert cash(chat, 300) == 4998.0
ok, msg = betting.place_bet(chat, inv, U(300), "a", 4000); assert ok, msg      # «безгранично»: лимит — только баланс
assert cash(chat, 300) == 998.0 and inv["bets"]["entries"]["300"]["cash"] == 4002.5
ok, msg = betting.place_bet(chat, inv, U(300), "a", 998); assert ok and cash(chat, 300) == 0.0
ok, msg = betting.place_bet(chat, inv, U(300), "a", 1); assert not ok and "Не хватает" in msg
fund(chat, 301, 10)
ok, msg = betting.place_bet(chat, inv, U(301), "b", 0.05); assert not ok and "Минимальная" in msg
assert cash(chat, 301) == 10.0
config.BET_MAX = 50                                                         # опциональный потолок
fund(chat, 302, 1000)
ok, msg = betting.place_bet(chat, inv, U(302), "b", 60); assert not ok and "Лимит" in msg
ok, msg = betting.place_bet(chat, inv, U(302), "b", 50); assert ok
config.BET_MAX = None

# разбор написанной суммы
P = betting.parse_amount
assert P("25", 100) == (25.0, "") and P("2,5", 100) == (2.5, "") and P("1 000", 5000) == (1000.0, "")
assert P("25 Те", 100) == (25.0, "") and P("ставлю 25", 100) == (25.0, "") and P("  7.25  ", 100) == (7.25, "")
assert P("всё", 12.34) == (12.34, "") and P("Все", 12.34) == (12.34, "") and P("all", 3) == (3.0, "")
assert P("всё", 0)[0] is None
for bad in ("абв", "", "-5", "0", "nan", "inf", "10 из 20", "5,5,5"):
    amt, err = P(bad, 100); assert amt is None and err, bad

# ---------- 4. расчёт: победа платит ставка×икс, проигрыш сгорает, бонус чемпиону
chat, inv = make((12, 20), (3, 10))
xa, xb = inv["bets"]["odds"]["a"], inv["bets"]["odds"]["b"]
for uid in (300, 400, 401, 402): fund(chat, uid, 100)
betting.place_bet(chat, inv, U(300), "a", 20)
betting.place_bet(chat, inv, U(400), "a", 10)
betting.place_bet(chat, inv, U(401), "b", 30)
fund(chat, 402, 0)
assert betting.place_bet(chat, inv, U(402), "b", "c")[0]                    # купон
c100 = cash(chat, 100)
s = betting.settle(chat, inv["bets"], "a", winner_player_id=100)
print("winner a, x =", s["mult"], "bounty", s["bounty"], s["winners"])
assert s["mult"] == xa and not s["refunded"]
assert cash(chat, 300) == round(80 + 20 * xa, 2) and cash(chat, 400) == round(90 + 10 * xa, 2)
assert cash(chat, 401) == 70.0                      # проиграл 30
assert cash(chat, 402) == 0.0                       # купон проиграл — ничего не потеряно
assert s["bounty"] == round(0.05 * 30, 2) and cash(chat, 100) == round(c100 + 1.5, 2)   # 5% от наличных на победителя
assert s["pool"] == {"a": 30.0, "b": 31.0}
assert betting.settle(chat, inv["bets"], "b", 200) is None and betting.refund(chat, inv["bets"]) == 0   # повторно — ничего

# купон-победитель получает только прибыль (икс − 1)
chat, inv = make((12, 20), (3, 10)); fund(chat, 500, 0)
betting.place_bet(chat, inv, U(500), "b", "c")
s = betting.settle(chat, inv["bets"], "b", 200)
assert cash(chat, 500) == round(1.0 * (xb - 1), 2), (cash(chat, 500), xb)
assert s["bounty"] == 0.0                           # купоны в базу бонуса чемпиона не входят

# одна сторона без противовеса — платится по иксу (тотализатор сам себе противоположность)
chat, inv = make(); fund(chat, 600, 100)
betting.place_bet(chat, inv, U(600), "a", 50)
s = betting.settle(chat, inv["bets"], "a", 100); assert not s["refunded"] and cash(chat, 600) == round(50 + 50 * 1.84, 2)

# ---------- 5. ничья и отмена — возврат полностью (и купон возвращается)
chat, inv = make(); fund(chat, 700, 40); fund(chat, 701, 0)
betting.place_bet(chat, inv, U(700), "a", 15); betting.place_bet(chat, inv, U(701), "b", "c")
assert chat["players"]["701"]["free_bet_day"] is not None
s = betting.settle(chat, inv["bets"], None)
assert s["refunded"] and s["reason"] == "draw" and cash(chat, 700) == 40.0 and cash(chat, 701) == 0.0
assert chat["players"]["701"]["free_bet_day"] is None
chat, inv = make(); fund(chat, 702, 9); betting.place_bet(chat, inv, U(702), "b", 9)
assert betting.refund_chat_bets({**chat, "pending_invite": inv, "active_duels": {}}) == 1 and cash(chat, 702) == 9.0

# вызов, созданный старой версией (без иксов): иксы досчитываются при первом показе, при расчёте без них — возврат
chat, inv = make(); fund(chat, 703, 10)
old = {"open": True, "settled": False, "entries": {"703": {"side": "a", "cash": 5.0, "coupon": 0.0, "coupon_day": None, "name": "x"}}}
chat["players"]["703"]["currency"] = 5.0
assert betting.settle(chat, old, "a", 100)["refunded"] and cash(chat, 703) == 10.0
inv["bets"] = {"open": True, "settled": False, "entries": {}}
assert betting.ensure_odds(chat, inv)["odds"] == {"a": 1.84, "b": 1.84}

# ---------- 6. ограничения
chat, inv = make()
fund(chat, 101, 100); fund(chat, 300, 100)
assert not betting.place_bet(chat, inv, U(100), "a", 1)[0]                  # играющий
assert not betting.check_can_bet(chat, inv, U(200), "b")[0]
assert not betting.place_bet(chat, inv, U(101), "b", 1)[0]                  # клан A ставит против своих
assert betting.place_bet(chat, inv, U(101), "a", 1)[0]
assert betting.place_bet(chat, inv, U(300), "b", 1)[0]                      # третий клан — любая сторона
ok, msg = betting.place_bet(chat, inv, U(300), "a", 1); assert not ok and "на двух сразу" in msg
fund(chat, 800, 0)
assert betting.place_bet(chat, inv, U(800), "a", "c")[0]
ok, msg = betting.place_bet(chat, inv, U(800), "a", "c"); assert not ok
inv["bets"]["open"] = False
ok, msg = betting.place_bet(chat, inv, U(300), "b", 1); assert not ok and "закрыты" in msg

# ---------- 7. случайные прогоны: инварианты
random.seed(11)
for trial in range(400):
    chat, inv = make((random.randint(0, 30), 30), (random.randint(0, 30), 30))
    users = list(range(5000, 5000 + random.randint(2, 12)))
    start = {}
    for u in users:
        fund(chat, u, random.choice([0, 3, 12, 40, 500]))
        start[u] = cash(chat, u)
    for u in users:
        side = random.choice("ab")
        for _ in range(random.randint(0, 3)):
            betting.place_bet(chat, inv, U(u), side, random.choice([0.5, 2, 7.25, 30, "c", "c"]))
    for u in users:
        assert cash(chat, u) >= 0
    ws = random.choice(["a", "b", None])
    s = betting.settle(chat, inv["bets"], ws, 100 if ws == "a" else 200)
    for u in users:
        e = inv["bets"]["entries"].get(str(u))
        end = cash(chat, u)
        if s is None or e is None:
            assert end == start[u]
        elif s["refunded"]:
            assert abs(end - start[u]) < 1e-9
        elif e["side"] == ws:
            assert end >= start[u] - 1e-9                                   # угадавший не уходит в минус
            assert abs(end - (start[u] - e["cash"] + e["cash"] * s["mult"] + e["coupon"] * (s["mult"] - 1))) < 0.02
        else:
            assert abs(end - (start[u] - e["cash"])) < 1e-9                 # проигравший теряет ровно ставку
print("ALL BETTING TESTS PASSED")
