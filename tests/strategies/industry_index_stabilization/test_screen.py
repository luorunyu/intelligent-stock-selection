import pandas as pd

from stock_selection.strategies.industry_index_stabilization.screen import (
    IndustryStabilizationState,
    _trailing_stable_starts,
    calculate_industry_index_stabilization,
    calculate_industry_index_stabilization_states,
    extract_stabilization_episodes,
    latest_industry_stabilization_states,
    normalise_industry_index_data,
    screen_industry_index_stabilization,
)


def _dictionary(*codes: str, level: str = "L2") -> dict:
    return {
        f"level{level[-1]}_index": {
            code: {"name": f"行业{position}", "stock_codes": []}
            for position, code in enumerate(codes, start=1)
        }
    }


def _bars(
    code: str,
    future_closes: list[float],
    *,
    short_decline: bool = False,
) -> pd.DataFrame:
    decline_count = 10 if short_decline else 22
    decline_start = 200.0
    decline_end = 120.0 if not short_decline else 125.0
    decline_closes = [
        decline_start
        + (decline_end - decline_start) * position / (decline_count - 1)
        for position in range(decline_count)
    ]
    stable_open = [101.0, 103.0, 100.0, 106.0, 102.0]
    stable_close = [102.0, 99.0, 104.0, 100.0, 101.0]
    closes = decline_closes + stable_close + future_closes
    opens = decline_closes + stable_open + future_closes
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D").strftime("%Y%m%d")
    return pd.DataFrame(
        {
            "ts_code": code,
            "name": code,
            "trade_date": dates,
            "open": opens,
            "close": closes,
        }
    )


def test_finds_historical_signal_and_validates_recovery_to_peak_ratio():
    data = _bars("L2A", [110, 120, 130, 161, 165, 163, 162, 160, 159, 158])
    result = calculate_industry_index_stabilization(
        data,
        _dictionary("L2A"),
        forward_window=10,
    )

    assert len(result) == 1
    signal = result.iloc[0]
    assert signal["stable_days"] == 5
    assert signal["stable_price_min"] == 99
    assert signal["stable_price_max"] == 106
    assert signal["stable_range_points"] == 7
    assert signal["downtrend_by_duration"]
    assert signal["target_hit"]
    assert signal["validation_status"] == "success"
    assert signal["trading_days_to_target"] == 4
    assert signal["target_close"] == 160
    assert signal["max_forward_peak_ratio"] > 0.80


def test_marks_complete_non_hit_signal_as_failed_and_deduplicates_episode():
    data = _bars("L2A", [110, 120, 125, 130, 129, 128, 127, 126, 125, 124])
    result = calculate_industry_index_stabilization(
        data,
        _dictionary("L2A"),
        forward_window=10,
    )

    assert len(result) == 1
    assert result.iloc[0]["validation_status"] == "failed"
    assert not result.iloc[0]["target_hit"]


def test_marks_recent_signal_pending_when_forward_window_is_incomplete():
    data = _bars("L2A", [110, 115, 120])
    result = calculate_industry_index_stabilization(
        data,
        _dictionary("L2A"),
        forward_window=10,
    )

    assert len(result) == 1
    assert result.iloc[0]["forward_observations"] == 3
    assert result.iloc[0]["validation_status"] == "pending"


def test_large_drawdown_qualifies_even_when_decline_is_shorter_than_one_month():
    data = _bars(
        "L2A",
        [110, 120, 130, 161, 165, 163, 162, 160, 159, 158],
        short_decline=True,
    )
    result = calculate_industry_index_stabilization(
        data,
        _dictionary("L2A"),
        forward_window=10,
    )

    assert len(result) == 1
    assert not result.iloc[0]["downtrend_by_duration"]
    assert result.iloc[0]["downtrend_by_drawdown"]


def test_open_close_extremes_must_share_one_ten_point_band():
    data = _bars("L2A", [130, 140, 150])
    stable_start = 22
    data.loc[stable_start : stable_start + 4, "open"] = [100, 101, 102, 103, 104]
    data.loc[stable_start : stable_start + 4, "close"] = [105, 106, 107, 108, 110]
    exact = calculate_industry_index_stabilization(
        data,
        _dictionary("L2A"),
        forward_window=3,
    )
    assert len(exact) == 1
    assert exact.iloc[0]["stable_range_points"] == 10

    data.loc[stable_start + 4, "close"] = 110.01
    outside = calculate_industry_index_stabilization(
        data,
        _dictionary("L2A"),
        forward_window=3,
    )
    assert outside.empty


def test_linear_stable_window_matches_longest_trailing_intervals():
    data = pd.DataFrame(
        {
            "open": [100, 102, 108, 111, 105, 104],
            "close": [101, 99, 109, 110, 103, 106],
        }
    )

    assert _trailing_stable_starts(data, 10) == [0, 0, 0, 2, 2, 2]


def test_stable_window_handles_single_day_wider_than_price_band():
    data = pd.DataFrame(
        {
            "open": [100, 120, 105],
            "close": [101, 100, 106],
        }
    )

    assert _trailing_stable_starts(data, 10) == [0, 2, 2]


def test_filters_to_dictionary_l2_codes_and_supports_result_screening():
    data = pd.concat(
        [
            _bars("L2A", [110, 120, 130, 161, 165, 163, 162, 160, 159, 158]),
            _bars("NOT_L2", [110, 120, 130, 161, 165, 163, 162, 160, 159, 158]),
        ],
        ignore_index=True,
    )
    result = calculate_industry_index_stabilization(
        data,
        _dictionary("L2A"),
        forward_window=10,
    )

    assert set(result["index_code"]) == {"L2A"}
    assert len(
        screen_industry_index_stabilization(result, validation_status="success")
    ) == 1


def test_level_parameter_supports_l3_and_defaults_to_l2():
    l2_data = _bars("L2A", [110, 120, 130, 161, 165, 163, 162, 160, 159, 158])
    l3_data = _bars("L3A", [110, 120, 130, 161, 165, 163, 162, 160, 159, 158])
    data = pd.concat([l2_data, l3_data], ignore_index=True)
    dictionary = {
        **_dictionary("L2A", level="L2"),
        **_dictionary("L3A", level="L3"),
    }

    default_result = calculate_industry_index_stabilization(
        data, dictionary, forward_window=10
    )
    l3_result = calculate_industry_index_stabilization(
        data, dictionary, level="l3", forward_window=10
    )

    assert set(default_result["index_code"]) == {"L2A"}
    assert set(default_result["level"]) == {"L2"}
    assert set(l3_result["index_code"]) == {"L3A"}
    assert set(l3_result["level"]) == {"L3"}


def test_normalise_requires_valid_open_and_close():
    data = _bars("L2A", [110]).iloc[:2].copy()
    data.loc[data.index[0], "open"] = None
    result = normalise_industry_index_data(data)
    assert len(result) == 1


def test_daily_states_backfill_confirmed_stabilization_interval():
    data = _bars("L2A", [110, 120, 130])
    states = calculate_industry_index_stabilization_states(
        data,
        _dictionary("L2A"),
    )

    confirmed = states[
        states["state"].eq(int(IndustryStabilizationState.STABILIZED))
    ]
    assert len(confirmed) == 5
    assert confirmed["episode_id"].nunique() == 1
    assert confirmed["state_days"].tolist() == [1, 2, 3, 4, 5]
    assert confirmed["signal_date"].nunique() == 1
    assert confirmed["signal_date"].iloc[0] == confirmed["trade_date"].iloc[-1]


def test_incomplete_stabilization_remains_candidate_without_event():
    data = _bars("L2A", []).iloc[:-1].copy()
    states = calculate_industry_index_stabilization_states(
        data,
        _dictionary("L2A"),
    )
    latest = latest_industry_stabilization_states(states).iloc[0]
    episodes = extract_stabilization_episodes(states)

    assert latest["state"] == int(
        IndustryStabilizationState.STABILIZING_CANDIDATE
    )
    assert episodes.empty


def test_event_extraction_uses_full_confirmed_segment_dates():
    data = _bars("L2A", [102, 103, 104, 120])
    states = calculate_industry_index_stabilization_states(
        data,
        _dictionary("L2A"),
    )
    episodes = extract_stabilization_episodes(states, forward_window=2)

    assert len(episodes) == 1
    episode = episodes.iloc[0]
    assert episode["stable_start_date"] < episode["signal_date"]
    assert episode["stable_end_date"] >= episode["signal_date"]
    assert episode["stable_days"] == 5
    assert episode["episode_trading_days"] == len(
        states[
            states["state"].eq(int(IndustryStabilizationState.STABILIZED))
        ]
    )
