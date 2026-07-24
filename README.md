# GitHub Org Activity Leaderboard

Ranks the members of a GitHub organisation by their contribution activity in
that organisation's repositories, and writes a self-contained HTML report
showing each member's avatar and profile achievement badges.

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)
- A `GITHUB_TOKEN` in the environment with the `read:org` and `repo` scopes.
  `read:org` enumerates members; `repo` reads commits from private
  repositories, which is where most of the signal lives in a company org.

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

## Where the numbers come from

The two metrics use different GitHub APIs, for a reason worth knowing about.

**Commits** are counted from the REST repository API: every org repo pushed
within the window is scanned via `/repos/{owner}/{repo}/commits`, and commits
are tallied by author login. Only commits whose email is linked to a GitHub
account can be attributed; unlinked commits are skipped.

**Pull requests, reviews and issues** come from the GraphQL
`contributionsCollection`, scoped to the organisation.

The split exists because `contributionsCollection` does **not** report
contributions to private repositories. GitHub returns them as
`restrictedContributionsCount` instead — a count of work it declines to
attribute. On an org whose repositories are all private, that makes the GraphQL
commit count zero for every member, no matter what scopes the token holds. The
only way to surface those contributions through that API is for each member to
enable *Include private contributions on my profile* in their own account
settings, which is not something an org admin can do on their behalf.

Reading commits from the repository API sidesteps this entirely. Pull request,
review and issue counts remain subject to it, so the report states how many
contributions GitHub withheld whenever that number is non-zero — treat those
three columns as a lower bound.

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

68 tests covering the scoring maths, the achievement parser (against a saved
fixture page), the commit counter, the GraphQL query builder and the CLI. None
of them touch the network.
