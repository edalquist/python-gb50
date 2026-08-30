"""Unit tests for XML protocol building and parsing."""

import pytest
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
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getErrorResponse</Command>
  <DatabaseManager>
    <ERROR Point="SetTemp" Code="0101" Message="Out of Range" />
  </DatabaseManager>
</Packet>"""
    with pytest.raises(GB50ProtocolError) as exc_info:
        parse_system_info(xml)
    assert exc_info.value.point == "SetTemp"
    assert exc_info.value.code == "0101"


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
    assert 'FloorGroupRecord Group="31" Floor="2"' in req_set

    req_del = build_delete_group_request(31)
    assert 'GroupNameWeb=""' in req_del
    assert 'FloorGroupRecord Group="31" Floor="0"' in req_del

    req_flr = build_set_floor_mapping_request(15, 1)
    assert 'FloorGroupRecord Group="15" Floor="1"' in req_flr

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

