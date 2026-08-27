"""Interactive local rehearsal for persistent mock approvals and execution."""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import InvalidOperation
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from invest_agent.approval.contracts import ApprovalPolicy, OrderIntent
from invest_agent.approval.drafts import MockOrderDraft, render_mock_approval_summary
from invest_agent.approval.sqlite_gate import SqliteApprovalGate

from .mock import MockOutcome
from .sqlite_mock import PersistentMockExecutionGateway, SimulatedProcessCrash


DEFAULT_POLICY = Path("config/phase6_mock_approval_policy_v1.json")
DEFAULT_DATABASE = Path("data/private/mock-execution.sqlite3")


def _timestamp(value: str | None) -> datetime:
    parsed = (
        datetime.now(ZoneInfo("Asia/Shanghai"))
        if value is None
        else datetime.fromisoformat(value)
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--at must include a timezone offset")
    return parsed


def _load_intent(path: Path) -> OrderIntent:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("intent file must contain one JSON object")
    return OrderIntent.from_mapping(payload)


def _gateway(policy_path: Path, database: Path) -> tuple[SqliteApprovalGate, PersistentMockExecutionGateway]:
    policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
    policy = ApprovalPolicy.from_mapping(policy_payload)
    gate = SqliteApprovalGate(policy, database)
    return gate, PersistentMockExecutionGateway(gate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run network-free Phase 6 mock execution rehearsals"
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--at")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rehearse = subparsers.add_parser("rehearse")
    rehearse.add_argument("--intent", type=Path, required=True)
    rehearse.add_argument(
        "--outcome",
        choices=[item.value for item in MockOutcome],
        required=True,
    )
    rehearse.add_argument("--fault-after-begin", action="store_true")

    status = subparsers.add_parser("status")
    status.add_argument("--intent", type=Path, required=True)

    subparsers.add_parser("recover")

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--intent", type=Path, required=True)
    reconcile.add_argument(
        "--status",
        choices=[MockOutcome.SUBMITTED.value, MockOutcome.FAILED.value],
        required=True,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        now = _timestamp(args.at)
        gate, gateway = _gateway(args.policy, args.db)
        if args.command == "recover":
            payload = {
                "recovered_to_unknown": gateway.recover_incomplete(now=now),
                "mode": "mock_only",
                "real_trading_enabled": False,
            }
        else:
            intent = _load_intent(args.intent)
            if args.command == "status":
                payload = gateway.status(intent)
                if payload is None:
                    raise ValueError("mock execution state was not found")
            elif args.command == "reconcile":
                payload = gateway.reconcile(
                    intent, now=now, status=MockOutcome(args.status)
                )
            else:
                draft = MockOrderDraft(
                    simulation_id="interactive_mock_rehearsal",
                    sleeve="manual_rehearsal",
                    intent=intent,
                )
                print(render_mock_approval_summary(draft))
                expected = f"APPROVE MOCK {intent.sha256}"
                confirmation = input(f"\n请输入完整确认串：{expected}\n> ").strip()
                if confirmation != expected:
                    raise ValueError("exact mock approval confirmation did not match")
                approval = gate.record_explicit_approval(intent, approved_at=now)
                payload = gateway.submit(
                    intent,
                    approval,
                    now=now,
                    outcome=MockOutcome(args.outcome),
                    crash_after_begin=args.fault_after_begin,
                )
    except (
        InvalidOperation,
        OSError,
        SimulatedProcessCrash,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
