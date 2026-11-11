"""Ввод суммы в банке: раньше «положить 10» отменяло ввод, а следующее «10» бот игнорировал."""
import asyncio, os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="bank_")
HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import aiogram  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "stubs"))
sys.path.insert(0, os.path.join(HERE, ".."))
from bot.storage import Storage
from bot.chat_state import get_chat
from bot import players
from bot.handlers import bank

class FSM:
    def __init__(self): self.s, self.d = None, {}
    async def get_data(self): return dict(self.d)
    async def update_data(self, **k): self.d.update(k)
    async def set_state(self, s): self.s = s
    async def clear(self): self.s, self.d = None, {}
    async def get_state(self): return self.s

class Chat:
    id, type, title = -100, "supergroup", "g"
class User:
    id, username, first_name = 7, "andrey", "Андрей"
class Msg:
    def __init__(self, text, log): self.text, self.chat, self.from_user, self.log = text, Chat(), User(), log
    async def reply(self, text, **kw): self.log.append((text, kw.get("reply_markup")))

async def run(seq, currency=20.0):
    """Имитация маршрутизации aiogram: есть состояние — обработчик состояния, иначе команды/триггеры."""
    log, fsm = [], FSM()
    async with Storage() as db:
        chat = get_chat(db, -100)
        chat["players"] = {}
        pl = players.get_or_create_player(chat, 7, "andrey", "Андрей"); pl["currency"] = currency
    for text in seq:
        m = Msg(text, log)
        if fsm.s is not None:
            h = bank.process_deposit_amount if fsm.s == bank.BankStates.waiting_deposit_amount else bank.process_withdraw_amount
            await h(m, fsm)
        elif text.startswith("/bank"):
            await bank.cmd_bank(m, fsm)
        elif text.lower().startswith("банк"):
            await bank.text_bank_trigger(m, fsm)
        else:
            log.append(("(молчит)", None))
    async with Storage() as db:
        pl = players.find_player(get_chat(db, -100), 7)
        return round(pl["currency"], 2), round(pl["bank"]["balance"], 2), log, fsm

def check(seq, want_cash, want_bank, currency=20.0):
    cash, bnk, log, fsm = asyncio.run(run(seq, currency))
    assert (cash, bnk) == (want_cash, want_bank), (seq, cash, bnk, log[-1][0][:80])
    return log, fsm

# старый сценарий-баг: подсказка просит «положить СУММА»
check(["/bank", "положить 10"], 10.0, 10.0)
check(["/bank", "10"], 10.0, 10.0)
check(["/bank", "10 Те"], 10.0, 10.0)
check(["/bank", "1 000"], 20.0, 0.0, currency=20.0)           # 1000 > 20 -> «Недостаточно», но ввод не отменён
log, fsm = check(["/bank", "1 000", "5"], 15.0, 5.0)          # а следующая попытка проходит
check(["/bank", "10,5"], 9.5, 10.5)
check(["/bank", "всё"], 0.0, 20.0)
check(["/bank", "внести 7"], 13.0, 7.0)
check(["Банк 5"], 15.0, 5.0)                                    # голая сумма без глагола
check(["/bank положить 3"], 17.0, 3.0)
check(["/bank", "5", "/bank снять 2"], 17.0, 3.0)               # снятие тоже работает
log, fsm = check(["/bank", "абв"], 20.0, 0.0)                   # непонятное — переспрашивает, не отменяет
assert fsm.s is not None and "Не понял сумму" in log[-1][0] and log[-1][1] is not None
log, fsm = check(["/bank", "абв", "гд", "ее"], 20.0, 0.0)       # после 3 попыток — отмена
assert fsm.s is None and "отменён" in log[-1][0]
log, fsm = check(["/bank", "-5"], 20.0, 0.0)
assert fsm.s is not None and "положительной" in log[-1][0]
log, fsm = check(["/bank", "nan"], 20.0, 0.0)                    # nan/inf не принимаем
assert "Не понял" in log[-1][0]
log, fsm = check(["/bank"], 20.0, 0.0)
assert log[-1][1] is not None, "приглашение к вводу должно идти с ForceReply"
print("BANK TESTS PASSED")
