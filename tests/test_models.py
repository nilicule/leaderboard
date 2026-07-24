from leaderboard.models import Achievement, Contributions, Member, Organization


def test_contributions_default_to_zero():
    c = Contributions()
    assert (c.commits, c.pull_requests, c.reviews, c.issues) == (0, 0, 0, 0)
    assert (c.repositories, c.restricted) == (0, 0)


def test_member_defaults_are_not_shared_between_instances():
    a = Member(login="a", name=None, avatar_url="", profile_url="")
    b = Member(login="b", name=None, avatar_url="", profile_url="")
    a.achievements.append(
        Achievement(name="Pull Shark", slug="pull-shark", image_url="", tier="bronze", count=2)
    )
    assert a.achievements != b.achievements
    assert b.achievements == []
    assert a.stats_missing is False


def test_organization_fields():
    org = Organization(login="acme", name="Acme", node_id="O_abc", avatar_url="https://x/y.png")
    assert org.node_id == "O_abc"
