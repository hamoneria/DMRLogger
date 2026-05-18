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


poster = load_module("poster", "post_direct_recordings_telegram.py")
daily = load_module("daily", "daily_dmr_summary.py")


def test_topic_destination_resolves_chat_thread_and_hashtag(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100topic")
    cfg = {
        "telegram": {"default_chat_id_env": "TELEGRAM_CHAT_ID"},
        "posting": {"enabled": True, "add_hashtags": True},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "label": "TG2501",
            "hashtag": "#TG2501",
            "destination": {"type": "topic", "message_thread_id": 2},
        }]}],
    }
    telegram_cfg, route_map = poster.parse_routes_config(cfg)
    route = route_map[(2501, 1)]

    dest = poster.resolve_route_destination(route, telegram_cfg, cfg["posting"], None)

    assert dest["enabled"] is True
    assert dest["type"] == "topic"
    assert dest["chat_id"] == "-100topic"
    assert dest["message_thread_id"] == 2
    assert dest["hashtag"] == "#TG2501"
    assert dest["add_hashtags"] is True


def test_channel_per_route_destination_uses_route_chat_env(monkeypatch):
    monkeypatch.setenv("TG2501_CHAT", "-100channel2501")
    cfg = {
        "telegram": {"default_chat_id_env": "TELEGRAM_CHAT_ID"},
        "posting": {"enabled": True, "add_hashtags": False},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "label": "TG2501",
            "destination": {"type": "chat", "chat_id_env": "TG2501_CHAT"},
        }]}],
    }
    telegram_cfg, route_map = poster.parse_routes_config(cfg)

    dest = poster.resolve_route_destination(route_map[(2501, 1)], telegram_cfg, cfg["posting"], None)

    assert dest["type"] == "chat"
    assert dest["chat_id"] == "-100channel2501"
    assert dest["message_thread_id"] is None


def test_single_chat_hashtag_caption_appends_hashtag_once():
    caption = poster.build_caption(
        {"talkgroup": 2501, "source_id": 123, "start_time": "2026-05-17T00:00:00Z"},
        {"label": "TG2501", "hashtag": "#TG2501", "add_hashtags": True},
    )

    assert caption.splitlines()[0] == "TG2501"
    assert caption.splitlines()[-1] == "#TG2501"


def test_destination_none_disables_posting(monkeypatch):
    cfg = {
        "telegram": {"default_chat_id_env": "TELEGRAM_CHAT_ID"},
        "posting": {"enabled": True},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "label": "TG2501",
            "destination": {"type": "none"},
        }]}],
    }
    telegram_cfg, route_map = poster.parse_routes_config(cfg)

    dest = poster.resolve_route_destination(route_map[(2501, 1)], telegram_cfg, cfg["posting"], "-100fallback")

    assert dest["enabled"] is False
    assert dest["chat_id"] == ""


def test_legacy_message_thread_id_config_still_works(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100legacy")
    cfg = {
        "telegram": {"chat_id_env": "TELEGRAM_CHAT_ID"},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "label": "TG2501",
            "message_thread_id": 2,
        }]}],
    }
    telegram_cfg, route_map = poster.parse_routes_config(cfg)

    dest = poster.resolve_route_destination(route_map[(2501, 1)], telegram_cfg, {}, None)

    assert dest["chat_id"] == "-100legacy"
    assert dest["message_thread_id"] == 2


def test_summary_config_merges_global_and_route_settings():
    cfg = {
        "summary": {"enabled": True, "mode": "per_route", "interval": "6h", "pin": True},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "summary": {"enabled": False, "pin": False},
        }]}],
    }
    _telegram_cfg, route_map, summary_cfg = daily.parse_routes_config(cfg)

    route_summary = daily.resolve_route_summary_config(route_map[(2501, 1)], summary_cfg)

    assert summary_cfg["interval"] == "6h"
    assert route_summary["enabled"] is False
    assert route_summary["pin"] is False


def test_v1_minimal_legacy_config_without_new_sections_still_works(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100legacy")
    cfg = {
        "telegram": {"chat_id_env": "TELEGRAM_CHAT_ID"},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "label": "TG2501",
            "message_thread_id": 2,
        }]}],
    }

    telegram_cfg, route_map = poster.parse_routes_config(cfg)
    dest = poster.resolve_route_destination(route_map[(2501, 1)], telegram_cfg, {}, None)

    assert dest["enabled"] is True
    assert dest["type"] == "topic"
    assert dest["chat_id"] == "-100legacy"
    assert dest["message_thread_id"] == 2


def test_future_optional_provider_fields_do_not_break_telegram_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100future")
    cfg = {
        "config_version": 1,
        "providers": {
            "transcription": {"provider": "faster-whisper", "model_env": "WHISPER_MODEL"},
            "summary": {"provider": "openrouter", "model_env": "DMR_SUMMARY_MODEL"},
            "publishing": {"default_provider": "telegram"},
        },
        "telegram": {"default_chat_id_env": "TELEGRAM_CHAT_ID"},
        "posting": {"enabled": True},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "label": "TG2501",
            "destination": {
                "provider": "telegram",
                "type": "topic",
                "message_thread_id": 2,
                "future_field": "ignored",
            },
        }]}],
    }

    telegram_cfg, route_map = poster.parse_routes_config(cfg)
    dest = poster.resolve_route_destination(route_map[(2501, 1)], telegram_cfg, cfg["posting"], None)

    assert dest["enabled"] is True
    assert dest["type"] == "topic"
    assert dest["chat_id"] == "-100future"
    assert dest["message_thread_id"] == 2


def test_unknown_future_publisher_provider_is_not_misrouted_to_telegram(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100telegram")
    cfg = {
        "telegram": {"default_chat_id_env": "TELEGRAM_CHAT_ID"},
        "peers": [{"radio_id": 1, "groups": [{
            "tg": 2501,
            "slot": 1,
            "label": "TG2501",
            "destination": {"provider": "discord", "webhook_url_env": "DISCORD_WEBHOOK"},
        }]}],
    }

    telegram_cfg, route_map = poster.parse_routes_config(cfg)
    dest = poster.resolve_route_destination(route_map[(2501, 1)], telegram_cfg, {}, None)

    assert dest["enabled"] is False
    assert dest["type"] == "unsupported"
    assert dest["provider"] == "discord"
    assert dest["chat_id"] == ""
