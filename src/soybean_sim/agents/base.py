"""Specify the structural contract for agents observing and proposing actions."""

from typing import Protocol

from soybean_sim.models import MarketSnapshot

from .models import AgentDecision, AgentIdentity


class Agent(Protocol):
    """Expose a stable identity and propose decisions without mutating observations."""

    @property
    def identity(self) -> AgentIdentity:
        """Return the agent identity used on every decision it produces."""
        ...

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Propose this agent's actions for snapshot.month; an empty tuple abstains."""
        ...
