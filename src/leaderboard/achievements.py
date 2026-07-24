"""Read achievement badges from a GitHub profile page.

GitHub exposes achievements through neither the REST nor the GraphQL API, so
the profile page is the only source. That markup is not a public interface and
may change without notice, which is why every failure here degrades to an empty
badge list rather than propagating.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .models import Achievement

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
