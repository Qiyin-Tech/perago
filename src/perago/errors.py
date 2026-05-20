class TaskDefinitionError(ValueError):
    """Raised when a task module violates the Perago task contract."""


class RuntimeConfigError(ValueError):
    """Raised when local runtime configuration is invalid."""


class TaskInputError(ValueError):
    """Raised when Conductor task input does not match the Perago contract."""


class GuardrailViolation(RuntimeError):
    """Raised when a workspace guardrail check fails."""


class PreGuardrailViolation(GuardrailViolation):
    """Raised when pre guardrails fail before the task function runs."""


class PostGuardrailViolation(GuardrailViolation):
    """Raised when post guardrails fail after the task function returns."""


class PublishFenceError(RuntimeError):
    """Raised when a workspace branch cannot be safely advanced by an attempt."""
