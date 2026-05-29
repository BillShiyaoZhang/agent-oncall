import base64
import os
import re
import subprocess
import threading
import time
from typing import Callable, Optional

class CommAdapter:
    """Base class for communication adapters."""
    def send_message(self, target_urn: str, envelope_bytes: bytes) -> Optional[bytes]:
        """
        Sends the serialized envelope bytes to target_urn.
        If synchronous reply is available, returns the response envelope bytes.
        """
        raise NotImplementedError()

    def register_receive_callback(self, callback: Callable[[str, bytes], Optional[bytes]]):
        """
        Registers a callback to be invoked when a message envelope is received.
        callback signature: callback(sender_urn: str, envelope_bytes: bytes) -> Optional[bytes]
        """
        raise NotImplementedError()


class MockCommAdapter(CommAdapter):
    """In-memory communication adapter for local testing and simulation."""
    def __init__(self):
        self.agents = {}
        self.callback = None

    def register_agent(self, urn: str, agent_instance):
        self.agents[urn] = agent_instance

    def send_message(self, target_urn: str, envelope_bytes: bytes) -> Optional[bytes]:
        if target_urn in self.agents:
            agent = self.agents[target_urn]
            from agent_oncall.pb import agent_oncall_pb2
            envelope = agent_oncall_pb2.OnCallEnvelope()
            envelope.ParseFromString(envelope_bytes)
            sender_urn = ""
            payload_type = envelope.WhichOneof("payload")
            if payload_type == "call_request":
                sender_urn = envelope.call_request.caller_urn
            elif payload_type == "discovery_request":
                sender_urn = envelope.discovery_request.query_urn
            return agent.handle_incoming_envelope_bytes(sender_urn, envelope_bytes)
        raise ValueError(f"Agent {target_urn} is not registered in this mock network")

    def register_receive_callback(self, callback: Callable[[str, bytes], Optional[bytes]]):
        self.callback = callback


class SubprocessCommAdapter(CommAdapter):
    """
    Subprocess-based adapter that executes the compiled agent-comm binary.
    Launches 'agent-comm listen' as a background daemon and parses stdout to receive envelopes.
    """
    def __init__(self, agent_comm_path: str, keys_dir: Optional[str] = None, bootstrap_addr: Optional[str] = None):
        self.agent_comm_path = agent_comm_path
        self.keys_dir = keys_dir
        self.bootstrap_addr = bootstrap_addr
        self.callback = None
        self.listen_process = None
        self.listener_thread = None
        self.running = False

        # Regular expressions to parse incoming messages from agent-comm stdout
        # Direct: [urn:hermes:agent:alice] 💬 <msg> or [urn:hermes:agent:alice] <msg>
        self.direct_pattern = re.compile(r"^\[(?P<urn>urn:hermes:agent:[a-zA-Z0-9]+)\]\s*(?:💬\s*)?(?P<msg>[A-Za-z0-9+/=]+)$")
        # Offline Pull: 💬 from urn:hermes:agent:alice: "<msg>" (ts=...)
        # or from urn:hermes:agent:alice: [raw] "<msg>" (parse error...)
        self.pull_pattern = re.compile(r"from\s+(?P<urn>urn:hermes:agent:[a-zA-Z0-9]+):\s*(?:\[raw\]|💬)?\s*\"(?P<msg>[A-Za-z0-9+/=]+)\"")

    def _get_base_args(self) -> list[str]:
        args = [self.agent_comm_path]
        if self.keys_dir:
            args.extend(["--keysdir", self.keys_dir])
        if self.bootstrap_addr:
            args.extend(["--bootstrap", self.bootstrap_addr])
        return args

    def send_message(self, target_urn: str, envelope_bytes: bytes) -> Optional[bytes]:
        """Runs the 'agent-comm send' CLI command to send a base64 encoded envelope."""
        base64_payload = base64.b64encode(envelope_bytes).decode('utf-8')
        cmd = self._get_base_args() + ["send", target_urn, base64_payload]
        
        # Run process synchronously
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"agent-comm send failed: {result.stderr.strip()}")
            
        # We don't get a direct sync return envelope from 'agent-comm send' since P2P sending
        # is fire-and-forget in the CLI command, or responses are received asynchronously.
        return None

    def register_receive_callback(self, callback: Callable[[str, bytes], Optional[bytes]]):
        self.callback = callback

    def start(self):
        """Starts the 'agent-comm listen' background subprocess and parser thread."""
        if self.running:
            return
            
        self.running = True
        cmd = self._get_base_args() + ["listen"]
        
        # Start the listener process
        self.listen_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Spawn thread to read stdout
        self.listener_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.listener_thread.start()

    def stop(self):
        """Stops the listener subprocess."""
        self.running = False
        if self.listen_process:
            self.listen_process.terminate()
            try:
                self.listen_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.listen_process.kill()
            self.listen_process = None
        if self.listener_thread:
            self.listener_thread.join(timeout=2)
            self.listener_thread = None

    def _read_stdout(self):
        while self.running and self.listen_process:
            line = self.listen_process.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            # Try to match direct pattern
            match = self.direct_pattern.match(line)
            if not match:
                # Try to match pull pattern
                match = self.pull_pattern.search(line)

            if match:
                sender_urn = match.group("urn")
                base64_msg = match.group("msg")
                
                try:
                    envelope_bytes = base64.b64decode(base64_msg)
                    if self.callback:
                        # Invoke callback. If it returns response bytes, send the reply back
                        reply_bytes = self.callback(sender_urn, envelope_bytes)
                        if reply_bytes:
                            # Send reply envelope back to sender
                            self.send_message(sender_urn, reply_bytes)
                except Exception as e:
                    # Ignore invalid base64 or decoding issues, as it might be a normal chat text message
                    pass
