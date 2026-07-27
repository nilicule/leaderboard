import json
import re
from datetime import datetime, timezone

import httpx
import pytest

from leaderboard.activity import (
    SEARCH_BATCH_SIZE,
    Activity,
    build_activity_query,
    count_org_activity,
)
from leaderboard.github import GitHubClient, GitHubError

SINCE = datetime(2026, 4, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _counts(**aliases):
    return {"data": {alias: {"issueCount": n} for alias, n in aliases.items()}}


# --- build_activity_query: aliasing, scoping, escaping ---


def test_query_aliases_three_searches_per_login():
    query = build_activity_query(["alice", "bob"], "acme", SINCE, UNTIL)
    for alias in ("p0:", "r0:", "i0:", "p1:", "r1:", "i1:"):
        assert alias in query


def test_query_scopes_every_search_to_the_organisation():
    query = build_activity_query(["alice"], "acme", SINCE, UNTIL)
    assert query.count("org:acme") == 3


def test_query_bounds_every_search_by_the_window():
    query = build_activity_query(["alice"], "acme", SINCE, UNTIL)
    window = "created:2026-04-01T00:00:00+00:00..2026-07-01T00:00:00+00:00"
    assert query.count(window) == 3


def test_pull_requests_and_issues_are_searched_by_author():
    query = build_activity_query(["alice"], "acme", SINCE, UNTIL)
    assert "author:alice" in query
    assert "type:pr" in query
    assert "type:issue" in query


def test_reviews_are_searched_by_reviewed_by_not_by_author():
    # A review is a contribution to someone else's pull request, so the author
    # qualifier would count the wrong thing entirely.
    query = build_activity_query(["alice"], "acme", SINCE, UNTIL)
    review_line = next(line for line in query.splitlines() if line.startswith("  r0:"))
    assert "reviewed-by:alice" in review_line
    assert "author:alice" not in review_line


def test_search_strings_are_escaped_rather_than_interpolated_raw():
    query = build_activity_query(['a"b'], "acme", SINCE, UNTIL)
    assert '\\"' in query


# --- count_org_activity: tallying, batching, fail-soft behaviour ---


@pytest.mark.asyncio
async def test_counts_pull_requests_reviews_and_issues_per_member():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_counts(p0=9, r0=4, i0=2, p1=1, r1=0, i1=0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, failed = await count_org_activity(
            api, ["alice", "bob"], "acme", SINCE, UNTIL
        )

    assert counts["alice"] == Activity(pull_requests=9, reviews=4, issues=2)
    assert counts["bob"] == Activity(pull_requests=1, reviews=0, issues=0)
    assert failed == 0


@pytest.mark.asyncio
async def test_counts_are_keyed_by_lowercased_login():
    # The commit tally is keyed the same way, so the two sources join cleanly.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_counts(p0=1, r0=0, i0=0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, failed = await count_org_activity(api, ["AliceN2G"], "acme", SINCE, UNTIL)

    assert counts == {"alicen2g": Activity(pull_requests=1, reviews=0, issues=0)}


@pytest.mark.asyncio
async def test_logins_are_batched_across_several_requests():
    logins = [f"user{i}" for i in range(SEARCH_BATCH_SIZE + 1)]
    batches = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        batches.append(len(re.findall(r"^  p\d+:", query, re.MULTILINE)))
        return httpx.Response(
            200,
            json=_counts(
                **{
                    f"{kind}{index}": 1
                    for index in range(SEARCH_BATCH_SIZE)
                    for kind in "pri"
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, failed = await count_org_activity(api, logins, "acme", SINCE, UNTIL)

    assert batches == [SEARCH_BATCH_SIZE, 1]
    assert counts["user0"] == Activity(pull_requests=1, reviews=1, issues=1)


@pytest.mark.asyncio
async def test_a_member_github_returns_no_counts_for_is_reported_as_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"p0": {"issueCount": 3}, "r0": None, "i0": {"issueCount": 0}},
                "errors": [{"message": "timed out"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, failed = await count_org_activity(api, ["alice"], "acme", SINCE, UNTIL)

    assert "alice" not in counts
    assert failed == 1


@pytest.mark.asyncio
async def test_a_failing_batch_does_not_abort_the_whole_scan():
    logins = [f"user{i}" for i in range(SEARCH_BATCH_SIZE + 1)]
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(502, json={"message": "boom"})
        return httpx.Response(200, json=_counts(p0=7, r0=0, i0=0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, failed = await count_org_activity(api, logins, "acme", SINCE, UNTIL)

    assert failed == SEARCH_BATCH_SIZE
    assert counts[logins[SEARCH_BATCH_SIZE]] == Activity(pull_requests=7)


@pytest.mark.asyncio
async def test_rejected_credentials_are_fatal_rather_than_fail_soft():
    # A bad token yields zeroes for everyone, which would silently look like a
    # quiet organisation rather than a configuration error.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        with pytest.raises(GitHubError, match="repo"):
            await count_org_activity(api, ["alice"], "acme", SINCE, UNTIL)
