"""Банк: ОДНО сообщение-панель с кнопками «Положить»/«Снять», которое редактируется на месте.
Сумма — кнопками «Всё»/«Половина» или текстом (число / всё / половина, с глаголом и без)."""
import asyncio, os, sys, tempfile, time
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

GROUP = -100


class Chat:
    def __init__(self, id=GROUP): self.id, self.type, self.title = id, "supergroup", "g"

class User:
    def __init__(self, id=7, username="andrey", first="Андрей"): self.id, self.username, self.first_name = id, username, first

class Sent:
    def __init__(self, mid): self.message_id = mid

class FakeBot:
    """Хранит текущий текст/клавиатуру каждого сообщения бота — как их видит человек."""
    def __init__(self):
        self.mid, self.sends, self.edits, self.panels = 500, 0, 0, {}
    async def send(self, text, kb):
        self.mid += 1; self.sends += 1; self.panels[self.mid] = (text, kb); return Sent(self.mid)
    async def edit_message_text(self, chat_id=None, message_id=None, text=None, parse_mode=None, reply_markup=None, **kw):
        if message_id not in self.panels: raise RuntimeError("message to edit not found")
        if self.panels[message_id][0] == text and self.panels[message_id][1] is reply_markup: raise RuntimeError("message is not modified")
        self.edits += 1; self.panels[message_id] = (text, reply_markup)

class Msg:
    def __init__(self, bot, text, user, reply_to=None, chat=None):
        self.bot, self.text, self.from_user, self.chat, self.reply_to_message = bot, text, user, chat or Chat(), reply_to
    async def reply(self, text, reply_markup=None, **kw): return await self.bot.send(text, reply_markup)

class PanelMsg:
    def __init__(self, mid, chat=None): self.message_id, self.chat = mid, chat or Chat()

class Cb:
    def __init__(self, data, user, panel): self.data, self.from_user, self.message, self.answers = data, user, panel, []
    async def answer(self, text=None, show_alert=False, **kw): self.answers.append((text, show_alert))


class Session:
    """Один человек в одной группе: имитирует маршрутизацию aiogram (команды → триггер «Банк» → ввод суммы)."""
    def __init__(self, user=None):
        self.bot, self.user, self.panel = FakeBot(), user or User(), None
        bank._WAIT.clear()

    @classmethod
    async def create(cls, cash=20.0):
        s = cls(); await s._seed(cash); return s

    async def _seed(self, cash):
        async with Storage() as db:
            chat = get_chat(db, GROUP); chat["players"] = {}
            for u in (User(7, "andrey"), User(8, "boris", "Борис")):
                pl = players.get_or_create_player(chat, u.id, u.username, u.first_name); pl["currency"] = cash if u.id == 7 else 5.0

    # --- действия
    async def say(self, text, reply_to=None, user=None):
        m = Msg(self.bot, text, user or self.user, reply_to)
        if text.startswith("/bank"): await bank.cmd_bank(m)
        elif text.lower().startswith("банк"): await bank.text_bank_trigger(m)
        elif await bank._is_bank_input(m): await bank.on_bank_text(m, self.bot)
        else: return "silent"
        if self.panel is None and self.bot.panels: self.panel = self.bot.mid
        return "handled"

    async def press(self, act, user=None, panel=None):
        u = user or self.user
        cb = Cb(f"bank:{act}:{GROUP}:{self.user.id}", u, PanelMsg(panel or self.panel))
        await bank.cb_bank(cb, self.bot); return cb

    # --- чтение
    def text(self, mid=None): return self.bot.panels[mid or self.panel][0]
    def labels(self, mid=None):
        kb = self.bot.panels[mid or self.panel][1]; return [[b.text for b in row] for row in kb.inline_keyboard]
    async def money(self, uid=7):
        async with Storage() as db:
            pl = players.find_player(get_chat(db, GROUP), uid); return round(pl["currency"], 2), round(pl["bank"]["balance"], 2)



async def t_panel_flow():
    s = await Session.create(20.0)
    assert await s.say("/bank") == "handled" and s.bot.sends == 1
    assert s.labels() == [["➕ Положить", "➖ Снять"]], s.labels()
    t = s.text(); print(t.replace("\n\n", "\n"));
    assert "На руках: 20.00" in t and "На вкладе: <b>0.00 Те</b>" in t and "с учётом уже накапавших процентов" in t

    # нажатие «Положить» меняет ТО ЖЕ сообщение: новых сообщений нет, кнопки «Всё»/«Половина», видно баланс
    await s.press("dep"); assert s.bot.sends == 1 and s.bot.edits == 1
    assert "Сколько положить" in s.text() and "На руках: 20.00" in s.text()
    assert s.labels() == [["Всё · 20.00", "Половина · 10.00"], ["↩️ Назад"]], s.labels()
    await s.press("menu"); assert s.labels() == [["➕ Положить", "➖ Снять"]]; await s.press("dep")

    # число текстом -> то же сообщение показывает результат, кнопки вернулись
    assert await s.say("10") == "handled"; assert s.bot.sends == 1
    assert await s.money() == (10.0, 10.0) and "Внесено 10.00 Те" in s.text() and s.labels() == [["➕ Положить", "➖ Снять"]]

    # «Половина» кнопкой и словом, «Всё»
    await s.press("dep"); await s.press("dephalf"); assert await s.money() == (5.0, 15.0)
    await s.press("dep"); await s.say("половина"); assert await s.money() == (2.5, 17.5), await s.money()
    await s.press("wd"); await s.say("всё"); assert await s.money() == (20.0, 0.0), await s.money()
    await s.press("dep"); await s.say("всё"); assert await s.money() == (0.0, 20.0)
    assert s.bot.sends == 1, "за весь сценарий — ровно одно сообщение бота"
    print("панель: ОК")


async def t_habit_words():
    """Человек по привычке пишет глагол — работает и без нажатия кнопки, и «не той» кнопкой."""
    s = await Session.create(20.0); await s.say("/bank")
    assert await s.say("положить 7") == "handled" and await s.money() == (13.0, 7.0)          # панель в обычном виде
    assert await s.say("снять половину") == "handled" and await s.money() == (16.5, 3.5)
    await s.press("dep"); await s.say("снять всё"); assert await s.money() == (20.0, 0.0)       # глагол главнее кнопки
    await s.press("wd"); await s.say("положить 1 000")                                           # 1000 > 20: отказ, режим остаётся
    assert "Недостаточно" in s.text() and "Сколько положить" in s.text(), s.text()
    await s.say("5"); assert await s.money() == (15.0, 5.0)                                      # следующая попытка проходит
    await s.press("dep"); await s.say("10,5"); assert await s.money() == (4.5, 15.5)
    await s.press("dep"); await s.say("2 Те"); assert await s.money() == (2.5, 17.5)
    await s.say("внести 1"); assert await s.money() == (1.5, 18.5)
    assert s.bot.sends == 1
    print("«по привычке»: ОК")


async def t_not_chatter():
    """Обычная болтовня и чужие ответы не трогаются."""
    s = await Session.create(20.0); await s.say("/bank")
    assert await s.say("5") == "silent"                       # голое число без нажатой кнопки — просто чат
    assert await s.say("привет всем") == "silent"
    await s.press("dep")
    assert await s.say("абв") == "silent" and await s.say("как дела") == "silent"   # в режиме ввода непонятный текст молча игнорируется
    assert await s.say("5", reply_to=PanelMsg(12345)) == "silent"                  # ответ на ЧУЖОЕ сообщение (например, приглашение ставки)
    assert await s.say("5", reply_to=PanelMsg(s.panel)) == "handled"               # ответ на саму панель — работает
    assert await s.say("5", user=User(8, "boris", "Борис")) == "silent"            # чужой человек
    assert await s.money() == (15.0, 5.0)
    await s.press("dep"); assert await s.say("-5") == "handled" and "положительной" in s.text() and await s.money() == (15.0, 5.0)
    await s.press("dep"); assert await s.say("nan") == "silent" and await s.say("inf") == "silent"
    print("не мешает чату: ОК")


async def t_owner_only_and_limits():
    s = await Session.create(20.0); await s.say("/bank")
    c = await s.press("dep", user=User(8, "boris", "Борис"))
    assert c.answers[-1][1] and "другого игрока" in c.answers[-1][0] and s.bot.edits == 0
    # нечего вносить / снимать — всплывающее окно, сообщение не меняется на пустой запрос
    s2 = await Session.create(0.0); await s2.say("/bank")
    c = await s2.press("dep"); assert c.answers[-1][1] and "нет Те" in c.answers[-1][0] and "Сколько" not in s2.text()
    c = await s2.press("wd"); assert c.answers[-1][1] and "пусто" in c.answers[-1][0]
    print("только владелец / пустые режимы: ОК")


async def t_commands():
    for seq, want in (
        (["/bank положить 3"], (17.0, 3.0)), (["Банк 5"], (15.0, 5.0)), (["/bank 5"], (15.0, 5.0)),
        (["/bank внести 7"], (13.0, 7.0)), (["/bank всё"], (0.0, 20.0)), (["Банк положить половину"], (10.0, 10.0)),
        (["/bank положить 10 Те"], (10.0, 10.0)), (["/bank положить 1 000"], (20.0, 0.0)),   # отказ: 1000 > 20
        (["/bank положить 4", "/bank снять 1"], (17.0, 3.0)), (["/bank положить 4", "Банк снять всё"], (20.0, 0.0)),
        (["/bank положить 4", "/bank снять половину"], (18.0, 2.0)),
    ):
        s = await Session.create(20.0)
        for t in seq: assert await s.say(t) == "handled", t
        assert await s.money() == want, (seq, await s.money(), want)
        assert s.bot.sends == len(seq), "каждая явная команда — одно сообщение-панель"
    # «/bank положить» без суммы сразу спрашивает; нераспознанное слово — обычная панель
    s = await Session.create(20.0); await s.say("/bank положить"); assert "Сколько положить" in s.text()
    await s.say("/bank абв"); assert "Не понял команду" in s.text(s.bot.mid) and s.labels(s.bot.mid) == [["➕ Положить", "➖ Снять"]]
    print("команды: ОК")


async def t_interest():
    """Видно «с учётом накапавших» и «сколько накапало»; отказ не удваивает проценты."""
    s = await Session.create(20.0)
    async with Storage() as db:
        pl = players.find_player(get_chat(db, GROUP), 7)
        pl["bank"].update(balance=100.0, deposited_at=time.time() - 10 * 86400)
    await s.say("/bank"); t = s.text(); print(t.replace("\n\n", "\n"))
    assert "На вкладе: <b>104.07 Те</b>" in t and "Накапало с последней операции: +4.07 Те" in t
    before = (await s.money())[1]
    await s.press("wd"); await s.say("500")                                             # снять больше, чем есть
    assert "На вкладе только 104.07" in s.text()
    async with Storage() as db:
        assert abs(players.bank_current_balance(players.find_player(get_chat(db, GROUP), 7)) - 104.07) < 0.02   # не удвоилось
    await s.press("wdall"); cash, bal = await s.money(); assert abs(cash - 124.07) < 0.02 and bal == 0.0, (cash, bal)
    assert "Всего начислено за всё время" not in s.text() or "4.07" in s.text()
    print("проценты: ОК")


async def t_restart():
    """После перезапуска кнопки на старой панели всё равно работают (они ничего не хранят в памяти)."""
    s = await Session.create(20.0); await s.say("/bank"); bank._WAIT.clear()
    await s.press("dep"); assert "Сколько положить" in s.text()
    assert await s.say("5") == "handled" and await s.money() == (15.0, 5.0)
    print("после перезапуска: ОК")


async def t_parse():
    p = bank._parse_token
    assert p("50") == ("num", 50.0) and p("50,5") == ("num", 50.5) and p("1 000") == ("num", 1000.0) and p("50 Те") == ("num", 50.0)
    assert p("всё") == ("all", 0.0) and p("Все") == ("all", 0.0) and p("половина") == ("half", 0.0) and p("ПОЛОВИНУ") == ("half", 0.0) and p("пополам") == ("half", 0.0)
    assert p("абв") is None and p("") is None and p("nan") is None and p("inf") is None
    print("разбор суммы: ОК")


SCENARIOS = (t_parse, t_panel_flow, t_habit_words, t_not_chatter, t_owner_only_and_limits, t_commands, t_interest, t_restart)

async def main():
    for t in SCENARIOS:
        await t()

asyncio.run(main())
print("BANK TESTS PASSED")
