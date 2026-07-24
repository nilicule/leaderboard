from datetime import datetime, timezone

import httpx
import pytest

from leaderboard.commits import count_org_commits
from leaderboard.github import GitHubClient

SINCE = datetime(2026, 4, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _repo(name, pushed_at="2026-06-15T00:00:00Z"):
    return {"name": name, "full_name": f"acme/{name}", "pushed_at": pushed_at}


def _commit(login):
    return {
        "commit": {"author": {"date": "2026-06-01T00:00:00Z"}},
        "author": {"login": login} if login else None,
    }


@pytest.mark.asyncio
async def test_tallies_commits_across_multiple_repos():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("one"), _repo("two")])
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(200, json=[_commit("alice"), _commit("bob")])
        if request.url.path == "/repos/acme/two/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"alice": 2, "bob": 1}


@pytest.mark.asyncio
async def test_follows_link_header_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("one")])
        if request.url.path == "/repos/acme/one/commits":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=[_commit("alice")])
            return httpx.Response(
                200,
                json=[_commit("bob")],
                headers={
                    "Link": '<https://api.github.com/repos/acme/one/commits?page=2>; rel="next"'
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"bob": 1, "alice": 1}


@pytest.mark.asyncio
async def test_skips_repos_not_pushed_since_the_cutoff():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(
                200,
                json=[
                    _repo("stale", pushed_at="2020-01-01T00:00:00Z"),
                    _repo("never_pushed", pushed_at=None),
                    _repo("fresh"),
                ],
            )
        if request.url.path == "/repos/acme/fresh/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"alice": 1}
    assert "/repos/acme/stale/commits" not in calls
    assert "/repos/acme/never_pushed/commits" not in calls


@pytest.mark.asyncio
async def test_skips_commits_with_no_linked_author():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("one")])
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(200, json=[_commit(None), _commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"alice": 1}


@pytest.mark.asyncio
async def test_a_409_empty_repository_is_skipped_without_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("empty"), _repo("real")])
        if request.url.path == "/repos/acme/empty/commits":
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        if request.url.path == "/repos/acme/real/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"alice": 1}


@pytest.mark.asyncio
async def test_a_404_repository_is_skipped_without_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("gone"), _repo("real")])
        if request.url.path == "/repos/acme/gone/commits":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/repos/acme/real/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"alice": 1}


@pytest.mark.asyncio
async def test_other_errors_propagate():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("broken")])
        if request.url.path == "/repos/acme/broken/commits":
            return httpx.Response(500, json={"message": "boom"})
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        with pytest.raises(httpx.HTTPStatusError):
            await count_org_commits(api, "acme", SINCE, UNTIL)


@pytest.mark.asyncio
async def test_login_matching_is_case_insensitive_via_lowercasing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("one")])
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(
                200, json=[_commit("AliceN2G"), _commit("aliceN2g")]
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"alicen2g": 2}


@pytest.mark.asyncio
async def test_repo_listing_follows_pagination_too():
    page1 = [_repo(f"r{i}") for i in range(100)]
    page2 = [_repo("r100")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            page = request.url.params.get("page", "1")
            if page == "1":
                return httpx.Response(200, json=page1)
            if page == "2":
                return httpx.Response(200, json=page2)
            return httpx.Response(200, json=[])
        if request.url.path.startswith("/repos/acme/") and request.url.path.endswith(
            "/commits"
        ):
            name = request.url.path.split("/")[3]
            if name == "r100":
                return httpx.Response(200, json=[_commit("alice")])
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result = await count_org_commits(api, "acme", SINCE, UNTIL)

    assert result == {"alice": 1}
