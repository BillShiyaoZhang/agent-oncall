import time
from typing import Dict, List, Tuple, Optional
from agent_oncall.pb import agent_oncall_pb2
from agent_oncall import crypto

# Define standard trust levels
TIER_1_FAMILY = "Tier_1_Family"
TIER_2_FRIEND = "Tier_2_Friend"
TIER_3_STRANGER = "Tier_3_Stranger"

class TrustDatabase:
    """In-memory database storing URN to public key and trust level mappings."""
    def __init__(self):
        self.contacts: Dict[str, Dict[str, str]] = {}

    def add_contact(self, urn: str, public_key_hex: str, trust_level: str = TIER_3_STRANGER):
        self.contacts[urn] = {
            "public_key_hex": public_key_hex,
            "trust_level": trust_level
        }

    def get_contact_pubkey(self, urn: str) -> Optional[str]:
        contact = self.contacts.get(urn)
        return contact["public_key_hex"] if contact else None

    def get_contact_trust_level(self, urn: str) -> str:
        contact = self.contacts.get(urn)
        return contact["trust_level"] if contact else TIER_3_STRANGER

    def is_trusted(self, urn: str) -> bool:
        return self.get_contact_trust_level(urn) in (TIER_1_FAMILY, TIER_2_FRIEND)


class PolicyEngine:
    """Evaluates access policies based on caller URN and Intent requirements."""
    def __init__(self):
        # Maps intent name to lists of allowed trust tiers
        self.policies: Dict[str, List[str]] = {
            "calendar.query_availability": [TIER_1_FAMILY, TIER_2_FRIEND],
            "calendar.book_event": [TIER_1_FAMILY],
        }
        self.default_allowed_tiers = [TIER_1_FAMILY, TIER_2_FRIEND]

    def register_policy(self, intent_name: str, allowed_tiers: List[str]):
        """Allows registering custom policy requirements for an intent."""
        self.policies[intent_name] = allowed_tiers

    def evaluate_policy(self, sender_urn: str, intent_name: str, trust_db: TrustDatabase) -> Tuple[bool, str]:
        """
        Evaluates whether a sender is allowed to invoke an intent.
        Returns (allowed, reason).
        """
        trust_level = trust_db.get_contact_trust_level(sender_urn)
        
        allowed_tiers = self.policies.get(intent_name, self.default_allowed_tiers)
        
        if trust_level in allowed_tiers:
            return True, f"Caller has trust level {trust_level} which is allowed"
            
        return False, f"Caller trust level {trust_level} is not in allowed tiers {allowed_tiers}"


def sign_capability_token(
    private_key_hex: str,
    issuer_urn: str,
    audience_urn: str,
    expires_in_seconds: int,
    constraints: List[Dict[str, any]]
) -> agent_oncall_pb2.CapabilityToken:
    """Helper to construct and sign a CapabilityToken (HCT)."""
    token = agent_oncall_pb2.CapabilityToken()
    token.issuer_urn = issuer_urn
    token.audience_urn = audience_urn
    token.expires_at = int(time.time()) + expires_in_seconds
    
    for c_dict in constraints:
        constraint = token.constraints.add()
        constraint.resource = c_dict.get("resource", "")
        constraint.action = c_dict.get("action", "")
        if "filters" in c_dict:
            constraint.filters.extend(c_dict["filters"])
            
    # Serialize token excluding the signature field
    # Protobuf oneof/signature is not yet set, we serialize the current state
    token_copy = agent_oncall_pb2.CapabilityToken()
    token_copy.CopyFrom(token)
    token_copy.signature = b""
    
    serialized_bytes = token_copy.SerializeToString()
    
    private_key = crypto.load_private_key_from_hex(private_key_hex)
    signature_hex = crypto.sign_payload(private_key, serialized_bytes)
    
    # Store raw bytes of the signature (since the proto field signature is bytes)
    token.signature = bytes.fromhex(signature_hex)
    return token


def verify_capability_token(
    token: agent_oncall_pb2.CapabilityToken,
    caller_urn: str,
    target_resource: str,
    target_action: str,
    trust_db: TrustDatabase,
    current_time: Optional[float] = None
) -> Tuple[bool, str]:
    """
    Verifies the capability token structure, signature, and constraints.
    Returns (valid, reason).
    """
    if current_time is None:
        current_time = time.time()
        
    # 1. Verify caller is the intended audience
    if token.audience_urn != caller_urn:
        return False, f"Token audience {token.audience_urn} does not match caller {caller_urn}"
        
    # 2. Check expiration
    if token.expires_at < current_time:
        return False, f"Token expired at {token.expires_at}, current time {current_time}"
        
    # 3. Resolve issuer public key and verify signature
    issuer_pubkey_hex = trust_db.get_contact_pubkey(token.issuer_urn)
    if not issuer_pubkey_hex:
        return False, f"Issuer {token.issuer_urn} is not in trust database"
        
    token_copy = agent_oncall_pb2.CapabilityToken()
    token_copy.CopyFrom(token)
    token_copy.signature = b""
    serialized_bytes = token_copy.SerializeToString()
    
    issuer_pubkey = crypto.load_public_key_from_hex(issuer_pubkey_hex)
    sig_hex = token.signature.hex()
    
    if not crypto.verify_signature(issuer_pubkey, serialized_bytes, sig_hex):
        return False, "Token signature verification failed"
        
    # 4. Match target resource and action against allowed constraints
    matched = False
    for constraint in token.constraints:
        res_match = (constraint.resource == "*" or constraint.resource == target_resource)
        act_match = (constraint.action == "*" or constraint.action == target_action)
        if res_match and act_match:
            matched = True
            break
            
    if not matched:
        return False, f"Constraints do not allow action '{target_action}' on resource '{target_resource}'"
        
    return True, "Token is valid and constraints match"
