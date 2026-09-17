class UIError(RuntimeError):
    """Safe diagnostics: never include page text, input values or raw driver errors."""

    def __init__(self, code: str, target: str | None = None):
        self.code = code
        self.target = target
        super().__init__(code)


    @property
    def result_code(self):
        """Map adapter details to the frozen v1 run-result error vocabulary."""
        return {'control_denied': 'policy_denied', 'stale_observation': 'precondition_failed',
                'session_lost': 'app_error', 'unexpected_dialog': 'app_error',
                'observation_failed': 'app_error', 'evidence_unavailable': 'app_error', 'invalid_policy': 'policy_denied'}.get(self.code, self.code)
