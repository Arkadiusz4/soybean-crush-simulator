"""Record validated immutable monthly histories and completed simulation results."""

from collections import Counter
from datetime import date
from itertools import pairwise
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.agents import AgentDecision
from soybean_sim.market import MarketState, SettlementResult

from . import calendar


class MonthlyResult(BaseModel):
    """Record monthly starting state, decisions, settlement, and ending state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    starting_state: MarketState
    decisions: tuple[AgentDecision, ...]
    settlement_results: tuple[SettlementResult, ...]
    ending_state: MarketState

    @model_validator(mode="after")
    def monthly_records_are_consistent(self) -> Self:
        """Require matching state months, decision months, and settlement coverage."""
        if (
            self.starting_state.snapshot.month != self.month
            or self.ending_state.snapshot.month != self.month
        ):
            raise ValueError("starting and ending states must match the result month")

        if any(decision.month != self.month for decision in self.decisions):
            raise ValueError("all decisions must match the result month")

        if self.starting_state.snapshot.prices != self.ending_state.snapshot.prices:
            raise ValueError("reference prices must remain unchanged within a month")

        if Counter(self.decisions) != Counter(
            result.decision for result in self.settlement_results
        ):
            raise ValueError(
                "settlement results must cover every decision exactly once"
            )

        return self


class SimulationResult(BaseModel):
    """Group a nonempty monthly history with its initial and last completed states."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_state: MarketState
    monthly_results: Annotated[tuple[MonthlyResult, ...], Field(min_length=1)]
    final_state: MarketState

    @model_validator(mode="after")
    def history_is_continuous(self) -> Self:
        """Require connected months, carried stocks, and constant prices."""
        if self.monthly_results[0].starting_state != self.initial_state:
            raise ValueError("initial_state must match the first starting state")

        if self.monthly_results[-1].ending_state != self.final_state:
            raise ValueError("final_state must match the last completed ending state")

        for previous, current in pairwise(self.monthly_results):
            if current.starting_state != calendar.advance_market_month(
                previous.ending_state
            ):
                raise ValueError(
                    "monthly history must carry stocks and constant prices "
                    "into the next month"
                )

        return self
