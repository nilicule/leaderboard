"""Turn a ranked leaderboard into a single self-contained HTML file."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime

import httpx
from jinja2 import Environment, PackageLoader, select_autoescape

from .models import Organization, ScoredMember

REQUEST_TIMEOUT = 20.0

# A small grey circle, shown in place of an avatar or badge whose download
# failed. Returning this instead of the original remote URL keeps the report
# rendering fully offline even when a single image fetch is flaky — the
# alternative is a data: URI's only real substitute, a request the reader's
# browser would silently make to a third party on open.
PLACEHOLDER_IMAGE = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
        b'<circle cx="48" cy="48" r="48" fill="#8b949e"/></svg>'
    ).decode("ascii")
)

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
) -> tuple[dict[str, str], int]:
    """Map each distinct image URL to a base64 data URI.

    A URL that cannot be downloaded maps to `PLACEHOLDER_IMAGE` instead of the
    original remote URL, so the report keeps rendering fully offline even when
    a download fails. Returns the cache alongside how many URLs fell back to
    the placeholder.
    """
    unique = sorted({url for url in urls if url})
    if not unique:
        return {}, 0

    semaphore = asyncio.Semaphore(concurrency)
    embedded = await asyncio.gather(
        *(_embed(client, url, semaphore) for url in unique)
    )
    failures = sum(1 for result in embedded if result == PLACEHOLDER_IMAGE)
    return dict(zip(unique, embedded)), failures


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
            return PLACEHOLDER_IMAGE
        mime = (response.headers.get("content-type") or "image/png").split(";")[0].strip()
        payload = base64.b64encode(response.content).decode("ascii")
        return f"data:{mime};base64,{payload}"
    except Exception:
        return PLACEHOLDER_IMAGE


def render_html(
    organization: Organization,
    ranked: list[ScoredMember],
    *,
    images: dict[str, str],
    days: int,
    generated_at: datetime,
    scrape_failures: int,
    repo_failures: int = 0,
    activity_failures: int = 0,
    image_failures: int = 0,
) -> str:
    """Render the full report as a single HTML string."""
    template = _environment.get_template("leaderboard.html.j2")
    return template.render(
        organization=organization,
        ranked=ranked,
        podium=ranked[:3],
        images=images,
        days=days,
        generated_at=generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        scrape_failures=scrape_failures,
        repo_failures=repo_failures,
        activity_failures=activity_failures,
        image_failures=image_failures,
        org_avatar=images.get(organization.avatar_url, PLACEHOLDER_IMAGE),
        placeholder=PLACEHOLDER_IMAGE,
    )
