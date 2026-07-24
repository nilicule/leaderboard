"""Count commits per author from the REST API.

GraphQL's `contributionsCollection(organizationID: ...)` reports every
contribution to a private repository as "restricted" and folds it into
`restrictedContributionsCount` instead of the per-user totals, even for a
token with the `repo` scope. The REST commits endpoint has no such
restriction, so this module re-derives commit counts by listing every repo
in the org and paging through its commit history directly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from urllib.parse import urlparse

from .github import REST_ROOT, GitHubClient


async def _list_org_repos(api: GitHubClient, org: str) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        response = await api.request(
            "GET",
            f"{REST_ROOT}/orgs/{org}/repos",
            params={"per_page": 100, "page": page, "type": "all"},
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def _parse_pushed_at(repo: dict) -> datetime | None:
    pushed_at = repo.get("pushed_at")
    if not pushed_at:
        return None
    return datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))


async def _count_repo_commits(
    api: GitHubClient,
    full_name: str,
    since: datetime,
    until: datetime,
    tally: dict[str, int],
    lock: asyncio.Lock,
    semaphore: asyncio.Semaphore,
) -> None:
    url = f"{REST_ROOT}/repos/{full_name}/commits"
    params = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "per_page": 100,
    }

    async with semaphore:
        while url is not None:
            response = await api.request("GET", url, params=params)
            if response.status_code in (404, 409):
                return
            response.raise_for_status()

            counts: dict[str, int] = {}
            for commit in response.json():
                author = commit.get("author")
                if not author:
                    continue
                login = author.get("login")
                if not login:
                    continue
                key = login.lower()
                counts[key] = counts.get(key, 0) + 1

            if counts:
                async with lock:
                    for key, count in counts.items():
                        tally[key] = tally.get(key, 0) + count

            next_url = response.links.get("next", {}).get("url")
            if next_url and urlparse(next_url).netloc != "api.github.com":
                # An authenticated request only ever goes back to the API host
                # it came from; anything else could be leaking the token to a
                # third party via a spoofed Link header.
                next_url = None
            url = next_url
            params = None


async def find_scannable_repos(
    api: GitHubClient, org: str, since: datetime
) -> list[dict]:
    """List org repos, keeping only those that could contain a commit since `since`.

    Exposed separately so callers (e.g. the CLI) can report how many repos
    will be scanned before the, potentially slow, per-repo commit walk starts.
    """
    repos = await _list_org_repos(api, org)
    return [
        repo
        for repo in repos
        if not repo.get("fork")
        and (pushed_at := _parse_pushed_at(repo)) is not None
        and pushed_at >= since
    ]


async def count_org_commits(
    api: GitHubClient,
    repos: list[dict],
    since: datetime,
    until: datetime,
    concurrency: int = 8,
) -> tuple[dict[str, int], int]:
    """Return (lowercased-login -> commit count, number of repos that failed).

    A repo whose request errors (rate limit exhaustion, a transient 5xx, a
    network error, ...) is counted as failed rather than aborting the whole
    scan — losing a handful of repos out of hundreds is far better than
    discarding the entire run. Pass `repos` as returned by
    `find_scannable_repos`.
    """
    tally: dict[str, int] = {}
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(concurrency)

    results = await asyncio.gather(
        *(
            _count_repo_commits(
                api, repo["full_name"], since, until, tally, lock, semaphore
            )
            for repo in repos
        ),
        return_exceptions=True,
    )

    failed = sum(1 for result in results if isinstance(result, BaseException))
    return tally, failed
