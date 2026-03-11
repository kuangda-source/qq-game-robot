class BotError(Exception):
    """Base error for business logic."""


class DataSourceUnavailable(BotError):
    """Raised when external source fails in a recoverable way."""


class NotFoundError(BotError):
    """Raised when no game is found."""


class AmbiguousQueryError(BotError):
    """Raised when query matches too many games."""


class ValidationError(BotError):
    """Raised when user input is invalid."""
