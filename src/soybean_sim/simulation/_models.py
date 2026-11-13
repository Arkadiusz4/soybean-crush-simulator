"""Record validated immutable monthly histories and completed simulation results."""

from collections import Counter
from datetime import date
from itertools import pairwise
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.agents import AgentDecision, DecisionAction
from soybean_sim.market import (
    MarketFlow,
    MarketPressure,
    MarketState,
    RegionalPrice,
    SettlementResult,
    TradeExecution,
)

from . import _calendar


class MonthlyResult(BaseModel):
    """Record monthly starting state, decisions, settlement, and ending state.

    Args:
        month (date): First calendar day of the completed simulation month.
        starting_state (MarketState): Opening state before production or decisions.
        decisions (tuple[AgentDecision, ...]): Collected agent intentions for the month.
        settlement_results: All decision results in legacy mode, or crushing results
            only in physical-cycle mode.
        ending_state (MarketState): Market state after settlement, dated to the same
            month.
        physical_cycle (bool): Distinguishes physical activity from legacy settlement,
            including physical months with no flows or matched trades.
        market_flows (tuple[MarketFlow, ...]): Full monthly flows, including references.
        executed_trades: Matched and settled physical trades.
        regional_prices: Regional values observed by agents during this month.
        market_pressures: Physical supply/demand signals used for next-month pricing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    month: Annotated[date, Field(strict=True)]
    starting_state: MarketState
    decisions: tuple[AgentDecision, ...]
    settlement_results: tuple[SettlementResult, ...]
    ending_state: MarketState
    physical_cycle: bool = Field(default=False, strict=True)
    market_flows: tuple[MarketFlow, ...] = ()
    executed_trades: tuple[TradeExecution, ...] = ()
    regional_prices: tuple[RegionalPrice, ...] = ()
    market_pressures: tuple[MarketPressure, ...] = ()

    @model_validator(mode="after")
    def monthly_records_are_consistent(self) -> Self:
        """Require matching state months, decision months, and settlement coverage.

        Returns:
            Self: The validated monthly result instance.
        """
        if (
            self.starting_state.snapshot.month != self.month
            or self.ending_state.snapshot.month != self.month
        ):
            raise ValueError("starting and ending states must match the result month")

        if any(decision.month != self.month for decision in self.decisions):
            raise ValueError("all decisions must match the result month")

        if self.starting_state.snapshot.prices != self.ending_state.snapshot.prices:
            raise ValueError("reference prices must remain unchanged within a month")

        settled_decisions = (
            decision
            for decision in self.decisions
            if not self.physical_cycle or decision.action == DecisionAction.CRUSH
        )
        if Counter(settled_decisions) != Counter(
            result.decision for result in self.settlement_results
        ):
            covered = "CRUSH decision" if self.physical_cycle else "decision"
            raise ValueError(
                f"settlement results must cover every {covered} exactly once"
            )

        return self

    @model_validator(mode="after")
    def physical_records_match_mode_and_month(self) -> Self:
        """Require physical activity records to match the mode and recorded month.

        Returns:
            The validated monthly result.
        """
        if not self.physical_cycle and (self.market_flows or self.executed_trades):
            raise ValueError("physical activity records require physical_cycle=True")

        if any(flow.month != self.month for flow in self.market_flows):
            raise ValueError("all market flows must match the result month")

        if any(trade.month != self.month for trade in self.executed_trades):
            raise ValueError("all executed trades must match the result month")

        return self

    @model_validator(mode="after")
    def regional_records_match_month_and_keys(self) -> Self:
        """Require unique current-month regional observations and physical pressures.

        Returns:
            Self: The validated monthly result.
        """
        if self.market_pressures and not self.physical_cycle:
            raise ValueError("market pressures require physical_cycle=True")

        for label, records in (
            ("regional price", self.regional_prices),
            ("market pressure", self.market_pressures),
        ):
            if any(record.month != self.month for record in records):
                raise ValueError(f"all {label} records must match the result month")

            keys = [(record.region, record.commodity) for record in records]
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate {label} records")

        return self


class SimulationResult(BaseModel):
    """Group a nonempty monthly history with its initial and last completed states.

    Args:
        initial_state (MarketState): Market state at the start of the first simulated
            month.
        monthly_results (tuple[MonthlyResult, ...]): Nonempty sequence of connected
            monthly results with carried stocks and constant global references.
        final_state (MarketState): Ending state of the last completed month, without an
            extra calendar advance.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_state: MarketState
    monthly_results: Annotated[tuple[MonthlyResult, ...], Field(min_length=1)]
    final_state: MarketState

    @model_validator(mode="after")
    def history_is_continuous(self) -> Self:
        """Require connected months, carried stocks, and constant prices.

        Returns:
            Self: The validated simulation result instance.
        """
        if self.monthly_results[0].starting_state != self.initial_state:
            raise ValueError("initial_state must match the first starting state")

        if self.monthly_results[-1].ending_state != self.final_state:
            raise ValueError("final_state must match the last completed ending state")

        for previous, current in pairwise(self.monthly_results):
            if current.starting_state != _calendar.advance_market_month(
                previous.ending_state
            ):
                raise ValueError(
                    "monthly history must carry stocks and constant prices "
                    "into the next month"
                )

        return self
