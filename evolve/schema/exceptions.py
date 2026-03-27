"""
Custom exceptions for Evolve.
"""


class EvolveException(Exception):
    """Base exception class for all evolve errors."""

    pass


class NamespaceNotFoundException(EvolveException):
    """Raised when a namespace is not found."""

    pass


class NamespaceAlreadyExistsException(EvolveException):
    """Raised when a namespace already exists."""

    pass
