class TaskDefinitionError(ValueError):
    """Raised when a task module violates the Perago task contract."""


class RuntimeConfigError(ValueError):
    """Raised when local runtime configuration is invalid."""


class GuardrailViolation(RuntimeError):
    """Raised when a workspace guardrail check fails."""
