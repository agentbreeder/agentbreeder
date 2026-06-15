from api.routes.analytics import _percentile, _scorecard_from_rows


def test_percentile_p50_p90():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(data, 50) == 30.0
    # Linear interpolation (numpy 'linear' default): p90 of this set is 46.0,
    # not the max. k = (5-1)*0.9 = 3.6 -> 40 + (50-40)*0.6.
    assert _percentile(data, 90) == 46.0


def test_percentile_empty_is_none():
    assert _percentile([], 50) is None


def test_scorecard_rates():
    # 4 sessions: 3 valid specs, 2 deploy successes, avg 5 turns, 1 hallucination
    sc = _scorecard_from_rows(
        engine="claude", samples=4, valid=3, deployed=2, total_turns=20, hallucinated=1
    )
    assert sc.spec_validity_rate == 0.75
    assert sc.deploy_success_rate == 0.5
    assert sc.turns_to_spec == 5.0
    assert sc.hallucinated_field_rate == 0.25
