"""Mitsubishi Electric GB-50 / G-50 HVAC Controller Embeddable Python Library."""

from .constants import (
    DriveState,
    OperationMode,
    AirDirection,
    FanSpeed,
    RemoteControlPermission,
    SignStatus,
    ModelType,
)
from .models import (
    GroupStatus,
    GroupCapabilities,
    GroupControlRequest,
    SystemInfo,
    ScheduleItem,
    AlarmRecord,
    c_to_f,
    f_to_c,
)
from .protocol import GB50ProtocolError
from .client import GB50Client
from .state_manager import StateManager

__version__ = "1.0.0"

__all__ = [
    "GB50Client",
    "StateManager",
    "GB50ProtocolError",
    "DriveState",
    "OperationMode",
    "AirDirection",
    "FanSpeed",
    "RemoteControlPermission",
    "SignStatus",
    "ModelType",
    "GroupStatus",
    "GroupCapabilities",
    "GroupControlRequest",
    "SystemInfo",
    "ScheduleItem",
    "AlarmRecord",
    "c_to_f",
    "f_to_c",
]
