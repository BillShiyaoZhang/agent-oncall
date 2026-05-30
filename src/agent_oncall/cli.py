"""
agent-oncall CLI — a structured entry point for agent callers.

Usage:
    uv run agent-oncall discover --target URN [--category FILTER]
    uv run agent-oncall call --target URN --intent NAME --args JSON
    uv run agent-oncall token issue --issuer URN --audience URN --resource NAME --action NAME --expires SECONDS
    uv run agent-oncall serve --urn URN --privkey HEX [--trust-db PATH] [--hitl-allow]
"""

import argparse
import json
import sys
from typing import Optional

from agent_oncall import (
    AgentOnCall,
    MockCommAdapter,
    crypto,
    sign_capability_token,
    StdinStdoutHandler,
    TIER_1_FAMILY,
    TIER_2_FRIEND,
    TIER_3_STRANGER,
)


def _build_local_agent(urn: str, privkey_hex: str, comm: MockCommAdapter) -> AgentOnCall:
    """Create a local AgentOnCall instance for CLI identity."""
    return AgentOnCall(
        agent_urn=urn,
        private_key_hex=privkey_hex,
        comm_adapter=comm,
    )


# ------------------------------------------------------------------
# discover
# ------------------------------------------------------------------

def cmd_discover(args):
    """Ask a target agent for its visible intent list."""
    comm = MockCommAdapter()
    local = _build_local_agent(args.urn, args.privkey, comm)

    # Parse trusted contacts: issuer_urn,pubkey_hex,tier triples
    for contact_spec in (args.trust or []):
        parts = contact_spec.split(",")
        if len(parts) != 3:
            sys.exit(f"Invalid --trust format: {contact_spec} (expected urn,pubkey_hex,tier)")
        c_urn, c_pub, c_tier = parts
        local.trust_db.add_contact(c_urn, c_pub, c_tier)

    try:
        intents = local.discover_remote(args.target, category_filter=args.category or "")
    except Exception as e:
        sys.exit(f"Discovery failed: {e}")

    if not intents:
        print("[]")
        return

    for intent in intents:
        print(f"{intent['name']} | safe_description={intent['safe_description']} | hitl={intent['requires_hitl']} | schema={intent['input_schema_json']}")


# ------------------------------------------------------------------
# call
# ------------------------------------------------------------------

def cmd_call(args):
    """Invoke an intent on a target agent."""
    comm = MockCommAdapter()
    local = _build_local_agent(args.urn, args.privkey, comm)

    for contact_spec in (args.trust or []):
        parts = contact_spec.split(",")
        if len(parts) != 3:
            sys.exit(f"Invalid --trust format: {contact_spec}")
        c_urn, c_pub, c_tier = parts
        local.trust_db.add_contact(c_urn, c_pub, c_tier)

    try:
        call_args = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as e:
        sys.exit(f"Invalid --args JSON: {e}")

    try:
        result = local.call_remote(args.target, args.intent, call_args)
    except Exception as e:
        sys.exit(f"Call failed: {e}")

    if result.get("success"):
        print(json.dumps(result.get("result"), indent=2))
    else:
        print(json.dumps(result), indent=2)
        sys.exit(1)


# ------------------------------------------------------------------
# token
# ------------------------------------------------------------------

def cmd_token(args):
    """Issue a signed HCT capability token."""
    if args.token_action == "issue":
        constraints = []
        for spec in (args.constraint or []):
            # format: resource:action or resource:action:filter1,filter2
            parts = spec.split(":")
            resource = parts[0]
            action = parts[1] if len(parts) > 1 else "*"
            filters = parts[2].split(",") if len(parts) > 2 and parts[2] else []
            constraints.append({"resource": resource, "action": action, "filters": filters})

        token = sign_capability_token(
            private_key_hex=args.privkey,
            issuer_urn=args.issuer,
            audience_urn=args.audience,
            expires_in_seconds=args.expires,
            constraints=constraints,
        )
        # Output base64-encoded serialized token for portability
        import base64
        sys.stdout.write(base64.b64encode(token.SerializeToString()).decode("utf-8"))
        sys.stdout.write("\n")
    else:
        sys.exit(f"Unknown token action: {args.token_action}")


# ------------------------------------------------------------------
# serve
# ------------------------------------------------------------------

def cmd_serve(args):
    """Run the STDIN/STDOUT pipe loop with a locally-registered agent."""
    from agent_oncall.hitl import InteractiveHITLHandler

    comm = MockCommAdapter()
    agent = AgentOnCall(
        agent_urn=args.urn,
        private_key_hex=args.privkey,
        comm_adapter=comm,
        hitl_handler=InteractiveHITLHandler(default_response=args.hitl_allow),
        trust_db_path=args.trust_db,
    )
    comm.register_agent(agent.agent_urn, agent)

    # Optionally register intents from a JSON file
    if args.intents:
        try:
            with open(args.intents, "r", encoding="utf-8") as f:
                intent_defs = json.load(f)
            for defn in intent_defs:
                name = defn.get("name")
                desc = defn.get("description", "")
                schema = defn.get("input_schema", {"type": "object"})
                safe_desc = defn.get("safe_description", desc)
                hitl = defn.get("requires_hitl", False)
                resource = defn.get("resource", name.split(".")[0] if "." in name else "")
                action = defn.get("action", "execute")
                handler_code = defn.get("handler")

                if handler_code:
                    # Compile a dynamic handler from a JSON-serialized lambda repr
                    # Accepts {"type": "eval", "expr": "lambda sender, args: {...}"}
                    import ast
                    if handler_code.get("type") == "eval":
                        handler = eval(handler_code["expr"], {"__builtins__": {}})
                    else:
                        sys.exit(f"Unknown handler type: {handler_code.get('type')}")
                else:
                    def default_handler(sender, args):
                        return {"ok": True, "intent": name}
                    handler = default_handler

                agent.register_intent(
                    name=name,
                    description=desc,
                    safe_description=safe_desc,
                    input_schema=schema,
                    handler=handler,
                    requires_hitl=hitl,
                    resource=resource,
                    action=action,
                )
        except Exception as e:
            sys.exit(f"Failed to load intents from {args.intents}: {e}")

    print("agent-oncall stdin handler started", file=sys.stderr)
    handler = StdinStdoutHandler(agent)
    handler.run_loop()


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="agent-oncall CLI — structured capability discovery and invocation"
    )
    sub = root.add_subparsers(dest="command", required=True)

    # discover
    p_discover = sub.add_parser("discover", help="Discover visible intents on a target agent")
    p_discover.add_argument("--urn", "--identity-urn", dest="urn", required=True, help="Local agent URN")
    p_discover.add_argument("--privkey", required=True, help="Local agent private key (hex)")
    p_discover.add_argument("--target", required=True, help="Target agent URN")
    p_discover.add_argument("--category", help="Filter intents by name prefix (e.g. calendar)")
    p_discover.add_argument("--trust", nargs="+", metavar="URN,PUBKEY_HEX,TIER",
                            help="Add a trusted contact (can be repeated)")

    # call
    p_call = sub.add_parser("call", help="Invoke an intent on a target agent")
    p_call.add_argument("--urn", "--identity-urn", dest="urn", required=True)
    p_call.add_argument("--privkey", required=True)
    p_call.add_argument("--target", required=True)
    p_call.add_argument("--intent", required=True, help="Intent name to invoke")
    p_call.add_argument("--args", default="{}", help="JSON arguments")
    p_call.add_argument("--trust", nargs="+", metavar="URN,PUBKEY_HEX,TIER")

    # token
    p_token = sub.add_parser("token", help="Issue or inspect HCT capability tokens")
    p_token.add_argument("token_action", choices=["issue"], help="Action to perform")
    p_token.add_argument("--privkey", required=True, help="Issuer private key (hex)")
    p_token.add_argument("--issuer", required=True, help="Issuer URN")
    p_token.add_argument("--audience", required=True, help="Audience (delegatee) URN")
    p_token.add_argument("--expires", type=int, required=True, help="Seconds until token expires")
    p_token.add_argument("--constraint", nargs="+", metavar="RESOURCE:ACTION[:FILTERS]",
                         help="Constraint spec (can be repeated)")
    p_token.add_argument("--output", default="-", help="Output file (default: stdout)")

    # serve
    p_serve = sub.add_parser("serve", help="Run the STDIN/STDOUT pipe handler")
    p_serve.add_argument("--urn", required=True, help="Local agent URN")
    p_serve.add_argument("--privkey", required=True)
    p_serve.add_argument("--trust-db", help="Path to JSON trust database")
    p_serve.add_argument("--intents", help="Path to JSON file with intent definitions")
    p_serve.add_argument("--hitl-allow", action="store_true",
                         help="Auto-approve HITL confirmations (default: prompt)")

    return root


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "discover":
        cmd_discover(args)
    elif args.command == "call":
        cmd_call(args)
    elif args.command == "token":
        cmd_token(args)
    elif args.command == "serve":
        cmd_serve(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()