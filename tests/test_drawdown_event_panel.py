from datetime import date, timedelta
from decimal import Decimal
import unittest

from invest_agent.metrics.drawdown_events import _cluster_events, _episodes
from invest_agent.metrics.fund import NavPoint


def _points(count: int = 500):
    start = date(2020, 1, 1)
    return tuple(
        NavPoint(
            nav_date=start + timedelta(days=index),
            nav=Decimal("1"),
            visibility_status="historical_visibility_assumed",
            batch_id="batch",
            content_sha256="hash",
        )
        for index in range(count)
    )


class DrawdownEventPanelTests(unittest.TestCase):
    def test_same_fund_recrossing_inside_separation_is_not_new_episode(self) -> None:
        points = _points()
        path = (
            {"index": 130, "review_date": date(2020, 6, 1), "nav_date": points[130].nav_date, "peak_date": points[0].nav_date, "peak_nav": Decimal("1"), "drawdown": Decimal("-0.20")},
            {"index": 160, "review_date": date(2020, 7, 1), "nav_date": points[160].nav_date, "peak_date": points[0].nav_date, "peak_nav": Decimal("1"), "drawdown": Decimal("-0.05")},
            {"index": 200, "review_date": date(2020, 8, 1), "nav_date": points[200].nav_date, "peak_date": points[0].nav_date, "peak_nav": Decimal("1"), "drawdown": Decimal("-0.20")},
            {"index": 230, "review_date": date(2020, 9, 1), "nav_date": points[230].nav_date, "peak_date": points[0].nav_date, "peak_nav": Decimal("1"), "drawdown": Decimal("-0.05")},
            {"index": 300, "review_date": date(2020, 11, 1), "nav_date": points[300].nav_date, "peak_date": points[0].nav_date, "peak_nav": Decimal("1"), "drawdown": Decimal("-0.20")},
        )

        episodes = _episodes(
            points,
            path,
            threshold=Decimal("-0.20"),
            horizons=(3,),
            maximum_gap_days=10,
            minimum_index=126,
            minimum_episode_separation_observations=126,
        )

        self.assertEqual([item["start_date"] for item in episodes], ["2020-06-01", "2020-11-01"])

    def test_cross_fund_nearby_starts_share_one_date_cluster(self) -> None:
        base_forward = {"3_months": {"return": "0.10"}}
        clusters = _cluster_events(
            (
                {"start_date": "2022-04-01", "fund_code": "000001", "forward": base_forward},
                {"start_date": "2022-05-01", "fund_code": "000002", "forward": base_forward},
                {"start_date": "2022-10-01", "fund_code": "000003", "forward": base_forward},
            ),
            cluster_days=90,
        )

        self.assertEqual(len(clusters), 2)
        self.assertEqual(clusters[0]["fund_codes"], ["000001", "000002"])


if __name__ == "__main__":
    unittest.main()
