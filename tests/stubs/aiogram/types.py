class InlineKeyboardButton:
    def __init__(self, text, callback_data=None, url=None, **kw):
        self.text, self.callback_data = text, callback_data

class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard=None, **kw):
        self.inline_keyboard = inline_keyboard or []

class ReplyParameters:
    def __init__(self, message_id, allow_sending_without_reply=None, **kw):
        self.message_id = message_id
        self.allow_sending_without_reply = allow_sending_without_reply

def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return type(name, (), {"__init__": lambda self, *a, **k: None})
