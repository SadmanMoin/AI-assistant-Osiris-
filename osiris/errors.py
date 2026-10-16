"""Application errors with messages that are safe to show in the UI."""


class OsirisError(Exception):
    """Base error for expected Osiris failures."""


class ApprovalError(OsirisError):
    """An automation cannot be approved or enabled."""


class PolicyError(OsirisError):
    """A permission or confirmation check refused an action."""


class ValidationError(OsirisError):
    """A script failed static validation."""


class ExecutionError(OsirisError):
    """A script or tool run failed."""


class IntegrationError(OsirisError):
    """An external integration could not complete a live check or action."""
