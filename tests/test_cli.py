from datetime import datetime, timedelta, timezone

import pytest

from leaderboard.activity import Activity
from leaderboard.cli import (
    EXIT_CONFIG,
    apply_activity_counts,
    apply_commit_counts,
    main,
    parse_args,
    resolve_window,
    write_report,
)
from leaderboard.models import Contributions, Member, Organization, ScoredMember


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
    apply_commit_counts(members, {"alice": 42}, {"alice": 6})
    assert members[0].contributions.commits == 42


def test_apply_commit_counts_defaults_to_zero_when_absent():
    members = [_member("bob", commits=7)]
    apply_commit_counts(members, {}, {})
    assert members[0].contributions.commits == 0


def test_apply_commit_counts_also_sets_the_repository_count():
    members = [_member("Alice", commits=3)]
    apply_commit_counts(members, {"alice": 42}, {"alice": 6})
    assert members[0].contributions.repositories == 6


def test_apply_commit_counts_leaves_other_contribution_fields_untouched():
    members = [_member("carol", commits=1)]
    apply_commit_counts(members, {"carol": 5}, {"carol": 2})
    contributions = members[0].contributions
    assert (contributions.pull_requests, contributions.reviews, contributions.issues) == (9, 9, 9)


def test_apply_activity_counts_replaces_pull_requests_reviews_and_issues():
    members = [_member("Alice")]
    apply_activity_counts(members, {"alice": Activity(pull_requests=4, reviews=3, issues=2)})
    contributions = members[0].contributions
    assert (contributions.pull_requests, contributions.reviews, contributions.issues) == (4, 3, 2)


def test_apply_activity_counts_leaves_commits_untouched():
    members = [_member("alice", commits=11)]
    apply_activity_counts(members, {"alice": Activity(pull_requests=4)})
    assert members[0].contributions.commits == 11


def test_apply_activity_counts_flags_a_member_the_search_could_not_cover():
    # Zeroes from a failed search must not read as a member who opened nothing.
    members = [_member("alice"), _member("bob")]
    apply_activity_counts(members, {"alice": Activity(pull_requests=4)})
    assert members[0].activity_missing is False
    assert members[1].activity_missing is True
    assert members[1].contributions.pull_requests == 0


def _organization():
    return Organization(login="acme", name="Acme", avatar_url="")


def _scored(login, rank, score, commits=0):
    member = _member(login, commits=commits)
    return ScoredMember(member=member, score=score, rank=rank)


def test_print_summary_reports_repo_failures_when_nonzero(capsys):
    from leaderboard.cli import _print_summary

    members = [_member("alice")]
    ranked = [_scored("alice", 1, 10)]
    _print_summary(
        _organization(), ranked, members, scrape_failures=0, repo_failures=4,
        activity_failures=0, output="out.html",
    )
    out = capsys.readouterr().out
    assert "4 repositor" in out


def test_print_summary_omits_repo_failure_note_when_zero(capsys):
    from leaderboard.cli import _print_summary

    members = [_member("alice")]
    ranked = [_scored("alice", 1, 10)]
    _print_summary(
        _organization(), ranked, members, scrape_failures=0, repo_failures=0,
        activity_failures=0, output="out.html",
    )
    out = capsys.readouterr().out
    assert "repositor" not in out


def test_print_summary_reports_members_the_activity_search_missed(capsys):
    from leaderboard.cli import _print_summary

    members = [_member("alice")]
    ranked = [_scored("alice", 1, 10)]
    _print_summary(
        _organization(), ranked, members, scrape_failures=0, repo_failures=0,
        activity_failures=3, output="out.html",
    )
    out = capsys.readouterr().out
    assert "3 member" in out


def test_write_report_writes_the_file(tmp_path):
    output = tmp_path / "out.html"
    assert write_report(output, "<html></html>") is None
    assert output.read_text(encoding="utf-8") == "<html></html>"


def test_write_report_guards_an_unwritable_path(tmp_path, capsys):
    # The parent directory does not exist, so write_text raises OSError.
    bad_path = tmp_path / "missing-dir" / "out.html"
    result = write_report(bad_path, "<html></html>")
    assert result == EXIT_CONFIG
    err = capsys.readouterr().err
    assert "Could not write" in err
    assert str(bad_path) in err


