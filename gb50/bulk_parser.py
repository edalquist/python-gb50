"""Parser for the Mitsubishi GB-50 65-byte binary telemetry payload (Bulk attribute)."""

from typing import Dict, Any, Optional
import struct

from .constants import (
    DriveState,
    OperationMode,
    AirDirection,
    FanSpeed,
    RemoteControlPermission,
    SignStatus,
    ModelType,
    BULK_DRIVE_MAP,
    BULK_MODE_MAP,
    BULK_AIR_DIR_MAP,
    BULK_FAN_SPEED_MAP,
    BULK_MODEL_MAP,
)
from .models import GroupCapabilities


def parse_bcd_temp(int_byte: int, dec_nibble: int) -> float:
    """Parse BCD-encoded limit temperature with decimal fractional part."""
    if int_byte == 0:
        return 0.0
    # The integer part in GB-50 is formatted as hex representation of decimal digits (e.g. 0x19 -> 19)
    try:
        val = int(f"{int_byte:02x}")
    except ValueError:
        val = int_byte
    if 0 < dec_nibble < 10:
        return float(f"{val}.{dec_nibble}")
    return float(val)


def parse_bulk_telemetry(bulk_hex: str) -> Dict[str, Any]:
    """Parse a 130-character hex string (65 bytes) into structured HVAC state dictionary.
    
    Args:
        bulk_hex: 130-character hex string returned in the Bulk XML attribute.
        
    Returns:
        Dictionary of decoded telemetry fields, temperatures, and capability flags.
    """
    if not bulk_hex or len(bulk_hex) < 130:
        raise ValueError(f"Invalid bulk payload length: expected 130 hex chars, got {len(bulk_hex) if bulk_hex else 0}")
    
    data = bytes.fromhex(bulk_hex[:130])
    if data[0] != 0x01:
        raise ValueError(f"Invalid bulk packet header: expected 0x01, got {data[0]:#04x}")

    # 1. Drive State (Byte 1)
    drive = BULK_DRIVE_MAP.get(data[1], DriveState.OFF)

    # 2. Mode (Byte 2)
    mode = BULK_MODE_MAP.get(data[2], OperationMode.AUTO)

    # 3. Target Set Temperature (Bytes 3-4)
    set_int = data[3]
    set_dec = data[4]
    if set_dec > 0 and set_dec < 10:
        set_temp_c = float(f"{set_int}.{set_dec}")
    else:
        set_temp_c = float(set_int) if set_int > 0 else None

    # 4. Inlet / Room Temperature (Bytes 5-6: 16-bit signed integer / 10.0)
    raw_inlet = struct.unpack(">h", data[5:7])[0]
    inlet_temp_c = round(raw_inlet / 10.0, 1)

    # 5. Air Direction (Byte 7)
    air_direction = BULK_AIR_DIR_MAP.get(data[7], AirDirection.HORIZONTAL)

    # 6. Fan Speed (Byte 8)
    fan_speed = BULK_FAN_SPEED_MAP.get(data[8], FanSpeed.AUTO)

    # 7. Remote Controller Lock (Byte 9)
    remote_lock = RemoteControlPermission.PROHIBIT if data[9] == 1 else RemoteControlPermission.PERMIT

    # 8. Filter Sign & Error Sign (Bytes 15-16)
    filter_dirty = data[15] == 1
    error_active = data[16] == 1

    # 9. Hardware Model (Byte 17)
    model = BULK_MODEL_MAP.get(data[17], ModelType.IC)

    # 10. Schedule Active (Byte 21)
    schedule_enabled = data[21] == 1

    # 11. Fan Stages & Capabilities (Bytes 23-40)
    auto_sw = data[23] == 1
    dry_sw = data[24] == 1
    fan_stage_code = data[25]
    fan_stages = 4 if fan_stage_code == 1 else (3 if fan_stage_code == 3 else 2)
    air_dir_sw = data[26] == 1
    swing_sw = data[27] == 1
    venti_sw = data[28] == 1
    bypass_sw = data[29] == 1
    lc_auto_sw = data[30] == 1
    heat_rec_sw = data[31] == 1
    air_stage_code = data[45]
    air_stages = 5 if air_stage_code == 1 else 4

    # Temperature Limits (Bytes 32-40)
    cool_min = parse_bcd_temp(data[32], (data[38] >> 4) & 0xF) or 19.0
    heat_max = parse_bcd_temp(data[33], data[38] & 0xF) or 28.0
    cool_max = parse_bcd_temp(data[34], (data[39] >> 4) & 0xF) or 30.0
    heat_min = parse_bcd_temp(data[35], data[39] & 0xF) or 17.0
    auto_min = parse_bcd_temp(data[36], (data[40] >> 4) & 0xF) or 19.0
    auto_max = parse_bcd_temp(data[37], data[40] & 0xF) or 28.0

    capabilities = GroupCapabilities(
        has_auto_mode=auto_sw,
        has_dry_mode=dry_sw,
        has_fan_speed=fan_stage_code != 2,
        has_air_direction=air_dir_sw,
        has_swing=swing_sw,
        has_ventilation=venti_sw,
        has_bypass=bypass_sw,
        has_heat_recovery=heat_rec_sw,
        fan_speed_stages=fan_stages,
        air_direction_stages=air_stages,
        temp_min_cool_c=cool_min,
        temp_max_cool_c=cool_max,
        temp_min_heat_c=heat_min,
        temp_max_heat_c=heat_max,
        temp_min_auto_c=auto_min,
        temp_max_auto_c=auto_max,
    )

    return {
        "drive": drive,
        "mode": mode,
        "set_temp_c": set_temp_c,
        "inlet_temp_c": inlet_temp_c,
        "air_direction": air_direction,
        "fan_speed": fan_speed,
        "remote_lock": remote_lock,
        "filter_dirty": filter_dirty,
        "error_active": error_active,
        "model": model,
        "schedule_enabled": schedule_enabled,
        "capabilities": capabilities,
    }
