"""
High-level helper functions for agent-oncall.

These are the primary entry points for an AI agent — they wrap the full
initialization and contact-import logic into single-call functions so the
agent does not need to understand internal details.
"""

from typing import Optional

from agent_oncall import (
    AgentOnCall,
    MockCommAdapter,
    SubprocessCommAdapter,
    TrustDatabase,
    crypto as oncall_crypto,
    TIER_1_FAMILY,
    TIER_2_FRIEND,
    TIER_3_STRANGER,
)


def create_agent(
    urn: str,
    private_key_hex: str,
    comm_adapter: Optional[object] = None,
    trust_db_path: Optional[str] = None,
    hitl_allow: bool = False,
) -> AgentOnCall:
    """Create and register a locally-identified AgentOnCall instance.

    Args:
        urn: The agent's URN, e.g. "urn:hermes:agent:alice".
        private_key_hex: The agent's Ed25519 private key as a 64-char hex string.
        comm_adapter: Communication adapter (MockCommAdapter by default).
        trust_db_path: Optional path to a JSON trust database file.
        hitl_allow: If True, auto-approve all HITL confirmations.
                    If False, the default InteractiveHITLHandler will prompt.
    Returns:
        A fully initialized AgentOnCall instance registered with the comm adapter.
    """
    from agent_oncall.hitl import InteractiveHITLHandler

    adapter = comm_adapter or MockCommAdapter()
    agent = AgentOnCall(
        agent_urn=urn,
        private_key_hex=private_key_hex,
        comm_adapter=adapter,
        trust_db_path=trust_db_path,
        hitl_handler=InteractiveHITLHandler(default_response=hitl_allow),
    )
    adapter.register_agent(agent.agent_urn, agent)
    return agent


def create_agent_with_new_key(
    urn: str,
    comm_adapter: Optional[object] = None,
    trust_db_path: Optional[str] = None,
    hitl_allow: bool = False,
) -> tuple[AgentOnCall, str, str]:
    """Create an AgentOnCall instance with a freshly generated Ed25519 keypair.

    Args:
        urn: The agent's URN, e.g. "urn:hermes:agent:alice".
        comm_adapter: Communication adapter (MockCommAdapter by default).
        trust_db_path: Optional path to a JSON trust database file.
        hitl_allow: If True, auto-approve all HITL confirmations.
    Returns:
        A tuple of (agent, private_key_hex, public_key_hex).
        Save the private_key_hex securely — it is needed to re-create the agent.
    """
    priv, pub = oncall_crypto.generate_keypair()
    priv_hex = oncall_crypto.private_key_to_hex(priv)
    pub_hex = oncall_crypto.public_key_to_hex(pub)
    agent = create_agent(
        urn=urn,
        private_key_hex=priv_hex,
        comm_adapter=comm_adapter,
        trust_db_path=trust_db_path,
        hitl_allow=hitl_allow,
    )
    return agent, priv_hex, pub_hex


def import_contact_from_card(
    trust_db: TrustDatabase,
    card_text: str,
    trust_level: str = TIER_3_STRANGER,
) -> Optional[str]:
    """Parse an agent-comm contact card and add the contact to trust_db.

    This is the zero-copy way to register a peer after exchanging contact cards
    via agent-comm.

    Args:
        trust_db: The TrustDatabase to write the contact into.
        card_text: The full text block produced by ``agent-comm share``.
        trust_level: One of TIER_1_FAMILY, TIER_2_FRIEND, TIER_3_STRANGER.

    Returns:
        The URN of the added contact, or None if parsing failed.
    """
    return trust_db.add_contact_from_card(card_text, trust_level)


def create_subprocess_adapter(
    agent_comm_path: Optional[str] = None,
    keys_dir: Optional[str] = None,
    bootstrap_addr: Optional[str] = None,
) -> SubprocessCommAdapter:
    """Create a SubprocessCommAdapter backed by the agent-comm binary.

    Resolution order for agent_comm_path:
      1. The ``agent_comm_path`` argument (if provided)
      2. ``AGENT_COMM_PATH`` environment variable
      3. ``agent-comm`` found in PATH
      4. Fallback to /usr/local/bin/agent-comm

    Args:
        agent_comm_path: Explicit path to the agent-comm binary.
        keys_dir: --keysdir argument for agent-comm.
        bootstrap_addr: --bootstrap address for agent-comm.
    """
    return SubprocessCommAdapter(
        agent_comm_path=agent_comm_path,
        keys_dir=keys_dir,
        bootstrap_addr=bootstrap_addr,
    )