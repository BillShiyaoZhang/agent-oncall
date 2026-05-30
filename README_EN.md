# agent-oncall 📞

`agent-oncall` is a lightweight, secure execution and discovery library designed for AI agent frameworks (such as Hermes or OpenClaw). It acts as an RPC and capability discovery middleware, running on top of [agent-comm](https://github.com/BillShiyaoZhang/agent-comm) or other P2P transport adapters to enable zero-trust, authenticated interactions between autonomous agents.

## 🏗️ Core Architecture & Features

- **Envelope Security**: Messages are wrapped in a signed protobuf envelope (`OnCallEnvelope`). Signature verification is handled via Ed25519 cryptography (via Python `cryptography` package) and protected against replay attacks.
- **Dynamic Visibility & Passive Disclosure**: When responding to a discovery handshake, capability visibility is filtered dynamically according to the caller's URN and their assigned trust tier (`Tier_1_Family`, `Tier_2_Friend`, `Tier_3_Stranger`).
- **Policy Engine & Capability Tokens (HCT)**: Enforces trust tiers for each registered intent. Supports delegating capability permissions using Ed25519-signed Capability Tokens (HCTs) with resource/action/filter constraints.
- **Human-in-the-loop (HITL) Interceptor**: Pluggable interceptor framework to prompt users for approval before executing high-risk intents (e.g. initiating database operations, making budget changes).
- **Communication Adapters**:
  - `MockCommAdapter`: Simulated, in-memory routing between local agents for tests and mocks.
  - `SubprocessCommAdapter`: Spawns and wraps the Go-based `agent-comm` daemon, sending messages and parsing stdout to pipe envelopes.
- **Subprocess Piping (模式 A)**: Integrated stdin/stdout handler that allows external systems (such as `agent-comm listen`) to pipe message JSON envelopes into a subprocess and receive replies on stdout.
- **Service Description Alignment (SDA)**: Implements a dialogue state machine aligning ambiguous third-party service descriptions to local schemas using pluggable LLM callbacks.

---

## 📂 Project Directory Structure

```text
agent-oncall/
├── proto/
│   └── agent_oncall.proto       # Protobuf message definitions
├── src/agent_oncall/
│   ├── pb/                      # Generated protobuf bindings
│   ├── crypto.py                # Ed25519 key management, signing & verification
│   ├── policy.py                # Trust tiers, Policy Engine, and HCT token validator
│   ├── hitl.py                  # Pluggable HITL interceptor (Console Prompt)
│   ├── comm.py                  # Communication adapters (Mock & agent-comm CLI wrappers)
│   ├── stdin_handler.py         # Subprocess piping runner (模式 A)
│   ├── alignment.py             # Service Description Alignment (SDA) state machine
│   └── core.py                  # Main AgentOnCall router & manager
├── tests/
│   └── test_agent_oncall.py     # Comprehensive pytest unit tests
├── run_demo.py                  # Orchestrated demo runner simulating full scenario
└── pyproject.toml               # Project dependencies and packaging definition (uv)
```

---

## 🚀 Quick Start

### 1. Installation

Manage the project dependencies using `uv`:
```bash
# Sync virtual environment and dependencies
uv sync
```

### 2. Basic Usage

Here is a simple example of registering an intent and performing a policy-checked remote call:

```python
from agent_oncall import AgentOnCall, MockCommAdapter, crypto, TIER_2_FRIEND

# Initialize mock network
comm = MockCommAdapter()

# Generate cryptographic keypairs
alice_priv, alice_pub = crypto.generate_keypair()
bob_priv, bob_pub = crypto.generate_keypair()

# Initialize Agents
alice = AgentOnCall(
    agent_urn="urn:hermes:agent:alice",
    private_key_hex=crypto.private_key_to_hex(alice_priv),
    comm_adapter=comm
)
comm.register_agent(alice.agent_urn, alice)

bob = AgentOnCall(
    agent_urn="urn:hermes:agent:bob",
    private_key_hex=crypto.private_key_to_hex(bob_priv),
    comm_adapter=comm
)
comm.register_agent(bob.agent_urn, bob)

# Alice configures Bob as a Friend, and Bob trusts Alice
alice.trust_db.add_contact(bob.agent_urn, crypto.public_key_to_hex(bob_pub), TIER_2_FRIEND)
bob.trust_db.add_contact(alice.agent_urn, crypto.public_key_to_hex(alice_pub), TIER_2_FRIEND)

# Alice registers a custom tool/intent with a JSON Schema check
alice.register_intent(
    name="calendar.query_availability",
    description="Check free slot",
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string"}
        },
        "required": ["date"]
    },
    handler=lambda sender, args: {"date": args["date"], "available": True}
)

# Bob calls Alice's intent remotely
response = bob.call_remote("urn:hermes:agent:alice", "calendar.query_availability", {"date": "2026-06-01"})
print("Response:", response)
# Outputs: {'success': True, 'result': {'date': '2026-06-01', 'available': True}}
```

### 3. Pipeline Integration (模式 A：STDIN/STDOUT 管道模式)

You can launch a pipeline script using `StdinStdoutHandler` to process incoming JSON lines from stdout of `agent-comm listen`:

```python
import sys
from agent_oncall import AgentOnCall, StdinStdoutHandler, MockCommAdapter

# Initialize your AgentOnCall instance ...
agent = AgentOnCall(...)

# Run the stdin/stdout event loop
handler = StdinStdoutHandler(agent)
handler.run_loop()
```

Input event format (Stdin):
```json
{
  "event": "message_received",
  "sender_urn": "urn:hermes:agent:bob",
  "payload_base64": "Gg0KB2hlbGxv..."
}
```

Output event format (Stdout):
```json
{
  "event": "send_reply",
  "payload_base64": "Hh8KB29uY2Fsb..."
}
```

---

## 🧪 Verification and Tests

### Run Automated Unit Tests
```bash
uv run pytest tests/
```

### Run Scenario Demo
Run the orchestrator demo simulating dynamic visibility discovery, direct policy checks, capability tokens (HCTs), HITL approval intercepts, and Service Description Alignment (SDA):
```bash
uv run python run_demo.py
```
