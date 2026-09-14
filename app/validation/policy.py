"""Non-negotiable analytics policy, including legacy administrative entry points."""


def deny_analytics_write() -> None:
    raise PermissionError("Baseball analytics resources are read-only in this runtime.")
