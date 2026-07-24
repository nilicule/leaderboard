from datetime import timedelta

import pytest

from leaderboard.cli import apply_commit_counts, main, parse_args, resolve_window
from leaderboard.models import Contributions, Member


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


def _member(login, commits=0):
    return Member(
        login=login,
        name=None,
        avatar_url="",
        profile_url="",
        contributions=Contributions(commits=commits, pull_requests=9, reviews=9, issues=9),
    )


def test_apply_commit_counts_replaces_commits_matching_lowercased_login():
    members = [_member("Alice", commits=3)]
    apply_commit_counts(members, {"alice": 42})
    assert members[0].contributions.commits == 42


def test_apply_commit_counts_defaults_to_zero_when_absent():
    members = [_member("bob", commits=7)]
    apply_commit_counts(members, {})
    assert members[0].contributions.commits == 0


def test_apply_commit_counts_leaves_other_contribution_fields_untouched():
    members = [_member("carol", commits=1)]
    apply_commit_counts(members, {"carol": 5})
    contributions = members[0].contributions
    assert (contributions.pull_requests, contributions.reviews, contributions.issues) == (9, 9, 9)
