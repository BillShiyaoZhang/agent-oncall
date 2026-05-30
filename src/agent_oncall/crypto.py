import binascii
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

def generate_keypair() -> tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Generates an Ed25519 private and public key pair."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key

def private_key_to_hex(private_key: ed25519.Ed25519PrivateKey) -> str:
    """Serializes private key to hex string."""
    raw_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    return binascii.hexlify(raw_bytes).decode('utf-8')

def public_key_to_hex(public_key: ed25519.Ed25519PublicKey) -> str:
    """Serializes public key to hex string."""
    raw_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )
    return binascii.hexlify(raw_bytes).decode('utf-8')

def load_private_key_from_hex(hex_str: str) -> ed25519.Ed25519PrivateKey:
    """Loads an Ed25519 private key from hex string."""
    raw_bytes = binascii.unhexlify(hex_str)
    return ed25519.Ed25519PrivateKey.from_private_bytes(raw_bytes)

def load_public_key_from_hex(hex_str: str) -> ed25519.Ed25519PublicKey:
    """Loads an Ed25519 public key from hex string."""
    raw_bytes = binascii.unhexlify(hex_str)
    return ed25519.Ed25519PublicKey.from_public_bytes(raw_bytes)

def sign_payload(private_key: ed25519.Ed25519PrivateKey, payload_bytes: bytes) -> str:
    """Signs payload bytes and returns hex-encoded signature."""
    signature = private_key.sign(payload_bytes)
    return binascii.hexlify(signature).decode('utf-8')

def verify_signature(public_key: ed25519.Ed25519PublicKey, payload_bytes: bytes, signature_hex: str) -> bool:
    """Verifies hex-encoded signature against payload bytes. Returns True if valid, False otherwise."""
    try:
        signature_bytes = binascii.unhexlify(signature_hex)
        public_key.verify(signature_bytes, payload_bytes)
        return True
    except Exception:
        return False
