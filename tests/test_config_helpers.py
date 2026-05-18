import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_poster_telegram_chat_id_reads_env_reference(monkeypatch):
    poster = load_module("poster", "post_direct_recordings_telegram.py")
    monkeypatch.setenv("TEST_TELEGRAM_CHAT_ID", "-100123")

    assert poster.telegram_chat_id({"chat_id_env": "TEST_TELEGRAM_CHAT_ID"}, None) == "-100123"


def test_poster_route_chat_id_prefers_route_env_over_fallback(monkeypatch):
    poster = load_module("poster", "post_direct_recordings_telegram.py")
    monkeypatch.setenv("ROUTE_CHAT_ID", "-100route")

    assert poster.route_chat_id({"chat_id_env": "ROUTE_CHAT_ID"}, "-100fallback") == "-100route"


def test_daily_telegram_chat_id_reads_env_reference(monkeypatch):
    daily = load_module("daily", "daily_dmr_summary.py")
    monkeypatch.setenv("SUMMARY_CHAT_ID", "-100summary")

    assert daily.telegram_chat_id({"chat_id_env": "SUMMARY_CHAT_ID"}, None) == "-100summary"


def test_example_routes_parse_to_expected_route_map():
    poster = load_module("poster", "post_direct_recordings_telegram.py")
    telegram_cfg, routes = poster.load_routes(ROOT / "configs" / "bm_direct_routes.example.json")

    assert telegram_cfg["bot_token_env"] == "BM_DIRECT_TELEGRAM_BOT_TOKEN"
    assert telegram_cfg["chat_id_env"] == "TELEGRAM_CHAT_ID"
    assert (2501, 1) in routes
    assert routes[(2501, 1)]["label"] == "TG2501"
