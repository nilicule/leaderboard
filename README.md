# GitHub Org Activity Leaderboard

Ranks the members of a GitHub organisation by their contribution activity in
that organisation's repositories, and writes a self-contained HTML report
showing each member's avatar and profile achievement badges.

## Requirements

- Python 3.13 or newer
- [uv](https://docs.astral.sh/uv/)
- A `GITHUB_TOKEN` in the environment with the `read:org` and `repo` scopes.
  `read:org` enumerates members; `repo` reads commits, pull requests, reviews
  and issues from private repositories, which is where most of the signal lives
  in a company org.

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

Nothing on the leaderboard comes from GitHub's `contributionsCollection`, the
obvious API for the job, because it does **not** report contributions to
private repositories. GitHub folds them into `restrictedContributionsCount`
instead — a count of work it declines to attribute. On an org whose
repositories are all private that zeroes every column for every member, no
matter what scopes the token holds; the only way to lift it is for each member
to enable *Include private contributions on my profile* in their own account
settings, which an org admin cannot do on their behalf. Both sources below
sidestep it.

**Commits** and **repos** are counted from the REST repository API: every org
repo pushed within the window is scanned via `/repos/{owner}/{repo}/commits`,
and commits are tallied by author login. Only commits whose email is linked to
a GitHub account can be attributed; unlinked commits are skipped. Forks are
excluded from the scan entirely — a fork's commit history is the upstream
project's history, so scanning it would credit upstream work to this org. Merge
commits are counted (GitHub's own contribution graph counts them too, and they
are a meaningful share of commits in an active org), and only commits reachable
from each repository's default branch are seen, since that is all the endpoint
returns.

**Pull requests, reviews and issues** are counted with the search API, which
with the `repo` scope matches issues and pull requests in every private
repository the token can read: `author:LOGIN org:ORG type:pr`, `reviewed-by:`,
and `type:issue`, each bounded by `created:` over the window. Three aliased
searches per member are batched into one GraphQL request.

Search has no qualifier for the date a *review* was submitted, so reviews are
counted as pull requests created in the window that the member reviewed. On a
year of a busy org, checked against the review timestamps themselves, that came
to 8 of a true 8 for one member and 508 of a true 519 for another. The
alternative, `updated:`, overshot by 50% and 41% — a pull request touched
during the window may well have been reviewed long before it.

### Reading the numbers against a GitHub profile

A profile page's "N contributions in the last year" counts commits, pull
requests, reviews and issues *together*, across all of GitHub. The Commits
column here counts commits only, in this org only, so the profile figure is
normally the larger of the two. Compare it against the sum of the Commits, PRs,
Reviews and Issues columns instead.

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

111 tests covering the scoring maths, the achievement parser (against a saved
fixture page), the commit counter, the activity search, the GraphQL query
builders and the CLI. None of them touch the network.
