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
    with pytest.raises(ValueError, match="Invalid bulk payload length"):
        parse_bulk_telemetry("010002")


def test_invalid_bulk_header():
    with pytest.raises(ValueError, match="Invalid bulk packet header"):
        # Header byte is 0x02 instead of 0x01
        parse_bulk_telemetry("02" + "00" * 64)
