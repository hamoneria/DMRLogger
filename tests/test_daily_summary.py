import importlib.util
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
