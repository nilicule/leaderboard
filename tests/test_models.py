from leaderboard.models import Achievement, Contributions, Member, Organization


def test_contributions_default_to_zero():
    c = Contributions()
    assert (c.commits, c.pull_requests, c.reviews, c.issues) == (0, 0, 0, 0)
    assert c.repositories == 0


def test_member_defaults_are_not_shared_between_instances():
    a = Member(login="a", name=None, avatar_url="", profile_url="")
    b = Member(login="b", name=None, avatar_url="", profile_url="")
    a.achievements.append(
        Achievement(name="Pull Shark", slug="pull-shark", image_url="", tier="bronze", count=2)
    )
    assert a.achievements != b.achievements
    assert b.achievements == []
    assert a.profile_missing is False
    assert a.activity_missing is False


def test_organization_fields():
    org = Organization(login="acme", name="Acme", avatar_url="https://x/y.png")
    assert (org.login, org.name) == ("acme", "Acme")
