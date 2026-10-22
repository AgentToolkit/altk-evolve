"""Errors every layer of Forge raises and the API reports."""


class InputError(ValueError):
    """A request the user can fix (missing field, unknown id, ...)."""
