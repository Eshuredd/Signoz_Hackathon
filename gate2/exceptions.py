from __future__ import annotations


class Gate2Error(RuntimeError):
    """Base class for Gate 2 failures with user-facing messages."""


class ConfigurationError(Gate2Error):
    pass


class ConnectionFailure(Gate2Error):
    pass


class RequestTimeout(Gate2Error):
    pass


class AuthenticationFailure(Gate2Error):
    pass


class AuthorizationFailure(Gate2Error):
    pass


class UnsupportedAPIOperation(Gate2Error):
    pass


class InvalidResponseSchema(Gate2Error):
    pass


class TraceNotFound(Gate2Error):
    pass


class EmptySearchResults(Gate2Error):
    pass


class MCPUnavailable(Gate2Error):
    pass


class MCPToolUnavailable(Gate2Error):
    pass


class IncompleteMCPTelemetry(Gate2Error):
    pass
