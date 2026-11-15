"""Record validated immutable monthly histories and completed simulation results."""

import math
from collections import Counter
from datetime import date
from itertools import pairwise
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soybean_sim.agents import AgentDecision, DecisionAction
from soybean_sim.market import (
    ExternalTradeFlow,
    MarketFlow,
    MarketFlowType,
    MarketPressure,
    MarketState,
    RegionalPrice,
    SettlementResult,
    TradeExecution,
    validate_external_trades,
)
from soybean_sim.models import Commodity, NonNegativeTonnes, Region

from . import _calendar


class ConsumptionResult(BaseModel):
    """Record one market's demand and stock available at consumption settlement.

    Args:
        month (date): First day of the simulation month.
        region (Region): Region consuming the commodity.
        commodity (Commodity): Consumed commodity.
        available_tonnes (NonNegativeTonnes): Stock before consumption.
        requested_tonnes (NonNegativeTonnes): Original demand.
        fulfilled_tonnes (NonNegativeTonnes): Physically consumed stock.
        unmet_tonnes (NonNegativeTonnes): Unfulfilled demand.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    month: Annotated[date, Field(strict=True)]
    region: Region
    commodity: Commodity
    available_tonnes: NonNegativeTonnes
    requested_tonnes: NonNegativeTonnes
    fulfilled_tonnes: NonNegativeTonnes
    unmet_tonnes: NonNegativeTonnes

    @model_validator(mode="after")
    def quantities_are_consistent(self) -> Self:
        """Require demand accounting and a stock-bounded withdrawal.

        Returns:
            Self: Validated demand and consumption quantities.
        """
        if self.month.day != 1:
            raise ValueError("consumption month must be the first day of a month")
        if self.fulfilled_tonnes != min(self.requested_tonnes, self.available_tonnes):
            raise ValueError("fulfilled consumption must equal the available demand")
        if not math.isclose(
            self.requested_tonnes,
            self.fulfilled_tonnes + self.unmet_tonnes,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError("requested consumption must equal fulfilled plus unmet")
        return self


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
        external_trade_flows: Fully executed movements across the clearing boundary.
        consumption_results: Per-market demand accounting in opt-in mode, or None.
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
    external_trade_flows: tuple[ExternalTradeFlow, ...] = ()
    consumption_results: tuple[ConsumptionResult, ...] | None = None

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
        if not self.physical_cycle and (
            self.market_flows or self.executed_trades or self.external_trade_flows
        ):
            raise ValueError("physical activity records require physical_cycle=True")

        if any(flow.month != self.month for flow in self.market_flows):
            raise ValueError("all market flows must match the result month")

        if any(trade.month != self.month for trade in self.executed_trades):
            raise ValueError("all executed trades must match the result month")
        validate_external_trades(self.external_trade_flows)
        if any(trade.month != self.month for trade in self.external_trade_flows):
            raise ValueError("all external trades must match the result month")
        if self.external_trade_flows and self.executed_trades:
            raise ValueError(
                "boundary schedules and agent trade executions are exclusive"
            )

        if self.consumption_results is not None:
            if not self.physical_cycle:
                raise ValueError("consumption results require physical_cycle=True")
            keys = {
                (stock.region, stock.commodity)
                for stock in self.starting_state.snapshot.inventories
            }
            results = {
                (item.region, item.commodity): item for item in self.consumption_results
            }
            if len(results) != len(self.consumption_results) or set(results) != keys:
                raise ValueError(
                    "consumption results must cover each inventory exactly once"
                )
            requested_flows = tuple(
                flow
                for flow in self.market_flows
                if flow.flow_type == MarketFlowType.CONSUMPTION
            )
            requests = {
                (flow.region, flow.commodity): flow.quantity_tonnes
                for flow in requested_flows
            }
            if len(requests) != len(requested_flows) or not set(requests) <= keys:
                raise ValueError("consumption requests must uniquely match inventories")
            if any(
                item.month != self.month
                or item.requested_tonnes != requests.get(key, 0)
                for key, item in results.items()
            ):
                raise ValueError(
                    "consumption results must match the month's requested flows"
                )

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
