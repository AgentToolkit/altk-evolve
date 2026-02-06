"""
Custom exceptions for Kaizen.
"""


class KaizenException(Exception):
    """Base exception class for all kaizen errors."""

    pass


class NamespaceNotFoundException(KaizenException):
    """Raised when a namespace is not found."""

    pass


class NamespaceAlreadyExistsException(KaizenException):
    """Raised when a namespace already exists."""

    pass
