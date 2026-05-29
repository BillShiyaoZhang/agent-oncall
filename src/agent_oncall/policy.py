import json
import os
import time
from typing import Dict, List, Tuple, Optional
from agent_oncall.pb import agent_oncall_pb2
from agent_oncall import crypto

# Define standard trust levels
TIER_1_FAMILY = "Tier_1_Family"
TIER_2_FRIEND = "Tier_2_Friend"
TIER_3_STRANGER = "Tier_3_Stranger"

def match_pattern(pattern: str, intent_name: str) -> bool:
    """Matches an intent name against a wildcard pattern (e.g. '*' or 'calendar.*')."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return intent_name.startswith(prefix + ".")
    return pattern == intent_name


class TrustDatabase:
    """File-driven trust database storing contacts, public keys, trust tiers, and allowed intents."""
    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path
        self.contacts: Dict[str, Dict] = {}
        self.tier_permissions: Dict[str, List[str]] = {
            TIER_1_FAMILY: ["*"],
            TIER_2_FRIEND: ["calendar.query_availability"],
            TIER_3_STRANGER: []
        }
        if self.file_path:
            self.load()

    def load(self):
        """Loads contacts and tier permissions from the JSON file."""
        if not self.file_path or not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.contacts = data.get("contacts", {})
                self.tier_permissions = data.get("tier_permissions", self.tier_permissions)
        except Exception as e:
            # Fallback if parsing fails
            pass

    def save(self):
        """Saves current state to the JSON file."""
        if not self.file_path:
            return
        try:
            # Ensure parent directories exist
            parent_dir = os.path.dirname(self.file_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
                
            data = {
                "tier_permissions": self.tier_permissions,
                "contacts": self.contacts
            }
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            pass

    def add_contact(
        self,
        urn: str,
        public_key_hex: str,
        trust_level: str = TIER_3_STRANGER,
        allowed_intents: Optional[List[str]] = None
    ):
        """Adds a contact with public key, trust level, and optional custom allowed intents."""
        self.contacts[urn] = {
            "public_key_hex": public_key_hex,
            "trust_level": trust_level,
            "allowed_intents": allowed_intents or []
        }
        self.save()

    def remove_contact(self, urn: str):
        """Removes a contact from the database."""
        if urn in self.contacts:
            del self.contacts[urn]
            self.save()

    def set_allowed_intents(self, urn: str, allowed_intents: List[str]):
        """Sets URN-specific allowed intents override list."""
        if urn in self.contacts:
            self.contacts[urn]["allowed_intents"] = allowed_intents
            self.save()

    def set_tier_permissions(self, tier: str, allowed_intents: List[str]):
        """Modifies the global allowed intents list for a specific trust tier."""
        self.tier_permissions[tier] = allowed_intents
        self.save()

    def get_contact_pubkey(self, urn: str) -> Optional[str]:
        contact = self.contacts.get(urn)
        return contact["public_key_hex"] if contact else None

    def get_contact_trust_level(self, urn: str) -> str:
        contact = self.contacts.get(urn)
        return contact["trust_level"] if contact else TIER_3_STRANGER

    def get_contact_allowed_intents(self, urn: str) -> List[str]:
        contact = self.contacts.get(urn)
        return contact.get("allowed_intents", []) if contact else []

    def is_trusted(self, urn: str) -> bool:
        return self.get_contact_trust_level(urn) in (TIER_1_FAMILY, TIER_2_FRIEND)


class PolicyEngine:
    """Evaluates access policies based on URN overrides and trust level tier permissions."""
    def evaluate_policy(self, sender_urn: str, intent_name: str, trust_db: TrustDatabase) -> Tuple[bool, str]:
        """
        Evaluates whether a sender URN is allowed to invoke an intent.
        Order of evaluation:
        1. URN-specific overrides (allowed_intents list in contacts).
        2. Global tier_permissions based on sender URN's trust level.
        Returns (allowed, reason).
        """
        # 1. Check URN-specific overrides
        allowed_intents = trust_db.get_contact_allowed_intents(sender_urn)
        for pattern in allowed_intents:
            if match_pattern(pattern, intent_name):
                return True, f"Allowed by URN-specific permission override '{pattern}'"

        # 2. Check Global Tier permissions
        trust_level = trust_db.get_contact_trust_level(sender_urn)
        tier_allowed_patterns = trust_db.tier_permissions.get(trust_level, [])
        for pattern in tier_allowed_patterns:
            if match_pattern(pattern, intent_name):
                return True, f"Allowed by trust tier '{trust_level}' permission '{pattern}'"
                
        return False, f"Denied: Trust tier '{trust_level}' does not allow intent '{intent_name}'"


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
            
    token_copy = agent_oncall_pb2.CapabilityToken()
    token_copy.CopyFrom(token)
    token_copy.signature = b""
    
    serialized_bytes = token_copy.SerializeToString()
    
    private_key = crypto.load_private_key_from_hex(private_key_hex)
    signature_hex = crypto.sign_payload(private_key, serialized_bytes)
    
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
    """Verifies CapabilityToken signatures and constraints."""
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
