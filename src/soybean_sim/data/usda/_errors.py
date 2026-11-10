"""Distinguish file access, CSV structure, and observation failures in PSD loading."""


class USDADataError(ValueError):
    """Base error for reading or validating local USDA PSD data."""


class USDAFileError(USDADataError):
    """A USDA CSV file cannot be opened, read, or decoded as UTF-8."""


class USDAFormatError(USDADataError):
    """CSV syntax, headers, or row widths do not match the expected structure."""


class USDAObservationError(USDADataError):
    """An observation has invalid metadata, quantities, or conflicting duplicates."""
