"""Exception hierarchy."""


class KKPayError(Exception):
    """Base error for all client failures."""


class KKPayConfigurationError(KKPayError, ValueError):
    """The client configuration is incomplete or unsafe."""


class KKPayHTTPError(KKPayError):
    """The gateway could not be reached or returned invalid HTTP."""


class KKPayAPIError(KKPayError):
    """The gateway returned a valid error response."""

    def __init__(self, message: str, *, status_code: int | None = None, request_id: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class KKPaySignatureError(KKPayError):
    """A callback signature is missing or invalid."""

