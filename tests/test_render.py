from datetime import datetime, timezone

import httpx
import pytest

from leaderboard.models import Achievement, Contributions, Member, Organization, ScoredMember
from leaderboard.render import build_image_cache, render_html

ORG = Organization(login="acme", name="Acme", node_id="O_1", avatar_url="https://x/org.png")
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def scored(login: str, rank: int, score: int, badges=()) -> ScoredMember:
    member = Member(
        login=login,
        name=login.title(),
        avatar_url=f"https://x/{login}.png",
        profile_url=f"https://github.com/{login}",
        contributions=Contributions(commits=score, pull_requests=1, reviews=2, issues=3),
        achievements=list(badges),
    )
    return ScoredMember(member=member, score=score, rank=rank)


def render(ranked, images=None, **kwargs) -> str:
    defaults = dict(
        images=images or {},
        days=365,
        generated_at=NOW,
        restricted_total=0,
        scrape_failures=0,
    )
    return render_html(ORG, ranked, **{**defaults, **kwargs})


def test_report_makes_no_external_requests():
    badge = Achievement(
        name="Pull Shark", slug="pull-shark", image_url="https://x/shark.png", tier="bronze", count=2
    )
    html = render(
        [scored("alice", 1, 100, [badge])],
        images={
            "https://x/alice.png": "data:image/png;base64,AAAA",
            "https://x/shark.png": "data:image/png;base64,BBBB",
            "https://x/org.png": "data:image/png;base64,CCCC",
        },
    )
    assert "https://x/alice.png" not in html
    assert "data:image/png;base64,AAAA" in html
    for forbidden in ("<script src=", "<link rel=\"stylesheet\"", "cdn.", "fonts.googleapis"):
        assert forbidden not in html


def test_every_member_gets_an_avatar():
    # Top 3 appear twice: once on the podium, once in the full table.
    ranked = [scored("alice", 1, 10), scored("bob", 2, 5)]
    html = render(ranked)
    assert html.count('class="avatar"') == 4  # 2 podium + 2 table
    for entry in ranked:
        assert f'alt="{entry.member.display_name}"' in html


def test_table_lists_every_member_including_the_podium():
    ranked = [scored(name, i + 1, 100 - i) for i, name in enumerate("abcd")]
    html = render(ranked)
    assert html.count('class="avatar"') == 7  # 3 podium + 4 table


def test_achievements_render_with_tier_and_multiplier():
    badge = Achievement(
        name="Pull Shark", slug="pull-shark", image_url="https://x/s.png", tier="gold", count=4
    )
    html = render([scored("alice", 1, 10, [badge])])
    assert "tier-gold" in html
    assert "x4" in html
    assert "Pull Shark" in html


def test_member_without_achievements_renders_no_badge_elements():
    html = render([scored("alice", 1, 10)])
    assert "alice" in html
    # "badges" (the always-present wrapper) contains "badge" as a substring, so
    # match the per-badge element's own class value precisely rather than the
    # wrapper's.
    assert '<span class="badge"' not in html
    assert '<span class="badge ' not in html


def test_scores_and_metrics_appear():
    html = render([scored("alice", 1, 42)])
    assert "42" in html
    assert "Alice" in html
    assert "@alice" in html


def test_restricted_contributions_are_disclosed():
    html = render([scored("alice", 1, 10)], restricted_total=17)
    assert "17" in html
    assert "hidden" in html.lower()


def test_no_disclosure_when_nothing_was_restricted():
    html = render([scored("alice", 1, 10)], restricted_total=0)
    assert "hidden from this token" not in html.lower()


def test_scrape_failures_are_disclosed():
    html = render([scored("alice", 1, 10)], scrape_failures=3)
    assert "3" in html


def test_login_is_escaped_rather_than_injected():
    evil = scored("alice", 1, 10)
    evil.member.name = "<script>alert(1)</script>"
    html = render([evil])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_leaderboard_renders_a_placeholder():
    html = render([])
    assert "No members" in html


@pytest.mark.asyncio
async def test_image_cache_embeds_as_data_uris():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG-bytes", headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = await build_image_cache(client, ["https://x/a.png", "https://x/a.png"])

    assert list(cache) == ["https://x/a.png"]
    assert cache["https://x/a.png"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_image_cache_falls_back_to_the_remote_url_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = await build_image_cache(client, ["https://x/a.png"])

    assert cache["https://x/a.png"] == "https://x/a.png"


@pytest.mark.asyncio
async def test_image_cache_ignores_empty_urls():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x"))
    ) as client:
        assert await build_image_cache(client, ["", None]) == {}
