"""Data structures shared across the leaderboard pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Achievement:
    """A badge shown on a GitHub profile, e.g. Pull Shark x2."""

    name: str
    slug: str
    image_url: str
    tier: str | None
    count: int


@dataclass(frozen=True)
class Contributions:
    """Contribution counts for one member, scoped to one organisation."""

    commits: int = 0
    pull_requests: int = 0
    reviews: int = 0
    issues: int = 0
    repositories: int = 0


@dataclass
class Member:
    """An organisation member and everything gathered about them."""

    login: str
    name: str | None
    avatar_url: str
    profile_url: str
    contributions: Contributions = field(default_factory=Contributions)
    achievements: list[Achievement] = field(default_factory=list)
    # GitHub returned no profile node for this member: their display name and
    # avatar fall back to the REST member listing, everything else is unaffected.
    profile_missing: bool = False
    # The pull request / review / issue search did not cover this member, so
    # those three columns are zero for want of an answer, not because they are.
    activity_missing: bool = False

    @property
    def display_name(self) -> str:
        return self.name or self.login


@dataclass(frozen=True)
class ScoredMember:
    member: Member
    score: int
    rank: int


@dataclass(frozen=True)
class Organization:
    login: str
    name: str
    avatar_url: str
