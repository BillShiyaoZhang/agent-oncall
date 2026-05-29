import base64
import io
import json
import time
import sys
import pytest

from agent_oncall import (
    AgentOnCall,
    crypto,
    MockCommAdapter,
    TrustDatabase,
    PolicyEngine,
    sign_capability_token,
    verify_capability_token,
    TIER_1_FAMILY,
    TIER_2_FRIEND,
    TIER_3_STRANGER,
    InteractiveHITLHandler,
    ServiceDescriptionAlignment,
    AlignmentState,
    StdinStdoutHandler
)

@pytest.fixture
def keys():
    alice_priv, alice_pub = crypto.generate_keypair()
    bob_priv, bob_pub = crypto.generate_keypair()
    charlie_priv, charlie_pub = crypto.generate_keypair()
    return {
        "alice": (alice_priv, alice_pub, crypto.private_key_to_hex(alice_priv), crypto.public_key_to_hex(alice_pub)),
        "bob": (bob_priv, bob_pub, crypto.private_key_to_hex(bob_priv), crypto.public_key_to_hex(bob_pub)),
        "charlie": (charlie_priv, charlie_pub, crypto.private_key_to_hex(charlie_priv), crypto.public_key_to_hex(charlie_pub)),
    }

def test_crypto_helpers():
    priv, pub = crypto.generate_keypair()
    priv_hex = crypto.private_key_to_hex(priv)
    pub_hex = crypto.public_key_to_hex(pub)
    
    loaded_priv = crypto.load_private_key_from_hex(priv_hex)
    loaded_pub = crypto.load_public_key_from_hex(pub_hex)
    
    payload = b"hello test crypto"
    sig = crypto.sign_payload(loaded_priv, payload)
    
    assert crypto.verify_signature(loaded_pub, payload, sig) is True
    assert crypto.verify_signature(loaded_pub, payload + b"bad", sig) is False
    assert crypto.verify_signature(loaded_pub, payload, sig[:-2] + "00") is False

def test_schema_validation(keys):
    comm = MockCommAdapter()
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm
    )
    comm.register_agent(alice.agent_urn, alice)
    
    # Register an intent with JSON Schema
    input_schema = {
        "type": "object",
        "properties": {
            "event_name": {"type": "string"},
            "duration": {"type": "integer"}
        },
        "required": ["event_name"]
    }
    
    def dummy_handler(sender, args):
        return {"status": "booked", "event": args["event_name"]}
        
    alice.register_intent(
        "calendar.book_event",
        "Book a calendar event",
        input_schema,
        dummy_handler,
        requires_hitl=False
    )
    
    # Configure Bob as a Family contact in Alice's trust db so policy check passes
    alice.trust_db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_1_FAMILY)
    
    # Set up Bob agent
    bob = AgentOnCall(
        agent_urn="urn:hermes:agent:bob",
        private_key_hex=keys["bob"][2],
        comm_adapter=comm
    )
    comm.register_agent(bob.agent_urn, bob)
    bob.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_1_FAMILY)
    
    # 1. Successful call
    res = bob.call_remote("urn:hermes:agent:alice", "calendar.book_event", {"event_name": "Lunch", "duration": 60})
    assert res["success"] is True
    assert res["result"]["status"] == "booked"
    
    # 2. Schema mismatch (duration is not integer)
    res = bob.call_remote("urn:hermes:agent:alice", "calendar.book_event", {"event_name": "Lunch", "duration": "sixty"})
    assert res["success"] is False
    assert "Schema validation failed" in res["error_message"]

def test_trust_tiers_and_discovery(keys):
    comm = MockCommAdapter()
    
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm
    )
    comm.register_agent(alice.agent_urn, alice)
    
    # Register intents
    alice.register_intent(
        "calendar.query_availability",
        "Query calendar",
        {"type": "object"},
        lambda s, a: {"available": True}
    )
    alice.register_intent(
        "calendar.book_event",
        "Book event",
        {"type": "object"},
        lambda s, a: {"booked": True}
    )
    
    # Configure relationships in Alice
    # Bob is Friend
    alice.trust_db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_2_FRIEND)
    # Charlie is Stranger
    alice.trust_db.add_contact("urn:hermes:agent:charlie", keys["charlie"][3], TIER_3_STRANGER)
    
    # Setup Bob
    bob = AgentOnCall(
        agent_urn="urn:hermes:agent:bob",
        private_key_hex=keys["bob"][2],
        comm_adapter=comm
    )
    comm.register_agent(bob.agent_urn, bob)
    bob.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_2_FRIEND)
    
    # Setup Charlie
    charlie = AgentOnCall(
        agent_urn="urn:hermes:agent:charlie",
        private_key_hex=keys["charlie"][2],
        comm_adapter=comm
    )
    comm.register_agent(charlie.agent_urn, charlie)
    charlie.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_3_STRANGER)
    
    # Bob discovers Alice's capabilities.
    # Policy says:
    #   - query_availability: allowed for Family & Friend. Bob (Friend) should see this.
    #   - book_event: allowed for Family. Bob (Friend) should NOT see this.
    bob_discovery = bob.discover_remote("urn:hermes:agent:alice")
    bob_intents = [x["name"] for x in bob_discovery]
    assert "calendar.query_availability" in bob_intents
    assert "calendar.book_event" not in bob_intents
    
    # Charlie (stranger) discovers Alice's capabilities.
    # Policy says both require at least Friend. Charlie should get an empty list.
    charlie_discovery = charlie.discover_remote("urn:hermes:agent:alice")
    assert len(charlie_discovery) == 0

def test_capability_token_delegation(keys):
    comm = MockCommAdapter()
    
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm
    )
    comm.register_agent(alice.agent_urn, alice)
    
    # Register intent
    alice.register_intent(
        "calendar.book_event",
        "Book event",
        {"type": "object"},
        lambda s, a: {"booked": True},
        resource="calendar",
        action="write"
    )
    
    # Alice's trust database
    # Charlie is a Stranger (so Charlie normally can't book events)
    alice.trust_db.add_contact("urn:hermes:agent:charlie", keys["charlie"][3], TIER_3_STRANGER)
    # Alice trusts herself as Family (default), and Charlie trusts Alice
    charlie = AgentOnCall(
        agent_urn="urn:hermes:agent:charlie",
        private_key_hex=keys["charlie"][2],
        comm_adapter=comm
    )
    comm.register_agent(charlie.agent_urn, charlie)
    charlie.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_2_FRIEND)
    
    # 1. Charlie calls directly -> gets blocked (no trust)
    res = charlie.call_remote("urn:hermes:agent:alice", "calendar.book_event", {})
    assert res["success"] is False
    assert "Policy block" in res["error_message"]
    
    # 2. Alice issues a HCT capability token delegating calendar.write to Charlie
    # Note: Alice is the owner, so her URN is the issuer.
    token = sign_capability_token(
        private_key_hex=keys["alice"][2],
        issuer_urn="urn:hermes:agent:alice",
        audience_urn="urn:hermes:agent:charlie",
        expires_in_seconds=60,
        constraints=[{"resource": "calendar", "action": "write"}]
    )
    
    # Charlie calls carrying the token -> allowed!
    res = charlie.call_remote("urn:hermes:agent:alice", "calendar.book_event", {}, hct_token=token)
    assert res["success"] is True
    assert res["result"]["booked"] is True

def test_hitl_interceptor(keys):
    comm = MockCommAdapter()
    
    # Config HITL with default_response=False (rejection)
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm,
        hitl_handler=InteractiveHITLHandler(default_response=False)
    )
    comm.register_agent(alice.agent_urn, alice)
    alice.trust_db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_1_FAMILY)
    
    # Intent requires HITL
    alice.register_intent(
        "calendar.delete_all",
        "Clear calendar",
        {"type": "object"},
        lambda s, a: {"deleted": True},
        requires_hitl=True
    )
    
    bob = AgentOnCall(
        agent_urn="urn:hermes:agent:bob",
        private_key_hex=keys["bob"][2],
        comm_adapter=comm
    )
    comm.register_agent(bob.agent_urn, bob)
    bob.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_1_FAMILY)
    
    # Call rejected by HITL
    res = bob.call_remote("urn:hermes:agent:alice", "calendar.delete_all", {})
    assert res["success"] is False
    assert "User rejected the operation" in res["error_message"]
    
    # Change HITL default_response to True (approved)
    alice.hitl_handler.default_response = True
    res = bob.call_remote("urn:hermes:agent:alice", "calendar.delete_all", {})
    assert res["success"] is True
    assert res["result"]["deleted"] is True

def test_service_description_alignment():
    # Service Description Alignment test
    # Description: "Allows querying my calendar. Takes a date string (YYYY-MM-DD)."
    ds = "Allows querying my calendar. Takes a date string (YYYY-MM-DD)."
    
    # Pluggable aligner simulator
    def mock_aligner(description, history):
        # Generates profile, sample request, desired response
        profile = "calendar_query"
        q = "query_availability(date=2026-06-01)"
        rd = "available=true"
        return profile, q, rd
        
    # Matcher simulator
    def mock_matcher(response, expected):
        return response.strip() == expected.strip()
        
    sda = ServiceDescriptionAlignment(
        ds=ds,
        target_urn="urn:hermes:agent:bob",
        aligner_cb=mock_aligner,
        matcher_cb=mock_matcher,
        max_attempts=3
    )
    
    assert sda.state == AlignmentState.INIT
    
    # Trigger alignment
    q = sda.start_alignment()
    assert q == "query_availability(date=2026-06-01)"
    assert sda.state == AlignmentState.WAITING_FOR_RESPONSE
    
    # Receive matching response -> success
    success, next_q = sda.handle_response("available=true")
    assert success is True
    assert next_q is None
    assert sda.state == AlignmentState.SUCCESS
    assert sda.get_service_profile() == "calendar_query"

def test_stdin_stdout_handler(keys, monkeypatch):
    # Tests StdinStdoutHandler (模式 A integration)
    comm = MockCommAdapter()
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm
    )
    # Register dummy intent
    alice.register_intent(
        "hello",
        "Say hello",
        {"type": "object"},
        lambda s, a: {"hello": "world"}
    )
    alice.trust_db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_2_FRIEND)

    handler = StdinStdoutHandler(alice)
    
    # 1. Create a discovery request envelope from Bob to Alice
    bob = AgentOnCall(
        agent_urn="urn:hermes:agent:bob",
        private_key_hex=keys["bob"][2],
        comm_adapter=comm
    )
    # Let bob construct the raw envelope bytes
    envelope = bob._execute_discovery # Just use discover_remote logic but output envelope string
    
    # We can mock the discovery request envelope directly
    from agent_oncall.pb import agent_oncall_pb2
    req_envelope = agent_oncall_pb2.OnCallEnvelope()
    req_envelope.version = "1.0.0"
    req_envelope.timestamp = int(time.time())
    disc = req_envelope.discovery_request
    disc.request_id = "test-req-id"
    disc.query_urn = "urn:hermes:agent:bob"
    
    # Sign it using Bob's key
    envelope_copy = agent_oncall_pb2.OnCallEnvelope()
    envelope_copy.CopyFrom(req_envelope)
    envelope_copy.signature = ""
    serialized_bytes = envelope_copy.SerializeToString()
    private_key = crypto.load_private_key_from_hex(keys["bob"][2])
    sig_hex = crypto.sign_payload(private_key, serialized_bytes)
    req_envelope.signature = sig_hex
    
    envelope_bytes = req_envelope.SerializeToString()
    payload_base64 = base64.b64encode(envelope_bytes).decode('utf-8')
    
    # Input event JSON
    input_json = {
        "event": "message_received",
        "sender_urn": "urn:hermes:agent:bob",
        "payload_base64": payload_base64
    }
    
    # Mock sys.stdin and sys.stdout
    stdin_mock = io.StringIO(json.dumps(input_json) + "\n")
    stdout_mock = io.StringIO()
    
    monkeypatch.setattr(sys, "stdin", stdin_mock)
    monkeypatch.setattr(sys, "stdout", stdout_mock)
    
    handler.run_loop()
    
    output_lines = stdout_mock.getvalue().strip().split("\n")
    assert len(output_lines) == 2 # "started" banner and response event
    assert output_lines[0] == "agent-oncall stdin handler started"
    
    resp_data = json.loads(output_lines[1])
    assert resp_data["event"] == "send_reply"
    
    # Decrypt or parse the reply envelope
    reply_bytes = base64.b64decode(resp_data["payload_base64"])
    reply_envelope = agent_oncall_pb2.OnCallEnvelope()
    reply_envelope.ParseFromString(reply_bytes)
    
    assert reply_envelope.WhichOneof("payload") == "discovery_response"
    assert reply_envelope.discovery_response.request_id == "test-req-id"
    assert len(reply_envelope.discovery_response.intents) == 1
    assert reply_envelope.discovery_response.intents[0].name == "hello"
