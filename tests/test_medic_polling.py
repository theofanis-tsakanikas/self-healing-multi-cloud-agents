"""Unit tests for the Medic CI-poll back-off (extracted pure helpers).

These prove the documented sequence (30→60→120→240→300, capped) and the stop
condition (after 5 attempts) WITHOUT sleeping or running the whole medic node.
"""
import pytest

from agents.medic import _poll_backoff_seconds, _should_stop_polling, _MAX_POLL_WAIT_SECONDS


class TestPollBackoffSeconds:
    @pytest.mark.parametrize("attempt,expected", [
        (0, 30),
        (1, 60),
        (2, 120),
        (3, 240),
        (4, 300),   # 30*16=480 capped to 300
        (5, 300),   # stays capped
        (10, 300),
    ])
    def test_sequence(self, attempt, expected):
        assert _poll_backoff_seconds(attempt) == expected

    def test_never_exceeds_ceiling(self):
        assert all(_poll_backoff_seconds(a) <= _MAX_POLL_WAIT_SECONDS for a in range(0, 20))

    def test_monotonic_non_decreasing(self):
        seq = [_poll_backoff_seconds(a) for a in range(0, 10)]
        assert seq == sorted(seq)


class TestShouldStopPolling:
    def test_continues_below_five(self):
        assert all(_should_stop_polling(a) is False for a in range(0, 5))

    def test_stops_at_five_and_beyond(self):
        assert _should_stop_polling(5) is True
        assert _should_stop_polling(6) is True
