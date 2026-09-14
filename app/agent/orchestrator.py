"""Orchestration entry point, awaiting the verified planning/response loop."""

from app.config import Settings, settings


def run_all_channel_baseball_agent(user_prompt: str, config: Settings = settings) -> str:
    """Reject legacy execution until accepted-product finalization is implemented."""
    raise RuntimeError("The validated planning/response loop is not yet available; legacy execution is disabled.")
