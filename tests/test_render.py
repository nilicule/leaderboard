from datetime import datetime, timezone

import httpx
import pytest

from leaderboard.models import Achievement, Contributions, Member, Organization, ScoredMember
from leaderboard.render import PLACEHOLDER_IMAGE, build_image_cache, render_html

ORG = Organization(login="acme", name="Acme", avatar_url="https://x/org.png")
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
        scrape_failures=0,
        repo_failures=0,
        activity_failures=0,
        image_failures=0,
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


def test_report_with_no_cached_images_still_makes_no_external_requests():
    # This is the test that actually proves the offline guarantee: previous
    # coverage only ever exercised a fully-populated cache, so a fallback to
    # the remote URL on a failed download would have gone unnoticed.
    badge = Achievement(
        name="Pull Shark", slug="pull-shark", image_url="https://x/shark.png", tier="bronze", count=2
    )
    html = render([scored("alice", 1, 100, [badge])], images={})
    assert 'src="http' not in html


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


def test_activity_failures_are_disclosed():
    html = render([scored("alice", 1, 10)], activity_failures=2)
    assert "2 members" in html


def test_no_activity_failure_notice_when_zero():
    html = render([scored("alice", 1, 10)], activity_failures=0)
    assert "could not be searched" not in html


def test_scrape_failures_are_disclosed():
    html = render([scored("alice", 1, 10)], scrape_failures=3)
    assert "Achievement badges could not be read for 3" in html


def test_no_scrape_failure_notice_when_zero():
    html = render([scored("alice", 1, 10)], scrape_failures=0)
    assert "Achievement badges could not be read" not in html


def test_repo_failures_are_disclosed():
    html = render([scored("alice", 1, 10)], repo_failures=2)
    assert "Commits could not be read for 2 repositories" in html


def test_repo_failures_singular_wording():
    html = render([scored("alice", 1, 10)], repo_failures=1)
    assert "Commits could not be read for 1 repository." in html


def test_no_repo_failure_notice_when_zero():
    html = render([scored("alice", 1, 10)], repo_failures=0)
    assert "Commits could not be read for" not in html


def test_image_failures_are_disclosed():
    html = render([scored("alice", 1, 10)], image_failures=4)
    assert "4 image" in html
    assert "placeholder" in html.lower()


def test_no_image_failure_notice_when_zero():
    html = render([scored("alice", 1, 10)], image_failures=0)
    assert "could not be embedded" not in html


def test_login_is_escaped_rather_than_injected():
    evil = scored("alice", 1, 10)
    evil.member.name = "<script>alert(1)</script>"
    html = render([evil])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_leaderboard_renders_a_placeholder():
    html = render([])
    assert "No members" in html


def test_a_member_the_activity_search_missed_is_marked_in_the_table():
    # Their zeroes would otherwise be indistinguishable from a real zero, and
    # they still have a genuine REST commit count, so "no data" overstates it.
    entry = scored("alice", 1, 10)
    entry.member.activity_missing = True
    html = render([entry])
    assert "no PR/review/issue data" in html
    assert "\u00b7 no data" not in html


@pytest.mark.asyncio
async def test_image_cache_embeds_as_data_uris():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x89PNG-bytes", headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache, failures = await build_image_cache(client, ["https://x/a.png", "https://x/a.png"])

    assert list(cache) == ["https://x/a.png"]
    assert cache["https://x/a.png"].startswith("data:image/png;base64,")
    assert failures == 0


@pytest.mark.asyncio
async def test_image_cache_falls_back_to_a_placeholder_on_failure():
    # Falling back to the remote URL would silently break the "renders fully
    # offline" guarantee on a single flaky download; a placeholder keeps it.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache, failures = await build_image_cache(client, ["https://x/a.png"])

    assert cache["https://x/a.png"] == PLACEHOLDER_IMAGE
    assert not cache["https://x/a.png"].startswith("http")
    assert failures == 1


@pytest.mark.asyncio
async def test_image_cache_falls_back_to_a_placeholder_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache, failures = await build_image_cache(client, ["https://x/a.png"])

    assert cache["https://x/a.png"] == PLACEHOLDER_IMAGE
    assert failures == 1


@pytest.mark.asyncio
async def test_image_cache_ignores_empty_urls():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x"))
    ) as client:
        cache, failures = await build_image_cache(client, ["", None])

    assert cache == {}
    assert failures == 0
