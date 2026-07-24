"""Read achievement badges from a GitHub profile page.

GitHub exposes achievements through neither the REST nor the GraphQL API, so
the profile page is the only source. That markup is not a public interface and
may change without notice, which is why every failure here degrades to an empty
badge list rather than propagating.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from .models import Achievement, Member

_ALT_PREFIX = "Achievement: "
_SLUG_RE = re.compile(r"/achievements/([^/?#]+)")
_TIER_RE = re.compile(r"achievement-tier-label--(\w+)")
_COUNT_RE = re.compile(r"x(\d+)")


def parse_achievements(html: str) -> list[Achievement]:
    """Extract achievement badges from profile page HTML.

    Returns an empty list for malformed input rather than raising. The badge
    list appears twice on a real profile page, so results are deduplicated by
    slug, keeping first occurrence.
    """
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    achievements: list[Achievement] = []
    seen: set[str] = set()

    for img in soup.select('img[data-hovercard-type="achievement"]'):
        slug_match = _SLUG_RE.search(img.get("data-hovercard-url") or "")
        if slug_match is None:
            continue
        slug = slug_match.group(1)
        if slug in seen:
            continue
        seen.add(slug)

        alt = img.get("alt") or ""
        name = alt.removeprefix(_ALT_PREFIX).strip() or slug
        tier, count = _tier_and_count(img)

        achievements.append(
            Achievement(
                name=name,
                slug=slug,
                image_url=img.get("src") or "",
                tier=tier,
                count=count,
            )
        )

    return achievements


def _tier_and_count(img) -> tuple[str | None, int]:
    """Read the tier colour and multiplier from the badge's own container.

    The label lives in the image's immediate parent. Searching any wider scope
    would pick up the neighbouring badges' labels instead.
    """
    parent = img.parent
    if parent is None:
        return None, 1

    label = parent.select_one('span[class*="achievement-tier-label"]')
    if label is None:
        return None, 1

    tier_match = _TIER_RE.search(" ".join(label.get("class") or []))
    count_match = _COUNT_RE.search(label.get_text(strip=True))
    return (
        tier_match.group(1) if tier_match else None,
        int(count_match.group(1)) if count_match else 1,
    )


PROFILE_URL = "https://github.com/{login}?tab=achievements"
USER_AGENT = "leaderboard-script (+https://github.com)"
REQUEST_TIMEOUT = 20.0


async def fetch_all_achievements(
    client: httpx.AsyncClient,
    members: list[Member],
    concurrency: int = 8,
) -> int:
    """Fill in each member's achievements, returning how many fetches failed.

    Scraping is best-effort by design: a member whose page cannot be read or
    parsed simply shows no badges, and the leaderboard is otherwise unaffected.
    """
    if not members:
        return 0

    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *(_fetch_one(client, member.login, semaphore) for member in members)
    )

    failures = 0
    for member, badges in zip(members, results):
        if badges is None:
            member.achievements = []
            failures += 1
        else:
            member.achievements = badges
    return failures


async def _fetch_one(
    client: httpx.AsyncClient,
    login: str,
    semaphore: asyncio.Semaphore,
) -> list[Achievement] | None:
    """Return badges for one login, or None if the page could not be read.

    GitHub returns 404 for the achievements tab of a user who has zero
    badges (as opposed to their bare profile, which is 200), so a 404 here
    is a successful, empty fetch rather than a failure.
    """
    try:
        async with semaphore:
            response = await client.get(
                PROFILE_URL.format(login=login),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=REQUEST_TIMEOUT,
            )
        if response.status_code == 404:
            return []
        if response.status_code != 200:
            return None
        return parse_achievements(response.text)
    except Exception:
        return None
