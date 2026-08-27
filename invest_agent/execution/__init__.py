"""Execution boundaries for mock and exact-confirmation controlled live purchases."""

from .aijijin import AijijinCliAdapter, ControlledLivePolicy, normalize_redeem_preview
from .live import LiveApproval, LivePurchaseEnvelope, PersistentControlledLiveGateway
from .mock import MockExecutionGateway, MockOutcome
from .sqlite_mock import PersistentMockExecutionGateway, SimulatedProcessCrash

__all__ = [
    "AijijinCliAdapter",
    "ControlledLivePolicy",
    "normalize_redeem_preview",
    "LiveApproval",
    "LivePurchaseEnvelope",
    "PersistentControlledLiveGateway",
    "MockExecutionGateway",
    "MockOutcome",
    "PersistentMockExecutionGateway",
    "SimulatedProcessCrash",
]
