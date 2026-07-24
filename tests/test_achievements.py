from pathlib import Path

from leaderboard.achievements import parse_achievements

FIXTURE = Path(__file__).parent / "fixtures" / "achievements_torvalds.html"


def load_fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_duplicate_badge_blocks_are_deduplicated_by_slug():
    html = load_fixture()
    # The page renders the badge list twice: ten <img> tags, five achievements.
    assert html.count('data-hovercard-type="achievement"') == 10
    assert len(parse_achievements(html)) == 5


def test_parses_names_slugs_tiers_and_counts():
    parsed = {a.slug: a for a in parse_achievements(load_fixture())}
    assert set(parsed) == {
        "pair-extraordinaire",
        "mars-2020-contributor",
        "arctic-code-vault-contributor",
        "pull-shark",
        "starstruck",
    }

    shark = parsed["pull-shark"]
    assert shark.name == "Pull Shark"
    assert shark.tier == "bronze"
    assert shark.count == 2
    assert shark.image_url.startswith("https://github.githubassets.com/assets/pull-shark")

    pair = parsed["pair-extraordinaire"]
    assert pair.tier == "gold"
    assert pair.count == 4


def test_badge_without_a_tier_label_defaults_to_count_one():
    parsed = {a.slug: a for a in parse_achievements(load_fixture())}
    mars = parsed["mars-2020-contributor"]
    assert mars.tier is None
    assert mars.count == 1


def test_alt_text_prefix_is_stripped_from_the_name():
    names = [a.name for a in parse_achievements(load_fixture())]
    assert all(not n.startswith("Achievement:") for n in names)
    assert "Arctic Code Vault Contributor" in names


def test_page_without_achievements_returns_empty_list():
    assert parse_achievements("<html><body><p>Nothing here</p></body></html>") == []


def test_malformed_html_returns_empty_list_instead_of_raising():
    assert parse_achievements("<<<not really html") == []
    assert parse_achievements("") == []


def test_badge_missing_a_hovercard_url_is_skipped():
    html = '<a><img data-hovercard-type="achievement" alt="Achievement: Mystery" src="x.png"></a>'
    assert parse_achievements(html) == []
