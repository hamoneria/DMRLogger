import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_daily():
    spec = importlib.util.spec_from_file_location("daily", ROOT / "daily_dmr_summary.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fallback_summary_includes_stats_without_transcript_fragments():
    daily = load_daily()
    payload = {
        "window": {"hours": 24, "start_msk": "2026-01-01 09:00:00", "end_msk": "2026-01-02 09:00:00"},
        "routes": {
            "2501:1": {
                "label": "TG2501",
                "count": 1,
                "total_seconds": 12.5,
                "speakers": {"R1ABC / Test (2500001)": {"count": 1, "seconds": 12.5}},
                "items": [{"time_msk": "2026-01-01 10:00:00", "speaker": "R1ABC / Test (2500001)", "transcript": "Проверка связи."}],
            }
        },
    }

    text = daily.fallback_summary(payload, "2501:1")

    assert "TG2501" in text
    assert "Передач: 1" in text
    assert "Активные корреспонденты" in text
    assert "Проверка связи" not in text
    assert "Фрагменты расшифровок" not in text
    assert "только статистика" in text


def test_telegram_safe_text_truncates_under_safe_limit():
    daily = load_daily()
    text = daily.telegram_safe_text("x" * 5000)

    assert len(text) <= daily.TELEGRAM_SAFE_TEXT_LIMIT
    assert text.endswith("…обрезано из-за лимита Telegram.")


def test_split_telegram_text_labels_multiple_parts():
    daily = load_daily()
    text = ("first paragraph " * 120) + "\n\n" + ("second paragraph " * 120)
    parts = daily.split_telegram_text(text, limit=200)

    assert len(parts) > 1
    assert parts[0].startswith("Часть 1/")
    assert all(len(part) <= 200 for part in parts)


def test_openrouter_failure_falls_back_to_gemini_before_stats_only(monkeypatch, capsys):
    daily = load_daily()
    calls = []

    def fake_collect_payload(args):
        return {
            "window": {"hours": 24},
            "routes": {
                "2501:1": {
                    "label": "TG2501",
                    "count": 1,
                    "total_seconds": 12.5,
                    "speakers": {},
                    "items": [],
                }
            },
        }

    def fake_openrouter_summary(payload, route_key, model):
        calls.append("openrouter")
        raise daily.urllib.error.HTTPError("https://openrouter.ai", 402, "Payment Required", None, None)

    def fake_gemini_summary(payload, route_key, model):
        calls.append("gemini")
        return "Gemini summary ok"

    monkeypatch.setattr(daily, "collect_payload", fake_collect_payload)
    monkeypatch.setattr(daily, "openrouter_summary", fake_openrouter_summary)
    monkeypatch.setattr(daily, "gemini_summary", fake_gemini_summary)

    class Args:
        route_key = "2501:1"
        summary_provider = "openrouter"
        openrouter_model = "google/gemini-2.5-flash"
        gemini_model = "gemini-2.5-flash"
        no_gemini = False
        output_dir = None

    assert daily.summarize(Args()) == 0
    out = capsys.readouterr().out

    assert calls == ["openrouter", "gemini"]
    assert "Gemini summary ok" in out
    assert "openrouter недоступен" not in out
    assert "Payment Required" not in out
    assert "только статистика" not in out


def test_llm_failures_do_not_leak_service_errors_into_public_summary(monkeypatch, capsys):
    daily = load_daily()

    def fake_collect_payload(args):
        return {
            "window": {"hours": 24},
            "routes": {
                "2501:1": {
                    "label": "TG2501",
                    "count": 1,
                    "total_seconds": 12.5,
                    "speakers": {},
                    "items": [],
                }
            },
        }

    def fake_openrouter_summary(payload, route_key, model):
        raise daily.urllib.error.HTTPError("https://openrouter.ai", 402, "Payment Required", None, None)

    def fake_gemini_summary(payload, route_key, model):
        raise RuntimeError("Gemini quota exhausted: secret diagnostic detail")

    monkeypatch.setattr(daily, "collect_payload", fake_collect_payload)
    monkeypatch.setattr(daily, "openrouter_summary", fake_openrouter_summary)
    monkeypatch.setattr(daily, "gemini_summary", fake_gemini_summary)

    class Args:
        route_key = "2501:1"
        summary_provider = "openrouter"
        openrouter_model = "google/gemini-2.5-flash"
        gemini_model = "gemini-2.5-flash"
        no_gemini = False
        output_dir = None

    assert daily.summarize(Args()) == 0
    captured = capsys.readouterr()

    assert "только статистика" in captured.out
    assert "openrouter недоступен" not in captured.out
    assert "gemini недоступен" not in captured.out
    assert "Payment Required" not in captured.out
    assert "secret diagnostic detail" not in captured.out
    assert "summary provider failure" in captured.err


def test_openrouter_default_model_is_free_router(monkeypatch):
    daily = load_daily()
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("BM_SUMMARY_MODEL", raising=False)
    monkeypatch.setenv("BM_SUMMARY_PROVIDER", "openrouter")
    monkeypatch.setattr(
        sys,
        "argv",
        ["daily_dmr_summary.py", "summarize", "--routes-config", "routes.json"],
    )

    args = daily.parse_args()

    assert args.summary_provider == "openrouter"
    assert args.openrouter_model == "openrouter/free"
