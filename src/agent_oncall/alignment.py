from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple

class AlignmentState(Enum):
    INIT = auto()
    INTERPRETING = auto()       # State 3A: Interpret working information
    SAMPLE_READY = auto()       # State 4A: Form sample request
    WAITING_FOR_RESPONSE = auto() # Waiting for Callee response
    SUCCESS = auto()            # State: Save service
    FAILED = auto()             # State: Failed to align / End dialogue


class ServiceDescriptionAlignment:
    """
    Implements the Service Description Alignment dialogue flow (Fig 3 in PDF).
    Uses pluggable callbacks to run the LLM-based interpretation and matching.
    """
    def __init__(
        self,
        ds: str,
        target_urn: str,
        aligner_cb: Callable[[str, List[Dict[str, str]]], Tuple[str, str, str]],
        matcher_cb: Callable[[str, str], bool],
        max_attempts: int = 3
    ):
        self.ds = ds
        self.target_urn = target_urn
        self.aligner_cb = aligner_cb
        self.matcher_cb = matcher_cb
        self.max_attempts = max_attempts
        
        self.state = AlignmentState.INIT
        self.attempts = 0
        self.working_set = {
            "ds": ds,
            "profile": None,
            "sample_request": None,
            "expected_response": None,
        }
        self.history: List[Dict[str, str]] = []

    def start_alignment(self) -> Optional[str]:
        """
        Triggers the first interpretation step.
        Returns the generated sample request to send to the callee, or None.
        """
        if self.state != AlignmentState.INIT:
            return None
            
        self.state = AlignmentState.INTERPRETING
        return self._run_interpretation_step()

    def handle_response(self, response: str) -> Tuple[bool, Optional[str]]:
        """
        Processes response 'r' received from the callee.
        Compares with desired response 'rd' using matcher callback.
        Returns (finished_successfully, next_sample_request_or_none).
        """
        if self.state != AlignmentState.WAITING_FOR_RESPONSE:
            return False, None
            
        self.attempts += 1
        rd = self.working_set["expected_response"]
        
        # Log to history
        self.history.append({"q": self.working_set["sample_request"], "r": response, "rd": rd})
        
        # Run matcher_cb(r, rd)
        match_success = self.matcher_cb(response, rd)
        
        if match_success:
            self.state = AlignmentState.SUCCESS
            return True, None
            
        if self.attempts >= self.max_attempts:
            self.state = AlignmentState.FAILED
            return False, None
            
        # If mismatch/error, transit back to interpreting to adjust
        self.state = AlignmentState.INTERPRETING
        next_request = self._run_interpretation_step()
        return False, next_request

    def _run_interpretation_step(self) -> Optional[str]:
        """Runs the LLM aligner callback to generate ps, q, rd."""
        try:
            profile, q, rd = self.aligner_cb(self.ds, self.history)
            self.working_set["profile"] = profile
            self.working_set["sample_request"] = q
            self.working_set["expected_response"] = rd
            self.state = AlignmentState.WAITING_FOR_RESPONSE
            return q
        except Exception as e:
            self.state = AlignmentState.FAILED
            return None
            
    def get_service_profile(self) -> Optional[str]:
        """Returns the generated and aligned service profile if successful."""
        return self.working_set["profile"] if self.state == AlignmentState.SUCCESS else None
