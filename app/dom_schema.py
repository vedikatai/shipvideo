from __future__ import annotations

from typing import Dict, List, Literal, TypedDict


class ButtonCandidate(TypedDict):
    text: str                                                  
    testid: str                                       
    aria: str                                        
    title: str                                                                    
    id: str                          
    role: str                                           
    selector: str                                                


class LinkCandidate(TypedDict):
    text: str                                                
    href: str                              
    testid: str                                     
    aria: str                                      
    id: str                        


class InputCandidate(TypedDict):
    placeholder: str                               
    name: str                               
    input_type: str                                                       
    testid: str                                          
    aria: str                                           
    id: str                             


class TestIdCandidate(TypedDict):
    testid: str                               
    tag: str                              
    text: str                                               


class DomSnapshot(TypedDict):
    current_path: str                                         
    routes: List[str]                                          
    buttons: List[ButtonCandidate]
    links: List[LinkCandidate]
    inputs: List[InputCandidate]
    data_testids: List[TestIdCandidate]




















ExperimentMode = Literal["deterministic", "deterministic_plus_llm"]



SuccessConditionType = Literal["url_match", "text_present", "element_present"]


class SuccessCondition(TypedDict):

    type: SuccessConditionType
    value: str

class AgentBrowserElement(TypedDict):

    ref: str
    role: str
    name: str
    url: str
    visible: bool


class AgentBrowserSnapshot(TypedDict):

    current_url: str
    snapshot_text: str
    interactive_elements: List[AgentBrowserElement]
    context_elements: List[AgentBrowserElement]
    raw_snapshot_path: str
