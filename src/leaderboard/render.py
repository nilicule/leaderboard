"""Turn a ranked leaderboard into a single self-contained HTML file."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime

import httpx
from jinja2 import Environment, PackageLoader, select_autoescape

from .models import Organization, ScoredMember

REQUEST_TIMEOUT = 20.0

_environment = Environment(
    loader=PackageLoader("leaderboard", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


async def build_image_cache(
    client: httpx.AsyncClient,
    urls: list[str],
    concurrency: int = 8,
) -> dict[str, str]:
    """Map each distinct image URL to a base64 data URI.

    A URL that cannot be downloaded maps to itself, so the report still shows
    the image when the reader is online rather than showing nothing.
    """
    unique = sorted({url for url in urls if url})
    if not unique:
        return {}

    semaphore = asyncio.Semaphore(concurrency)
    embedded = await asyncio.gather(
        *(_embed(client, url, semaphore) for url in unique)
    )
    return dict(zip(unique, embedded))


async def _embed(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
) -> str:
    try:
        async with semaphore:
            response = await client.get(
                url, follow_redirects=True, timeout=REQUEST_TIMEOUT
            )
        if response.status_code != 200:
            return url
        mime = (response.headers.get("content-type") or "image/png").split(";")[0]
        payload = base64.b64encode(response.content).decode("ascii")
        return f"data:{mime};base64,{payload}"
    except Exception:
        return url


def render_html(
    organization: Organization,
    ranked: list[ScoredMember],
    *,
    images: dict[str, str],
    days: int,
    generated_at: datetime,
    restricted_total: int,
    scrape_failures: int,
) -> str:
    """Render the full report as a single HTML string."""
    template = _environment.get_template("leaderboard.html.j2")
    return template.render(
        organization=organization,
        ranked=ranked,
        podium=ranked[:3],
        rest=ranked[3:],
        images=images,
        days=days,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        restricted_total=restricted_total,
        scrape_failures=scrape_failures,
        org_avatar=images.get(organization.avatar_url, organization.avatar_url),
    )
