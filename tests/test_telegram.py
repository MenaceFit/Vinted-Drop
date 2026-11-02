"""Démarrage du bot, canal Telegram et sécurité des logs (sans réseau)."""
import asyncio
import logging

import pytest


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from vinted_drop import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", {111})
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_ID", -1004303946427)


def test_channel_id_parsing():
    from vinted_drop.config import _chat_id
    assert _chat_id("https://web.telegram.org/a/#-1004303946427") == -1004303946427
    assert _chat_id("-1004303946427") == -1004303946427
    assert _chat_id("") is None


def test_notifications_go_to_channel_and_commands_allowed_from_both():
    from vinted_drop import config
    assert config.notify_targets() == {-1004303946427}
    assert config.telegram_chats() == {111, -1004303946427}


def test_handlers_accept_channel_posts():
    from telegram.ext import CommandHandler
    from vinted_drop.telegram_bot import build_app
    app = build_app()
    cmds = [h for h in app.handlers[0] if isinstance(h, CommandHandler)]
    assert cmds and "channel" in repr(cmds[0].filters).lower()


def test_token_not_logged():
    from vinted_drop.telegram_bot import setup_logging
    setup_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_run_with_dashboard_single_loop(monkeypatch):
    """Le bot et le dashboard tournent dans la même boucle, le bot est initialisé avant de démarrer."""
    from vinted_drop import telegram_bot
    calls = []

    class Updater:
        async def start_polling(self, **kw): calls.append("polling")
        async def stop(self): calls.append("updater_stop")

    class Bot:
        async def get_me(self):
            class Me: username = "vinted_drop_bot"
            return Me()
        async def send_message(self, chat_id, text): calls.append(("msg", chat_id))

    class App:
        updater, bot = Updater(), Bot()
        async def __aenter__(self): calls.append("initialize"); return self
        async def __aexit__(self, *a): calls.append("shutdown")
        async def start(self): calls.append("start")
        async def stop(self): calls.append("stop")

    class Server:
        async def serve(self): calls.append("serve")

    monkeypatch.setattr(telegram_bot, "build_app", lambda: App())
    asyncio.run(telegram_bot.run_with_dashboard(Server()))
    assert calls[:3] == ["initialize", "start", "polling"]
    assert ("msg", -1004303946427) in calls and "serve" in calls and calls[-1] == "shutdown"


def test_channel_button_requires_admin(monkeypatch):
    from vinted_drop import telegram_bot

    class Member:
        def __init__(self, status): self.status = status

    class Bot:
        async def get_chat_member(self, chat_id, user_id):
            return Member("administrator" if user_id == 1 else "member")

    telegram_bot._ADMIN_CACHE.clear()
    assert asyncio.run(telegram_bot._is_channel_admin(Bot(), -100, 1))
    assert not asyncio.run(telegram_bot._is_channel_admin(Bot(), -100, 2))
