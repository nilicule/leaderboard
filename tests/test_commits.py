from datetime import datetime, timezone

import httpx
import pytest

from leaderboard.commits import count_org_commits, find_scannable_repos
from leaderboard.github import GitHubClient

SINCE = datetime(2026, 4, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _repo(name, pushed_at="2026-06-15T00:00:00Z", fork=False):
    return {
        "name": name,
        "full_name": f"acme/{name}",
        "pushed_at": pushed_at,
        "fork": fork,
    }


def _commit(login):
    return {
        "commit": {"author": {"date": "2026-06-01T00:00:00Z"}},
        "author": {"login": login} if login else None,
    }


# --- find_scannable_repos: listing, pagination, recency filter, forks ---


@pytest.mark.asyncio
async def test_lists_repos_pushed_since_the_cutoff():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[_repo("one"), _repo("two")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        repos = await find_scannable_repos(api, "acme", SINCE)

    assert {repo["name"] for repo in repos} == {"one", "two"}


@pytest.mark.asyncio
async def test_skips_repos_not_pushed_since_the_cutoff():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(
                200,
                json=[
                    _repo("stale", pushed_at="2020-01-01T00:00:00Z"),
                    _repo("never_pushed", pushed_at=None),
                    _repo("fresh"),
                ],
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        repos = await find_scannable_repos(api, "acme", SINCE)

    assert {repo["name"] for repo in repos} == {"fresh"}


@pytest.mark.asyncio
async def test_forks_are_excluded_even_if_recently_pushed():
    # A fork's /commits history is the upstream project's history, and its
    # pushed_at bumps on any upstream sync, so it would otherwise pass the
    # recency filter and credit upstream work to this org.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(
                200,
                json=[_repo("upstream-fork", fork=True), _repo("own")],
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        repos = await find_scannable_repos(api, "acme", SINCE)

    assert {repo["name"] for repo in repos} == {"own"}


@pytest.mark.asyncio
async def test_repo_listing_follows_pagination():
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
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        repos = await find_scannable_repos(api, "acme", SINCE)

    assert {repo["name"] for repo in repos} == {f"r{i}" for i in range(101)}


# --- count_org_commits: tallying, pagination, fail-soft behaviour ---


@pytest.mark.asyncio
async def test_tallies_commits_across_multiple_repos():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(200, json=[_commit("alice"), _commit("bob")])
        if request.url.path == "/repos/acme/two/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result, repos, failed = await count_org_commits(
            api, [_repo("one"), _repo("two")], SINCE, UNTIL
        )

    assert result == {"alice": 2, "bob": 1}
    assert failed == 0


@pytest.mark.asyncio
async def test_also_tallies_how_many_repos_each_author_committed_to():
    # totalRepositoriesWithContributedCommits is restricted for private repos
    # just like the commit count is, but the scan already knows the answer.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(200, json=[_commit("alice"), _commit("bob")])
        if request.url.path == "/repos/acme/two/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        _, repos, _ = await count_org_commits(
            api, [_repo("one"), _repo("two")], SINCE, UNTIL
        )

    assert repos == {"alice": 2, "bob": 1}


@pytest.mark.asyncio
async def test_a_repo_is_counted_once_however_many_pages_of_commits_it_has():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/one/commits":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=[_commit("alice")])
            return httpx.Response(
                200,
                json=[_commit("alice")],
                headers={
                    "Link": '<https://api.github.com/repos/acme/one/commits?page=2>; rel="next"'
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, repos, _ = await count_org_commits(api, [_repo("one")], SINCE, UNTIL)

    assert counts == {"alice": 2}
    assert repos == {"alice": 1}


@pytest.mark.asyncio
async def test_follows_link_header_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
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
        result, _, failed = await count_org_commits(api, [_repo("one")], SINCE, UNTIL)

    assert result == {"bob": 1, "alice": 1}


@pytest.mark.asyncio
async def test_pagination_refuses_to_follow_a_next_link_to_another_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(
                200,
                json=[_commit("bob")],
                headers={"Link": '<https://evil.example/steal-token>; rel="next"'},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result, _, failed = await count_org_commits(api, [_repo("one")], SINCE, UNTIL)

    assert result == {"bob": 1}
    assert failed == 0


@pytest.mark.asyncio
async def test_skips_commits_with_no_linked_author():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(200, json=[_commit(None), _commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result, _, failed = await count_org_commits(api, [_repo("one")], SINCE, UNTIL)

    assert result == {"alice": 1}


@pytest.mark.asyncio
async def test_a_409_empty_repository_is_skipped_without_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/empty/commits":
            return httpx.Response(409, json={"message": "Git Repository is empty."})
        if request.url.path == "/repos/acme/real/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result, _, failed = await count_org_commits(
            api, [_repo("empty"), _repo("real")], SINCE, UNTIL
        )

    assert result == {"alice": 1}
    assert failed == 0


@pytest.mark.asyncio
async def test_a_404_repository_is_skipped_without_failing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/gone/commits":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/repos/acme/real/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result, _, failed = await count_org_commits(
            api, [_repo("gone"), _repo("real")], SINCE, UNTIL
        )

    assert result == {"alice": 1}
    assert failed == 0


@pytest.mark.asyncio
async def test_a_failing_repo_does_not_abort_the_whole_scan():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/broken/commits":
            return httpx.Response(500, json={"message": "boom"})
        if request.url.path == "/repos/acme/real/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, _, failed = await count_org_commits(
            api, [_repo("broken"), _repo("real")], SINCE, UNTIL
        )

    assert counts == {"alice": 1}
    assert failed == 1


@pytest.mark.asyncio
async def test_a_repo_whose_request_raises_is_counted_as_failed_not_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/explodes/commits":
            raise httpx.ConnectError("boom", request=request)
        if request.url.path == "/repos/acme/real/commits":
            return httpx.Response(200, json=[_commit("alice"), _commit("bob")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, _, failed = await count_org_commits(
            api, [_repo("explodes"), _repo("real")], SINCE, UNTIL
        )

    assert counts == {"alice": 1, "bob": 1}
    assert failed == 1


@pytest.mark.asyncio
async def test_no_failures_reports_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(200, json=[_commit("alice")])
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, _, failed = await count_org_commits(api, [_repo("one")], SINCE, UNTIL)

    assert counts == {"alice": 1}
    assert failed == 0


@pytest.mark.asyncio
async def test_other_errors_are_fail_soft_too():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/broken/commits":
            return httpx.Response(500, json={"message": "boom"})
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        counts, _, failed = await count_org_commits(api, [_repo("broken")], SINCE, UNTIL)

    assert counts == {}
    assert failed == 1


@pytest.mark.asyncio
async def test_login_matching_is_case_insensitive_via_lowercasing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/one/commits":
            return httpx.Response(
                200, json=[_commit("AliceN2G"), _commit("aliceN2g")]
            )
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        api = GitHubClient(token="fake", client=http)
        result, _, failed = await count_org_commits(api, [_repo("one")], SINCE, UNTIL)

    assert result == {"alicen2g": 2}
