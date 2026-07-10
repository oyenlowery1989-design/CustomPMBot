"""Tests for config.py's env-validation helpers. A bare os.environ[...]
KeyError/ValueError at import time used to crash-loop forever under the
systemd unit's Restart=always (M10, docs/AUDIT-2026-07-10.md) — these
helpers must fail fast with one clear stderr line instead of a raw
traceback. config.py itself is already imported with a valid test env by
the time these run (see the os.environ.setdefault calls at the top of this
file), so these tests exercise the extracted helper functions directly
rather than re-importing the module with a broken environment."""
import pytest

import config


class TestRequireStr:
    def test_present_value_returned(self, monkeypatch):
        monkeypatch.setenv("SOME_VAR", "hello")
        assert config._require_str("SOME_VAR") == "hello"

    def test_missing_exits_with_message(self, monkeypatch, capsys):
        monkeypatch.delenv("SOME_VAR", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            config._require_str("SOME_VAR")
        assert exc_info.value.code == 1
        assert "SOME_VAR" in capsys.readouterr().err

    def test_empty_string_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("SOME_VAR", "")
        with pytest.raises(SystemExit):
            config._require_str("SOME_VAR")


class TestRequireInt:
    def test_valid_int_parsed(self, monkeypatch):
        monkeypatch.setenv("SOME_INT", "42")
        assert config._require_int("SOME_INT") == 42

    def test_non_numeric_exits_with_message(self, monkeypatch, capsys):
        monkeypatch.setenv("SOME_INT", "not-a-number")
        with pytest.raises(SystemExit) as exc_info:
            config._require_int("SOME_INT")
        assert exc_info.value.code == 1
        assert "SOME_INT" in capsys.readouterr().err

    def test_missing_exits_with_message(self, monkeypatch):
        monkeypatch.delenv("SOME_INT", raising=False)
        with pytest.raises(SystemExit):
            config._require_int("SOME_INT")


class TestOptionalInt:
    def test_missing_returns_default(self, monkeypatch):
        monkeypatch.delenv("SOME_OPT", raising=False)
        assert config._optional_int("SOME_OPT", 99) == 99

    def test_present_overrides_default(self, monkeypatch):
        monkeypatch.setenv("SOME_OPT", "7")
        assert config._optional_int("SOME_OPT", 99) == 7

    def test_non_numeric_exits_with_message(self, monkeypatch, capsys):
        monkeypatch.setenv("SOME_OPT", "nope")
        with pytest.raises(SystemExit) as exc_info:
            config._optional_int("SOME_OPT", 99)
        assert exc_info.value.code == 1
        assert "SOME_OPT" in capsys.readouterr().err
