"""A4.3 — Unit tests for IndexPersistence debounce behavior.

Tests:
- 100 rapid schedule_save() calls result in exactly 1 save() call.
- flush() triggers immediate save without waiting for debounce timer.
"""

import sys
import os
import time
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db import StateKV
from search import SearchIndex, VectorIndex
from functions import IndexPersistence

# Speed up debounce for tests
FAST_DEBOUNCE = 0.05


def make_kv(tmp_path):
    return StateKV(db_path=str(tmp_path / "test_debounce.db"))


class TestDebounce:
    def test_100_rapid_calls_result_in_1_save(self, tmp_path):
        """100 rapid schedule_save() calls must fire exactly 1 save()."""
        kv = make_kv(tmp_path)
        bm25 = SearchIndex()
        vector = VectorIndex()

        persistence = IndexPersistence(kv, bm25, vector)
        persistence.DEBOUNCE_SECONDS = FAST_DEBOUNCE

        save_call_count = [0]

        original_save = persistence.save

        def counting_save():
            save_call_count[0] += 1
            original_save()

        with mock.patch.object(persistence, "save", side_effect=counting_save):
            for _ in range(100):
                persistence.schedule_save()
            # Wait for the debounce timer to fire (2× debounce window is plenty)
            time.sleep(FAST_DEBOUNCE * 4)

        assert save_call_count[0] == 1, (
            f"Expected exactly 1 save() call; got {save_call_count[0]}"
        )

    def test_rapid_calls_with_dirty_bm25(self, tmp_path):
        """schedule_save() fires exactly once even when BM25 is dirty."""
        kv = make_kv(tmp_path)
        bm25 = SearchIndex()
        vector = VectorIndex()

        # Add a doc so the index is dirty
        bm25.add(
            {
                "id": "obs_test1",
                "sessionId": "sess1",
                "title": "hello world",
                "facts": [],
                "concepts": [],
                "files": [],
                "type": "other",
            }
        )

        persistence = IndexPersistence(kv, bm25, vector)
        persistence.DEBOUNCE_SECONDS = FAST_DEBOUNCE

        save_call_count = [0]
        original_save = persistence.save

        def counting_save():
            save_call_count[0] += 1
            original_save()

        with mock.patch.object(persistence, "save", side_effect=counting_save):
            for _ in range(100):
                persistence.schedule_save()
            time.sleep(FAST_DEBOUNCE * 4)

        assert save_call_count[0] == 1

    def test_flush_triggers_immediate_save(self, tmp_path):
        """flush() must call save() immediately without waiting for the debounce timer."""
        kv = make_kv(tmp_path)
        bm25 = SearchIndex()
        vector = VectorIndex()

        persistence = IndexPersistence(kv, bm25, vector)
        persistence.DEBOUNCE_SECONDS = 60.0  # very long timer — flush must bypass it

        save_call_count = [0]
        original_save = persistence.save

        def counting_save():
            save_call_count[0] += 1
            original_save()

        with mock.patch.object(persistence, "save", side_effect=counting_save):
            persistence.schedule_save()
            # Timer is set but hasn't fired yet (60s window)
            assert save_call_count[0] == 0, "save() should not have been called yet"

            # flush() must cancel the timer and call save() synchronously
            persistence.flush()

        assert save_call_count[0] == 1, (
            f"flush() should trigger exactly 1 save(); got {save_call_count[0]}"
        )

    def test_flush_after_no_pending_save_is_safe(self, tmp_path):
        """flush() with no pending timer should still call save() once."""
        kv = make_kv(tmp_path)
        bm25 = SearchIndex()
        vector = VectorIndex()

        persistence = IndexPersistence(kv, bm25, vector)

        save_call_count = [0]
        original_save = persistence.save

        def counting_save():
            save_call_count[0] += 1
            original_save()

        with mock.patch.object(persistence, "save", side_effect=counting_save):
            persistence.flush()

        assert save_call_count[0] == 1

    def test_subsequent_schedule_after_fire_starts_new_timer(self, tmp_path):
        """Two bursts of saves separated by more than DEBOUNCE_SECONDS should fire 2 saves."""
        kv = make_kv(tmp_path)
        bm25 = SearchIndex()
        vector = VectorIndex()

        persistence = IndexPersistence(kv, bm25, vector)
        persistence.DEBOUNCE_SECONDS = FAST_DEBOUNCE

        save_call_count = [0]
        original_save = persistence.save

        def counting_save():
            save_call_count[0] += 1
            original_save()

        with mock.patch.object(persistence, "save", side_effect=counting_save):
            # First burst
            for _ in range(10):
                persistence.schedule_save()
            # Wait for first timer to fire
            time.sleep(FAST_DEBOUNCE * 4)

            # Second burst
            for _ in range(10):
                persistence.schedule_save()
            # Wait for second timer to fire
            time.sleep(FAST_DEBOUNCE * 4)

        assert save_call_count[0] == 2, (
            f"Expected 2 save() calls for two separate bursts; got {save_call_count[0]}"
        )
