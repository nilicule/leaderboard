import httpx
import pytest

from leaderboard.github import (
    BATCH_SIZE,
    GitHubClient,
    GitHubError,
    _is_rate_limited,
    build_profile_query,
    retry_delay,
)
from leaderboard.models import Member


def test_query_aliases_every_login():
    query = build_profile_query(["alice", "bob"])
    assert "u0: user(login: \"alice\")" in query
    assert "u1: user(login: \"bob\")" in query


def test_query_requests_only_the_profile_details_the_report_displays():
    # Contribution counts all come from the REST scan and the search API now,
    # because contributionsCollection cannot see private repositories.
    query = build_profile_query(["alice"])
    for field in ("login", "name", "avatarUrl", "url"):
        assert field in query
    assert "contributionsCollection" not in query


def test_logins_are_escaped_rather_than_interpolated_raw():
    # A login can never contain a quote, but the query must not be breakable.
    query = build_profile_query(['a"b'])
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
        # Missing "login" — must not raise a bare KeyError.
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
async def test_fetch_profiles_fills_in_display_name_and_avatar():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "u0": {
                        "login": "alice",
                        "name": "Alice",
                        "avatarUrl": "https://example.test/alice.png",
                        "url": "https://github.com/alice",
                    }
                }
            },
        )

    members = [Member(login="alice", name=None, avatar_url="", profile_url="")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(token="fake-token", client=http_client)
        await client.fetch_profiles(members)

    assert members[0].name == "Alice"
    assert members[0].avatar_url == "https://example.test/alice.png"
    assert members[0].profile_url == "https://github.com/alice"
    assert members[0].profile_missing is False


@pytest.mark.asyncio
async def test_a_member_github_returns_no_profile_for_is_flagged_not_dropped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"u0": None}})

    members = [Member(login="ghost", name=None, avatar_url="", profile_url="")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GitHubClient(token="fake-token", client=http_client)
        await client.fetch_profiles(members)

    assert members[0].profile_missing is True
    assert members[0].login == "ghost"
