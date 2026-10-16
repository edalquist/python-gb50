"""Unit tests for 65-byte Bulk telemetry parser."""

import pytest
from gb50.bulk_parser import parse_bulk_telemetry
from gb50.constants import DriveState, OperationMode, AirDirection, FanSpeed, ModelType, RemoteControlPermission


def test_parse_indoor_unit_bulk():
    # Synthetic packet assembled from documented telemetry fields.
    bulk_hex = "010002140000E6040601000000000000001F0000000100010000010000000000000000000000000000000000000000000000000000000000000000000000000000"
    parsed = parse_bulk_telemetry(bulk_hex)

    assert parsed["drive"] == DriveState.OFF
    assert parsed["mode"] == OperationMode.HEAT
    assert parsed["set_temp_c"] == 20.0
    assert parsed["inlet_temp_c"] == 23.0
    assert parsed["air_direction"] == AirDirection.HORIZONTAL
    assert parsed["fan_speed"] == FanSpeed.AUTO
    assert parsed["remote_lock"] == RemoteControlPermission.PROHIBIT
    assert parsed["filter_dirty"] is False
    assert parsed["error_active"] is False
    assert parsed["model"] == ModelType.IC
    assert parsed["schedule_enabled"] is True
    assert parsed["capabilities"].has_auto_mode is True
    assert parsed["capabilities"].has_air_direction is True


def test_parse_lossnay_unit_bulk():
    # Synthetic packet assembled from documented telemetry fields.
    bulk_hex = "0100821600000004030000000000000000020000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    parsed = parse_bulk_telemetry(bulk_hex)

    assert parsed["drive"] == DriveState.OFF
    assert parsed["mode"] == OperationMode.LC_AUTO
    assert parsed["set_temp_c"] == 22.0
    assert parsed["inlet_temp_c"] == 0.0
    assert parsed["air_direction"] == AirDirection.HORIZONTAL
    assert parsed["fan_speed"] == FanSpeed.HIGH
    assert parsed["model"] == ModelType.LC
    assert parsed["schedule_enabled"] is False


def test_invalid_bulk_length():
    from gb50.exceptions import GB50ParseError
    with pytest.raises(GB50ParseError, match="Invalid bulk payload length"):
        parse_bulk_telemetry("010002")


def test_invalid_bulk_header():
    from gb50.exceptions import GB50ParseError
    with pytest.raises(GB50ParseError, match="Invalid bulk packet header"):
        # Header byte is 0x02 instead of 0x01
        parse_bulk_telemetry("02" + "00" * 64)


def test_unknown_drive_code_raises():
    from gb50.exceptions import GB50ParseError
    # Byte 1 is 0xFF (unknown drive)
    hex_data = "01FF02140000E6040601000000000000001F0000000100010000010000000000000000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(GB50ParseError) as exc_info:
        parse_bulk_telemetry(hex_data)
    assert exc_info.value.byte_index == 1
    assert "Unknown bulk drive code" in str(exc_info.value)


def test_unknown_mode_code_raises():
    from gb50.exceptions import GB50ParseError
    # Byte 2 is 0xFE (unknown mode)
    hex_data = "0100FE140000E6040601000000000000001F0000000100010000010000000000000000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(GB50ParseError) as exc_info:
        parse_bulk_telemetry(hex_data)
    assert exc_info.value.byte_index == 2
    assert "Unknown bulk operation mode code" in str(exc_info.value)


def test_invalid_bulk_length_too_long():
    from gb50.exceptions import GB50ParseError
    hex_data = "01" * 66  # 132 chars
    with pytest.raises(GB50ParseError, match="expected exactly 130 hex chars"):
        parse_bulk_telemetry(hex_data)


def test_invalid_bulk_non_hex_characters():
    from gb50.exceptions import GB50ParseError
    hex_data = "01" * 64 + "ZZ"  # 130 chars with invalid hex 'ZZ'
    with pytest.raises(GB50ParseError, match="Invalid hexadecimal encoding"):
        parse_bulk_telemetry(hex_data)


def test_granular_button_lockout_detection():
    # Byte 9 (RemoCon) is 0 (PERMIT), but Byte 10 (DriveItem) is 1 (CHK_ON)
    base_bytes = bytearray(bytes.fromhex("010002140000E6040600000000000000001F0000000100010000010000000000000000000000000000000000000000000000000000000000000000000000000000"))
    assert parse_bulk_telemetry(base_bytes.hex())["remote_lock"] == RemoteControlPermission.PERMIT

    # Test DriveItem = 1 (Byte 10)
    drive_locked = bytearray(base_bytes)
    drive_locked[10] = 1
    assert parse_bulk_telemetry(drive_locked.hex())["remote_lock"] == RemoteControlPermission.PROHIBIT

    # Test ModeItem = 1 (Byte 11)
    mode_locked = bytearray(base_bytes)
    mode_locked[11] = 1
    assert parse_bulk_telemetry(mode_locked.hex())["remote_lock"] == RemoteControlPermission.PROHIBIT

    # Test SetTempItem = 1 (Byte 12)
    temp_locked = bytearray(base_bytes)
    temp_locked[12] = 1
    assert parse_bulk_telemetry(temp_locked.hex())["remote_lock"] == RemoteControlPermission.PROHIBIT


def test_supported_fan_speeds_stages_and_models():
    # Base 65-byte packet for an IC unit
    base = bytearray(65)
    base[0] = 0x01   # Header
    base[17] = 0x1F  # IC unit
    base[23] = 1     # auto_sw
    base[24] = 1     # dry_sw

    # 1. 2-Stage IC unit with Auto fan speed (like Group 22 / Address 18)
    base[25] = 0     # 2STAGES
    base[47] = 1     # FanAutoSW = ENABLE
    parsed_2stage_auto = parse_bulk_telemetry(base.hex())
    assert parsed_2stage_auto["capabilities"].fan_speed_stages == 2
    assert parsed_2stage_auto["capabilities"].has_auto_fan_speed is True
    assert parsed_2stage_auto["capabilities"].supported_fan_speeds == [
        FanSpeed.AUTO,
        FanSpeed.MID2,
        FanSpeed.HIGH,
    ]

    # 2. 2-Stage IC unit WITHOUT Auto fan speed
    base[47] = 0     # FanAutoSW = DISABLE
    parsed_2stage_no_auto = parse_bulk_telemetry(base.hex())
    assert parsed_2stage_no_auto["capabilities"].supported_fan_speeds == [
        FanSpeed.MID2,
        FanSpeed.HIGH,
    ]

    # 3. 3-Stage IC unit with Auto fan speed
    base[25] = 3     # 3STAGES
    base[47] = 1     # FanAutoSW = ENABLE
    parsed_3stage = parse_bulk_telemetry(base.hex())
    assert parsed_3stage["capabilities"].fan_speed_stages == 3
    assert parsed_3stage["capabilities"].supported_fan_speeds == [
        FanSpeed.AUTO,
        FanSpeed.MID2,
        FanSpeed.MID1,
        FanSpeed.HIGH,
    ]

    # 4. 4-Stage IC unit with Auto fan speed
    base[25] = 1     # 4STAGES
    base[47] = 1     # FanAutoSW = ENABLE
    parsed_4stage = parse_bulk_telemetry(base.hex())
    assert parsed_4stage["capabilities"].fan_speed_stages == 4
    assert parsed_4stage["capabilities"].supported_fan_speeds == [
        FanSpeed.AUTO,
        FanSpeed.LOW,
        FanSpeed.MID2,
        FanSpeed.MID1,
        FanSpeed.HIGH,
    ]

    # 5. Fixed fan speed IC unit (no speed change)
    base[25] = 2     # NONE
    parsed_fixed = parse_bulk_telemetry(base.hex())
    assert parsed_fixed["capabilities"].has_fan_speed is False
    assert parsed_fixed["capabilities"].supported_fan_speeds == []

    # 6. LOSSNAY (LC) ventilator unit
    base[17] = 0x02  # LC model
    base[25] = 0     # 2 stages
    base[47] = 0     # FanAutoSW = DISABLE
    parsed_lc = parse_bulk_telemetry(base.hex())
    assert parsed_lc["capabilities"].supported_fan_speeds == [
        FanSpeed.LOW,
        FanSpeed.HIGH,
    ]



