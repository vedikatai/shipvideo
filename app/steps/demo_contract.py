from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Literal, Optional


@dataclass
class TargetRef:

    label: str                                                               
    selector: str = ""                                                 
    role: str = ""                                                   
    required: bool = True                                                       


@dataclass
class TerminalCondition:

    type: Literal["url_match", "text_present", "element_present"]
    value: str                                                   


@dataclass
class DemoContract:

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
        return bool(self.start_route) and len(self.targets) > 0

    def summary(self) -> str:
        terminal_str = f"{self.terminal.type}:{self.terminal.value!r}" if self.terminal else "none"
        return (
            f"contract_id={self.contract_id} "
            f"confidence={self.confidence} "
            f"start_route={self.start_route!r} "
            f"targets={len(self.targets)} "
            f"terminal={terminal_str}"
        )
