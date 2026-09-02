"""Unit tests for XML protocol building and parsing."""

import pytest
import xml.etree.ElementTree as ET
from gb50.protocol import (
    build_get_system_info_request,
    build_get_topology_request,
    build_get_groups_telemetry_request,
    build_set_group_request,
    build_set_groups_batch_request,
    build_reset_filter_request,
    build_get_summertime_request,
    build_get_setback_request,
    parse_system_info,
    parse_topology,
    parse_groups_telemetry,
    parse_today_schedule,
    parse_summertime,
    parse_setback,
    check_error_response,
    GB50ProtocolError,
)
from gb50.models import GroupControlRequest
from gb50.constants import DriveState, OperationMode, AirDirection, FanSpeed


def test_build_requests():
    req_sys = build_get_system_info_request()
    assert "<Command>getRequest</Command>" in req_sys
    assert "<SystemData" in req_sys
    assert "<FunctionControl>" in req_sys

    req_top = build_get_topology_request()
    assert "<ControlGroup>" in req_top

    req_batch = build_get_groups_telemetry_request([1, 2, 15])
    assert 'Group="1"' in req_batch
    assert 'Group="2"' in req_batch
    assert 'Group="15"' in req_batch

    req_set = build_set_group_request(
        1,
        GroupControlRequest(
            drive=DriveState.ON,
            mode=OperationMode.HEAT,
            set_temp_f=70.0,
            fan_speed=FanSpeed.HIGH,
        ),
    )
    assert '<Command>setRequest</Command>' in req_set
    assert 'Group="1"' in req_set
    assert 'Drive="ON"' in req_set
    assert 'Mode="HEAT"' in req_set
    assert 'SetTemp="21.1"' in req_set
    assert 'FanSpeed="HIGH"' in req_set

    req_filter = build_reset_filter_request(1)
    assert 'FilterSign="RESET"' in req_filter

    req_st = build_get_summertime_request()
    assert 'Month1="*"' in req_st
    assert 'Month2="*"' in req_st

    req_sb = build_get_setback_request()
    assert 'SetbackFunc="*"' in req_sb


def test_parse_system_info():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getResponse</Command>
  <DatabaseManager>
    <SystemData Version="2.80" Model="GB-50ADA-A" TempUnit="F" Name="Example Facility" Number="00000-001" IPAdrsLan="192.0.2.90" SubnetMaskLan="255.255.255.0" GwLan="192.0.2.1" MacAddress="020000000001" MnetAdrs="0" DateFormat="MMDDYYYY" TimeFormat="12" />
    <FunctionControl>
      <FunctionList>
        <FunctionRecord Index="1" Status="ENABLE" />
        <FunctionRecord Index="2" Status="ENABLE" />
        <FunctionRecord Index="3" Status="DISABLE" />
      </FunctionList>
    </FunctionControl>
  </DatabaseManager>
</Packet>"""
    info = parse_system_info(xml)
    assert info.version == "2.80"
    assert info.system_name == "Example Facility"
    assert info.licensed_functions.get("WebBrowse") is True
    assert info.licensed_functions.get("Schedule") is True
    assert info.licensed_functions.get("Account") is False


def test_parse_summertime():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getResponse</Command>
  <DatabaseManager>
    <SummerTime CountryCode="US0" Month1="3" Day1="8" Hour1="2" Minute1="0" ShiftMin1="60" Month2="11" Day2="1" Hour2="2" Minute2="0" ShiftMin2="-60" />
  </DatabaseManager>
</Packet>"""
    st = parse_summertime(xml)
    assert st["country_code"] == "US0"
    assert st["month1"] == "3"
    assert st["day1"] == "8"
    assert st["month2"] == "11"
    assert st["shift_min1"] == "60"


def test_parse_setback():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getResponse</Command>
  <DatabaseManager>
    <SetbackControl SetbackFunc="USE" StartHour="22" StartMinute="0" EndHour="6" EndMinute="0" />
  </DatabaseManager>
</Packet>"""
    sb = parse_setback(xml)
    assert sb["enabled"] is True
    assert sb["start_hour"] == 22
    assert sb["end_hour"] == 6


def test_parse_error_response():
    # 1. getErrorResponse with ERROR element
    xml_get_err = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getErrorResponse</Command>
  <DatabaseManager>
    <ERROR Point="SetTemp" Code="0101" Message="Out of Range" />
  </DatabaseManager>
</Packet>"""
    with pytest.raises(GB50ProtocolError) as exc_info:
        parse_system_info(xml_get_err)
    assert exc_info.value.point == "SetTemp"
    assert exc_info.value.code == "0101"
    assert exc_info.value.error_code == 101

    # 2. setErrorResponse with ERROR element
    xml_set_err = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>setErrorResponse</Command>
  <DatabaseManager>
    <ERROR Point="Drive" Code="0102" Message="Invalid State" />
  </DatabaseManager>
</Packet>"""
    root_set_err = ET.fromstring(xml_set_err)
    with pytest.raises(GB50ProtocolError) as exc_info2:
        check_error_response(root_set_err, raw_xml=xml_set_err)
    assert exc_info2.value.point == "Drive"
    assert exc_info2.value.code == "0102"
    assert exc_info2.value.error_code == 102
    assert "Invalid State" in str(exc_info2.value)

    # 3. setErrorResponse without ERROR element
    xml_set_err_no_elem = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>setErrorResponse</Command>
  <DatabaseManager />
</Packet>"""
    root_set_err_no_elem = ET.fromstring(xml_set_err_no_elem)
    with pytest.raises(GB50ProtocolError) as exc_info3:
        check_error_response(root_set_err_no_elem, raw_xml=xml_set_err_no_elem)
    assert "setErrorResponse" in str(exc_info3.value)

    # 4. Generic ErrorResponse command
    xml_generic_err = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>ErrorResponse</Command>
  <DatabaseManager />
</Packet>"""
    root_generic_err = ET.fromstring(xml_generic_err)
    with pytest.raises(GB50ProtocolError) as exc_info4:
        check_error_response(root_generic_err, raw_xml=xml_generic_err)
    assert "ErrorResponse" in str(exc_info4.value)

    # 5. Normal command but with embedded ERROR element
    xml_embedded_err = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getResponse</Command>
  <DatabaseManager>
    <ERROR Point="Mnet" Code="9999" Message="Communication Loss" />
  </DatabaseManager>
</Packet>"""
    root_embedded_err = ET.fromstring(xml_embedded_err)
    with pytest.raises(GB50ProtocolError) as exc_info5:
        check_error_response(root_embedded_err, raw_xml=xml_embedded_err)
    assert exc_info5.value.point == "Mnet"
    assert exc_info5.value.code == "9999"
    assert exc_info5.value.error_code == 9999



def test_group_crud_and_floor_protocol():
    from gb50.protocol import (
        build_set_group_topology_request,
        build_delete_group_request,
        build_get_floor_mapping_request,
        parse_floor_mapping,
        build_set_floor_mapping_request,
    )

    req_set = build_set_group_topology_request(
        group_id=31,
        name="Youth Room",
        primary_ic=31,
        model="IC",
        slave_ics=[32],
        rcs=[131],
        floor=2,
    )
    assert 'GroupNameWeb="Youth Room"' in req_set
    assert 'Group="31" Model="IC" Address="31"' in req_set
    assert 'Group="31" Model="IC" Address="32"' in req_set
    assert 'Group="31" Model="RC" Address="131"' in req_set
    assert 'FloorGroupRecord Group="31" Floor="2" FloorX="0" FloorY="0"' in req_set

    req_del = build_delete_group_request(31)
    assert 'GroupNameWeb=""' in req_del
    assert 'FloorGroupRecord Group="31" Floor="0" FloorX="0" FloorY="0"' in req_del

    req_flr = build_set_floor_mapping_request(15, 1)
    assert 'FloorGroupRecord Group="15" Floor="1" FloorX="0" FloorY="0"' in req_flr

    xml_floor = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getResponse</Command>
  <DatabaseManager>
    <ControlGroup>
      <FloorGroupList>
        <FloorGroupRecord Group="1" Floor="1" />
        <FloorGroupRecord Group="2" Floor="1" />
        <FloorGroupRecord Group="20" Floor="2" />
      </FloorGroupList>
    </ControlGroup>
  </DatabaseManager>
</Packet>"""
    mapping = parse_floor_mapping(xml_floor)
    assert mapping[1] == 1
    assert mapping[2] == 1
    assert mapping[20] == 2

    # Test full topology request builder
    from gb50.protocol import build_set_all_group_names_request, build_set_full_topology_request
    all_names = {1: "FC1-1", 2: "RM107", 3: "FC1-3"}
    req_names = build_set_all_group_names_request(all_names)
    assert 'Group="1" GroupNameWeb="FC1-1"' in req_names
    assert 'Group="2" GroupNameWeb="RM107"' in req_names
    assert 'Group="3" GroupNameWeb="FC1-3"' in req_names

    top = {
        1: {"name": "FC1-1", "address": 1, "model": "IC", "slaves": []},
        2: {"name": "RM107", "address": 2, "model": "IC", "slaves": [3]},
    }
    req_full_top = build_set_full_topology_request(top, floor_mappings={1: 1, 2: 1})
    assert 'Group="1" GroupNameWeb="FC1-1"' in req_full_top
    assert 'Group="2" GroupNameWeb="RM107"' in req_full_top
    assert 'Group="2" Model="IC" Address="3"' in req_full_top
    assert 'FloorGroupRecord Group="2" Floor="1" FloorX="0" FloorY="0"' in req_full_top

    # Test schedule request builders generate DriveItem, ModeItem, SetTempItem
    from gb50.protocol import build_set_weekly_schedule_request, build_set_today_schedule_request
    events = [
        {"hour": 8, "minute": 30, "drive": "ON", "mode": "COOL", "set_temp_c": 22.0, "fan_speed": "AUTO"},
        {"hour": 17, "minute": 0, "drive": "OFF"},
    ]
    req_weekly = build_set_weekly_schedule_request([1, 2], 1, events)
    assert '<WPatternList Group="1" Season="1" Pattern="1">' in req_weekly
    assert '<WPatternList Group="2" Season="1" Pattern="1">' in req_weekly
    assert 'Drive="ON" Mode="COOL" SetTemp="22.0" AirDirection="AUTO" FanSpeed="AUTO" DriveItem="CHK_ON" ModeItem="CHK_ON" SetTempItem="CHK_ON"' in req_weekly
    assert 'Drive="OFF" Mode="AUTO" SetTemp="0" AirDirection="AUTO" FanSpeed="AUTO" DriveItem="CHK_ON" ModeItem="CHK_ON" SetTempItem="CHK_OFF"' in req_weekly

    req_today = build_set_today_schedule_request([1], events)
    assert '<TodayList Group="1">' in req_today
    assert 'Drive="ON" Mode="COOL" SetTemp="22.0" AirDirection="AUTO" FanSpeed="AUTO" DriveItem="CHK_ON" ModeItem="CHK_ON" SetTempItem="CHK_ON"' in req_today


