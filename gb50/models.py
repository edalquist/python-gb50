"""Data models for Mitsubishi GB-50 HVAC controller entities and telemetry."""

from __future__ import annotations

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, computed_field

from .constants import (
    DriveState,
    OperationMode,
    AirDirection,
    FanSpeed,
    RemoteControlPermission,
    SignStatus,
    ModelType,
)


def c_to_f(celsius: Optional[float]) -> Optional[float]:
    """Convert Celsius to Fahrenheit rounded to 1 decimal place."""
    if celsius is None:
        return None
    return round((celsius * 9.0 / 5.0) + 32.0, 1)


def f_to_c(fahrenheit: Optional[float]) -> Optional[float]:
    """Convert Fahrenheit to Celsius rounded to 1 decimal place."""
    if fahrenheit is None:
        return None
    return round((fahrenheit - 32.0) * 5.0 / 9.0, 1)


class GroupCapabilities(BaseModel):
    """Capabilities and supported hardware features for a specific HVAC group."""
    has_auto_mode: bool = True
    has_dry_mode: bool = True
    has_fan_speed: bool = True
    has_air_direction: bool = True
    has_swing: bool = True
    has_ventilation: bool = False
    has_bypass: bool = False
    has_heat_recovery: bool = False
    fan_speed_stages: int = 4
    air_direction_stages: int = 5
    temp_min_cool_c: float = 19.0
    temp_max_cool_c: float = 30.0
    temp_min_heat_c: float = 17.0
    temp_max_heat_c: float = 28.0
    temp_min_auto_c: float = 19.0
    temp_max_auto_c: float = 28.0

    @computed_field
    @property
    def temp_min_cool_f(self) -> float:
        return c_to_f(self.temp_min_cool_c) or 67.0

    @computed_field
    @property
    def temp_max_cool_f(self) -> float:
        return c_to_f(self.temp_max_cool_c) or 86.0

    @computed_field
    @property
    def temp_min_heat_f(self) -> float:
        return c_to_f(self.temp_min_heat_c) or 63.0

    @computed_field
    @property
    def temp_max_heat_f(self) -> float:
        return c_to_f(self.temp_max_heat_c) or 82.0


class GroupStatus(BaseModel):
    """Real-time status and telemetry for an HVAC group / zone."""
    group_id: int = Field(..., description="Logical group ID (1..50)")
    name: str = Field(..., description="Web display name (e.g. 'FC1-1')")
    model: ModelType = Field(ModelType.IC, description="Equipment model (IC=Indoor, LC=Lossnay)")
    address: int = Field(..., description="Primary M-Net hardware address")
    slave_addresses: List[int] = Field(default_factory=list, description="Slave/sub indoor unit addresses")
    
    # Live operational state
    drive: DriveState = Field(DriveState.OFF, description="Power drive state (ON/OFF)")
    mode: OperationMode = Field(OperationMode.AUTO, description="Current operating mode")
    set_temp_c: Optional[float] = Field(None, description="Target temperature in Celsius")
    inlet_temp_c: Optional[float] = Field(None, description="Current intake/room temperature in Celsius")
    air_direction: AirDirection = Field(AirDirection.HORIZONTAL, description="Louver vane direction")
    fan_speed: FanSpeed = Field(FanSpeed.AUTO, description="Fan speed airflow stage")
    
    # Flags and Locks
    schedule_enabled: bool = Field(False, description="Weekly schedule active for this group")
    filter_dirty: bool = Field(False, description="True if air filter cleaning sign is active")
    error_active: bool = Field(False, description="True if unit error/alarm is active")
    remote_lock: RemoteControlPermission = Field(RemoteControlPermission.PERMIT, description="Local wall controller lock")
    
    # Optional detailed capabilities & raw hex telemetry
    capabilities: Optional[GroupCapabilities] = None
    raw_bulk: Optional[str] = Field(None, description="65-byte raw hex telemetry string from controller")

    @computed_field
    @property
    def set_temp_f(self) -> Optional[float]:
        """Target temperature in Fahrenheit."""
        return c_to_f(self.set_temp_c)

    @computed_field
    @property
    def inlet_temp_f(self) -> Optional[float]:
        """Current intake/room temperature in Fahrenheit."""
        return c_to_f(self.inlet_temp_c)


class GroupControlRequest(BaseModel):
    """Payload to mutate HVAC group parameters."""
    drive: Optional[DriveState] = None
    mode: Optional[OperationMode] = None
    set_temp_c: Optional[float] = None
    set_temp_f: Optional[float] = None
    air_direction: Optional[AirDirection] = None
    fan_speed: Optional[FanSpeed] = None
    remote_lock: Optional[RemoteControlPermission] = None

    def resolved_set_temp_c(self) -> Optional[float]:
        """Resolve setpoint in Celsius, giving precedence to explicit set_temp_c or converting set_temp_f."""
        if self.set_temp_c is not None:
            return self.set_temp_c
        if self.set_temp_f is not None:
            return f_to_c(self.set_temp_f)
        return None


class SystemInfo(BaseModel):
    """Central controller hardware metadata and global configurations."""
    version: str = Field(..., description="ROM firmware version")
    model: str = Field(..., description="Controller model (e.g. GB-50ADA-A)")
    serial_number: str = Field("", description="Hardware serial number")
    system_name: str = Field("", description="Configured facility name (e.g. Example Facility)")
    location_id: str = Field("", description="Location ID")
    ip_address: str = Field(..., description="Controller LAN IP")
    subnet_mask: str = Field(..., description="Subnet mask")
    gateway: str = Field(..., description="Default gateway")
    mac_address: str = Field("", description="Hardware MAC address")
    mnet_address: int = Field(0, description="Central M-Net controller address")
    temp_unit: str = Field("F", description="Controller display unit (F or C)")
    date_format: str = Field("MMDDYYYY", description="Date display format")
    time_format: str = Field("12", description="Time format (12 or 24)")
    licensed_functions: Dict[str, bool] = Field(default_factory=dict, description="Licensed software features")


class ScheduleItem(BaseModel):
    """A scheduled timer event for an HVAC group."""
    index: int = Field(..., description="Event sequence index")
    hour: int = Field(..., description="Hour (0..23)")
    minute: int = Field(..., description="Minute (0..59)")
    drive: Optional[DriveState] = None
    mode: Optional[OperationMode] = None
    set_temp_c: Optional[float] = None
    air_direction: Optional[AirDirection] = None
    fan_speed: Optional[FanSpeed] = None

    @computed_field
    @property
    def set_temp_f(self) -> Optional[float]:
        return c_to_f(self.set_temp_c)

    @computed_field
    @property
    def time_str(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


class AlarmRecord(BaseModel):
    """System fault or unit error event with rich diagnostics and timestamps."""
    index: int = Field(..., description="Event index")
    address: int = Field(..., description="M-Net hardware unit address experiencing fault")
    unit_name: Optional[str] = Field(None, description="Resolved human-readable unit name")
    unit_model: str = Field("", description="Unit model (IC, OC, LC, BC, etc.)")
    detect_address: int = Field(0, description="M-Net unit address that detected the fault")
    detect_name: Optional[str] = Field(None, description="Resolved name of detecting unit")
    error_code: str = Field(..., description="Mitsubishi 4-digit error code")
    priority_level: int = Field(2, description="Priority level (0=Comm, 1=Warning, 2=Unit Error, 3=Emergency)")
    occurred_at: Optional[str] = Field(None, description="ISO timestamp when fault started")
    recovered_at: Optional[str] = Field(None, description="ISO timestamp when fault resolved")
    is_active: bool = Field(False, description="True if fault is active/unresolved")
    duration_str: Optional[str] = Field(None, description="Formatted outage duration")
    title: str = Field("", description="Official Mitsubishi diagnostic title")
    category: str = Field("General", description="Fault category")
    description: str = Field("", description="Plain-English explanation of the error")
    troubleshooting: str = Field("", description="Recommended field troubleshooting steps")
    message: str = Field("", description="Summary message string")
