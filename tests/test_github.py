import httpx

from leaderboard.github import BATCH_SIZE, build_contributions_query, retry_delay


def test_query_aliases_every_login():
    query = build_contributions_query(["alice", "bob"])
    assert "u0: user(login: \"alice\")" in query
    assert "u1: user(login: \"bob\")" in query


def test_query_requests_every_metric_we_score_on():
    query = build_contributions_query(["alice"])
    for field in (
        "totalCommitContributions",
        "totalPullRequestContributions",
        "totalPullRequestReviewContributions",
        "totalIssueContributions",
        "totalRepositoriesWithContributedCommits",
        "restrictedContributionsCount",
    ):
        assert field in query


def test_query_scopes_contributions_to_the_organisation():
    query = build_contributions_query(["alice"])
    assert "organizationID: $orgId" in query
    assert "query($orgId: ID!, $from: DateTime!, $to: DateTime!)" in query


def test_logins_are_escaped_rather_than_interpolated_raw():
    # A login can never contain a quote, but the query must not be breakable.
    query = build_contributions_query(['a"b'])
    assert '\\"' in query


def test_batch_size_is_conservative():
    assert 1 <= BATCH_SIZE <= 50


def test_retry_delay_prefers_the_retry_after_header():
    response = httpx.Response(429, headers={"Retry-After": "7"})
    assert retry_delay(response, attempt=0) == 7.0


def test_retry_delay_falls_back_to_exponential_backoff():
    response = httpx.Response(429)
    assert retry_delay(response, attempt=0) == 1.0
    assert retry_delay(response, attempt=2) == 4.0


def test_retry_delay_is_capped():
    response = httpx.Response(429, headers={"Retry-After": "99999"})
    assert retry_delay(response, attempt=0) == 60.0
