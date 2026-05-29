import base64
import json
import sys
from typing import Any

class StdinStdoutHandler:
    """
    Implements 模式 A: STDIN/STDOUT 管道模式.
    Reads JSON lines from stdin, processes them using the provided agent,
    and writes JSON responses to stdout.
    """
    def __init__(self, agent_instance):
        self.agent = agent_instance

    def run_loop(self):
        """Infinite loop reading from stdin and writing to stdout."""
        sys.stdout.write("agent-oncall stdin handler started\n")
        sys.stdout.flush()

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                self._send_error("Invalid JSON format")
                continue

            event = data.get("event")
            if event != "message_received":
                self._send_error(f"Unsupported event type: {event}")
                continue

            sender_urn = data.get("sender_urn")
            payload_base64 = data.get("payload_base64")

            if not sender_urn or not payload_base64:
                self._send_error("Missing sender_urn or payload_base64")
                continue

            try:
                raw_envelope_bytes = base64.b64decode(payload_base64)
            except Exception as e:
                self._send_error(f"Base64 decoding failed: {str(e)}")
                continue

            try:
                # Process incoming message via AgentOnCall
                reply_envelope_bytes = self.agent.handle_incoming_envelope_bytes(sender_urn, raw_envelope_bytes)
                
                reply_base64 = base64.b64encode(reply_envelope_bytes).decode('utf-8')
                response = {
                    "event": "send_reply",
                    "payload_base64": reply_base64
                }
                
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
            except Exception as e:
                self._send_error(f"Error handling envelope: {str(e)}")

    def _send_error(self, message: str):
        error_response = {
            "event": "error",
            "error_message": message
        }
        sys.stdout.write(json.dumps(error_response) + "\n")
        sys.stdout.flush()
