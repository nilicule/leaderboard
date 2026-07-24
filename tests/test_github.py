from datetime import datetime, timezone

import httpx
import pytest

from leaderboard.github import (
    BATCH_SIZE,
    GitHubClient,
    GitHubError,
    _is_rate_limited,
    build_contributions_query,
    retry_delay,
)
from leaderboard.models import Member


def test_query_aliases_every_login():
    query = build_contributions_query(["alice", "bob"])
    assert "u0: user(login: \"alice\")" in query
    assert "u1: user(login: \"bob\")" in query


def test_query_requests_every_metric_we_score_on():
    query = build_contributions_query(["alice"])
    for field in (
        "totalCommitContributions",
        "totalPullRequestContributions",
        "totalPullRequestReviewContributions",
        "totalIssueContributions",
        "totalRepositoriesWithContributedCommits",
        "restrictedContributionsCount",
    ):
        assert field in query


def test_query_scopes_contributions_to_the_organisation():
    query = build_contributions_query(["alice"])
    assert "organizationID: $orgId" in query
    assert "query($orgId: ID!, $from: DateTime!, $to: DateTime!)" in query


def test_query_also_requests_an_unfiltered_collection_for_restricted_count():
    # organizationID scoping makes GitHub always report restrictedContributionsCount
    # as 0, even when the user has restricted contributions. An additional,
    # unfiltered collection is the only way to see the real count.
    query = build_contributions_query(["alice"])
    assert "contributionsCollection(from: $from, to: $to)" in query
    # The unfiltered collection must not itself carry the org filter.
    unfiltered_line = next(
        line for line in query.splitlines() if "contributionsCollection(from: $from, to: $to)" in line
    )
    assert "organizationID" not in unfiltered_line


def test_logins_are_escaped_rather_than_interpolated_raw():
    # A login can never contain a quote, but the query must not be breakable.
    query = build_contributions_query(['a"b'])
    assert '\\"' in query


def test_batch_size_is_conservative():
    assert 1 <= BATCH_SIZE <= 50


def test_retry_delay_prefers_the_retry_after_header():
    response = httpx.Response(429, headers={"Retry-After": "7"})
    assert retry_delay(response, attempt=0) == 7.0


def test_retry_delay_falls_back_to_exponential_backoff():
    response = httpx.Response(429)
    assert retry_delay(response, attempt=0) == 1.0
    assert retry_delay(response, attempt=2) == 4.0


def test_retry_delay_is_capped():
    response = httpx.Response(429, headers={"Retry-After": "99999"})
    assert retry_delay(response, attempt=0) == 60.0


def test_403_with_zero_remaining_is_rate_limited():
    response = httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})
    assert _is_rate_limited(response) is True


def test_403_with_retry_after_is_a_secondary_rate_limit_even_with_remaining():
    response = httpx.Response(
        403,
        headers={"X-RateLimit-Remaining": "42", "Retry-After": "5"},
    )
    assert _is_rate_limited(response) is True


def test_403_mentioning_secondary_rate_limit_in_body_is_rate_limited():
    response = httpx.Response(
        403,
        headers={"X-RateLimit-Remaining": "42"},
        json={"message": "You have exceeded a secondary rate limit."},
    )
    assert _is_rate_limited(response) is True


@pytest.mark.asyncio
async def test_get_organization_raises_a_clear_error_on_a_malformed_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        # Missing "login" and "node_id" — must not raise a bare KeyError.
        return httpx.Response(200, json={"name": "Acme"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(token="fake", client=http_client)
        with pytest.raises(GitHubError):
            await client.get_organization("acme")


@pytest.mark.asyncio
async def test_request_follows_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/old-name":
            return httpx.Response(
                301, headers={"Location": "https://api.github.com/orgs/new-name"}
            )
        if request.url.path == "/orgs/new-name":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(token="fake", client=http_client)
        response = await client.request("GET", "https://api.github.com/orgs/old-name")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_403_with_remaining_and_no_retry_after_or_secondary_message_is_not_rate_limited():
    response = httpx.Response(
        403,
        headers={"X-RateLimit-Remaining": "42"},
        json={"message": "Forbidden"},
    )
    assert _is_rate_limited(response) is False


@pytest.mark.asyncio
async def test_fetch_contributions_reads_restricted_count_from_the_unfiltered_collection():
    # The org-filtered collection always reports restrictedContributionsCount as 0
    # (that is the bug); the unfiltered one carries the true count.
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "data": {
                "u0": {
                    "login": "alice",
                    "name": "Alice",
                    "avatarUrl": "https://example.test/alice.png",
                    "url": "https://github.com/alice",
                    "contributionsCollection": {
                        "totalCommitContributions": 3,
                        "totalPullRequestContributions": 1,
                        "totalPullRequestReviewContributions": 0,
                        "totalIssueContributions": 0,
                        "totalRepositoriesWithContributedCommits": 2,
                        "restrictedContributionsCount": 0,
                    },
                    "unfilteredContributionsCollection": {
                        "restrictedContributionsCount": 677,
                    },
                }
            }
        }
        return httpx.Response(200, json=body)

    members = [Member(login="alice", name=None, avatar_url="", profile_url="")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(token="fake-token", client=http_client)
        await client.fetch_contributions(
            members,
            org_id="O_1",
            since=datetime(2025, 1, 1, tzinfo=timezone.utc),
            until=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    assert members[0].contributions.restricted == 677
    assert members[0].contributions.commits == 3
