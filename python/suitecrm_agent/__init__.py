"""SuiteCRM-aware code generation agent package."""

from .agent import SuiteCRMAgent
from .config import AgentConfig
from .models import AgentTask

__all__ = ["SuiteCRMAgent", "AgentConfig", "AgentTask"]
