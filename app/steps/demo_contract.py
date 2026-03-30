"""
DemoContract — single source of truth for what a demo run must achieve.

Built BEFORE step planning from static diff analysis. Never derived from
the planner that generates steps. This breaks the circular validation where
the planner's own output was the only correctness reference.

Usage:
    from app.steps.contract_extraction import extract_contract_static
    contract = extract_contract_static(diff_files)
    # contract is passed into generate_steps_from_diff() and run_capture()
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Literal, Optional


@dataclass
class TargetRef:
    """One expected click target that the demo must reach."""

    label: str                                                               
    selector: str = ""                                                 
    role: str = ""                                                   
    required: bool = True                                                       


@dataclass
class TerminalCondition:
    """How to verify the demo run completed its objective."""

    type: Literal["url_match", "text_present", "element_present"]
    value: str                                                   


@dataclass
class DemoContract:
    """Authoritative runtime definition of what one demo run must accomplish."""

    start_route: str                                                           
    targets: List[TargetRef]                                                       
    terminal: Optional[TerminalCondition]                                              

    contract_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    confidence: Literal["high", "medium", "low"] = "low"


    source_static: bool = False                                          
    source_extraction_llm: bool = False                                       
    agreement_score: float = 0.0                                       
    extraction_notes: List[str] = field(default_factory=list)

    def is_runnable(self) -> bool:
        """
        A contract is runnable when it has a start route and at least one target.

        Low-confidence contracts are still runnable — confidence affects gating
        policy upstream, not this check.
        """
        return bool(self.start_route) and len(self.targets) > 0

    def summary(self) -> str:
        """Single-line description for logging."""
        terminal_str = f"{self.terminal.type}:{self.terminal.value!r}" if self.terminal else "none"
        return (
            f"contract_id={self.contract_id} "
            f"confidence={self.confidence} "
            f"start_route={self.start_route!r} "
            f"targets={len(self.targets)} "
            f"terminal={terminal_str}"
        )
