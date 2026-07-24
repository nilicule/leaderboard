from pathlib import Path

from leaderboard.achievements import parse_achievements

FIXTURE = Path(__file__).parent / "fixtures" / "achievements_torvalds.html"


def load_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_duplicate_badge_blocks_are_deduplicated_by_slug():
    html = load_fixture()
    # The page renders the badge list twice: ten <img> tags, five achievements.
    assert html.count('data-hovercard-type="achievement"') == 10
    assert len(parse_achievements(html)) == 5


def test_parses_names_slugs_tiers_and_counts():
    parsed = {a.slug: a for a in parse_achievements(load_fixture())}
    assert set(parsed) == {
        "pair-extraordinaire",
        "mars-2020-contributor",
        "arctic-code-vault-contributor",
        "pull-shark",
        "starstruck",
    }

    shark = parsed["pull-shark"]
    assert shark.name == "Pull Shark"
    assert shark.tier == "bronze"
    assert shark.count == 2
    assert shark.image_url.startswith("https://github.githubassets.com/assets/pull-shark")

    pair = parsed["pair-extraordinaire"]
    assert pair.tier == "gold"
    assert pair.count == 4


def test_badge_without_a_tier_label_defaults_to_count_one():
    parsed = {a.slug: a for a in parse_achievements(load_fixture())}
    mars = parsed["mars-2020-contributor"]
    assert mars.tier is None
    assert mars.count == 1


def test_alt_text_prefix_is_stripped_from_the_name():
    names = [a.name for a in parse_achievements(load_fixture())]
    assert all(not n.startswith("Achievement:") for n in names)
    assert "Arctic Code Vault Contributor" in names


def test_page_without_achievements_returns_empty_list():
    assert parse_achievements("<html><body><p>Nothing here</p></body></html>") == []


def test_malformed_html_returns_empty_list_instead_of_raising():
    assert parse_achievements("<<<not really html") == []
    assert parse_achievements("") == []


def test_badge_missing_a_hovercard_url_is_skipped():
    html = '<a><img data-hovercard-type="achievement" alt="Achievement: Mystery" src="x.png"></a>'
    assert parse_achievements(html) == []


import httpx
import pytest

from leaderboard.achievements import fetch_all_achievements
from leaderboard.models import Member


def make_member(login: str) -> Member:
    return Member(login=login, name=None, avatar_url="", profile_url="")


def client_returning(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_populates_achievements_for_each_member():
    fixture = load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "tab=achievements" in str(request.url)
        return httpx.Response(200, text=fixture)

    members = [make_member("alice"), make_member("bob")]
    async with client_returning(handler) as client:
        failures = await fetch_all_achievements(client, members)

    assert failures == 0
    assert all(len(m.achievements) == 5 for m in members)


@pytest.mark.asyncio
async def test_404_response_yields_empty_achievements_and_is_not_a_failure():
    # GitHub returns 404 on the achievements tab for a user with zero badges,
    # as distinct from a 200 profile page. That is a successful, empty fetch.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    members = [make_member("ghost")]
    async with client_returning(handler) as client:
        failures = await fetch_all_achievements(client, members)

    assert failures == 0
    assert members[0].achievements == []


@pytest.mark.asyncio
async def test_non_404_error_response_still_counts_as_a_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    members = [make_member("ghost")]
    async with client_returning(handler) as client:
        failures = await fetch_all_achievements(client, members)

    assert failures == 1
    assert members[0].achievements == []


@pytest.mark.asyncio
async def test_mixed_batch_404_200_and_connection_error():
    fixture = load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "no-badges" in url:
            return httpx.Response(404, text="Not Found")
        if "broken" in url:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text=fixture)

    members = [make_member("no-badges"), make_member("has-badges"), make_member("broken")]
    async with client_returning(handler) as client:
        failures = await fetch_all_achievements(client, members)

    assert failures == 1
    assert members[0].achievements == []
    assert len(members[1].achievements) == 5
    assert members[2].achievements == []


@pytest.mark.asyncio
async def test_network_error_does_not_abort_the_run():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    members = [make_member("a"), make_member("b")]
    async with client_returning(handler) as client:
        failures = await fetch_all_achievements(client, members)

    assert failures == 2
    assert all(m.achievements == [] for m in members)


@pytest.mark.asyncio
async def test_one_failure_does_not_affect_other_members():
    fixture = load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in str(request.url):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text=fixture)

    members = [make_member("good"), make_member("broken")]
    async with client_returning(handler) as client:
        failures = await fetch_all_achievements(client, members)

    assert failures == 1
    assert len(members[0].achievements) == 5
    assert members[1].achievements == []


@pytest.mark.asyncio
async def test_empty_member_list_reports_no_failures():
    async with client_returning(lambda r: httpx.Response(200, text="")) as client:
        assert await fetch_all_achievements(client, []) == 0
