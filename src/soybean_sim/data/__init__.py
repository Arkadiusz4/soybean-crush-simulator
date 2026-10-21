"""Historical dataset loading and preprocessing, independent of simulation state."""

from .selection import MarketDataSelectionError, select_market_data

__all__ = ["MarketDataSelectionError", "select_market_data"]
