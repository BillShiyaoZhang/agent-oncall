import base64
import json
import time
import sys
from typing import List, Dict, Tuple, Optional
from agent_oncall import (
    AgentOnCall,
    crypto,
    MockCommAdapter,
    TrustDatabase,
    PolicyEngine,
    sign_capability_token,
    TIER_1_FAMILY,
    TIER_2_FRIEND,
    TIER_3_STRANGER,
    InteractiveHITLHandler,
    ServiceDescriptionAlignment,
    AlignmentState
)

# Color Formatting for rich console output
C_GREEN = "\033[92m"
C_BLUE = "\033[94m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_MAGENTA = "\033[95m"
C_BOLD = "\033[1m"
C_END = "\033[0m"

def log_header(title: str):
    print(f"\n{C_BOLD}{C_MAGENTA}🚀 ====================================================================={C_END}")
    print(f"{C_BOLD}{C_MAGENTA}🔥 {title.upper()}{C_END}")
    print(f"{C_BOLD}{C_MAGENTA}========================================================================={C_END}\n")

def log_section(title: str):
    print(f"\n{C_BOLD}{C_BLUE}--- {title} ---{C_END}")

def log_info(msg: str):
    print(f"  ℹ️  {msg}")

def log_success(msg: str):
    print(f"  {C_GREEN}✅ {msg}{C_END}")

def log_warning(msg: str):
    print(f"  {C_YELLOW}⚠️  {msg}{C_END}")

def log_error(msg: str):
    print(f"  {C_RED}❌ {msg}{C_END}")

def log_agent_msg(agent_name: str, color: str, msg: str):
    print(f"  🤖 [{color}{agent_name}{C_END}]: {msg}")

def main():
    log_header("Agent-OnCall Complex Interactive Scenario Demo")

    # ----------------------------------------------------
    # Initialization
    # ----------------------------------------------------
    log_section("0. Initializing Local Agent Infrastructure")
    
    comm = MockCommAdapter()
    
    # Generate keys
    alice_priv, alice_pub = crypto.generate_keypair()
    bob_priv, bob_pub = crypto.generate_keypair()
    
    alice_urn = "urn:hermes:agent:alice"
    bob_urn = "urn:hermes:agent:bob"
    
    alice_pub_hex = crypto.public_key_to_hex(alice_pub)
    bob_pub_hex = crypto.public_key_to_hex(bob_pub)
    
    log_info(f"Alice Agent initialized at URN: {C_BOLD}{alice_urn}{C_END}")
    log_info(f"Bob Agent initialized at URN:   {C_BOLD}{bob_urn}{C_END}")
    
    # Setup Agents with Interactive/Controlled HITL Handlers
    alice_hitl = InteractiveHITLHandler()
    alice = AgentOnCall(
        agent_urn=alice_urn,
        private_key_hex=crypto.private_key_to_hex(alice_priv),
        comm_adapter=comm,
        hitl_handler=alice_hitl
    )
    comm.register_agent(alice_urn, alice)
    
    bob = AgentOnCall(
        agent_urn=bob_urn,
        private_key_hex=crypto.private_key_to_hex(bob_priv),
        comm_adapter=comm
    )
    comm.register_agent(bob_urn, bob)
    
    # Register Alice's Intents
    # Intent 1: Query schedule (Friend / Family allowed, no HITL)
    alice.register_intent(
        name="calendar.query_availability",
        description="Check if Alice has free time slots.",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}
            },
            "required": ["date"]
        },
        handler=lambda sender, args: {"date": args["date"], "available": True, "slots": ["09:00", "14:00", "16:00"]},
        requires_hitl=False,
        resource="calendar",
        action="read"
    )
    
    # Intent 2: Book slot (Family allowed, requires HITL)
    alice.register_intent(
        name="calendar.book_event",
        description="Book a slot on Alice's calendar.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "time": {"type": "string"}
            },
            "required": ["title", "time"]
        },
        handler=lambda sender, args: {"status": "confirmed", "title": args["title"], "time": args["time"]},
        requires_hitl=True,
        resource="calendar",
        action="write"
    )
    
    # Intent 3: Read sensitive private document (Blocked globally by default, requires specific HCT)
    alice.register_intent(
        name="files.read_private",
        description="Retrieve private agent configuration documentation.",
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string"}
            },
            "required": ["filename"]
        },
        handler=lambda sender, args: {"content": f"Confidential data for file {args['filename']}: SEC_KEY_9921"},
        requires_hitl=False,
        resource="files",
        action="read"
    )

    # Intent 4: A custom service with specific pattern/constraints for SDA alignment
    # If project_id doesn't match, the handler returns an error
    def execute_report(sender, args):
        proj_id = args.get("project_id", "")
        if not proj_id.startswith("PRJ-"):
            raise ValueError("Invalid project ID: must start with PRJ-")
        return {"status": "success", "report_url": f"http://reports/summary/{proj_id}"}

    alice.register_intent(
        name="analytics.generate_report",
        description="Generates a project report. Requires 'project_id' parameter starting with PRJ-",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"}
            },
            "required": ["project_id"]
        },
        handler=execute_report,
        requires_hitl=False,
        resource="analytics",
        action="write"
    )

    # ----------------------------------------------------
    # Scenario 1: Access Control & Discovery Tiers
    # ----------------------------------------------------
    log_header("Scenario 1: Access Control & Discovery Tiers")
    
    # By default, Bob is a Stranger to Alice
    alice.trust_db.add_contact(bob_urn, bob_pub_hex, TIER_3_STRANGER)
    bob.trust_db.add_contact(alice_urn, alice_pub_hex, TIER_3_STRANGER)
    
    log_agent_msg("Bob", C_BLUE, "Checking Alice's available capabilities...")
    bob_discovery = bob.discover_remote(alice_urn)
    log_info(f"Bob sees Alice's intents: {[x['name'] for x in bob_discovery]}")
    
    if len(bob_discovery) == 0:
        log_success("Bob sees no capabilities. Passive disclosure works: Stranger is blocked from viewing intents.")
        
    log_agent_msg("Bob", C_BLUE, "Trying to call calendar.query_availability directly...")
    res = bob.call_remote(alice_urn, "calendar.query_availability", {"date": "2026-06-01"})
    
    if not res["success"]:
        log_success(f"Execution blocked correctly: {res['error_message']}")
        
    # Promoting Bob to Friend
    log_info(f"Upgrading Bob's trust level in Alice's Trust Database: {TIER_3_STRANGER} -> {TIER_2_FRIEND}")
    alice.trust_db.add_contact(bob_urn, bob_pub_hex, TIER_2_FRIEND)
    
    log_agent_msg("Bob", C_BLUE, "Re-discovering Alice's capabilities...")
    bob_discovery = bob.discover_remote(alice_urn)
    discovered_names = [x['name'] for x in bob_discovery]
    log_info(f"Bob now sees Alice's intents: {discovered_names}")
    
    if "calendar.query_availability" in discovered_names:
        log_success("Bob can now see 'calendar.query_availability'.")
    if "calendar.book_event" not in discovered_names:
        log_success("Bob still cannot see 'calendar.book_event' (exclusively visible to Family tier or via HCT).")
        
    log_agent_msg("Bob", C_BLUE, "Calling calendar.query_availability on Alice...")
    res = bob.call_remote(alice_urn, "calendar.query_availability", {"date": "2026-06-01"})
    
    if res["success"]:
        log_success(f"Success! Alice returned: {res['result']}")

    # ----------------------------------------------------
    # Scenario 2: Human-in-the-Loop (HITL) Interception
    # ----------------------------------------------------
    log_header("Scenario 2: Human-in-the-Loop (HITL) Interception")
    
    # Alice updates Bob to Family tier to allow book_event calls, but they require HITL approval
    log_info(f"Upgrading Bob's trust level in Alice's Trust Database: {TIER_2_FRIEND} -> {TIER_1_FAMILY}")
    alice.trust_db.add_contact(bob_urn, bob_pub_hex, TIER_1_FAMILY)
    
    # Test Flow 2A: Rejection
    log_agent_msg("Bob", C_BLUE, "Attempting to book a calendar slot (title='Strategic sync', time='15:00')...")
    log_info("Setting Alice's HITL handler to reject high-risk calls...")
    alice_hitl.default_response = False
    
    res = bob.call_remote(alice_urn, "calendar.book_event", {"title": "Strategic sync", "time": "15:00"})
    if not res["success"]:
        log_success(f"Operation rejected successfully by HITL interceptor: {res['error_message']}")
        
    # Test Flow 2B: Approval
    print()
    log_agent_msg("Bob", C_BLUE, "Retrying booking calendar slot (title='Strategic sync', time='15:00')...")
    log_info("Setting Alice's HITL handler to approve high-risk calls...")
    alice_hitl.default_response = True
    
    res = bob.call_remote(alice_urn, "calendar.book_event", {"title": "Strategic sync", "time": "15:00"})
    if res["success"]:
        log_success(f"Operation approved successfully by HITL: {res['result']}")

    # ----------------------------------------------------
    # Scenario 3: Capability Token (HCT) Delegation
    # ----------------------------------------------------
    log_header("Scenario 3: Capability Token (HCT) Delegation")
    
    # Bob needs files.read_private. However, global family tier permissions does not include files.* by default.
    log_agent_msg("Bob", C_BLUE, "Attempting to read confidential file 'financial_q1.pdf' directly...")
    res = bob.call_remote(alice_urn, "files.read_private", {"filename": "financial_q1.pdf"})
    
    if not res["success"]:
        log_success(f"Access correctly denied: {res['error_message']}")
        
    # Alice generates a temporary HCT capability token delegating files.read access to Bob
    log_info("Alice issues a 60-second capability token (HCT) delegating 'files:read' permissions to Bob...")
    token = sign_capability_token(
        private_key_hex=crypto.private_key_to_hex(alice_priv),
        issuer_urn=alice_urn,
        audience_urn=bob_urn,
        expires_in_seconds=60,
        constraints=[{"resource": "files", "action": "read"}]
    )
    
    log_agent_msg("Bob", C_BLUE, "Retrying files.read_private by attaching the signed HCT Token...")
    res = bob.call_remote(
        target_urn=alice_urn,
        intent_name="files.read_private",
        arguments={"filename": "financial_q1.pdf"},
        hct_token=token
    )
    
    if res["success"]:
        log_success(f"Access granted via HCT Token! File contents: {res['result']}")
    else:
        log_error(f"Access failed: {res['error_message']}")

    # ----------------------------------------------------
    # Scenario 4: Service Description Alignment (SDA) with Error Recovery
    # ----------------------------------------------------
    log_header("Scenario 4: Service Description Alignment with Error Recovery")
    
    ambiguous_ds = (
        "A service named analytics.generate_report. Requires a project_id string matching a specific pattern. "
        "Returns report status URL."
    )
    
    # We will simulate an Aligner logic that fails on attempt 0 and adjusts on attempt 1.
    def mock_bob_llm_aligner(description: str, history: List[Dict[str, str]]) -> Tuple[str, str, str]:
        # History is a list of {"q": sample_request, "r": response, "rd": expected_response}
        if not history:
            # First attempt: Bob's 'LLM' makes a wrong guess. Guess project_id = "123" (missing the PRJ- prefix)
            log_agent_msg("Bob (LLM Aligner)", C_BLUE, "Attempt 1: Guessing parameters. Schema says project_id. Let's try '123'.")
            profile = "analytics.generate_report"
            sample_request = json.dumps({"project_id": "123"})
            expected_response = json.dumps({"status": "success", "report_url": "http://reports/summary/123"})
            return profile, sample_request, expected_response
        else:
            # Second attempt: LLM reads the error response "Invalid project ID: must start with PRJ-"
            last_err = history[-1]["r"]
            log_agent_msg("Bob (LLM Aligner)", C_BLUE, f"Attempt 2: Previous attempt failed with response: '{last_err}'. Adjusting parameters...")
            profile = "analytics.generate_report"
            sample_request = json.dumps({"project_id": "PRJ-9921"})
            expected_response = json.dumps({"status": "success", "report_url": "http://reports/summary/PRJ-9921"})
            return profile, sample_request, expected_response

    def mock_bob_llm_matcher(response: str, expected: str) -> bool:
        try:
            r_data = json.loads(response)
            e_data = json.loads(expected)
            match = r_data.get("status") == e_data.get("status")
            log_agent_msg("Bob (LLM Matcher)", C_BLUE, f"Comparing response '{response}' vs expected '{expected}'. Match result: {match}")
            return match
        except Exception:
            log_agent_msg("Bob (LLM Matcher)", C_BLUE, f"Invalid JSON/Failed Match. Return False.")
            return False

    log_info("Bob initializes a Service Description Alignment (SDA) flow to adapt to Alice's 'analytics.generate_report'...")
    sda_session = ServiceDescriptionAlignment(
        ds=ambiguous_ds,
        target_urn=alice_urn,
        aligner_cb=mock_bob_llm_aligner,
        matcher_cb=mock_bob_llm_matcher,
        max_attempts=3
    )

    # 1. Start Alignment (Interpret -> Generate Request Q1)
    q1 = sda_session.start_alignment()
    log_info(f"SDA session state: {sda_session.state}")
    log_agent_msg("Bob", C_BLUE, f"Sending probe CallRequest Q1: {q1}")
    
    # Send call to Alice
    args1 = json.loads(q1)
    res1 = bob.call_remote(alice_urn, "analytics.generate_report", args1)
    
    if not res1["success"]:
        # Alice correctly rejected the parameters
        err_msg = res1["error_message"]
        log_agent_msg("Alice", C_GREEN, f"Returned execution failure: {err_msg}")
        
        # Bob handles response & loops back
        log_info("Bob feeds response back into SDA...")
        finished, next_q = sda_session.handle_response(err_msg)
        log_info(f"SDA session state: {sda_session.state}")
        
        if not finished and next_q:
            # 2. Resend corrected request
            log_agent_msg("Bob", C_BLUE, f"Sending corrected probe CallRequest Q2: {next_q}")
            args2 = json.loads(next_q)
            res2 = bob.call_remote(alice_urn, "analytics.generate_report", args2)
            
            if res2["success"]:
                alice_resp_json = json.dumps(res2["result"])
                log_agent_msg("Alice", C_GREEN, f"Returned response: {alice_resp_json}")
                
                # Verify match
                log_info("Bob feeds response back into SDA...")
                finished_success, _ = sda_session.handle_response(alice_resp_json)
                log_info(f"SDA session state: {sda_session.state}")
                
                if finished_success:
                    log_success(f"Alignment Successful! Aligned service profile: '{sda_session.get_service_profile()}'")
                else:
                    log_error("Alignment failed.")
            else:
                log_error(f"Second call failed: {res2['error_message']}")
    else:
        log_error("Unexpected success on invalid first attempt!")

    log_header("Interactive Demo Completed Successfully!")

if __name__ == "__main__":
    main()
