"""Command-line entry point for the organisation leaderboard."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .achievements import fetch_all_achievements
from .activity import Activity, count_org_activity
from .commits import count_org_commits, find_scannable_repos
from .github import GitHubClient, GitHubError
from .models import Member, Organization, ScoredMember
from .render import build_image_cache, render_html
from .scoring import rank_members

MAX_DAYS = 365
EXIT_OK = 0
EXIT_NO_DATA = 1
EXIT_CONFIG = 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="leaderboard",
        description="Rank GitHub organisation members by contribution activity.",
    )
    parser.add_argument("org", help="Organisation login, e.g. net2grid")
    parser.add_argument(
        "--days",
        type=int,
        default=MAX_DAYS,
        help=f"Size of the trailing window in days (default: {MAX_DAYS}, maximum: {MAX_DAYS})",
    )
    parser.add_argument(
        "--output", default="leaderboard.html", help="Where to write the report"
    )
    parser.add_argument(
        "--no-achievements",
        action="store_true",
        help="Skip profile scraping for achievement badges",
    )
    parser.add_argument(
        "--open", action="store_true", help="Open the report when finished"
    )
    return parser.parse_args(argv)


def resolve_window(days: int) -> tuple[datetime, datetime]:
    until = datetime.now(timezone.utc)
    return until - timedelta(days=days), until


def apply_commit_counts(
    members: list[Member],
    commit_counts: dict[str, int],
    repo_counts: dict[str, int],
) -> None:
    """Set each member's commit and repository counts from the REST scan.

    Both tallies are keyed by lowercased login. Members absent from a tally
    committed nothing in the window.
    """
    for member in members:
        login = member.login.lower()
        member.contributions = replace(
            member.contributions,
            commits=commit_counts.get(login, 0),
            repositories=repo_counts.get(login, 0),
        )


def apply_activity_counts(
    members: list[Member], activity: dict[str, Activity]
) -> None:
    """Set each member's pull request, review and issue counts from the search.

    A member the search could not cover is flagged via `activity_missing`, so
    the report can distinguish "opened nothing" from "we could not find out".
    """
    for member in members:
        counts = activity.get(member.login.lower())
        member.activity_missing = counts is None
        counts = counts or Activity()
        member.contributions = replace(
            member.contributions,
            pull_requests=counts.pull_requests,
            reviews=counts.reviews,
            issues=counts.issues,
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        print(
            "GITHUB_TOKEN is not set. Export a GitHub token with the 'read:org' "
            "scope (and 'repo' if you want private contributions counted).",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    if not 1 <= args.days <= MAX_DAYS:
        print(
            f"--days must be between 1 and {MAX_DAYS}. GitHub caps a single "
            "contributions query at one year.",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    try:
        return asyncio.run(_run(args, token))
    except GitHubError as error:
        print(str(error), file=sys.stderr)
        return EXIT_CONFIG
    except httpx.HTTPError as error:
        print(f"Network error talking to GitHub: {error}", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return EXIT_CONFIG


async def _run(args: argparse.Namespace, token: str) -> int:
    since, until = resolve_window(args.days)

    async with httpx.AsyncClient(timeout=30.0) as http:
        api = GitHubClient(token, http)

        print(f"Looking up {args.org}…")
        organization = await api.get_organization(args.org)

        members = await api.list_members(args.org)
        if not members:
            print(
                f"No members of '{args.org}' are visible to this token. "
                "A token without the 'read:org' scope sees an empty list.",
                file=sys.stderr,
            )
            return EXIT_NO_DATA
        print(f"Found {len(members)} members.")

        print("Fetching member profiles…")
        await api.fetch_profiles(members)

        repos_to_scan = await find_scannable_repos(api, organization.login, since)
        print(f"Counting commits across {len(repos_to_scan)} repositories…")
        commit_counts, repo_counts, repo_failures = await count_org_commits(
            api, repos_to_scan, since, until
        )
        apply_commit_counts(members, commit_counts, repo_counts)

        print(f"Counting pull requests, reviews and issues for the last {args.days} days…")
        activity, activity_failures = await count_org_activity(
            api, [m.login for m in members], organization.login, since, until
        )
        apply_activity_counts(members, activity)

        scrape_failures = 0
        if args.no_achievements:
            print("Skipping achievements (--no-achievements).")
        else:
            print("Reading achievement badges…")
            scrape_failures = await fetch_all_achievements(http, members)

        ranked = rank_members(members)

        print("Embedding images…")
        images, image_failures = await build_image_cache(
            http,
            [organization.avatar_url]
            + [m.avatar_url for m in members]
            + [b.image_url for m in members for b in m.achievements],
        )

        html = render_html(
            organization,
            ranked,
            images=images,
            days=args.days,
            generated_at=until,
            scrape_failures=scrape_failures,
            repo_failures=repo_failures,
            activity_failures=activity_failures,
            image_failures=image_failures,
        )

    output = Path(args.output)
    write_error = write_report(output, html)
    if write_error is not None:
        return write_error

    _print_summary(
        organization,
        ranked,
        members,
        scrape_failures=scrape_failures,
        repo_failures=repo_failures,
        activity_failures=activity_failures,
        output=output,
    )

    if args.open:
        webbrowser.open(output.resolve().as_uri())
    return EXIT_OK


def write_report(output: Path, html: str) -> int | None:
    """Write the report to disk, returning an exit code on failure, else None.

    By the time this runs the process has already done the org lookup, the
    member list, every GraphQL and REST call, every profile scrape and every
    image download. A bad --output path (typo, missing directory, permission
    error) must not turn that work into an unhandled traceback with nothing
    written anywhere.
    """
    try:
        output.write_text(html, encoding="utf-8")
    except OSError as error:
        print(f"Could not write {output}: {error}", file=sys.stderr)
        return EXIT_CONFIG
    return None


def _print_summary(
    organization: Organization,
    ranked: list[ScoredMember],
    members: list,
    *,
    scrape_failures: int,
    repo_failures: int,
    activity_failures: int,
    output: Path,
) -> None:
    print(f"\n{organization.name} — top {min(len(ranked), 10)}")
    for entry in ranked[:10]:
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(entry.rank, "  ")
        print(
            f"{medal} {entry.rank:>2}. {entry.member.display_name[:28]:<28}"
            f"{entry.score:>7,} pts  "
            f"{entry.member.contributions.commits:>5} commits  "
            f"{len(entry.member.achievements):>2} badges"
        )

    if activity_failures:
        print(
            f"\nNote: pull requests, reviews and issues could not be searched for "
            f"{activity_failures} member(s); those three columns read zero for them."
        )
    missing = sum(1 for m in members if m.profile_missing)
    if missing:
        print(f"Note: GitHub returned no profile for {missing} member(s).")
    if scrape_failures:
        print(f"Note: achievement badges were unavailable for {scrape_failures} member(s).")
    if repo_failures:
        print(
            f"Note: commits could not be read for {repo_failures} "
            f"repositor{'y' if repo_failures == 1 else 'ies'}; those commits are missing from these counts."
        )

    print(f"\nWrote {output}")


if __name__ == "__main__":
    raise SystemExit(main())
