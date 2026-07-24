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


def apply_commit_counts(members: list[Member], commit_counts: dict[str, int]) -> None:
    """Replace each member's commit count with the REST-derived figure.

    GraphQL's org-scoped contributionsCollection reports 0 commits for any
    member whose activity is entirely in private repos, so the REST commit
    tally (keyed by lowercased login) is the source of truth for this field.
    Every other Contributions field stays exactly as GraphQL supplied it.
    """
    for member in members:
        count = commit_counts.get(member.login.lower(), 0)
        member.contributions = replace(member.contributions, commits=count)


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

        print(f"Fetching contributions for the last {args.days} days…")
        await api.fetch_contributions(members, organization.node_id, since, until)

        repos_to_scan = await find_scannable_repos(api, organization.login, since)
        print(f"Counting commits across {len(repos_to_scan)} repositories…")
        commit_counts, repo_failures = await count_org_commits(
            api, repos_to_scan, since, until
        )
        apply_commit_counts(members, commit_counts)

        scrape_failures = 0
        if args.no_achievements:
            print("Skipping achievements (--no-achievements).")
        else:
            print("Reading achievement badges…")
            scrape_failures = await fetch_all_achievements(http, members)

        ranked = rank_members(members)
        restricted_total = sum(m.contributions.restricted for m in members)

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
            restricted_total=restricted_total,
            scrape_failures=scrape_failures,
            repo_failures=repo_failures,
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
        restricted_total=restricted_total,
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
    restricted_total: int,
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

    if restricted_total:
        print(
            f"\nNote: PR, review and issue counts are a lower bound. GitHub declines to\n"
            f"attribute {restricted_total:,} contributions for these members across all of GitHub;\n"
            "commit counts come from the repository API and are unaffected."
        )
    missing = sum(1 for m in members if m.stats_missing)
    if missing:
        print(f"Note: GitHub returned no statistics for {missing} member(s).")
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
