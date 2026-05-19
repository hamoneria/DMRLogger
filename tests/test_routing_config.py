import argparse
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


def test_english_caption_uses_english_labels_and_units():
    caption = poster.build_caption(
        {
            "talkgroup": 2501,
            "rf_src": 2500001,
            "callsign": "R1ABC",
            "operator_name": "Alice",
            "operator_city": "Moscow",
            "operator_country": "Russia",
            "approx_audio_seconds": 4.2,
            "started_at_utc": "2026-05-17T12:00:00+00:00",
        },
        {"label": "TG2501", "language": "en"},
    )

    assert "⏱ 4.2 sec" in caption
    assert "🕒 MSK:" in caption
    assert "сек" not in caption


def test_english_transcript_message_uses_english_header_and_empty_text(tmp_path):
    missing = tmp_path / "missing.txt"

    text = poster.transcript_message(missing, language="en")

    assert text.startswith("📝 Transcript:")
    assert "Transcript was not generated." in text
    assert "Расшифровка" not in text


def test_posting_language_is_inherited_from_global_config():
    cfg = {
        "posting": {"enabled": True, "language": "en"},
        "peers": [{"radio_id": 1, "groups": [{"tg": 2501, "slot": 1, "label": "TG2501"}]}],
    }

    _telegram_cfg, route_map = poster.parse_routes_config(cfg)

    assert route_map[(2501, 1)]["language"] == "en"


def test_summary_language_is_inherited_from_global_config():
    cfg = {
        "summary": {"enabled": True, "language": "en"},
        "peers": [{"radio_id": 1, "groups": [{"tg": 2501, "slot": 1, "label": "TG2501"}]}],
    }

    _telegram_cfg, route_map, summary_cfg = daily.parse_routes_config(cfg)
    route_summary = daily.resolve_route_summary_config(route_map[(2501, 1)], summary_cfg)

    assert summary_cfg["language"] == "en"
    assert route_map[(2501, 1)]["language"] == "en"
    assert route_summary["language"] == "en"


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


def test_send_transcript_false_saves_transcript_but_does_not_post_text(monkeypatch, tmp_path):
    meta_path = tmp_path / "recording.json"
    mp3 = tmp_path / "recording.mp3"
    txt = tmp_path / "recording.txt"
    mp3.write_bytes(b"mp3")
    meta_path.write_text(
        '{"talkgroup": 2501, "slot": 1, "rf_src": 2500001, "approx_audio_seconds": 4.0}',
        encoding="utf-8",
    )
    cfg = {
        "telegram": {"default_chat_id": "-100test"},
        "posting": {"enabled": True, "send_transcript": False},
        "peers": [{"radio_id": 1, "groups": [{"tg": 2501, "slot": 1, "label": "TG2501"}]}],
    }
    telegram_cfg, route_map = poster.parse_routes_config(cfg)
    state = {}
    calls = []

    def fake_ensure_media(path, args):
        txt.write_text("кривая расшифровка, но нужна для summary\n", encoding="utf-8")
        meta = poster.load_json(path)
        meta["mp3_path"] = str(mp3)
        meta["transcript_path"] = str(txt)
        poster.save_json(path, meta)
        return meta, mp3, txt

    def fake_tg_api(token, method, fields, files=None):
        calls.append((method, dict(fields), files))
        return {"message_id": len(calls)}

    monkeypatch.setattr(poster, "ensure_media", fake_ensure_media)
    monkeypatch.setattr(poster, "tg_api", fake_tg_api)

    args = argparse.Namespace(
        transcribe=True,
        min_duration=3.0,
        chat_id="-100fallback",
        tg="2501",
        state_file=tmp_path / "state.json",
    )

    assert poster.process_one(meta_path, args, state, "token", route_map, telegram_cfg, cfg["posting"]) is True

    assert txt.exists()
    assert [call[0] for call in calls] == ["sendAudio"]
    entry = state[str(meta_path)]
    assert entry["audio_message_id"] == 1
    assert "transcript_message_id" not in entry

    calls.clear()
    assert poster.process_one(meta_path, args, state, "token", route_map, telegram_cfg, cfg["posting"]) is False
    assert calls == []
