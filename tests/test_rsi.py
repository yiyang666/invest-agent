import unittest

from invest_agent.metrics.rsi import simple_rsi, wilder_rsi


class WilderRsiTests(unittest.TestCase):
    def test_requires_period_observed_changes(self):
        result = wilder_rsi([1.0] * 15)
        self.assertEqual(result[:14], [None] * 14)
        self.assertEqual(result[14], 50.0)

    def test_monotonic_series_reaches_directional_extremes(self):
        self.assertEqual(wilder_rsi([float(value) for value in range(1, 16)])[-1], 100.0)
        self.assertEqual(wilder_rsi([float(value) for value in range(16, 1, -1)])[-1], 0.0)

    def test_wilder_smoothing_matches_known_sequence(self):
        values = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                  46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
        result = wilder_rsi(values)
        self.assertAlmostEqual(result[14], 70.4641, places=3)
        self.assertAlmostEqual(result[15], 66.2496, places=3)

    def test_rejects_invalid_parameters_and_nav(self):
        with self.assertRaises(ValueError):
            wilder_rsi([1.0, 2.0], period=0)
        with self.assertRaises(ValueError):
            wilder_rsi([1.0, 0.0])


class SimpleRsiTests(unittest.TestCase):
    def test_uses_thirteen_latest_changes_without_smoothing(self):
        values = [1.00, 1.03, 1.02, 1.04, 1.01, 1.05, 1.06, 1.04,
                  1.07, 1.08, 1.06, 1.05, 1.09, 1.10, 1.08]
        changes = [current - previous for previous, current in zip(values[-14:], values[-13:])]
        expected = 100 * sum(max(change, 0) for change in changes) / sum(abs(change) for change in changes)
        result = simple_rsi(values)
        self.assertIsNone(result[12])
        self.assertAlmostEqual(result[-1], expected)

    def test_monotonic_and_flat_windows_have_defined_values(self):
        self.assertEqual(simple_rsi([float(value) for value in range(1, 15)])[-1], 100.0)
        self.assertEqual(simple_rsi([float(value) for value in range(14, 0, -1)])[-1], 0.0)
        self.assertEqual(simple_rsi([1.0] * 14)[-1], 50.0)


if __name__ == "__main__":
    unittest.main()
