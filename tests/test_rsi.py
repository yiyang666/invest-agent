import unittest

from invest_agent.metrics.rsi import wilder_rsi


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


if __name__ == "__main__":
    unittest.main()
