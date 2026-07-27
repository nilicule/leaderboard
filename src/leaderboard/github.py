"""Async GitHub REST and GraphQL access."""

from __future__ import annotations

import asyncio
import json

import httpx

from .models import Member, Organization

REST_ROOT = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"

# Number of users aliased into a single GraphQL request. Twenty keeps the
# request comfortably inside GitHub's node limit while cutting round trips.
BATCH_SIZE = 20

MAX_ATTEMPTS = 3
MAX_RETRY_DELAY = 60.0

# Only what the report displays. Every contribution count comes from the REST
# commit scan (commits.py) and the search API (activity.py) instead, because
# contributionsCollection reports nothing at all for private repositories.
_PROFILE_FRAGMENT = """
fragment Profile on User {
  login
  name
  avatarUrl(size: 96)
  url
}
"""


class GitHubError(Exception):
    """An error worth showing to the operator verbatim."""


def build_profile_query(logins: list[str]) -> str:
    """Build a GraphQL query aliasing one user selection per login."""
    selections = "\n".join(
        f"  u{index}: user(login: {json.dumps(login)}) {{ ...Profile }}"
        for index, login in enumerate(logins)
    )
    return "query {\n" + selections + "\n}\n" + _PROFILE_FRAGMENT


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
    if response.status_code != 403:
        return False
    if response.headers.get("X-RateLimit-Remaining") == "0":
        return True
    # GitHub's secondary rate limit returns 403 with a non-zero remaining
    # count; a Retry-After header or a message naming it is the only signal.
    if response.headers.get("Retry-After"):
        return True
    try:
        body = response.text
    except Exception:
        body = ""
    return "secondary rate limit" in body.lower()


class GitHubClient:
    """Thin async wrapper over the GitHub REST and GraphQL APIs."""

    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._client = client
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(MAX_ATTEMPTS):
            response = await self._client.request(
                method, url, headers=self._headers, follow_redirects=True, **kwargs
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
        response = await self.request("GET", f"{REST_ROOT}/orgs/{org}")
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
        login = data.get("login")
        if not login:
            raise GitHubError(
                f"GitHub returned an unexpected response for organisation '{org}' "
                "(missing 'login')."
            )
        return Organization(
            login=login,
            name=data.get("name") or login,
            avatar_url=data.get("avatar_url", ""),
        )

    async def list_members(self, org: str) -> list[Member]:
        members: list[Member] = []
        page = 1
        while True:
            response = await self.request(
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

    async def fetch_profiles(self, members: list[Member]) -> None:
        """Populate each member's display name, avatar and profile URL in place.

        A member GitHub returns no node for is flagged via `profile_missing`
        rather than dropped, so they still appear on the leaderboard with the
        details the REST member listing already supplied.
        """
        by_login = {member.login: member for member in members}
        logins = list(by_login)

        for start in range(0, len(logins), BATCH_SIZE):
            chunk = logins[start : start + BATCH_SIZE]
            response = await self.request(
                "POST",
                GRAPHQL_URL,
                json={"query": build_profile_query(chunk)},
            )
            if response.status_code in (401, 403):
                raise GitHubError(
                    "GitHub refused to fetch member profiles. "
                    "Check that GITHUB_TOKEN is valid and carries the 'read:org' scope."
                )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or {}

            if not data and body.get("errors"):
                message = body["errors"][0].get("message", "unknown error")
                raise GitHubError(f"GitHub rejected the profile query: {message}")

            for index, login in enumerate(chunk):
                node = data.get(f"u{index}")
                member = by_login[login]
                if not node:
                    member.profile_missing = True
                    continue
                member.name = node.get("name")
                member.avatar_url = node.get("avatarUrl") or member.avatar_url
                member.profile_url = node.get("url") or member.profile_url
