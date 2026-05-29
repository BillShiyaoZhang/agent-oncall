import base64
import json
import time
from typing import List, Dict, Tuple
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

# Colors for nice logging
C_GREEN = "\033[92m"
C_BLUE = "\033[94m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_BOLD = "\033[1m"
C_END = "\033[0m"

def log_section(title: str):
    print(f"\n{C_BOLD}{C_BLUE}=== {title} ==={C_END}")

def log_info(msg: str):
    print(f"ℹ️  {msg}")

def log_success(msg: str):
    print(f"{C_GREEN}✅ {msg}{C_END}")

def log_warning(msg: str):
    print(f"{C_YELLOW}⚠️  {msg}{C_END}")

def log_error(msg: str):
    print(f"{C_RED}❌ {msg}{C_END}")

def main():
    log_section("1. INITIALIZING AGENTS")
    
    # Setup communication adapter
    comm = MockCommAdapter()
    
    # Generate Ed25519 keys
    alice_priv, alice_pub = crypto.generate_keypair()
    bob_priv, bob_pub = crypto.generate_keypair()
    charlie_priv, charlie_pub = crypto.generate_keypair()
    
    alice_urn = "urn:hermes:agent:alice"
    bob_urn = "urn:hermes:agent:bob"
    charlie_urn = "urn:hermes:agent:charlie"
    
    alice_pub_hex = crypto.public_key_to_hex(alice_pub)
    bob_pub_hex = crypto.public_key_to_hex(bob_pub)
    charlie_pub_hex = crypto.public_key_to_hex(charlie_pub)
    
    log_info(f"Alice URN: {alice_urn}")
    log_info(f"Bob URN:   {bob_urn}")
    log_info(f"Charlie URN: {charlie_urn}")
    
    # Setup Alice
    # We configure Alice's HITL handler with auto-approval = True for the script,
    # but we can change it to show manual approval.
    alice_hitl = InteractiveHITLHandler(default_response=True)
    alice = AgentOnCall(
        agent_urn=alice_urn,
        private_key_hex=crypto.private_key_to_hex(alice_priv),
        comm_adapter=comm,
        hitl_handler=alice_hitl
    )
    comm.register_agent(alice_urn, alice)
    
    # Setup Bob
    bob = AgentOnCall(
        agent_urn=bob_urn,
        private_key_hex=crypto.private_key_to_hex(bob_priv),
        comm_adapter=comm
    )
    comm.register_agent(bob_urn, bob)
    
    # Setup Charlie
    charlie = AgentOnCall(
        agent_urn=charlie_urn,
        private_key_hex=crypto.private_key_to_hex(charlie_priv),
        comm_adapter=comm
    )
    comm.register_agent(charlie_urn, charlie)
    
    # Configure Trust relations on Alice
    # Bob is Friend, Charlie is Stranger. Alice is Family (default).
    alice.trust_db.add_contact(bob_urn, bob_pub_hex, TIER_2_FRIEND)
    alice.trust_db.add_contact(charlie_urn, charlie_pub_hex, TIER_3_STRANGER)
    
    # Configure Bob's trust db
    bob.trust_db.add_contact(alice_urn, alice_pub_hex, TIER_2_FRIEND)
    
    # Configure Charlie's trust db
    charlie.trust_db.add_contact(alice_urn, alice_pub_hex, TIER_3_STRANGER)
    
    # Register Alice's Intents
    # Intent 1: query calendar (Friend / Family allowed, no HITL)
    alice.register_intent(
        name="calendar.query_availability",
        description="Check if Alice has free time slot.",
        input_schema={
            "type": "object",
            "properties": {
                "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}
            },
            "required": ["date"]
        },
        handler=lambda sender, args: {"date": args["date"], "available": True},
        requires_hitl=False,
        resource="calendar",
        action="read"
    )
    
    # Intent 2: book appointment (Family allowed, requires HITL)
    alice.register_intent(
        name="calendar.book_event",
        description="Book an appointment on Alice's calendar.",
        input_schema={
            "type": "object",
            "properties": {
                "event_title": {"type": "string"},
                "time": {"type": "string"}
            },
            "required": ["event_title", "time"]
        },
        handler=lambda sender, args: {"status": "success", "title": args["event_title"]},
        requires_hitl=True,
        resource="calendar",
        action="write"
    )
    
    log_success("Agents initialized. Alice registered 'calendar.query_availability' and 'calendar.book_event'.")
    
    log_section("2. DYNAMIC DISCOVERY & PASSIVE DISCLOSURE")
    
    log_info("Bob (Friend) discovers Alice's capabilities...")
    bob_discovered = bob.discover_remote(alice_urn)
    log_info(f"Bob sees: {[x['name'] for x in bob_discovered]}")
    assert "calendar.query_availability" in [x["name"] for x in bob_discovered]
    assert "calendar.book_event" not in [x["name"] for x in bob_discovered]
    log_success("Bob only sees 'calendar.query_availability' (Passive Disclosure matches Friend tier).")
    
    print()
    log_info("Charlie (Stranger) discovers Alice's capabilities...")
    charlie_discovered = charlie.discover_remote(alice_urn)
    log_info(f"Charlie sees: {[x['name'] for x in charlie_discovered]}")
    assert len(charlie_discovered) == 0
    log_success("Charlie sees nothing (Passive Disclosure denies Strangers).")

    log_section("3. DIRECT POLICY CHECKING")
    
    log_info("Bob calls 'calendar.query_availability' on Alice...")
    res = bob.call_remote(alice_urn, "calendar.query_availability", {"date": "2026-06-01"})
    if res["success"]:
        log_success(f"Success! Result: {res['result']}")
    else:
        log_error(f"Failed: {res['error_message']}")
        
    print()
    log_info("Bob calls 'calendar.book_event' on Alice...")
    res = bob.call_remote(alice_urn, "calendar.book_event", {"event_title": "Lunch", "time": "12:00"})
    if res["success"]:
        log_success(f"Success! Result: {res['result']}")
    else:
        log_warning(f"Blocked by Policy Engine: {res['error_message']}")
        
    print()
    log_info("Charlie calls 'calendar.book_event' on Alice...")
    res = charlie.call_remote(alice_urn, "calendar.book_event", {"event_title": "Hack", "time": "00:00"})
    if res["success"]:
        log_success(f"Success! Result: {res['result']}")
    else:
        log_warning(f"Blocked by Policy Engine: {res['error_message']}")

    log_section("4. DELEGATED ACCESS VIA HCT (CAPABILITY TOKEN)")
    
    log_info("Alice issues a HCT capability token delegating calendar:write to Charlie...")
    token = sign_capability_token(
        private_key_hex=crypto.private_key_to_hex(alice_priv),
        issuer_urn=alice_urn,
        audience_urn=charlie_urn,
        expires_in_seconds=60,
        constraints=[{"resource": "calendar", "action": "write"}]
    )
    log_info("Token issued. Charlie now makes the call carrying this token...")
    
    # 1. Reject first to show HITL interceptor
    log_info("Setting Alice HITL to reject incoming high-risk calls...")
    alice.hitl_handler.default_response = False
    res = charlie.call_remote(alice_urn, "calendar.book_event", {"event_title": "Urgent Meeting", "time": "14:00"}, hct_token=token)
    if res["success"]:
        log_success(f"Success! Result: {res['result']}")
    else:
        log_warning(f"Call rejected: {res['error_message']} (HCT was valid, but rejected by Human-in-the-loop interceptor)")
        
    # 2. Approve now
    print()
    log_info("Setting Alice HITL to approve incoming high-risk calls...")
    alice.hitl_handler.default_response = True
    res = charlie.call_remote(alice_urn, "calendar.book_event", {"event_title": "Urgent Meeting", "time": "14:00"}, hct_token=token)
    if res["success"]:
        log_success(f"Success! Charlie bypassed direct policy via HCT delegation. Result: {res['result']}")
    else:
        log_error(f"Failed: {res['error_message']}")

    log_section("5. SERVICE DESCRIPTION ALIGNMENT (SDA)")
    
    log_info("Bob receives an ambiguous service description from Alice:")
    ambiguous_ds = "My intent name is calendar.query_availability. Inputs should have 'date' as a string in standard date format."
    log_info(f"  Description: '{ambiguous_ds}'")
    
    # Aligner Callback simulating Bob's LLM reasoning
    def bob_llm_aligner(description: str, history: list) -> Tuple[str, str, str]:
        log_info(f"[LLM Aligner] Interpreting service description...")
        # Simulates interpreting the description
        profile = "calendar.query_availability"
        sample_request = json.dumps({"date": "2026-06-01"})
        desired_response = json.dumps({"date": "2026-06-01", "available": True})
        return profile, sample_request, desired_response

    # Matcher Callback simulating Bob's LLM similarity match
    def bob_llm_matcher(response: str, desired: str) -> bool:
        log_info(f"[LLM Matcher] Comparing response '{response}' with expected '{desired}'...")
        r_dict = json.loads(response)
        d_dict = json.loads(desired)
        return r_dict.get("available") == d_dict.get("available")

    sda_session = ServiceDescriptionAlignment(
        ds=ambiguous_ds,
        target_urn=alice_urn,
        aligner_cb=bob_llm_aligner,
        matcher_cb=bob_llm_matcher
    )
    
    # Bob starts alignment
    sample_req_json = sda_session.start_alignment()
    log_info(f"Bob generated sample request: {sample_req_json}")
    
    # Bob sends the sample request call to Alice
    args = json.loads(sample_req_json)
    log_info("Bob invoking Alice's intent with the sample request...")
    res = bob.call_remote(alice_urn, "calendar.query_availability", args)
    
    if res["success"]:
        alice_response_json = json.dumps(res["result"])
        log_info(f"Alice responded: {alice_response_json}")
        
        # Bob handles the response and matches
        aligned, next_req = sda_session.handle_response(alice_response_json)
        if aligned:
            log_success(f"Alignment Success! Aligned profile: '{sda_session.get_service_profile()}'")
        else:
            log_error("Alignment failed.")
    else:
        log_error(f"Call failed: {res['error_message']}")

    log_section("DEMO COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    main()
