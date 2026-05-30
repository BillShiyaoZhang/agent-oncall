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
    alice.trust_db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_2_FRIEND, ["hello"])

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


def test_trust_db_json_serialization(keys, tmp_path):
    db_file = tmp_path / "test_trust_db.json"
    
    # 1. Create and populate db
    db = TrustDatabase(file_path=str(db_file))
    db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_2_FRIEND, ["calendar.*"])
    db.set_tier_permissions(TIER_3_STRANGER, ["info.*"])
    
    # 2. Reload in a new instance and verify
    db2 = TrustDatabase(file_path=str(db_file))
    assert db2.get_contact_trust_level("urn:hermes:agent:bob") == TIER_2_FRIEND
    assert db2.get_contact_pubkey("urn:hermes:agent:bob") == keys["bob"][3]
    assert db2.get_contact_allowed_intents("urn:hermes:agent:bob") == ["calendar.*"]
    assert db2.tier_permissions[TIER_3_STRANGER] == ["info.*"]


def test_urn_specific_permission_override(keys):
    comm = MockCommAdapter()
    
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm
    )
    comm.register_agent(alice.agent_urn, alice)
    
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
    
    # Charlie is a Stranger, but gets a URN-specific override for query_availability
    alice.trust_db.add_contact("urn:hermes:agent:charlie", keys["charlie"][3], TIER_3_STRANGER, ["calendar.query_availability"])
    
    charlie = AgentOnCall(
        agent_urn="urn:hermes:agent:charlie",
        private_key_hex=keys["charlie"][2],
        comm_adapter=comm
    )
    comm.register_agent(charlie.agent_urn, charlie)
    charlie.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_3_STRANGER)
    
    # 1. Charlie calls query_availability -> allowed due to URN override!
    res = charlie.call_remote("urn:hermes:agent:alice", "calendar.query_availability", {})
    assert res["success"] is True
    assert res["result"]["available"] is True
    
    # 2. Charlie calls book_event -> blocked (override only covers query_availability)
    res = charlie.call_remote("urn:hermes:agent:alice", "calendar.book_event", {})
    assert res["success"] is False
    assert "Denied" in res["error_message"]


def test_wildcard_matching(keys):
    comm = MockCommAdapter()
    
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm
    )
    comm.register_agent(alice.agent_urn, alice)
    
    alice.register_intent(
        "calendar.query",
        "Query calendar",
        {"type": "object"},
        lambda s, a: {"success": True}
    )
    alice.register_intent(
        "system.reboot",
        "Reboot system",
        {"type": "object"},
        lambda s, a: {"success": True}
    )
    
    # Setup Charlie as Stranger, with wildcard override for calendar.*
    alice.trust_db.add_contact("urn:hermes:agent:charlie", keys["charlie"][3], TIER_3_STRANGER, ["calendar.*"])
    
    charlie = AgentOnCall(
        agent_urn="urn:hermes:agent:charlie",
        private_key_hex=keys["charlie"][2],
        comm_adapter=comm
    )
    comm.register_agent(charlie.agent_urn, charlie)
    charlie.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_3_STRANGER)
    
    # 1. Charlie calls calendar.query -> matches calendar.* -> allowed
    res = charlie.call_remote("urn:hermes:agent:alice", "calendar.query", {})
    assert res["success"] is True
    
    # 2. Charlie calls system.reboot -> does not match calendar.* -> blocked
    res = charlie.call_remote("urn:hermes:agent:alice", "system.reboot", {})
    assert res["success"] is False
    assert "Denied" in res["error_message"]


def test_crypto_errors(keys):
    # Test verify_signature with invalid signatures/hex strings
    _, pub = crypto.generate_keypair()
    assert crypto.verify_signature(pub, b"test", "invalidhex") is False
    assert crypto.verify_signature(pub, b"test", "ab") is False  # Too short
    assert crypto.verify_signature(pub, b"test", "01" * 64) is False  # Wrong signature

    # Test load_private_key_from_hex with invalid keys
    with pytest.raises(Exception):
        crypto.load_private_key_from_hex("invalidhex")
    with pytest.raises(Exception):
        crypto.load_private_key_from_hex("01" * 31)

    with pytest.raises(Exception):
        crypto.load_public_key_from_hex("invalidhex")
    with pytest.raises(Exception):
        crypto.load_public_key_from_hex("01" * 31)


def test_match_pattern_unit():
    from agent_oncall.policy import match_pattern
    assert match_pattern("*", "any.intent") is True
    assert match_pattern("calendar.*", "calendar.query") is True
    assert match_pattern("calendar.*", "calendar.book") is True
    assert match_pattern("calendar.*", "system.reboot") is False
    assert match_pattern("exact.intent", "exact.intent") is True
    assert match_pattern("exact.intent", "exact.intent.other") is False


def test_trust_db_details(keys, tmp_path):
    # Test load and save with no file path
    db = TrustDatabase(file_path=None)
    db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_2_FRIEND)
    # Should not crash and should update in memory
    assert db.get_contact_trust_level("urn:hermes:agent:bob") == TIER_2_FRIEND
    db.save()
    db.load()
    
    # Test file-based database loading invalid JSON
    bad_db_file = tmp_path / "bad_trust_db.json"
    with open(bad_db_file, "w", encoding="utf-8") as f:
        f.write("{invalid json}")
        
    db_bad = TrustDatabase(file_path=str(bad_db_file))
    # Should load fallback defaults and not crash
    assert db_bad.contacts == {}
    
    # Test remove_contact
    db.remove_contact("urn:hermes:agent:bob")
    assert db.get_contact_trust_level("urn:hermes:agent:bob") == TIER_3_STRANGER
    
    # Test is_trusted
    assert db.is_trusted("urn:hermes:agent:alice") is False # Alice not added
    db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_1_FAMILY)
    assert db.is_trusted("urn:hermes:agent:alice") is True
    db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_3_STRANGER)
    assert db.is_trusted("urn:hermes:agent:alice") is False


def test_verify_capability_token_errors(keys):
    # Create valid token first
    db = TrustDatabase(file_path=None)
    db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_1_FAMILY)
    db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_2_FRIEND)

    # 1. Expired token
    expired_token = sign_capability_token(
        private_key_hex=keys["alice"][2],
        issuer_urn="urn:hermes:agent:alice",
        audience_urn="urn:hermes:agent:bob",
        expires_in_seconds=-10,
        constraints=[{"resource": "calendar", "action": "write"}]
    )
    allowed, reason = verify_capability_token(
        expired_token, "urn:hermes:agent:bob", "calendar", "write", db
    )
    assert allowed is False
    assert "expired" in reason

    # 2. Audience mismatch
    token = sign_capability_token(
        private_key_hex=keys["alice"][2],
        issuer_urn="urn:hermes:agent:alice",
        audience_urn="urn:hermes:agent:bob",
        expires_in_seconds=60,
        constraints=[{"resource": "calendar", "action": "write"}]
    )
    allowed, reason = verify_capability_token(
        token, "urn:hermes:agent:charlie", "calendar", "write", db
    )
    assert allowed is False
    assert "audience" in reason

    # 3. Unregistered issuer
    token_unreg = sign_capability_token(
        private_key_hex=keys["charlie"][2],
        issuer_urn="urn:hermes:agent:charlie",
        audience_urn="urn:hermes:agent:bob",
        expires_in_seconds=60,
        constraints=[{"resource": "calendar", "action": "write"}]
    )
    allowed, reason = verify_capability_token(
        token_unreg, "urn:hermes:agent:bob", "calendar", "write", db
    )
    assert allowed is False
    assert "not in trust database" in reason

    # 4. Signature validation failure
    bad_sig_token = sign_capability_token(
        private_key_hex=keys["alice"][2],
        issuer_urn="urn:hermes:agent:alice",
        audience_urn="urn:hermes:agent:bob",
        expires_in_seconds=60,
        constraints=[{"resource": "calendar", "action": "write"}]
    )
    # Modify signature
    bad_sig_token.signature = b"0" * 64
    allowed, reason = verify_capability_token(
        bad_sig_token, "urn:hermes:agent:bob", "calendar", "write", db
    )
    assert allowed is False
    assert "Signature verification failed" in reason or "Token signature verification failed" in reason

    # 5. Mismatched constraints
    allowed, reason = verify_capability_token(
        token, "urn:hermes:agent:bob", "calendar", "read", db
    )
    assert allowed is False
    assert "Constraints do not allow" in reason

    allowed, reason = verify_capability_token(
        token, "urn:hermes:agent:bob", "files", "write", db
    )
    assert allowed is False
    assert "Constraints do not allow" in reason

    # 6. Wildcard resource constraint
    wildcard_token = sign_capability_token(
        private_key_hex=keys["alice"][2],
        issuer_urn="urn:hermes:agent:alice",
        audience_urn="urn:hermes:agent:bob",
        expires_in_seconds=60,
        constraints=[{"resource": "*", "action": "write"}]
    )
    allowed, reason = verify_capability_token(
        wildcard_token, "urn:hermes:agent:bob", "calendar", "write", db
    )
    assert allowed is True

    # 7. Wildcard action constraint
    wildcard_token2 = sign_capability_token(
        private_key_hex=keys["alice"][2],
        issuer_urn="urn:hermes:agent:alice",
        audience_urn="urn:hermes:agent:bob",
        expires_in_seconds=60,
        constraints=[{"resource": "calendar", "action": "*"}]
    )
    allowed, reason = verify_capability_token(
        wildcard_token2, "urn:hermes:agent:bob", "calendar", "read", db
    )
    assert allowed is True


def test_interactive_hitl_handler(monkeypatch):
    # Test default response
    handler = InteractiveHITLHandler(default_response=True)
    assert handler.approve_call("urn:bob", "calendar.book", "{}") is True

    handler_refuse = InteractiveHITLHandler(default_response=False)
    assert handler_refuse.approve_call("urn:bob", "calendar.book", "{}") is False

    # Test console input approval
    handler_interactive = InteractiveHITLHandler(default_response=None)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert handler_interactive.approve_call("urn:bob", "calendar.book", "{}") is True

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert handler_interactive.approve_call("urn:bob", "calendar.book", "{}") is False

    # Test KeyboardInterrupt / EOFError handling
    def mock_input_interrupt(prompt):
        raise KeyboardInterrupt()
    monkeypatch.setattr("builtins.input", mock_input_interrupt)
    assert handler_interactive.approve_call("urn:bob", "calendar.book", "{}") is False


def test_subprocess_comm_adapter(keys, monkeypatch):
    from agent_oncall.comm import SubprocessCommAdapter
    adapter = SubprocessCommAdapter("/bin/agent-comm", keys_dir="/tmp/keys", bootstrap_addr="127.0.0.1:4000")
    
    # Test _get_base_args
    args = adapter._get_base_args()
    assert args == ["/bin/agent-comm", "--keysdir", "/tmp/keys", "--bootstrap", "127.0.0.1:4000"]

    # Test send_message command arguments
    mock_run_args = None
    class MockCompletedProcess:
        def __init__(self, returncode, stderr=""):
            self.returncode = returncode
            self.stderr = stderr
            
    def mock_subprocess_run(cmd, capture_output, text):
        nonlocal mock_run_args
        mock_run_args = cmd
        return MockCompletedProcess(returncode=0)
        
    monkeypatch.setattr("subprocess.run", mock_subprocess_run)
    adapter.send_message("urn:hermes:agent:alice", b"test envelope bytes")
    
    assert mock_run_args is not None
    assert "/bin/agent-comm" in mock_run_args
    assert "send" in mock_run_args
    assert "urn:hermes:agent:alice" in mock_run_args
    # Verify base64 message content
    base64_payload = base64.b64encode(b"test envelope bytes").decode('utf-8')
    assert base64_payload in mock_run_args

    # Test send_message failure raising RuntimeError
    def mock_subprocess_run_fail(cmd, capture_output, text):
        return MockCompletedProcess(returncode=1, stderr="Failed to connect to peer")
    monkeypatch.setattr("subprocess.run", mock_subprocess_run_fail)
    with pytest.raises(RuntimeError) as exc_info:
        adapter.send_message("urn:hermes:agent:alice", b"test envelope bytes")
    assert "Failed to connect to peer" in str(exc_info.value)

    # Test listen background Popen and parsing
    class MockStdout:
        def __init__(self, lines):
            self.lines = lines
            self.index = 0
        def readline(self):
            if self.index < len(self.lines):
                val = self.lines[self.index]
                self.index += 1
                return val
            return ""
            
    class MockPopen:
        def __init__(self, cmd, stdout, stderr, text, bufsize):
            self.stdout = MockStdout([
                "[urn:hermes:agent:bob] 💬 " + base64.b64encode(b"envelope1").decode('utf-8') + "\n",
                "from urn:hermes:agent:bob: \" " + base64.b64encode(b"envelope2").decode('utf-8') + "\"\n",
                "\n"
            ])
        def terminate(self):
            pass
        def wait(self, timeout=None):
            pass
            
    monkeypatch.setattr("subprocess.Popen", MockPopen)
    
    callback_calls = []
    def mock_callback(sender, data):
        callback_calls.append((sender, data))
        return b"reply_bytes"
        
    adapter.register_receive_callback(mock_callback)
    
    # We must also mock adapter.send_message to capture replies
    sent_replies = []
    def mock_send_message(target_urn, envelope_bytes):
        sent_replies.append((target_urn, envelope_bytes))
    monkeypatch.setattr(adapter, "send_message", mock_send_message)
    
    adapter.start()
    time.sleep(0.1) # Wait for thread to run
    adapter.stop()
    
    assert len(callback_calls) >= 1
    # Check that we parsed the sender URN and payload correctly
    assert callback_calls[0][0] == "urn:hermes:agent:bob"
    assert callback_calls[0][1] == b"envelope1"
    # Verification of sending response back
    assert len(sent_replies) >= 1
    assert sent_replies[0] == ("urn:hermes:agent:bob", b"reply_bytes")


def test_stdin_stdout_handler_errors(keys, monkeypatch):
    from agent_oncall import AgentOnCall, MockCommAdapter
    comm = MockCommAdapter()
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm
    )
    handler = StdinStdoutHandler(alice)
    
    stdout_mock = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_mock)
    
    # 1. Invalid JSON format on stdin
    stdin_mock = io.StringIO("invalid json string\n")
    monkeypatch.setattr(sys, "stdin", stdin_mock)
    handler.run_loop()
    assert "Invalid JSON format" in stdout_mock.getvalue()
    
    # 2. Unsupported event type
    stdout_mock.truncate(0)
    stdout_mock.seek(0)
    stdin_mock = io.StringIO('{"event": "other_event"}\n')
    monkeypatch.setattr(sys, "stdin", stdin_mock)
    handler.run_loop()
    assert "Unsupported event type" in stdout_mock.getvalue()

    # 3. Missing keys
    stdout_mock.truncate(0)
    stdout_mock.seek(0)
    stdin_mock = io.StringIO('{"event": "message_received", "sender_urn": "urn:bob"}\n')
    monkeypatch.setattr(sys, "stdin", stdin_mock)
    handler.run_loop()
    assert "Missing sender_urn or payload_base64" in stdout_mock.getvalue()

    # 4. Bad Base64 payload
    stdout_mock.truncate(0)
    stdout_mock.seek(0)
    stdin_mock = io.StringIO('{"event": "message_received", "sender_urn": "urn:bob", "payload_base64": "invalid_base64!!!"}\n')
    monkeypatch.setattr(sys, "stdin", stdin_mock)
    handler.run_loop()
    assert "Base64 decoding failed" in stdout_mock.getvalue()

    # 5. Core handler throwing exception (invalid envelope bytes parse fail)
    stdout_mock.truncate(0)
    stdout_mock.seek(0)
    stdin_mock = io.StringIO('{"event": "message_received", "sender_urn": "urn:bob", "payload_base64": "AAAA"}\n')
    monkeypatch.setattr(sys, "stdin", stdin_mock)
    handler.run_loop()
    # The agent handle_incoming_envelope_bytes will catch pb parse error and return error envelope bytes,
    # so we should get a "send_reply" containing the parsed error envelope rather than an "error" event.
    assert "send_reply" in stdout_mock.getvalue()


def test_agent_oncall_core_errors(keys, monkeypatch):
    comm = MockCommAdapter()
    
    # 1. Clock skew test
    alice = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=comm,
        clock_skew_tolerance=10 # very short tolerance
    )
    comm.register_agent(alice.agent_urn, alice)
    alice.trust_db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_1_FAMILY)
    alice.register_intent(
        "calendar.query_availability",
        "Query calendar",
        {"type": "object"},
        lambda s, a: {"available": True}
    )

    bob = AgentOnCall(
        agent_urn="urn:hermes:agent:bob",
        private_key_hex=keys["bob"][2],
        comm_adapter=comm
    )
    comm.register_agent(bob.agent_urn, bob)
    bob.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_1_FAMILY)

    # Modify Bob's message timestamp to be far in the past
    from agent_oncall.pb import agent_oncall_pb2
    envelope = agent_oncall_pb2.OnCallEnvelope()
    envelope.version = "1.0.0"
    envelope.timestamp = int(time.time()) - 100 # skewed
    
    disc = envelope.discovery_request
    disc.request_id = "test-skew-id"
    disc.query_urn = bob.agent_urn
    bob._sign_envelope(envelope)
    
    reply_bytes = alice.handle_incoming_envelope_bytes(bob.agent_urn, envelope.SerializeToString())
    reply_envelope = agent_oncall_pb2.OnCallEnvelope()
    reply_envelope.ParseFromString(reply_bytes)
    
    assert reply_envelope.WhichOneof("payload") == "call_response"
    assert reply_envelope.call_response.success is False
    assert "Timestamp expired/skewed" in reply_envelope.call_response.error_message

    # 2. Async Call Remote response (None return)
    class AsyncCommMock(MockCommAdapter):
        def send_message(self, target_urn, envelope_bytes):
            return None # Simulated async fire-and-forget
            
    async_comm = AsyncCommMock()
    bob_async = AgentOnCall(
        agent_urn="urn:hermes:agent:bob",
        private_key_hex=keys["bob"][2],
        comm_adapter=async_comm
    )
    res = bob_async.call_remote("urn:hermes:agent:alice", "calendar.query_availability", {"date": "2026-06-01"})
    assert res["success"] is False
    assert "Message sent asynchronously" in res["error_message"]

    # 3. Bad signature on response
    class BadResponseCommMock(MockCommAdapter):
        def register_agent(self, urn, agent_instance):
            self.agent = agent_instance
        def send_message(self, target_urn, envelope_bytes):
            # Intercept reply and modify it to have bad signature
            actual_reply = self.agent.handle_incoming_envelope_bytes("urn:hermes:agent:bob", envelope_bytes)
            reply_env = agent_oncall_pb2.OnCallEnvelope()
            reply_env.ParseFromString(actual_reply)
            reply_env.signature = "0" * 64
            return reply_env.SerializeToString()
            
    bad_comm = BadResponseCommMock()
    alice_bad = AgentOnCall(
        agent_urn="urn:hermes:agent:alice",
        private_key_hex=keys["alice"][2],
        comm_adapter=bad_comm
    )
    bad_comm.register_agent(alice_bad.agent_urn, alice_bad)
    alice_bad.trust_db.add_contact("urn:hermes:agent:bob", keys["bob"][3], TIER_1_FAMILY)
    
    bob_bad = AgentOnCall(
        agent_urn="urn:hermes:agent:bob",
        private_key_hex=keys["bob"][2],
        comm_adapter=bad_comm
    )
    bob_bad.trust_db.add_contact("urn:hermes:agent:alice", keys["alice"][3], TIER_1_FAMILY)

    res = bob_bad.call_remote("urn:hermes:agent:alice", "calendar.query_availability", {"date": "2026-06-01"})
    assert res["success"] is False
    assert "Response signature verification failed" in res["error_message"]

    # 4. Core Intent Exception handling
    def crashy_handler(sender, args):
        raise ValueError("Simulated crash")
        
    alice.register_intent("calendar.crash", "Crash test", {"type": "object"}, crashy_handler)
    res = bob.call_remote("urn:hermes:agent:alice", "calendar.crash", {})
    assert res["success"] is False
    assert "Execution failed: Simulated crash" in res["error_message"]

    # 5. Schema verification errors
    # Malformed JSON args string
    call_req = agent_oncall_pb2.CallRequest()
    call_req.request_id = "schema-err-id"
    call_req.caller_urn = bob.agent_urn
    call_req.intent_name = "calendar.query_availability"
    call_req.arguments_json = "{bad json"
    
    envelope_bad_args = agent_oncall_pb2.OnCallEnvelope()
    envelope_bad_args.version = "1.0.0"
    envelope_bad_args.timestamp = int(time.time())
    envelope_bad_args.call_request.CopyFrom(call_req)
    bob._sign_envelope(envelope_bad_args)
    
    reply_bytes = alice.handle_incoming_envelope_bytes(bob.agent_urn, envelope_bad_args.SerializeToString())
    reply_envelope = agent_oncall_pb2.OnCallEnvelope()
    reply_envelope.ParseFromString(reply_bytes)
    assert reply_envelope.call_response.success is False
    assert "Arguments payload is not valid JSON" in reply_envelope.call_response.error_message

    # 6. Category filtering when discovering intents
    alice.register_intent("info.hello", "Hello info", {"type": "object"}, lambda s, a: {})
    disc_list_all = bob.discover_remote(alice.agent_urn)
    assert "calendar.query_availability" in [x["name"] for x in disc_list_all]
    assert "info.hello" in [x["name"] for x in disc_list_all]

    disc_list_filtered = bob.discover_remote(alice.agent_urn, category_filter="calendar")
    assert "calendar.query_availability" in [x["name"] for x in disc_list_filtered]
    assert "info.hello" not in [x["name"] for x in disc_list_filtered]


def test_sda_alignment_errors():
    # 1. Aligner raising an exception transitions SDA to FAILED state
    def failing_aligner(description, history):
        raise RuntimeError("LLM offline")
        
    sda = ServiceDescriptionAlignment(
        ds="ambiguous description",
        target_urn="urn:bob",
        aligner_cb=failing_aligner,
        matcher_cb=lambda r, rd: False
    )
    
    assert sda.state == AlignmentState.INIT
    req = sda.start_alignment()
    assert req is None
    assert sda.state == AlignmentState.FAILED

    # 2. Exceeding max_attempts transitions SDA to FAILED state
    def mock_aligner(description, history):
        return "profile", "query", "expected"
        
    sda_retry = ServiceDescriptionAlignment(
        ds="ambiguous description",
        target_urn="urn:bob",
        aligner_cb=mock_aligner,
        matcher_cb=lambda r, rd: False, # Always mismatch
        max_attempts=2
    )
    
    sda_retry.start_alignment() # attempts = 0, state = WAITING_FOR_RESPONSE
    finished, next_req = sda_retry.handle_response("wrong_response_1")
    assert finished is False
    assert next_req == "query"
    assert sda_retry.state == AlignmentState.WAITING_FOR_RESPONSE
    
    finished2, next_req2 = sda_retry.handle_response("wrong_response_2")
    assert finished2 is False
    assert next_req2 is None
    assert sda_retry.state == AlignmentState.FAILED
    assert sda_retry.attempts == 2

