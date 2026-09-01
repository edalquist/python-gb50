"""Typed exception hierarchy for python-gb50."""

from typing import Optional, Any


class GB50Error(Exception):
    """Base exception for all GB-50 library errors."""
    pass


class GB50ProtocolError(GB50Error):
    """Protocol-level error returned by the GB-50 controller or invalid packet structure."""

    def __init__(
        self,
        message: str,
        point: str = "",
        code: str = "",
        error_code: Optional[int] = None,
        raw_xml: Optional[str] = None,
    ):
        detail = f"{message} (Point='{point}', Code='{code}')" if point or code else message
        super().__init__(detail)
        self.point = point
        self.code = code
        self.error_code = error_code if error_code is not None else (int(code) if code.isdigit() else None)
        self.raw_xml = raw_xml


class GB50TransportError(GB50Error):
    """HTTP/transport-level communication error with GB-50 controller."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GB50ParseError(GB50Error):
    """Error parsing binary telemetry (Bulk) or XML packet responses."""

    def __init__(self, message: str, byte_index: Optional[int] = None, raw_value: Any = None):
        super().__init__(message)
        self.byte_index = byte_index
        self.raw_value = raw_value
