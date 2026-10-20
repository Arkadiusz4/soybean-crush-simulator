"""Expose the installed version of the Soybean Crush Market Simulator package.

Configuration, historical data loading, and domain models live in subpackages.
The market simulation itself is not implemented yet.
"""

from importlib.metadata import version

__version__ = version("soybean-crush-simulator")
