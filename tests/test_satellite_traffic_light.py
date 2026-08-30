from datetime import date, timedelta
from decimal import Decimal
import unittest

from invest_agent.metrics.fund import NavPoint
from invest_agent.strategies.satellite_traffic_light import (
    build_satellite_traffic_light_signal,
    build_state_adjusted_target_gap_allocation,
    evaluate_satellite_traffic_light,
)


def _points(values: list[str], *, start: date = date(2025, 1, 1)) -> tuple[NavPoint, ...]:
    return tuple(
        NavPoint(
            nav_date=start + timedelta(days=index),
            nav=Decimal(value),
            visibility_status="historical_visibility_assumed",
            batch_id="batch",
            content_sha256="a" * 64,
        )
        for index, value in enumerate(values)
    )


class SatelliteTrafficLightTests(unittest.TestCase):
    def test_green_yellow_red_and_ineligible_states_are_deterministic(self) -> None:
        green = _points([str(1 + index / 1000) for index in range(201)])
        red = _points([str(2 - index / 1000) for index in range(201)])
        yellow_values = ["1"] * 201
        yellow_values[74] = "2"
        yellow_values[-1] = "1.5"
        yellow = _points(yellow_values)
        cutoff = green[-1].nav_date

        self.assertEqual(
            evaluate_satellite_traffic_light(
                sleeve="green", fund_code="000001", points=green, cutoff_date=cutoff
            )["state"],
            "green",
        )
        self.assertEqual(
            evaluate_satellite_traffic_light(
                sleeve="yellow", fund_code="000002", points=yellow, cutoff_date=cutoff
            )["state"],
            "yellow",
        )
        self.assertEqual(
            evaluate_satellite_traffic_light(
                sleeve="red", fund_code="000003", points=red, cutoff_date=cutoff
            )["state"],
            "red",
        )
        self.assertEqual(
            evaluate_satellite_traffic_light(
                sleeve="short",
                fund_code="000004",
                points=green[:100],
                cutoff_date=green[99].nav_date,
            )["state"],
            "ineligible",
        )

    def test_observations_after_cutoff_cannot_change_signal(self) -> None:
        points = _points([str(1 + index / 1000) for index in range(201)])
        cutoff = points[-1].nav_date
        future = NavPoint(
            nav_date=cutoff + timedelta(days=1),
            nav=Decimal("100"),
            visibility_status="historical_visibility_assumed",
            batch_id="future",
            content_sha256="b" * 64,
        )

        before = evaluate_satellite_traffic_light(
            sleeve="theme", fund_code="000001", points=points, cutoff_date=cutoff
        )
        after = evaluate_satellite_traffic_light(
            sleeve="theme",
            fund_code="000001",
            points=(*points, future),
            cutoff_date=cutoff,
        )

        self.assertEqual(before, after)

    def test_single_factor_modes_reuse_features_but_remove_yellow_state(self) -> None:
        values = ["1"] * 201
        values[74] = "2"
        values[-1] = "1.5"
        points = _points(values)
        cutoff = points[-1].nav_date
        dual = evaluate_satellite_traffic_light(
            sleeve="theme", fund_code="000001", points=points, cutoff_date=cutoff
        )
        long_only = evaluate_satellite_traffic_light(
            sleeve="theme",
            fund_code="000001",
            points=points,
            cutoff_date=cutoff,
            factor_mode="long_average_only",
        )
        momentum_only = evaluate_satellite_traffic_light(
            sleeve="theme",
            fund_code="000001",
            points=points,
            cutoff_date=cutoff,
            factor_mode="momentum_only",
        )
        self.assertEqual(dual["state"], "yellow")
        self.assertEqual(long_only["state"], "green")
        self.assertEqual(momentum_only["state"], "red")
        self.assertEqual(long_only["features"], momentum_only["features"])
        self.assertEqual(long_only["factor_mode"], "long_average_only")

    def test_default_dual_payload_remains_backward_compatible(self) -> None:
        green = _points([str(1 + index / 1000) for index in range(201)])
        evaluated = evaluate_satellite_traffic_light(
            sleeve="theme",
            fund_code="000001",
            points=green,
            cutoff_date=green[-1].nav_date,
        )
        self.assertNotIn("factor_mode", evaluated)

    def test_all_red_releases_satellite_target_to_cash_without_renormalizing(self) -> None:
        red = _points([str(2 - index / 1000) for index in range(201)])
        satellites = {
            "global_technology_satellite": ("100055", red),
            "semiconductor_satellite": ("007300", red),
            "robotics_satellite": ("014880", red),
            "biotechnology_satellite": ("017894", red),
        }
        strategic = {
            "core": Decimal("0.40"),
            "global_technology_satellite": Decimal("0.05"),
            "semiconductor_satellite": Decimal("0.05"),
            "robotics_satellite": Decimal("0.05"),
            "biotechnology_satellite": Decimal("0.05"),
            "defensive_bond": Decimal("0.20"),
            "gold_stabilizer": Decimal("0.10"),
            "dividend": Decimal("0.10"),
        }

        result = build_satellite_traffic_light_signal(
            review_date=red[-1].nav_date + timedelta(days=1),
            satellite_series=satellites,
            strategic_weights=strategic,
            released_weight_destination="cash",
        )

        self.assertEqual(result["state_counts"]["red"], 4)
        self.assertEqual(Decimal(result["cash_target_weight"]), Decimal("0.20"))
        effective = {
            key: Decimal(value) for key, value in result["effective_target_weights"].items()
        }
        self.assertEqual(
            sum(effective.values(), Decimal("0")), Decimal("0.80")
        )
        self.assertTrue(
            all(effective[sleeve] == 0 for sleeve in satellites)
        )

    def test_cash_released_by_red_state_can_be_deployed_after_recovery(self) -> None:
        red_weights = {"core": Decimal("0.4"), "theme": Decimal("0"), "defense": Decimal("0.4")}
        first = build_state_adjusted_target_gap_allocation(
            planned_date=date(2026, 1, 1),
            pre_contribution_portfolio_value_cny=Decimal("0"),
            monthly_contribution_cny=Decimal("3000"),
            current_position_values_cny={"core": Decimal("0"), "theme": Decimal("0"), "defense": Decimal("0")},
            prior_available_cash_cny=Decimal("0"),
            nondeployable_cash_cny=Decimal("0"),
            effective_target_weights=red_weights,
            strategy_spec_sha256="a" * 64,
        )
        self.assertEqual(
            sum((item.allocated_cny for item in first.allocations), Decimal("0")),
            Decimal("2400.00"),
        )
        self.assertEqual(first.unallocated_cash_cny, Decimal("600.00"))

        green_weights = {
            "core": Decimal("0.4"),
            "theme": Decimal("0.2"),
            "defense": Decimal("0.4"),
        }
        recovered = build_state_adjusted_target_gap_allocation(
            planned_date=date(2026, 2, 1),
            pre_contribution_portfolio_value_cny=Decimal("3000"),
            monthly_contribution_cny=Decimal("3000"),
            current_position_values_cny={"core": Decimal("1200"), "theme": Decimal("0"), "defense": Decimal("1200")},
            prior_available_cash_cny=Decimal("600"),
            nondeployable_cash_cny=Decimal("0"),
            effective_target_weights=green_weights,
            strategy_spec_sha256="a" * 64,
        )
        self.assertEqual(
            sum((item.allocated_cny for item in recovered.allocations), Decimal("0")),
            Decimal("3600.00"),
        )
        self.assertEqual(recovered.unallocated_cash_cny, Decimal("0.00"))

    def test_single_theme_diagnostic_controls_only_the_frozen_subset(self) -> None:
        red = _points([str(2 - index / 1000) for index in range(201)])
        satellites = {
            "global_technology_satellite": ("100055", red),
            "semiconductor_satellite": ("007300", red),
            "robotics_satellite": ("014880", red),
            "biotechnology_satellite": ("017894", red),
        }
        strategic = {
            "core": Decimal("0.40"),
            **{sleeve: Decimal("0.05") for sleeve in satellites},
            "defensive_bond": Decimal("0.20"),
            "gold_stabilizer": Decimal("0.10"),
            "dividend": Decimal("0.10"),
        }
        result = build_satellite_traffic_light_signal(
            review_date=red[-1].nav_date + timedelta(days=1),
            satellite_series=satellites,
            strategic_weights=strategic,
            controlled_satellite_sleeves=["robotics_satellite"],
            released_weight_destination="bond_gold_2_to_1",
        )
        effective = {
            key: Decimal(value)
            for key, value in result["effective_target_weights"].items()
        }
        self.assertEqual(result["controlled_satellite_sleeves"], ["robotics_satellite"])
        self.assertEqual(effective["robotics_satellite"], Decimal("0"))
        self.assertEqual(effective["semiconductor_satellite"], Decimal("0.05"))
        self.assertEqual(Decimal(result["released_weight"]), Decimal("0.05"))

    def test_strategic_floor_keeps_half_of_each_red_or_ineligible_sleeve(self) -> None:
        red = _points([str(2 - index / 1000) for index in range(201)])
        short = red[:100]
        satellites = {
            "global_technology_satellite": ("100055", red),
            "semiconductor_satellite": ("007300", red),
            "robotics_satellite": ("014880", short),
            "biotechnology_satellite": ("017894", red),
        }
        strategic = {
            "core": Decimal("0.40"),
            **{sleeve: Decimal("0.05") for sleeve in satellites},
            "defensive_bond": Decimal("0.20"),
            "gold_stabilizer": Decimal("0.10"),
            "dividend": Decimal("0.10"),
        }
        result = build_satellite_traffic_light_signal(
            review_date=red[-1].nav_date + timedelta(days=1),
            satellite_series=satellites,
            strategic_weights=strategic,
            released_weight_destination="bond_gold_2_to_1",
            strategic_floor_multiplier=Decimal("0.5"),
        )
        evaluations = {item["sleeve"]: item for item in result["satellite_evaluations"]}
        effective = {
            key: Decimal(value)
            for key, value in result["effective_target_weights"].items()
        }

        self.assertEqual(evaluations["robotics_satellite"]["state"], "ineligible")
        self.assertTrue(
            all(
                Decimal(evaluations[sleeve]["applied_multiplier"])
                == Decimal("0.5")
                for sleeve in satellites
            )
        )
        self.assertTrue(
            all(effective[sleeve] == Decimal("0.025") for sleeve in satellites)
        )
        self.assertEqual(Decimal(result["released_weight"]), Decimal("0.10"))
        self.assertEqual(
            result["signal_parameters"]["strategic_floor_multiplier"], "0.5"
        )
        self.assertEqual(
            result["model_version"], "satellite_traffic_light_strategic_floor_v1"
        )

    def test_strategic_floor_rejects_out_of_range_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "between zero and one"):
            build_satellite_traffic_light_signal(
                review_date=date(2026, 1, 1),
                satellite_series={},
                strategic_weights={"core": Decimal("1")},
                strategic_floor_multiplier=Decimal("1.1"),
            )


if __name__ == "__main__":
    unittest.main()
