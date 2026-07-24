from datetime import timedelta

import pytest

from leaderboard.cli import main, parse_args, resolve_window


def test_org_is_required():
    with pytest.raises(SystemExit):
        parse_args([])


def test_defaults():
    args = parse_args(["acme"])
    assert args.org == "acme"
    assert args.days == 365
    assert args.output == "leaderboard.html"
    assert args.no_achievements is False
    assert args.open is False


def test_flags_are_parsed():
    args = parse_args(["acme", "--days", "30", "--output", "out.html", "--no-achievements", "--open"])
    assert (args.days, args.output) == (30, "out.html")
    assert args.no_achievements is True
    assert args.open is True


def test_window_spans_the_requested_number_of_days():
    since, until = resolve_window(30)
    assert until > since
    assert abs((until - since) - timedelta(days=30)) < timedelta(seconds=2)
    assert since.tzinfo is not None and until.tzinfo is not None


def test_missing_token_exits_with_code_two(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["acme"]) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_empty_token_is_treated_as_missing(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "   ")
    assert main(["acme"]) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_days_above_the_github_cap_is_rejected(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    assert main(["acme", "--days", "400"]) == 2
    assert "365" in capsys.readouterr().err


def test_days_must_be_positive(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    assert main(["acme", "--days", "0"]) == 2
    assert "365" in capsys.readouterr().err
