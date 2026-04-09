from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from cryptobot.cli import app
from cryptobot.core.types import Bar
from cryptobot.data.storage import BarStore

runner = CliRunner()


def test_export_history_cli_writes_csv(monkeypatch, tmp_path: Path) -> None:
    scratch = tmp_path
    monkeypatch.setenv("CRYPTOBOT_DATA_DIR", str(scratch))

    store = BarStore(scratch)
    store.write(
        "BTC/USDT",
        "5m",
        [
            Bar(
                symbol="BTC/USDT",
                timeframe="5m",
                ts_open=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("12"),
            )
        ],
    )

    out = scratch / "bars.csv"
    result = runner.invoke(app, ["export-history", "--symbol", "BTC/USDT", "--timeframe", "5m", "--output", str(out)])

    assert result.exit_code == 0
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "open_time,open,high,low,close,volume" in text
    assert "2024-01-01 00:00:00,100.0,101.0,99.0,100.5,12.0" in text


def test_export_history_cli_errors_when_cache_missing(monkeypatch, tmp_path: Path) -> None:
    scratch = tmp_path
    monkeypatch.setenv("CRYPTOBOT_DATA_DIR", str(scratch))

    result = runner.invoke(app, ["export-history", "--symbol", "BTC/USDT", "--timeframe", "5m"])

    assert result.exit_code == 1
    assert "No cached bars found" in result.output
