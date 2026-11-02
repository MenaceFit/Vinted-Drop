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
    assert _chat_id("1004303946427") == -1004303946427   # "-" oublié
    assert _chat_id("123456789") == 123456789            # id d'une personne : inchangé


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
        started = False
        async def serve(self):
            self.started = True
            calls.append("serve")

    monkeypatch.setattr(telegram_bot, "build_app", lambda: App())
    asyncio.run(telegram_bot.run_with_dashboard(Server()))
    assert calls[:4] == ["serve", "initialize", "start", "polling"]  # le dashboard démarre d'abord
    assert ("msg", -1004303946427) in calls and calls[-1] == "shutdown"


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


def test_menu_has_ten_sections_and_views_render():
    from vinted_drop import db, telegram_bot
    db.init()
    labels = [t for row in telegram_bot.main_menu() for t, _ in row]
    for needed in ("Dashboard", "Finances", "Boutiques", "Produits", "Studio photo", "Niches", "Produits gagnants",
                   "Pilote automatique", "Statistiques", "Paramètres"):
        assert any(needed in l for l in labels), needed
    assert "Tableau de bord" in telegram_bot.dashboard_text()
    assert "bénéfice net" in telegram_bot.finance_text("7d")
    assert "Simulation" in telegram_bot.settings_text()


def test_assistant_pending_action_flow(monkeypatch):
    """L'assistant propose une publication : rien n'est publié tant que l'utilisateur n'a pas confirmé."""
    from vinted_drop import assistant, db
    db.init()
    calls = iter([
        {"tool_calls": [{"id": "1", "function": {"name": "publish_product", "arguments": '{"id": 1}'}}]},
        {"content": "Je te demande confirmation pour publier."}])
    monkeypatch.setattr(assistant.ai, "chat_tools", lambda m, t: next(calls))
    published = []
    monkeypatch.setattr(assistant.publisher, "publish", lambda pid: published.append(pid))
    answer, hist, pending = assistant.ask("publie le produit 1")
    assert pending and pending[0]["name"] == "publish_product" and published == []


def test_bot_closed_when_no_chat_configured(monkeypatch):
    from vinted_drop import config, telegram_bot
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_CHAT_IDS", set())
    monkeypatch.setattr(config, "TELEGRAM_CHANNEL_ID", None)
    ran, replies = [], []

    class Msg:
        async def reply_text(self, t, **k): replies.append(t)

    class Chat:
        id, type = 555, "private"

    class Upd:
        effective_chat, effective_message, callback_query = Chat(), Msg(), None

    @telegram_bot.authorized
    async def secret(update, ctx):
        ran.append(1)
    asyncio.run(secret(Upd(), None))
    assert not ran and "555" in replies[0]
