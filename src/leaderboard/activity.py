"""Count pull requests, reviews and issues per member from the search API.

`contributionsCollection` folds every private-repository contribution into
`restrictedContributionsCount`, so on an organisation whose repositories are
private it reports zero pull requests, reviews and issues for everyone — the
same blind spot that `commits.py` works around for commits. Search does not
share it: with the `repo` scope, `author:` and `reviewed-by:` match issues and
pull requests in every private repository the token can read.

Search has no qualifier for the date a review was submitted, so reviews are
counted as *pull requests created in the window that this member reviewed*.
Measured against the review timestamps themselves on a year of a busy
organisation, that came to 8 of 8 for one member and 508 of a true 519 for
another; the alternative, `updated:`, overshot by 50% and 41% respectively
because a pull request touched during the window may well have been reviewed
long before it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import httpx

from .github import GRAPHQL_URL, GitHubClient, GitHubError

# Members per GraphQL request. Each one costs three aliased searches, and
# search is expensive enough server-side that far larger batches risk the
# whole request timing out; ten keeps a 47-member org down to five round trips.
SEARCH_BATCH_SIZE = 10


@dataclass(frozen=True)
class Activity:
    """Non-commit contribution counts for one member, scoped to one org."""

    pull_requests: int = 0
    reviews: int = 0
    issues: int = 0


def _search(alias: str, query: str) -> str:
    return f"  {alias}: search(query: {json.dumps(query)}, type: ISSUE) {{ issueCount }}"


def build_activity_query(
    logins: list[str], org: str, since: datetime, until: datetime
) -> str:
    """Build a GraphQL query aliasing three counting searches per login."""
    window = f"created:{since.isoformat()}..{until.isoformat()}"
    scope = f"org:{org} {window}"
    lines = []
    for index, login in enumerate(logins):
        lines.append(_search(f"p{index}", f"author:{login} {scope} type:pr"))
        lines.append(_search(f"r{index}", f"reviewed-by:{login} {scope} type:pr"))
        lines.append(_search(f"i{index}", f"author:{login} {scope} type:issue"))
    return "query {\n" + "\n".join(lines) + "\n}\n"


def _read_batch(data: dict, chunk: list[str]) -> tuple[dict[str, Activity], int]:
    counts: dict[str, Activity] = {}
    failed = 0
    for index, login in enumerate(chunk):
        nodes = [data.get(f"{kind}{index}") for kind in "pri"]
        if any(node is None for node in nodes):
            # A search that times out comes back as a null alias alongside an
            # `errors` entry. Recording it as zero would be indistinguishable
            # from a member who genuinely opened nothing.
            failed += 1
            continue
        pulls, reviews, issues = (node.get("issueCount", 0) for node in nodes)
        counts[login.lower()] = Activity(
            pull_requests=pulls, reviews=reviews, issues=issues
        )
    return counts, failed


async def count_org_activity(
    api: GitHubClient,
    logins: list[str],
    org: str,
    since: datetime,
    until: datetime,
) -> tuple[dict[str, Activity], int]:
    """Return (lowercased-login -> Activity, number of members that failed).

    A batch whose request errors is counted as failed rather than aborting the
    run, matching the commit scan: losing a few members' pull request counts is
    better than discarding the whole report. Rejected credentials are fatal,
    because they would otherwise show up as a uniformly idle organisation.
    """
    counts: dict[str, Activity] = {}
    failed = 0

    for start in range(0, len(logins), SEARCH_BATCH_SIZE):
        chunk = logins[start : start + SEARCH_BATCH_SIZE]
        try:
            response = await api.request(
                "POST",
                GRAPHQL_URL,
                json={"query": build_activity_query(chunk, org, since, until)},
            )
            if response.status_code in (401, 403):
                raise GitHubError(
                    "GitHub refused to search for pull requests, reviews and issues. "
                    "Check that GITHUB_TOKEN is valid and carries the 'repo' scope."
                )
            response.raise_for_status()
            data = response.json().get("data") or {}
        except (httpx.HTTPError, ValueError):
            failed += len(chunk)
            continue

        batch_counts, batch_failed = _read_batch(data, chunk)
        counts.update(batch_counts)
        failed += batch_failed

    return counts, failed
