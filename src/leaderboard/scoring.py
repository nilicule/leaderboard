"""Turn contribution counts into a ranked leaderboard.

Pure functions only. Adjust the weights below to retune the ranking; they are
intentionally not exposed as command-line flags.
"""

from __future__ import annotations

from .models import Member, ScoredMember

COMMIT_WEIGHT = 1
PULL_REQUEST_WEIGHT = 5
REVIEW_WEIGHT = 3
ISSUE_WEIGHT = 2


def score_member(member: Member) -> int:
    """Return the weighted activity score for one member."""
    c = member.contributions
    return (
        c.commits * COMMIT_WEIGHT
        + c.pull_requests * PULL_REQUEST_WEIGHT
        + c.reviews * REVIEW_WEIGHT
        + c.issues * ISSUE_WEIGHT
    )


def rank_members(members: list[Member]) -> list[ScoredMember]:
    """Return members ordered best-first, with ranks starting at 1.

    Ties break on commit count, then on login, so the ordering is total and
    stable from one run to the next.
    """
    ordered = sorted(
        members,
        key=lambda m: (-score_member(m), -m.contributions.commits, m.login.lower()),
    )
    return [
        ScoredMember(member=member, score=score_member(member), rank=rank)
        for rank, member in enumerate(ordered, start=1)
    ]
