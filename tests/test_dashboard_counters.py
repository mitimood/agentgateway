"""Regression coverage for exact dashboard counts across Collector resets."""

import unittest

from scripts.check_dashboard import counter_deltas


def series(values, *, tool="", outcome="unauthenticated", **labels):
    return {"metric": {"demo_tool": tool, "access_outcome": outcome, **labels},
            "values": values}


class DashboardCounterTests(unittest.TestCase):
    def test_reported_401_reset_counts_both_requests(self):
        before = [series([[90, "2"], [100, "2"]])]
        after = [series([[100, "2"], [105, "2"], [110, "0"], [115, "2"], [120, "2"]])]
        self.assertEqual(counter_deltas(before, after, 100), {("", "unauthenticated"): 2})

    def test_regular_increments_ignore_baseline_samples(self):
        before = [series([[95, "12"]], tool="hello_world", outcome="ok")]
        after = [series([[95, "12"], [100, "12"], [105, "16"], [110, "21"]],
                        tool="hello_world", outcome="ok")]
        self.assertEqual(counter_deltas(before, after, 100), {("hello_world", "ok"): 9})

    def test_reset_is_computed_before_aggregation(self):
        before = [series([[95, "10"]], status_code="ERROR"),
                  series([[95, "20"]], status_code="UNSET")]
        after = [series([[105, "2"]], status_code="ERROR"),
                 series([[105, "25"]], status_code="UNSET")]
        self.assertEqual(counter_deltas(before, after, 100), {("", "unauthenticated"): 7})

    def test_new_series_counts_from_zero_and_missing_series_does_not_subtract(self):
        before = [series([[95, "10"]], instance="old")]
        after = [series([[105, "0"], [110, "2"]], instance="new")]
        self.assertEqual(counter_deltas(before, after, 100), {("", "unauthenticated"): 2})

    def test_multiple_resets_and_unchanged_samples(self):
        before = [series([[95, "4"]])]
        after = [series([[105, "6"], [110, "1"], [115, "1"], [120, "0"], [125, "3"]])]
        self.assertEqual(counter_deltas(before, after, 100), {("", "unauthenticated"): 6})

    def test_missing_401_events_remain_zero(self):
        before = [series([[95, "2"]])]
        after = [series([[105, "2"], [110, "0"], [115, "0"]])]
        self.assertEqual(counter_deltas(before, after, 100), {("", "unauthenticated"): 0})


if __name__ == "__main__":
    unittest.main()
