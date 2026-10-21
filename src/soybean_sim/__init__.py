"""Expose the installed package version without initializing a simulation."""

from importlib.metadata import version

__version__ = version("soybean-crush-simulator")
