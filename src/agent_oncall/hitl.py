class HITLHandler:
    """Base class for Human-in-the-Loop interceptors."""
    def approve_call(self, sender_urn: str, intent_name: str, arguments_json: str) -> bool:
        """
        Intercepts high-risk intent execution to ask for human confirmation.
        Returns True if approved, False otherwise.
        """
        raise NotImplementedError()


class InteractiveHITLHandler(HITLHandler):
    """
    Standard console-based HITL handler that prompts the user for y/n response.
    Can be configured with a default_response to bypass interactive prompts in automated tests.
    """
    def __init__(self, default_response: bool = None):
        self.default_response = default_response

    def approve_call(self, sender_urn: str, intent_name: str, arguments_json: str) -> bool:
        if self.default_response is not None:
            return self.default_response
            
        print(f"\n📞 ================= [HITL INTERCEPT] =================")
        print(f"👤 Caller:    {sender_urn}")
        print(f"🔧 Intent:    {intent_name}")
        print(f"📝 Arguments: {arguments_json}")
        print(f"=========================================================")
        
        try:
            choice = input("Do you approve this operation? (y/n): ").strip().lower()
            return choice in ('y', 'yes')
        except (KeyboardInterrupt, EOFError):
            print("\n❌ Operation rejected by default due to interrupt.")
            return False
