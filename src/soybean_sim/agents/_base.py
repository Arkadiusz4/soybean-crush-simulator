"""Specify the structural contract for agents observing and proposing actions."""

from typing import Protocol

from soybean_sim.models import MarketSnapshot

from ._models import AgentDecision, AgentIdentity


class Agent(Protocol):
    """Expose a stable identity and propose decisions without mutating observations."""

    @property
    def identity(self) -> AgentIdentity:
        """Return the agent identity used on every decision it produces.

        Returns:
            AgentIdentity: Stable identity attached to every decision from this agent.
        """
        ...

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Propose this agent's actions for snapshot.month; an empty tuple abstains.

        Args:
            snapshot (MarketSnapshot): Current-month global reference prices and
                regional inventories.

        Returns:
            tuple[AgentDecision, ...]: Proposed actions for the snapshot month, or an
                empty tuple to abstain.
        """
        ...
