# agent-oncall Skill

## What This Library Does

`agent-oncall` is a **secure capability discovery and invocation middleware** for AI agents. It provides:

- **Discovery**: Ask another agent "what can you do?" (filtered by trust tier)
- **Invocation**: Call a capability on another agent
- **Authorization**: Policy-enforced trust tiers and capability tokens (HCT)
- **HITL confirmation**: Pause before executing high-risk actions

---

## Entry Point

All operations go through a single CLI entry managed by **uv**:

```bash
uv run agent-oncall COMMAND [OPTIONS]
```

---

## Architecture: 4 Operations

Each encounter involves two axes:

| | **Initiator (you call)** | **Callee (messages arrive automatically)** |
|---|---|---|
| **Discovery** | `discover --target URN` | handled by `_execute_discovery()` |
| **Call** | `call --target URN --intent NAME --args JSON` | handled by `_execute_call()` |

The callee-side handlers are triggered automatically when messages arrive — no need to invoke them directly.

---

## Initialization Pattern

When you need a local agent identity (for `discover`/`call`/`token issue`), you need:

- An Ed25519 **private key** (hex)
- An **agent URN** (e.g. `urn:hermes:agent:alice`)
- Optionally, a **trust database** (JSON file path)

The **simplest path** — generate a fresh keypair on the spot:

```python
from agent_oncall import create_agent_with_new_key

# One call → agent is ready, private key is in your hands
agent, priv_hex, pub_hex = create_agent_with_new_key("urn:hermes:agent:alice")
# Save priv_hex securely — you need it to re-create the agent next time
```

**Re-create an existing agent** (when you have the private key from before):

```python
from agent_oncall import create_agent, MockCommAdapter

agent = create_agent(
    urn="urn:hermes:agent:alice",
    private_key_hex="<saved_privkey_hex>",
)
```

No need to understand `TrustDatabase`, `PolicyEngine`, or `CommAdapter` for basic usage — `create_agent`/`create_agent_with_new_key` wires everything automatically.

---

## Trust Tier Model

When you call another agent, **they** evaluate your trust tier from their own trust database. You must be pre-registered there.

| Tier | Default Access |
|------|--------------|
| `Tier_1_Family` | All intents (`*`) |
| `Tier_2_Friend` | Specific allowed intents |
| `Tier_3_Stranger` | Empty (only URN-specific overrides) |

Asymmetric: your `trust_db` controls **what you expose to others**, not what others let you access.

---

## Contact Card Integration (agent-comm ↔ agent-oncall)

When you use **agent-comm** to exchange contact cards with another agent, agent-oncall can consume the card directly without manual key extraction:

```python
from agent_oncall import TrustDatabase, import_contact_from_card, TIER_2_FRIEND

db = TrustDatabase()

# Paste the full text output from: agent-comm share
card_text = """...-----BEGIN AGENT-COMM CONTACT CARD-----
Ed25519PK: a1b2c3d4...
X25519PK: 123456...
-----END AGENT-COMM CONTACT CARD-----..."""

# One call — parses, derives URN, writes to trust_db
urn = import_contact_from_card(db, card_text, TIER_2_FRIEND)
print(urn)  # → urn:hermes:agent:7mX5Y8zK2pQ...
```

What happens under the hood:
1. Extract `Ed25519PK` hex from the card
2. Derive the URN (`sha256(pk)[:16] → base58`, same algorithm as agent-comm)
3. Persist the contact to the trust_db JSON file

**Workflow with agent-comm**:
```bash
# 1. Exchange cards via agent-comm (run on both sides)
./agent-comm share    # gives you a card text block

# 2. Give the card text to agent-oncall — no manual key entry needed
```

---

## discover — Find Available Intents

```bash
uv run agent-oncall discover \
    --urn "urn:hermes:agent:alice" \
    --privkey "<alice_privkey_hex>" \
    --target "urn:hermes:agent:bob" \
    [--category "calendar"] \
    [--trust "urn:hermes:agent:bob,<bob_pubkey_hex>,Tier_2_Friend"]
```

**Output**: one intent per line:
```
calendar.query_availability | safe_description=Query calendar free slots | hitl=False | schema={...}
calendar.book_event | safe_description=Book calendar event | hitl=True | schema={...}
```

**Decision point**: parse `safe_description` (not `description`) to decide which intent to call. `safe_description` is short and neutral — it will not contain embedded instructions.

---

## call — Invoke an Intent

```bash
uv run agent-oncall call \
    --urn "urn:hermes:agent:alice" \
    --privkey "<alice_privkey_hex>" \
    --target "urn:hermes:agent:bob" \
    --intent "calendar.query_availability" \
    --args '{"date": "2026-06-01"}' \
    [--trust "urn:hermes:agent:bob,<bob_pubkey_hex>,Tier_2_Friend"]
```

**Success output** (JSON result):
```json
{
  "date": "2026-06-01",
  "available": true
}
```

**Failure output** (non-zero exit):
```json
{
  "success": false,
  "error_code": 403,
  "error_message": "Policy block: ..."
}
```

**Async note**: when using the SubprocessCommAdapter (real P2P), responses may arrive asynchronously. Handle `error_code: 202` appropriately.

---

## token issue — Delegate Authority with HCT

Issue a signed capability token to delegate access without upgrading the delegatee's trust tier:

```bash
uv run agent-oncall token issue \
    --privkey "<issuer_privkey_hex>" \
    --issuer "urn:hermes:agent:alice" \
    --audience "urn:hermes:agent:charlie" \
    --expires 60 \
    --constraint "calendar:write"
```

**Output**: base64-encoded token (write to file or pass as `--hct-token` argument in `call`).

To use the token in a call, the agent library must pass it programmatically — the CLI token output is primarily for integration workflows.

---

## serve — Run the STDIN/STDOUT Pipe (模式 A)

Launch a persistent handler for external transport (e.g. `agent-comm listen`):

```bash
uv run agent-oncall serve \
    --urn "urn:hermes:agent:alice" \
    --privkey "<alice_privkey_hex>" \
    [--trust-db "/path/to/trust_db.json"] \
    [--intents "/path/to/intents.json"] \
    [--hitl-allow]
```

Input format (stdin):
```json
{"event": "message_received", "sender_urn": "urn:hermes:agent:bob", "payload_base64": "Gg0KB2hlbGxv..."}
```

Output format (stdout):
```json
{"event": "send_reply", "payload_base64": "Hh8KB29uY2Fsb..."}
```

Optionally load intent definitions from a JSON file:
```json
[
  {
    "name": "calendar.query_availability",
    "description": "Check free slots",
    "safe_description": "Query calendar free slots",
    "input_schema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]},
    "requires_hitl": false,
    "resource": "calendar",
    "action": "read",
    "handler": {"type": "eval", "expr": "lambda sender, args: {'date': args['date'], 'available': True}"}
  }
]
```

---

## Error Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 202 | Async send, no synchronous reply (normal with SubprocessCommAdapter) |
| 400 | Malformed envelope / JSON schema validation failed |
| 401 | Signature or timestamp verification failed |
| 403 | Policy blocked (trust tier or HCT rejected) |
| 404 | Intent not found |
| 500 | Handler execution error |

---

## Security Notes for the Agent

- **`safe_description`** is safe for agent reasoning; **`description`** is human-audit only
- **Trust is asymmetric** — your `trust_db` affects what you expose, not what others let you do
- **HITL** fires on the callee side before high-risk operations; do not bypass it
- **Timestamp check** rejects envelopes older than `clock_skew_tolerance` (default 300s) — ensure your system clock is accurate

---

## Transport Layer: agent-comm Dependency

`agent-oncall` requires **agent-comm** as the underlying transport. The `SubprocessCommAdapter` resolves the `agent-comm` binary in this order:

1. `AGENT_COMM_PATH` environment variable
2. `agent-comm` found in `PATH` via `shutil.which()`
3. Fallback to `/usr/local/bin/agent-comm`

Make sure `agent-comm` is installed and in `PATH`, or set the env var before using `SubprocessCommAdapter`:

```bash
export AGENT_COMM_PATH="/path/to/agent-comm"
```

**Two-layer security model**:

| Layer | What it protects | Who provides it |
|-------|------------------|-----------------|
| **agent-comm** (transport) | Network eavesdropping, MITM, replay | libp2p + ECIES + Double Ratchet |
| **agent-oncall** (application) | Identity spoofing, permission escalation | Ed25519 envelope signatures + HCT |

Both layers are needed for end-to-end security. agent-oncall assumes agent-comm has already secured the channel — it focuses on trust and authorization.

---

## JSON Trust Database

Editable directly at the path passed to `--trust-db`:

```json
{
  "tier_permissions": {
    "Tier_1_Family": ["*"],
    "Tier_2_Friend": ["calendar.query", "hello.*"],
    "Tier_3_Stranger": []
  },
  "contacts": {
    "urn:hermes:agent:bob": {
      "public_key_hex": "...",
      "trust_level": "Tier_2_Friend",
      "allowed_intents": []
    }
  }
}
```

Changes take effect immediately on next load. Use `add_contact`, `add_contact_from_card`, and `set_tier_permissions` from the library to persist programmatically.