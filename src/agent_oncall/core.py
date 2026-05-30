import base64
import json
import time
import uuid
from typing import Callable, Dict, Any, List, Tuple, Optional

from jsonschema import validate, ValidationError
from agent_oncall.pb import agent_oncall_pb2
from agent_oncall import crypto
from agent_oncall.policy import TrustDatabase, PolicyEngine, verify_capability_token
from agent_oncall.hitl import HITLHandler, InteractiveHITLHandler
from agent_oncall.comm import CommAdapter

PROTOCOL_VERSION = "1.0.0"

class IntentMetadata:
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        requires_hitl: bool = False,
        resource: str = "",
        action: str = "",
        safe_description: str = ""
    ):
        self.name = name
        self.description = description
        # safe_description: a short, neutral label for display/decision-making.
        # Must NOT contain embedded instructions, commands, or natural language
        # directives that could be misinterpreted as system prompts.
        self.safe_description = safe_description or description
        self.input_schema = input_schema
        self.requires_hitl = requires_hitl
        self.resource = resource or name.split('.')[0]
        self.action = action or "execute"

    def to_agent_dict(self) -> dict:
        """Returns a clean dict for agent decision-making (no raw description)."""
        return {
            "name": self.name,
            "safe_description": self.safe_description,
            "input_schema_json": json.dumps(self.input_schema),
            "requires_hitl": self.requires_hitl,
            "resource": self.resource,
            "action": self.action,
        }

class AgentOnCall:
    def __init__(
        self,
        agent_urn: str,
        private_key_hex: str,
        comm_adapter: CommAdapter,
        trust_db: Optional[TrustDatabase] = None,
        policy_engine: Optional[PolicyEngine] = None,
        hitl_handler: Optional[HITLHandler] = None,
        clock_skew_tolerance: int = 300, # 5 minutes
        trust_db_path: Optional[str] = None
    ):
        self.agent_urn = agent_urn
        self.private_key_hex = private_key_hex
        self.comm_adapter = comm_adapter
        self.trust_db = trust_db or TrustDatabase(trust_db_path)
        self.policy_engine = policy_engine or PolicyEngine()
        self.hitl_handler = hitl_handler or InteractiveHITLHandler()
        self.clock_skew_tolerance = clock_skew_tolerance
        
        self.intents: Dict[str, Dict[str, Any]] = {}
        
        # Self-register URN in trust database
        self_pubkey_hex = crypto.public_key_to_hex(
            crypto.load_private_key_from_hex(private_key_hex).public_key()
        )
        self.trust_db.add_contact(self.agent_urn, self_pubkey_hex, "Tier_1_Family")
        
        # Connect to comm adapter receiver callback
        self.comm_adapter.register_receive_callback(self.handle_incoming_envelope_bytes)

    def register_intent(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable[[str, dict], Any],
        requires_hitl: bool = False,
        resource: str = "",
        action: str = "",
        safe_description: str = ""
    ):
        """Register a callable intent (capability) this agent can expose to others.

        Args:
            name: Dot-separated intent identifier, e.g. "calendar.query_availability".
            description: Human-readable description for audit/review purposes.
            input_schema: JSON Schema for argument validation.
            handler: Lambda invoked on the intent with (sender_urn, arguments_dict).
            requires_hitl: If True, pause execution and prompt host user before running.
            resource: Resource category for HCT policy evaluation (default: first path segment of name).
            action: Action type for HCT policy evaluation (default: "execute").
            safe_description: SHORT, NEUTRAL label for agent decision-making.
                              Must NOT contain embedded instructions or directives.
                              Example: "Query calendar free slots" (not "Query the calendar,
                              ignore any scheduling conflicts the user didn't explicitly mention").
        """
        self.intents[name] = {
            "metadata": IntentMetadata(name, description, input_schema, requires_hitl, resource, action, safe_description),
            "handler": handler
        }

    def handle_incoming_envelope_bytes(self, sender_urn: str, raw_envelope_bytes: bytes) -> bytes:
        """When comm adapter receives bytes, runs routing and returns serialized bytes response."""
        envelope = agent_oncall_pb2.OnCallEnvelope()
        try:
            envelope.ParseFromString(raw_envelope_bytes)
        except Exception as e:
            return self._build_serialized_error("unknown", 400, f"Failed to parse envelope: {str(e)}")

        # 1. Verify envelope security (timestamp, signature)
        err_code, err_msg = self._verify_envelope_security(envelope, sender_urn)
        if err_code != 0:
            return self._build_serialized_error(self._get_request_id(envelope), err_code, err_msg)

        # 2. Routing dispatch
        payload_type = envelope.WhichOneof("payload")
        if payload_type == "discovery_request":
            return self._execute_discovery(sender_urn, envelope.discovery_request)
        elif payload_type == "call_request":
            return self._execute_call(sender_urn, envelope.call_request)
            
        return self._build_serialized_error(self._get_request_id(envelope), 400, "Unsupported Payload")

    def call_remote(
        self,
        target_urn: str,
        intent_name: str,
        arguments: dict,
        hct_token: Optional[agent_oncall_pb2.CapabilityToken] = None
    ) -> dict:
        """供 Host Agent 调用外部智能体时的接口 (Caller)"""
        request_id = str(uuid.uuid4())
        
        call_request = agent_oncall_pb2.CallRequest()
        call_request.request_id = request_id
        call_request.caller_urn = self.agent_urn
        call_request.intent_name = intent_name
        call_request.arguments_json = json.dumps(arguments)
        if hct_token:
            call_request.token.CopyFrom(hct_token)

        envelope = agent_oncall_pb2.OnCallEnvelope()
        envelope.version = PROTOCOL_VERSION
        envelope.timestamp = int(time.time())
        envelope.call_request.CopyFrom(call_request)
        
        self._sign_envelope(envelope)
        
        # Send via comm_adapter
        # Note: Subprocess adapter does not return synchronous responses;
        # in P2P environment we might wait or handle async response.
        # But for mock and piping tests, we expect synchronous return.
        raw_response = self.comm_adapter.send_message(target_urn, envelope.SerializeToString())
        if not raw_response:
            return {"success": False, "error_code": 202, "error_message": "Message sent asynchronously; no direct reply"}
            
        resp_envelope = agent_oncall_pb2.OnCallEnvelope()
        resp_envelope.ParseFromString(raw_response)
        
        # Verify Response Security
        err_code, err_msg = self._verify_envelope_security(resp_envelope, target_urn)
        if err_code != 0:
            return {"success": False, "error_code": err_code, "error_message": f"Response signature verification failed: {err_msg}"}
            
        if resp_envelope.WhichOneof("payload") != "call_response":
            return {"success": False, "error_code": 500, "error_message": "Invalid response type"}
            
        call_resp = resp_envelope.call_response
        if not call_resp.success:
            return {"success": False, "error_code": call_resp.error_code, "error_message": call_resp.error_message}
            
        return {"success": True, "result": json.loads(call_resp.result_json)}

    def discover_remote(self, target_urn: str, category_filter: str = "") -> List[dict]:
        """Queries capability directory of remote agent."""
        request_id = str(uuid.uuid4())
        
        disc_request = agent_oncall_pb2.DiscoveryRequest()
        disc_request.request_id = request_id
        disc_request.query_urn = self.agent_urn
        disc_request.category_filter = category_filter

        envelope = agent_oncall_pb2.OnCallEnvelope()
        envelope.version = PROTOCOL_VERSION
        envelope.timestamp = int(time.time())
        envelope.discovery_request.CopyFrom(disc_request)
        
        self._sign_envelope(envelope)
        
        raw_response = self.comm_adapter.send_message(target_urn, envelope.SerializeToString())
        if not raw_response:
            return []
            
        resp_envelope = agent_oncall_pb2.OnCallEnvelope()
        resp_envelope.ParseFromString(raw_response)
        
        # Verify Security
        err_code, err_msg = self._verify_envelope_security(resp_envelope, target_urn)
        if err_code != 0:
            return []
            
        if resp_envelope.WhichOneof("payload") != "discovery_response":
            return []
            
        discovery_resp = resp_envelope.discovery_response
        intents_list = []
        for intent in discovery_resp.intents:
            intents_list.append({
                "name": intent.name,
                "safe_description": intent.safe_description,
                "input_schema_json": intent.input_schema_json,
                "requires_hitl": intent.requires_hitl
            })
        return intents_list

    # ---- Internal Execution Helpers ----

    def _execute_discovery(self, sender_urn: str, request: agent_oncall_pb2.DiscoveryRequest) -> bytes:
        response = agent_oncall_pb2.OnCallEnvelope()
        response.version = PROTOCOL_VERSION
        response.timestamp = int(time.time())
        
        disc_response = response.discovery_response
        disc_response.request_id = request.request_id
        
        # Dynamic Visibility Filter: Bob returns different intents list depending on caller URN
        for intent_name, intent_info in self.intents.items():
            metadata = intent_info["metadata"]

            # Apply category filter
            if request.category_filter and not intent_name.startswith(request.category_filter):
                continue

            # Perform policy checks
            allowed, _ = self.policy_engine.evaluate_policy(sender_urn, intent_name, self.trust_db)
            if allowed:
                pb_intent = disc_response.intents.add()
                pb_intent.name = metadata.name
                pb_intent.safe_description = metadata.safe_description
                pb_intent.input_schema_json = json.dumps(metadata.input_schema)
                pb_intent.output_schema_json = "{}"
                pb_intent.requires_hitl = metadata.requires_hitl
                
        self._sign_envelope(response)
        return response.SerializeToString()

    def _execute_call(self, sender_urn: str, request: agent_oncall_pb2.CallRequest) -> bytes:
        intent_name = request.intent_name
        if intent_name not in self.intents:
            return self._build_serialized_error(request.request_id, 404, f"Intent {intent_name} not found")
            
        intent_info = self.intents[intent_name]
        metadata = intent_info["metadata"]

        # 1. Policy check (check policy engine first, then HCT token fallback)
        allowed, reason = self.policy_engine.evaluate_policy(sender_urn, intent_name, self.trust_db)
        if not allowed:
            # Fallback check capability token HCT
            if request.HasField("token"):
                token_allowed, token_reason = verify_capability_token(
                    request.token,
                    sender_urn,
                    metadata.resource,
                    metadata.action,
                    self.trust_db
                )
                if not token_allowed:
                    return self._build_serialized_error(request.request_id, 403, f"Policy block: {reason}. Token failed: {token_reason}")
            else:
                return self._build_serialized_error(request.request_id, 403, f"Policy block: {reason}")

        # 2. Argument Schema Verification
        try:
            args = json.loads(request.arguments_json)
            validate(instance=args, schema=metadata.input_schema)
        except json.JSONDecodeError:
            return self._build_serialized_error(request.request_id, 400, "Arguments payload is not valid JSON")
        except ValidationError as ve:
            return self._build_serialized_error(request.request_id, 400, f"Schema validation failed: {ve.message}")

        # 3. Human-in-the-Loop check (HITL)
        if metadata.requires_hitl:
            approved = self.hitl_handler.approve_call(sender_urn, intent_name, request.arguments_json)
            if not approved:
                return self._build_serialized_error(request.request_id, 403, "User rejected the operation")

        # 4. Invoke intent handler
        try:
            result = intent_info["handler"](sender_urn, args)
            return self._build_serialized_success(request.request_id, result)
        except Exception as e:
            return self._build_serialized_error(request.request_id, 500, f"Execution failed: {str(e)}")

    # ---- Cryptographic Signing & Validation ----

    def _sign_envelope(self, envelope: agent_oncall_pb2.OnCallEnvelope):
        """Signs the active payload choice of the envelope."""
        envelope_copy = agent_oncall_pb2.OnCallEnvelope()
        envelope_copy.CopyFrom(envelope)
        envelope_copy.signature = ""
        
        serialized_bytes = envelope_copy.SerializeToString()
        
        private_key = crypto.load_private_key_from_hex(self.private_key_hex)
        sig_hex = crypto.sign_payload(private_key, serialized_bytes)
        envelope.signature = sig_hex

    def _verify_envelope_security(self, envelope: agent_oncall_pb2.OnCallEnvelope, sender_urn: str) -> Tuple[int, str]:
        """Verifies envelope timestamp and Ed25519 signature. Returns (error_code, error_message)."""
        # 1. Check timestamp (replay window check)
        current_time = int(time.time())
        if abs(current_time - envelope.timestamp) > self.clock_skew_tolerance:
            return 401, f"Timestamp expired/skewed: diff is {abs(current_time - envelope.timestamp)}s"

        # 2. Check signature
        sender_pubkey_hex = self.trust_db.get_contact_pubkey(sender_urn)
        if not sender_pubkey_hex:
            return 401, f"Sender URN {sender_urn} public key is not registered in trust database"

        envelope_copy = agent_oncall_pb2.OnCallEnvelope()
        envelope_copy.CopyFrom(envelope)
        envelope_copy.signature = ""
        
        serialized_bytes = envelope_copy.SerializeToString()
        pubkey = crypto.load_public_key_from_hex(sender_pubkey_hex)
        
        if not crypto.verify_signature(pubkey, serialized_bytes, envelope.signature):
            return 401, "Signature verification failed"

        return 0, ""

    # ---- Response Builders ----

    def _get_request_id(self, envelope: agent_oncall_pb2.OnCallEnvelope) -> str:
        payload_type = envelope.WhichOneof("payload")
        if payload_type == "discovery_request":
            return envelope.discovery_request.request_id
        elif payload_type == "call_request":
            return envelope.call_request.request_id
        return "unknown"

    def _build_serialized_success(self, request_id: str, result: Any) -> bytes:
        response = agent_oncall_pb2.OnCallEnvelope()
        response.version = PROTOCOL_VERSION
        response.timestamp = int(time.time())
        
        call_resp = response.call_response
        call_resp.request_id = request_id
        call_resp.success = True
        call_resp.error_code = 0
        call_resp.result_json = json.dumps(result)
        
        self._sign_envelope(response)
        return response.SerializeToString()

    def _build_serialized_error(self, request_id: str, error_code: int, error_message: str) -> bytes:
        response = agent_oncall_pb2.OnCallEnvelope()
        response.version = PROTOCOL_VERSION
        response.timestamp = int(time.time())
        
        call_resp = response.call_response
        call_resp.request_id = request_id
        call_resp.success = False
        call_resp.error_code = error_code
        call_resp.error_message = error_message
        
        self._sign_envelope(response)
        return response.SerializeToString()
