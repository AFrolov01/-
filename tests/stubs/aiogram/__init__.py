class _Any:
    """Универсальная заглушка для F-выражений и прочего."""
    def __getattr__(self, name): return _Any()
    def __call__(self, *a, **k): return _Any()
    def __eq__(self, o): return _Any()
    def __ne__(self, o): return _Any()
    def __and__(self, o): return _Any()
    def __or__(self, o): return _Any()
    def __invert__(self): return _Any()
    __hash__ = object.__hash__

F = _Any()

class Router:
    def __init__(self, name=None): self.name = name
    def _deco(self, *a, **k):
        return lambda f: f
    message = callback_query = chat_member = my_chat_member = edited_message = _deco
    def include_router(self, r): pass
    def outer_middleware(self, *a, **k): pass

class Bot:  # тесты подменяют своим FakeBot
    pass

class Dispatcher:
    def __init__(self, *a, **k):
        self.message = Router(); self.callback_query = Router()
    def include_router(self, r): pass

class BaseMiddleware:
    pass
