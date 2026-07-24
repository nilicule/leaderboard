"""Async GitHub REST and GraphQL access."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx

from .models import Contributions, Member, Organization

REST_ROOT = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"

# Number of users aliased into a single GraphQL request. Twenty keeps the
# request comfortably inside GitHub's node limit while cutting round trips.
BATCH_SIZE = 20

MAX_ATTEMPTS = 3
MAX_RETRY_DELAY = 60.0

_STATS_FRAGMENT = """
fragment Stats on User {
  login
  name
  avatarUrl(size: 96)
  url
  contributionsCollection(organizationID: $orgId, from: $from, to: $to) {
    totalCommitContributions
    totalPullRequestContributions
    totalPullRequestReviewContributions
    totalIssueContributions
    totalRepositoriesWithContributedCommits
  }
  unfilteredContributionsCollection: contributionsCollection(from: $from, to: $to) {
    restrictedContributionsCount
  }
}
"""


class GitHubError(Exception):
    """An error worth showing to the operator verbatim."""


def build_contributions_query(logins: list[str]) -> str:
    """Build a GraphQL query aliasing one user selection per login."""
    selections = "\n".join(
        f"  u{index}: user(login: {json.dumps(login)}) {{ ...Stats }}"
        for index, login in enumerate(logins)
    )
    return (
        "query($orgId: ID!, $from: DateTime!, $to: DateTime!) {\n"
        f"{selections}\n"
        "}\n"
        f"{_STATS_FRAGMENT}"
    )


def retry_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying, honouring Retry-After when present."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), MAX_RETRY_DELAY)
        except ValueError:
            pass
    return min(2.0**attempt, MAX_RETRY_DELAY)


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    return (
        response.status_code == 403
        and response.headers.get("X-RateLimit-Remaining") == "0"
    )


class GitHubClient:
    """Thin async wrapper over the GitHub REST and GraphQL APIs."""

    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._client = client
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(MAX_ATTEMPTS):
            response = await self._client.request(
                method, url, headers=self._headers, **kwargs
            )
            if not _is_rate_limited(response):
                return response
            if attempt == MAX_ATTEMPTS - 1:
                raise GitHubError(
                    "GitHub rate limit reached and it did not clear after "
                    f"{MAX_ATTEMPTS} attempts. Wait a few minutes and try again."
                )
            await asyncio.sleep(retry_delay(response, attempt))
        raise GitHubError("Unreachable retry state")

    async def get_organization(self, org: str) -> Organization:
        response = await self._request("GET", f"{REST_ROOT}/orgs/{org}")
        if response.status_code == 404:
            raise GitHubError(
                f"No organisation named '{org}' is visible to this token. "
                "Check the spelling; a token without the 'read:org' scope "
                "cannot see private organisations."
            )
        if response.status_code in (401, 403):
            raise GitHubError(
                f"GitHub refused access to organisation '{org}'. "
                "Check that GITHUB_TOKEN is valid and carries the 'read:org' scope."
            )
        response.raise_for_status()
        data = response.json()
        return Organization(
            login=data["login"],
            name=data.get("name") or data["login"],
            node_id=data["node_id"],
            avatar_url=data.get("avatar_url", ""),
        )

    async def list_members(self, org: str) -> list[Member]:
        members: list[Member] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                f"{REST_ROOT}/orgs/{org}/members",
                params={"per_page": 100, "page": page},
            )
            if response.status_code in (401, 403):
                raise GitHubError(
                    "GitHub refused to list organisation members. "
                    "GITHUB_TOKEN needs the 'read:org' scope."
                )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            members.extend(
                Member(
                    login=user["login"],
                    name=None,
                    avatar_url=user.get("avatar_url", ""),
                    profile_url=user.get("html_url", ""),
                )
                for user in batch
            )
            if len(batch) < 100:
                break
            page += 1
        return members

    async def fetch_contributions(
        self,
        members: list[Member],
        org_id: str,
        since: datetime,
        until: datetime,
    ) -> None:
        """Populate each member's contributions in place.

        A member GitHub returns no data for is flagged via `stats_missing`
        rather than dropped, so they still appear on the leaderboard.
        """
        by_login = {member.login: member for member in members}
        logins = list(by_login)

        for start in range(0, len(logins), BATCH_SIZE):
            chunk = logins[start : start + BATCH_SIZE]
            response = await self._request(
                "POST",
                GRAPHQL_URL,
                json={
                    "query": build_contributions_query(chunk),
                    "variables": {
                        "orgId": org_id,
                        "from": since.isoformat(),
                        "to": until.isoformat(),
                    },
                },
            )
            if response.status_code in (401, 403):
                raise GitHubError(
                    "GitHub refused to fetch contributions data. "
                    "Check that GITHUB_TOKEN is valid and carries the 'read:org' and 'repo' scopes."
                )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or {}

            if not data and body.get("errors"):
                message = body["errors"][0].get("message", "unknown error")
                raise GitHubError(f"GitHub rejected the contributions query: {message}")

            for index, login in enumerate(chunk):
                node = data.get(f"u{index}")
                member = by_login[login]
                if not node:
                    member.stats_missing = True
                    continue
                self._apply_node(member, node)

    @staticmethod
    def _apply_node(member: Member, node: dict) -> None:
        collection = node.get("contributionsCollection") or {}
        # organizationID scoping makes GitHub always report restrictedContributionsCount
        # as 0, so the real count is read from the unfiltered collection instead.
        unfiltered = node.get("unfilteredContributionsCollection") or {}
        member.name = node.get("name")
        member.avatar_url = node.get("avatarUrl") or member.avatar_url
        member.profile_url = node.get("url") or member.profile_url
        member.contributions = Contributions(
            commits=collection.get("totalCommitContributions", 0),
            pull_requests=collection.get("totalPullRequestContributions", 0),
            reviews=collection.get("totalPullRequestReviewContributions", 0),
            issues=collection.get("totalIssueContributions", 0),
            repositories=collection.get("totalRepositoriesWithContributedCommits", 0),
            restricted=unfiltered.get("restrictedContributionsCount", 0),
        )
