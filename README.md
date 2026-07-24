# GitHub Org Activity Leaderboard

Ranks the members of a GitHub organisation by their contribution activity in
that organisation's repositories, and writes a self-contained HTML report
showing each member's avatar and profile achievement badges.

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)
- A `GITHUB_TOKEN` in the environment with the `read:org` scope. Add the `repo`
  scope to include contributions to private repositories; without it GitHub
  hides them and the report says so.

## Usage

```bash
uv sync
uv run leaderboard <org>
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--days N` | `365` | Trailing window in days. Maximum 365 — GitHub caps a single contributions query at one year. |
| `--output PATH` | `leaderboard.html` | Where to write the report |
| `--no-achievements` | off | Skip profile scraping |
| `--open` | off | Open the report when finished |

Example:

```bash
uv run leaderboard net2grid --days 90 --open
```

## Scoring

```
score = commits×1 + pull requests×5 + reviews×3 + issues×2
```

Only contributions to the organisation's own repositories count, so personal
side projects do not affect the ranking. Ties break on commit count, then on
login. Achievements are shown but do not affect the score.

To retune, edit the weight constants at the top of `src/leaderboard/scoring.py`.

## A caveat about achievements

GitHub exposes achievements through neither its REST nor its GraphQL API, so
they are read from the public profile page. That markup is not a public
interface and may change without notice. Scraping therefore fails soft: if it
breaks, affected members simply show no badges, the report says how many were
affected, and everything else keeps working. Run `uv run pytest` to find out
whether the page structure still matches what the parser expects.

## Tests

```bash
uv run pytest
```

Tests cover the scoring maths and the achievement parser (against a saved
fixture page) and use no network access.
