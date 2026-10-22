from dataclasses import dataclass
from datetime import date

from soybean_sim.agents import Agent, AgentDecision, AgentIdentity, AgentType
from soybean_sim.agents.base import Agent as AgentImplementation
from soybean_sim.models import Commodity, CommodityPrice, MarketSnapshot, Region


@dataclass(frozen=True)
class _PassiveAgent:
    """Demonstrate structural compatibility without inheriting an agent base class."""

    identity: AgentIdentity

    def decide(self, snapshot: MarketSnapshot) -> tuple[AgentDecision, ...]:
        """Abstain from decisions without changing the observed market."""
        return ()


def test_structural_agent_contract() -> None:
    """Accept an independent implementation and preserve its observed snapshot."""
    assert Agent is AgentImplementation
    identity = AgentIdentity(
        agent_id="passive-1", agent_type=AgentType.TRADER, region=Region.BRAZIL
    )
    agent: Agent = _PassiveAgent(identity)
    snapshot = MarketSnapshot(
        month=date(2021, 1, 1),
        prices=(
            CommodityPrice(
                commodity=Commodity.SOYBEANS,
                month=date(2021, 1, 1),
                price_usd_per_tonne=400,
                data_source="synthetic contract fixture",
            ),
        ),
        inventories=(),
    )
    original = snapshot.model_dump()
    assert agent.identity == identity
    assert agent.decide(snapshot) == ()
    assert snapshot.model_dump() == original
