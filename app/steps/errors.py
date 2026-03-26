"""Typed errors for the demo pipeline (contract, planning, integrity)."""
from __future__ import annotations
from typing import List, Optional
class ContractIntegrityError(Exception):
    def __init__(
        self,
        stage: str,
        field: str,
        expected: Any,
        actual: Any,
        contract_id: str,
        missing_targets: Optional[List[str]] = None,
    ):
        self.stage = stage
        self.field = field
        self.expected = expected
        self.actual = actual
        self.contract_id = contract_id
        self.missing_targets = missing_targets or []
        super().__init__(
            f"ContractIntegrityError stage={stage} field={field} "
            f"expected={expected!r} actual={actual!r} "
            f"contract={contract_id}"
        )