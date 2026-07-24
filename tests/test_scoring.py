from leaderboard.models import Contributions, Member
from leaderboard.scoring import rank_members, score_member


def make_member(login: str, commits: int = 0, prs: int = 0, reviews: int = 0, issues: int = 0) -> Member:
    return Member(
        login=login,
        name=None,
        avatar_url="",
        profile_url="",
        contributions=Contributions(
            commits=commits, pull_requests=prs, reviews=reviews, issues=issues
        ),
    )


def test_score_is_the_weighted_sum():
    member = make_member("a", commits=10, prs=2, reviews=3, issues=4)
    # 10*1 + 2*5 + 3*3 + 4*2 == 10 + 10 + 9 + 8 == 37
    assert score_member(member) == 37


def test_score_of_inactive_member_is_zero():
    assert score_member(make_member("idle")) == 0


def test_ranking_orders_by_descending_score():
    ranked = rank_members([make_member("low", commits=1), make_member("high", commits=99)])
    assert [s.member.login for s in ranked] == ["high", "low"]
    assert [s.rank for s in ranked] == [1, 2]


def test_equal_scores_break_on_commit_count():
    # Both score 10: one via 10 commits, one via 2 pull requests.
    ranked = rank_members([make_member("prs", prs=2), make_member("commits", commits=10)])
    assert [s.member.login for s in ranked] == ["commits", "prs"]
    assert {s.score for s in ranked} == {10}


def test_equal_score_and_commits_break_on_login_case_insensitively():
    ranked = rank_members([make_member("Zoe", commits=5), make_member("adam", commits=5)])
    assert [s.member.login for s in ranked] == ["adam", "Zoe"]


def test_empty_input_produces_empty_ranking():
    assert rank_members([]) == []


def test_ranking_does_not_mutate_the_input_list():
    members = [make_member("b", commits=1), make_member("a", commits=99)]
    original = list(members)
    rank_members(members)
    assert members == original
