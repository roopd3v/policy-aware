from fractions import Fraction
import unittest

from bemix_pilot.baseline import baseline_bands, baseline_bounty
from bemix_pilot.engine import ConfigurationError, EvidenceError, bemix


RATES = {"Low": 3000, "Medium": 4000, "High": 55000, "Critical": 506000}


class BaselineTests(unittest.TestCase):
    def test_example_vector_scores_and_cumulative_payout(self):
        self.assertEqual(bemix(4, 3, 0, 1)["score"], 23)
        self.assertEqual(bemix(4, 0, 0, 1)["score"], 17)
        self.assertEqual(baseline_bounty(17, RATES), 214000)
        self.assertEqual(baseline_bounty(23, RATES), 1446000)

    def test_all_example_band_endpoints(self):
        self.assertEqual(baseline_bands(RATES), {
            "Low": (3000, 21000), "Medium": (25000, 49000),
            "High": (104000, 434000), "Critical": (940000, 4988000)})

    def test_every_increment_matches_the_configured_tier(self):
        for score in range(1, 31):
            tier = "Low" if score <= 7 else "Medium" if score <= 14 else "High" if score <= 21 else "Critical"
            self.assertEqual(baseline_bounty(score, RATES) - baseline_bounty(score - 1, RATES), RATES[tier])

    def test_informational_and_invalid_scores(self):
        self.assertEqual(baseline_bounty(None, RATES), 0)
        for score in (-1, 31, True, Fraction(1), 17.0):
            with self.assertRaises(EvidenceError):
                baseline_bounty(score, RATES)

    def test_invalid_rates(self):
        for rates in ({}, {**RATES, "Low": -1}, {**RATES, "Low": True}):
            with self.assertRaises(ConfigurationError):
                baseline_bounty(17, rates)


if __name__ == "__main__":
    unittest.main()
