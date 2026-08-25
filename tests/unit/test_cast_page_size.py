from botgitgud.ingest.log_fetcher_aux import fetch_cast_timelines
from botgitgud.wcl.queries import QUERY_PLAYER_EVENTS


def test_cast_page_size_is_10000_and_pagination_preserves_complete_event_set() -> None:
    assert "limit: 10000" in QUERY_PLAYER_EVENTS
    all_events = [
        {"sourceID": 7, "type": "cast", "abilityGameID": 10, "timestamp": timestamp}
        for timestamp in (100, 200, 300, 400)
    ]

    def replay(_query: str, variables: dict[str, object], *, op_name: str) -> dict[str, object]:
        assert op_name == "fetch_player_events"
        start_value = variables["startTime"]
        assert isinstance(start_value, (int, float))
        start = float(start_value)
        page = all_events[:2] if start == 0 else all_events[2:]
        next_page = 250 if start == 0 else None
        return {
            "data": {
                "reportData": {"report": {"events": {"data": page, "nextPageTimestamp": next_page}}}
            }
        }

    flat, _phased = fetch_cast_timelines(
        replay,
        report_code="A" * 16,
        fight_id=1,
        player_id=7,
        start_time_ms=0,
        end_time_ms=1000,
        intervals=(),
    )
    assert flat == {10: (0.1, 0.2, 0.3, 0.4)}
