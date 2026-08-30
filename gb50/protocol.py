"""XML Protocol serialization, deserialization, and packet builder for Mitsubishi GB-50."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any
from datetime import datetime

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
    SystemInfo,
    ScheduleItem,
    AlarmRecord,
    GroupControlRequest,
)
from .bulk_parser import parse_bulk_telemetry


class GB50ProtocolError(Exception):
    """Protocol-level error returned by the GB-50 controller."""
    def __init__(self, message: str, point: str = "", code: str = "", raw_xml: str = ""):
        detail = f"{message} (Point='{point}', Code='{code}')" if point or code else message
        super().__init__(detail)
        self.point = point
        self.code = code
        self.raw_xml = raw_xml


def wrap_packet(command: str, body_xml: str) -> str:
    """Wrap XML body inside standard GB-50 <Packet> envelope."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\r\n'
        f'<Packet>\r\n'
        f'  <Command>{command}</Command>\r\n'
        f'  <DatabaseManager>\r\n'
        f'{body_xml}'
        f'  </DatabaseManager>\r\n'
        f'</Packet>'
    )


def check_error_response(root: ET.Element, raw_xml: str = "") -> None:
    """Check for error elements in the response and raise GB50ProtocolError if present."""
    cmd = root.find("Command")
    if cmd is not None and "getErrorResponse" in (cmd.text or ""):
        err = root.find(".//ERROR")
        if err is not None:
            point = err.attrib.get("Point", "")
            code = err.attrib.get("Code", "")
            msg = err.attrib.get("Message", "Unknown Controller Error")
            raise GB50ProtocolError(msg, point=point, code=code, raw_xml=raw_xml)
        raise GB50ProtocolError("Controller returned getErrorResponse", raw_xml=raw_xml)


# --- Request Builders ---

def build_get_system_info_request() -> str:
    """Build request for SystemData and FunctionControl tables."""
    body = (
        '    <SystemData Version="*" VersionDb="*" Model="*" TempUnit="*" LocationID="*" '
        'Name="*" Number="*" IPAdrsLan="*" SubnetMaskLan="*" GwLan="*" MacAddress="*" '
        'MnetAdrs="*" KaAdrs="*" Prohibit="*" External="*" TimeMaster="*" UseEc="*" '
        'IPEc1="*" IPEc2="*" IPEc3="*" DateFormat="*" TimeFormat="*" RoomTemp="*" '
        'TrendInterval="*" DecimalPoint="*" CSVSeparator="*" FilterSign="*" ShortName="*" />\r\n'
        '    <FunctionControl>\r\n'
        '      <FunctionList>\r\n'
        '        <FunctionRecord />\r\n'
        '      </FunctionList>\r\n'
        '    </FunctionControl>\r\n'
    )
    return wrap_packet("getRequest", body)


def build_set_system_data_request(settings: Dict[str, Any]) -> str:
    """Build request to update controller SystemData configuration."""
    attrs = []
    field_map = {
        "system_name": "Name",
        "location_id": "LocationID",
        "ip_address": "IPAdrsLan",
        "subnet_mask": "SubnetMaskLan",
        "gateway": "GwLan",
        "mnet_address": "MnetAdrs",
        "temp_unit": "TempUnit",
        "date_format": "DateFormat",
        "time_format": "TimeFormat",
        "room_temp_display": "RoomTemp",
        "filter_sign_display": "FilterSign",
        "short_name_display": "ShortName",
        "time_master": "TimeMaster",
        "use_ec": "UseEc",
        "prohibit_level": "Prohibit",
        "external_input": "External",
    }
    for key, attr_name in field_map.items():
        if key in settings and settings[key] is not None:
            val = str(settings[key]).replace("<", "").replace(">", "").replace("&", "").replace('"', "").replace("'", "")
            attrs.append(f'{attr_name}="{val}"')
    
    if not attrs:
        raise ValueError("No valid SystemData fields provided to update")
    
    body = f'    <SystemData {" ".join(attrs)} />\r\n'
    return wrap_packet("setRequest", body)


def build_get_topology_request() -> str:
    """Build request to discover all configured M-Net groups and web display names."""
    body = (
        '    <ControlGroup>\r\n'
        '      <MnetGroupList><MnetGroupRecord /></MnetGroupList>\r\n'
        '      <MnetList><MnetRecord /></MnetList>\r\n'
        '      <InterlockList><InterlockRecord /></InterlockList>\r\n'
        '    </ControlGroup>\r\n'
    )
    return wrap_packet("getRequest", body)


def build_set_all_group_names_request(names: Dict[int, str]) -> str:
    """Build request to set web display names for all groups (preventing wiping unmentioned groups)."""
    records = []
    for gid in sorted(names.keys()):
        name_val = names[gid]
        if name_val:
            clean_name = str(name_val).replace("<", "").replace(">", "").replace("&", "").replace('"', "").replace("'", "")[:20]
            records.append(f'        <MnetRecord Group="{gid}" GroupNameWeb="{clean_name}" />\r\n')
    body = (
        f'    <ControlGroup>\r\n'
        f'      <MnetList>\r\n'
        f'{"".join(records)}'
        f'      </MnetList>\r\n'
        f'    </ControlGroup>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_set_full_topology_request(
    topology: Dict[int, Dict[str, Any]],
    floor_mappings: Optional[Dict[int, int]] = None,
) -> str:
    """Build request to configure complete M-Net device mapping, names, and floors for all groups."""
    mnet_records = []
    group_records = []
    floor_records = []

    for gid in sorted(topology.keys()):
        info = topology[gid]
        name = info.get("name", f"Group {gid}")
        clean_name = str(name).replace("<", "").replace(">", "").replace("&", "").replace('"', "").replace("'", "")[:20]
        mnet_records.append(f'        <MnetRecord Group="{gid}" GroupNameWeb="{clean_name}" />\r\n')

        model = info.get("model", "IC")
        if hasattr(model, "value"):
            model = model.value
        addr = info.get("address", gid)
        group_records.append(f'        <MnetGroupRecord Group="{gid}" Model="{model}" Address="{addr}" />\r\n')

        for slave in info.get("slaves", info.get("slave_addresses", [])):
            group_records.append(f'        <MnetGroupRecord Group="{gid}" Model="{model}" Address="{slave}" />\r\n')

        for rc in info.get("rcs", []):
            group_records.append(f'        <MnetGroupRecord Group="{gid}" Model="RC" Address="{rc}" />\r\n')

        if floor_mappings and gid in floor_mappings:
            flr = floor_mappings[gid]
            if flr > 0:
                floor_records.append(f'        <FloorGroupRecord Group="{gid}" Floor="{flr}" />\r\n')

    floor_section = ""
    if floor_records:
        floor_section = f'      <FloorGroupList>\r\n{"".join(floor_records)}      </FloorGroupList>\r\n'

    body = (
        f'    <ControlGroup>\r\n'
        f'      <MnetList>\r\n'
        f'{"".join(mnet_records)}'
        f'      </MnetList>\r\n'
        f'      <MnetGroupList>\r\n'
        f'{"".join(group_records)}'
        f'      </MnetGroupList>\r\n'
        f'{floor_section}'
        f'    </ControlGroup>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_set_group_name_request(group_id: int, name: str) -> str:
    """Build request to rename a single HVAC group web display name."""
    clean_name = name.replace("<", "").replace(">", "").replace("&", "").replace('"', "").replace("'", "")[:20]
    body = (
        f'    <ControlGroup>\r\n'
        f'      <MnetList>\r\n'
        f'        <MnetRecord Group="{group_id}" GroupNameWeb="{clean_name}" />\r\n'
        f'      </MnetList>\r\n'
        f'    </ControlGroup>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_set_group_topology_request(
    group_id: int,
    name: str,
    primary_ic: int,
    model: str = "IC",
    slave_ics: Optional[List[int]] = None,
    rcs: Optional[List[int]] = None,
    floor: Optional[int] = None,
) -> str:
    """Build request to configure a group's name, primary address, slaves, remote controllers, and floor."""
    clean_name = name.replace("<", "").replace(">", "").replace("&", "").replace('"', "").replace("'", "")[:20]
    records = []
    # Primary unit
    records.append(f'        <MnetGroupRecord Group="{group_id}" Model="{model}" Address="{primary_ic}" />\r\n')
    # Slaves
    for slave in (slave_ics or []):
        records.append(f'        <MnetGroupRecord Group="{group_id}" Model="{model}" Address="{slave}" />\r\n')
    # Remote controllers (RC)
    for rc in (rcs or []):
        records.append(f'        <MnetGroupRecord Group="{group_id}" Model="RC" Address="{rc}" />\r\n')

    floor_xml = ""
    if floor is not None and floor > 0:
        floor_xml = (
            f'      <FloorGroupList>\r\n'
            f'        <FloorGroupRecord Group="{group_id}" Floor="{floor}" />\r\n'
            f'      </FloorGroupList>\r\n'
        )

    body = (
        f'    <ControlGroup>\r\n'
        f'      <MnetList>\r\n'
        f'        <MnetRecord Group="{group_id}" GroupNameWeb="{clean_name}" />\r\n'
        f'      </MnetList>\r\n'
        f'      <MnetGroupList>\r\n'
        f'{"".join(records)}'
        f'      </MnetGroupList>\r\n'
        f'{floor_xml}'
        f'    </ControlGroup>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_delete_group_request(group_id: int) -> str:
    """Build request to delete an HVAC group and unassign its devices and floor."""
    body = (
        f'    <ControlGroup>\r\n'
        f'      <MnetList>\r\n'
        f'        <MnetRecord Group="{group_id}" GroupNameWeb="" />\r\n'
        f'      </MnetList>\r\n'
        f'      <FloorGroupList>\r\n'
        f'        <FloorGroupRecord Group="{group_id}" Floor="0" />\r\n'
        f'      </FloorGroupList>\r\n'
        f'    </ControlGroup>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_get_floor_mapping_request() -> str:
    """Build request to query floor group mappings."""
    body = (
        '    <ControlGroup>\r\n'
        '      <FloorGroupList><FloorGroupRecord /></FloorGroupList>\r\n'
        '      <FloorList><FloorRecord /></FloorList>\r\n'
        '    </ControlGroup>\r\n'
    )
    return wrap_packet("getRequest", body)


def parse_floor_mapping(xml_str: str) -> Dict[int, int]:
    """Parse FloorGroupRecord mappings into {group_id: floor_number}."""
    root = ET.fromstring(xml_str)
    mapping = {}
    for elem in root.findall(".//FloorGroupRecord"):
        gid_str = elem.get("Group")
        floor_str = elem.get("Floor")
        if gid_str and floor_str:
            try:
                gid = int(gid_str)
                floor = int(floor_str)
                if floor > 0:
                    mapping[gid] = floor
            except ValueError:
                continue
    return mapping


def build_set_floor_mapping_request(group_id: int, floor: int) -> str:
    """Build request to assign an HVAC group to a specific floor."""
    body = (
        f'    <ControlGroup>\r\n'
        f'      <FloorGroupList>\r\n'
        f'        <FloorGroupRecord Group="{group_id}" Floor="{floor}" />\r\n'
        f'      </FloorGroupList>\r\n'
        f'    </ControlGroup>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_get_interlocks_request() -> str:
    """Build request to query LOSSNAY interlocked pairings."""
    body = (
        '    <ControlGroup>\r\n'
        '      <InterlockList><InterlockRecord /></InterlockList>\r\n'
        '    </ControlGroup>\r\n'
    )
    return wrap_packet("getRequest", body)


def build_set_interlocks_batch_request(pairings: List[Dict[str, int]]) -> str:
    """Build request to set multiple LOSSNAY interlock pairings."""
    records = []
    for pair in pairings:
        ic = pair["ic_address"]
        lc = pair["lc_address"]
        records.append(f'        <InterlockRecord Address="{ic}" Model="IC" BaseAddress="{lc}" />\r\n')
    
    body = (
        f'    <ControlGroup>\r\n'
        f'      <InterlockList>\r\n'
        f'{"".join(records)}'
        f'      </InterlockList>\r\n'
        f'    </ControlGroup>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_get_groups_telemetry_request(group_ids: List[int]) -> str:
    """Build high-speed batch telemetry request for specified groups."""
    lines = []
    for gid in group_ids:
        lines.append(
            f'    <Mnet Group="{gid}" Drive="*" Mode="*" SetTemp="*" InletTemp="*" '
            f'AirDirection="*" FanSpeed="*" Schedule="*" FilterSign="*" ErrorSign="*" Bulk="*" />\r\n'
        )
    return wrap_packet("getRequest", "".join(lines))


def build_set_group_request(group_id: int, update: GroupControlRequest) -> str:
    """Build mutation request to control a single HVAC group."""
    attrs = [f'Group="{group_id}"']
    if update.drive is not None:
        attrs.append(f'Drive="{update.drive.value}"')
    if update.mode is not None:
        attrs.append(f'Mode="{update.mode.value}"')
    set_temp = update.resolved_set_temp_c()
    if set_temp is not None:
        attrs.append(f'SetTemp="{set_temp:.1f}"')
    if update.air_direction is not None:
        attrs.append(f'AirDirection="{update.air_direction.value}"')
    if update.fan_speed is not None:
        attrs.append(f'FanSpeed="{update.fan_speed.value}"')
    if update.remote_lock is not None:
        attrs.append(f'RemoCon="{update.remote_lock.value}"')

    body = f'    <Mnet {" ".join(attrs)} />\r\n'
    return wrap_packet("setRequest", body)


def build_set_groups_batch_request(updates: Dict[int, GroupControlRequest]) -> str:
    """Build batch mutation request to control multiple HVAC groups in one HTTP call."""
    lines = []
    for group_id, update in updates.items():
        attrs = [f'Group="{group_id}"']
        if update.drive is not None:
            attrs.append(f'Drive="{update.drive.value}"')
        if update.mode is not None:
            attrs.append(f'Mode="{update.mode.value}"')
        set_temp = update.resolved_set_temp_c()
        if set_temp is not None:
            attrs.append(f'SetTemp="{set_temp:.1f}"')
        if update.air_direction is not None:
            attrs.append(f'AirDirection="{update.air_direction.value}"')
        if update.fan_speed is not None:
            attrs.append(f'FanSpeed="{update.fan_speed.value}"')
        if update.remote_lock is not None:
            attrs.append(f'RemoCon="{update.remote_lock.value}"')
        lines.append(f'    <Mnet {" ".join(attrs)} />\r\n')
    return wrap_packet("setRequest", "".join(lines))


def build_reset_filter_request(group_id: int) -> str:
    """Build request to clear the dirty filter indicator sign."""
    body = f'    <Mnet Group="{group_id}" FilterSign="RESET" />\r\n'
    return wrap_packet("setRequest", body)


def build_get_today_schedule_request(group_id: int) -> str:
    """Build request for active today schedule events for a group."""
    body = (
        f'    <ScheduleControl>\r\n'
        f'      <TodayList Group="{group_id}">\r\n'
        f'        <TodayRecord />\r\n'
        f'      </TodayList>\r\n'
        f'    </ScheduleControl>\r\n'
    )
    return wrap_packet("getRequest", body)


def build_get_all_schedules_request(group_ids: List[int]) -> str:
    """Build batch request to query today schedules for all specified groups simultaneously."""
    lines = [f'      <TodayList Group="{gid}"><TodayRecord /></TodayList>\r\n' for gid in group_ids]
    body = f'    <ScheduleControl>\r\n{"".join(lines)}    </ScheduleControl>\r\n'
    return wrap_packet("getRequest", body)


def build_get_weekly_schedule_request(group_id: int, season: int = 1) -> str:
    """Build request to query full 7-day weekly schedule patterns for a group."""
    lines = [f'      <WPatternList Group="{group_id}" Season="{season}" Pattern="{p}"><WPatternRecord /></WPatternList>\r\n' for p in range(1, 8)]
    body = f'    <ScheduleControl>\r\n{"".join(lines)}    </ScheduleControl>\r\n'
    return wrap_packet("getRequest", body)


def build_set_today_schedule_request(group_ids: List[int], events: List[Dict[str, Any]]) -> str:
    """Build request to update today's schedule for one or more groups."""
    lists = []
    for gid in group_ids:
        rec_lines = []
        for idx, ev in enumerate(events, 1):
            hr = ev.get("hour", 0)
            mn = ev.get("minute", 0)
            drive = ev.get("drive", "ON")
            mode = ev.get("mode", "AUTO")
            set_temp = ev.get("set_temp_c")
            st_attr = f'SetTemp="{set_temp:.1f}" ' if set_temp is not None else ''
            fan = ev.get("fan_speed", "AUTO")
            fan_attr = f'FanSpeed="{fan}" ' if fan else ''
            air_dir = ev.get("air_direction", "")
            air_attr = f'AirDirection="{air_dir}" ' if air_dir else ''
            rec_lines.append(
                f'        <TodayRecord Index="{idx}" Hour="{hr}" Minute="{mn}" Drive="{drive}" Mode="{mode}" {st_attr}{fan_attr}{air_attr}/>\r\n'
            )
        lists.append(
            f'      <TodayList Group="{gid}">\r\n{"".join(rec_lines)}      </TodayList>\r\n'
        )
    body = f'    <ScheduleControl>\r\n{"".join(lists)}    </ScheduleControl>\r\n'
    return wrap_packet("setRequest", body)


def build_set_weekly_schedule_request(group_ids: List[int], day_of_week: int, events: List[Dict[str, Any]], season: int = 1) -> str:
    """Build request to update weekly schedule pattern (day 1..7) for one or more groups."""
    lists = []
    for gid in group_ids:
        rec_lines = []
        for idx, ev in enumerate(events, 1):
            hr = ev.get("hour", 0)
            mn = ev.get("minute", 0)
            drive = ev.get("drive", "ON")
            mode = ev.get("mode", "AUTO")
            set_temp = ev.get("set_temp_c")
            st_attr = f'SetTemp="{set_temp:.1f}" ' if set_temp is not None else ''
            fan = ev.get("fan_speed", "AUTO")
            fan_attr = f'FanSpeed="{fan}" ' if fan else ''
            air_dir = ev.get("air_direction", "")
            air_attr = f'AirDirection="{air_dir}" ' if air_dir else ''
            rec_lines.append(
                f'        <WPatternRecord Index="{idx}" Hour="{hr}" Minute="{mn}" Drive="{drive}" Mode="{mode}" {st_attr}{fan_attr}{air_attr}/>\r\n'
            )
        lists.append(
            f'      <WPatternList Group="{gid}" Season="{season}" Pattern="{day_of_week}">\r\n{"".join(rec_lines)}      </WPatternList>\r\n'
        )
    body = f'    <ScheduleControl>\r\n{"".join(lists)}    </ScheduleControl>\r\n'
    return wrap_packet("setRequest", body)


def build_get_alarms_request(priority_level: int = 2) -> str:
    """Build request to retrieve active unit alarms or historical log."""
    body = (
        '    <Alarm>\r\n'
        f'      <AlarmList PriorityLevel="{priority_level}"><AlarmRecord /></AlarmList>\r\n'
        '    </Alarm>\r\n'
    )
    return wrap_packet("getRequest", body)


def build_delete_alarm_history_request(priority_level: int = 2) -> str:
    """Build request to clear historical alarms from controller memory."""
    body = (
        '    <Alarm>\r\n'
        f'      <AlarmList PriorityLevel="{priority_level}" Operation="DELETE" />\r\n'
        '    </Alarm>\r\n'
    )
    return wrap_packet("setRequest", body)


def build_get_datetime_request() -> str:
    """Build request for controller real-time clock."""
    body = '    <Clock Year="*" Month="*" Day="*" Hour="*" Minute="*" Second="*" />\r\n'
    return wrap_packet("getRequest", body)


def build_set_datetime_request(dt: datetime) -> str:
    """Build request to set the controller real-time clock."""
    body = (
        f'    <Clock Year="{dt.year}" Month="{dt.month}" Day="{dt.day}" '
        f'Hour="{dt.hour}" Minute="{dt.minute}" Second="{dt.second}" />\r\n'
    )
    return wrap_packet("setRequest", body)


def build_get_summertime_request() -> str:
    """Build request to query Daylight Saving Time / Summer Time configuration."""
    body = (
        '    <SummerTime CountryCode="*" Month1="*" Day1="*" Hour1="*" Minute1="*" ShiftMin1="*" '
        'Month2="*" Day2="*" Hour2="*" Minute2="*" ShiftMin2="*" />\r\n'
    )
    return wrap_packet("getRequest", body)


def build_set_summertime_request(config: Dict[str, Any]) -> str:
    """Build request to update Summer Time configuration."""
    cc = config.get("country_code", "US0")
    m1 = config.get("month1", "3")
    d1 = config.get("day1", "8")
    h1 = config.get("hour1", "2")
    mn1 = config.get("minute1", "0")
    sm1 = config.get("shift_min1", "60")
    m2 = config.get("month2", "11")
    d2 = config.get("day2", "1")
    h2 = config.get("hour2", "2")
    mn2 = config.get("minute2", "0")
    sm2 = config.get("shift_min2", "-60")
    body = (
        f'    <SummerTime CountryCode="{cc}" Month1="{m1}" Day1="{d1}" Hour1="{h1}" Minute1="{mn1}" ShiftMin1="{sm1}" '
        f'Month2="{m2}" Day2="{d2}" Hour2="{h2}" Minute2="{mn2}" ShiftMin2="{sm2}" />\r\n'
    )
    return wrap_packet("setRequest", body)


def build_get_setback_request() -> str:
    """Build request to query Night Setback start/end times."""
    body = '    <SetbackControl SetbackFunc="*" StartHour="*" StartMinute="*" EndHour="*" EndMinute="*" />\r\n'
    return wrap_packet("getRequest", body)


def build_get_setback_list_request() -> str:
    """Build request to query Night Setback per-group drift boundaries."""
    body = (
        '    <SetbackControl>\r\n'
        '      <SetbackControlList>\r\n'
        '        <SetbackControlRecord />\r\n'
        '      </SetbackControlList>\r\n'
        '    </SetbackControl>\r\n'
    )
    return wrap_packet("getRequest", body)


def build_set_setback_request(
    enabled: bool,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    group_records: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build request to update Night Setback configuration."""
    func_str = "USE" if enabled else "NOT_USE"
    body = (
        f'    <SetbackControl SetbackFunc="{func_str}" StartHour="{start_hour}" StartMinute="{start_minute}" '
        f'EndHour="{end_hour}" EndMinute="{end_minute}" />\r\n'
    )
    return wrap_packet("setRequest", body)


def build_register_option_request(func_index: int, func_id: str, key_code: str) -> str:
    """Build request to register a 16-char software license key."""
    body = (
        f'    <FunctionControl>\r\n'
        f'      <FunctionList>\r\n'
        f'        <FunctionRecord FunctionIndex="{func_index}" FunctionID="{func_id}" Status="ENABLE" KeyCode="{key_code}" Reply="*" Reason="*" />\r\n'
        f'      </FunctionList>\r\n'
        f'    </FunctionControl>\r\n'
    )
    return wrap_packet("setRequest", body)


# --- Response Parsers ---

def parse_system_info(xml_str: str) -> SystemInfo:
    """Parse SystemData and FunctionControl response into SystemInfo model."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    
    sd = root.find(".//SystemData")
    if sd is None:
        raise GB50ProtocolError("Missing SystemData element in response", raw_xml=xml_str)
    
    licenses: Dict[str, bool] = {}
    func_names = [
        None, "WebBrowse", "Schedule", "Account", "BeforeMal", "SendMail",
        "SaveEnergy", "MainteTool", "UserWeb", "Bacnet", "MainteFull",
        "PeakCut", "PLCIo", "GB50LP", "OCMeasure", "AG150ALP", "EnergyLP",
        "GB24LP", "Interlock", "TG2000", "GB50ADALP", "Ecocute", "ETShiftLP",
        "ETShift", "ETAccount"
    ]
    for frec in root.iter("FunctionRecord"):
        idx_str = frec.attrib.get("Index")
        status = frec.attrib.get("Status")
        if idx_str and idx_str.isdigit():
            idx = int(idx_str)
            name = func_names[idx] if 1 <= idx < len(func_names) else f"Func_{idx}"
            licenses[name] = (status == "ENABLE")

    return SystemInfo(
        version=sd.attrib.get("Version", ""),
        model=sd.attrib.get("Model", "GB-50ADA-A"),
        serial_number=sd.attrib.get("Number", ""),
        system_name=sd.attrib.get("Name", ""),
        location_id=sd.attrib.get("LocationID", ""),
        ip_address=sd.attrib.get("IPAdrsLan", ""),
        subnet_mask=sd.attrib.get("SubnetMaskLan", ""),
        gateway=sd.attrib.get("GwLan", ""),
        mac_address=sd.attrib.get("MacAddress", ""),
        mnet_address=int(sd.attrib.get("MnetAdrs", "0") or "0"),
        temp_unit=sd.attrib.get("TempUnit", "F"),
        date_format=sd.attrib.get("DateFormat", "MMDDYYYY"),
        time_format=sd.attrib.get("TimeFormat", "12"),
        licensed_functions=licenses,
    )


def parse_topology(xml_str: str) -> Dict[int, Dict[str, Any]]:
    """Parse ControlGroup response into group metadata map: group_id -> {name, model, address, slaves, rcs, interlocks}."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    
    topology: Dict[int, Dict[str, Any]] = {}
    
    # 1. Names
    for mrec in root.iter("MnetRecord"):
        gid_str = mrec.attrib.get("Group")
        if gid_str and gid_str.isdigit():
            gid = int(gid_str)
            name = mrec.attrib.get("GroupNameWeb") or f"Group {gid}"
            topology.setdefault(gid, {})["name"] = name

    # 2. Addresses, Models, RCs
    for grec in root.iter("MnetGroupRecord"):
        gid_str = grec.attrib.get("Group")
        if gid_str and gid_str.isdigit():
            gid = int(gid_str)
            model_str = grec.attrib.get("Model", "IC")
            addr_str = grec.attrib.get("Address", "0")
            addr = int(addr_str) if addr_str.isdigit() else 0
            entry = topology.setdefault(gid, {})
            
            if model_str == "RC":
                entry.setdefault("rcs", []).append(addr)
            elif model_str == "SC":
                entry.setdefault("scs", []).append(addr)
            else:
                model = ModelType(model_str) if model_str in ModelType.__members__ else ModelType.IC
                if "address" not in entry:
                    entry["address"] = addr
                    entry["model"] = model
                    entry["slaves"] = []
                    entry["rcs"] = []
                else:
                    entry.setdefault("slaves", []).append(addr)

    # 3. Interlocked Ventilation pairings
    for irec in root.iter("InterlockRecord"):
        ic_str = irec.attrib.get("Address") or irec.attrib.get("IcAddress")
        lc_str = irec.attrib.get("BaseAddress") or irec.attrib.get("LcAddress")
        if ic_str and lc_str and ic_str.isdigit() and lc_str.isdigit():
            ic_addr = int(ic_str)
            lc_addr = int(lc_str)
            for gid, meta in topology.items():
                if meta.get("address") == ic_addr:
                    meta["interlocked_lossnay_address"] = lc_addr

    return topology


def parse_interlocks_list(xml_str: str) -> List[Dict[str, int]]:
    """Parse InterlockList response into list of IC -> LC pairings."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    pairings: List[Dict[str, int]] = []
    for irec in root.iter("InterlockRecord"):
        ic_str = irec.attrib.get("Address") or irec.attrib.get("IcAddress")
        lc_str = irec.attrib.get("BaseAddress") or irec.attrib.get("LcAddress")
        if ic_str and lc_str and ic_str.isdigit() and lc_str.isdigit():
            pairings.append({
                "ic_address": int(ic_str),
                "lc_address": int(lc_str),
            })
    return pairings


def parse_summertime(xml_str: str) -> Dict[str, Any]:
    """Parse SummerTime response into config dict."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    st = root.find(".//SummerTime")
    if st is None:
        return {}
    return {
        "country_code": st.attrib.get("CountryCode", "US0"),
        "month1": st.attrib.get("Month1", "3"),
        "day1": st.attrib.get("Day1", "8"),
        "hour1": st.attrib.get("Hour1", "2"),
        "minute1": st.attrib.get("Minute1", "0"),
        "shift_min1": st.attrib.get("ShiftMin1", "60"),
        "month2": st.attrib.get("Month2", "11"),
        "day2": st.attrib.get("Day2", "1"),
        "hour2": st.attrib.get("Hour2", "2"),
        "minute2": st.attrib.get("Minute2", "0"),
        "shift_min2": st.attrib.get("ShiftMin2", "-60"),
    }


def parse_setback(xml_str: str) -> Dict[str, Any]:
    """Parse SetbackControl response into config dict."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    sc = root.find(".//SetbackControl")
    enabled = sc.attrib.get("SetbackFunc") == "USE" if sc is not None else False
    start_hour = int(sc.attrib.get("StartHour", "22") or "22") if sc is not None else 22
    start_minute = int(sc.attrib.get("StartMinute", "0") or "0") if sc is not None else 0
    end_hour = int(sc.attrib.get("EndHour", "6") or "6") if sc is not None else 6
    end_minute = int(sc.attrib.get("EndMinute", "0") or "0") if sc is not None else 0

    return {
        "enabled": enabled,
        "start_hour": start_hour,
        "start_minute": start_minute,
        "end_hour": end_hour,
        "end_minute": end_minute,
    }


def parse_groups_telemetry(xml_str: str, topology: Optional[Dict[int, Dict[str, Any]]] = None) -> List[GroupStatus]:
    """Parse Mnet telemetry elements into GroupStatus list."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    
    groups: List[GroupStatus] = []
    top_map = topology or {}
    
    for mnet in root.iter("Mnet"):
        gid_str = mnet.attrib.get("Group")
        if not gid_str or not gid_str.isdigit():
            continue
        gid = int(gid_str)
        meta = top_map.get(gid, {})
        name = meta.get("name", f"Group {gid}")
        address = meta.get("address", gid)
        slaves = meta.get("slaves", [])
        
        bulk_hex = mnet.attrib.get("Bulk")
        if bulk_hex and len(bulk_hex) >= 130:
            parsed = parse_bulk_telemetry(bulk_hex)
            status = GroupStatus(
                group_id=gid,
                name=name,
                model=parsed["model"],
                address=address,
                slave_addresses=slaves,
                drive=parsed["drive"],
                mode=parsed["mode"],
                set_temp_c=parsed["set_temp_c"],
                inlet_temp_c=parsed["inlet_temp_c"],
                air_direction=parsed["air_direction"],
                fan_speed=parsed["fan_speed"],
                schedule_enabled=parsed["schedule_enabled"],
                filter_dirty=parsed["filter_dirty"],
                error_active=parsed["error_active"],
                remote_lock=parsed["remote_lock"],
                capabilities=parsed["capabilities"],
                raw_bulk=bulk_hex,
            )
        else:
            drive = DriveState(mnet.attrib.get("Drive", "OFF"))
            mode = OperationMode(mnet.attrib.get("Mode", "AUTO"))
            st_str = mnet.attrib.get("SetTemp")
            set_temp = float(st_str) if st_str and st_str.replace(".", "", 1).isdigit() else None
            it_str = mnet.attrib.get("InletTemp")
            inlet_temp = float(it_str) if it_str and it_str.replace(".", "", 1).isdigit() else None
            air_dir = AirDirection(mnet.attrib.get("AirDirection", "HORIZONTAL"))
            fan_spd = FanSpeed(mnet.attrib.get("FanSpeed", "AUTO"))
            sched = mnet.attrib.get("Schedule") == "ON"
            filter_d = mnet.attrib.get("FilterSign") == "ON"
            err_act = mnet.attrib.get("ErrorSign") == "ON"
            model = meta.get("model", ModelType.IC)

            status = GroupStatus(
                group_id=gid,
                name=name,
                model=model,
                address=address,
                slave_addresses=slaves,
                drive=drive,
                mode=mode,
                set_temp_c=set_temp,
                inlet_temp_c=inlet_temp,
                air_direction=air_dir,
                fan_speed=fan_spd,
                schedule_enabled=sched,
                filter_dirty=filter_d,
                error_active=err_act,
                raw_bulk=None,
            )
        groups.append(status)
    
    return groups


def parse_today_schedule(xml_str: str) -> List[ScheduleItem]:
    """Parse TodayList response into list of ScheduleItem events."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    
    items: List[ScheduleItem] = []
    for rec in root.iter("TodayRecord"):
        idx_str = rec.attrib.get("Index")
        hr_str = rec.attrib.get("Hour")
        min_str = rec.attrib.get("Minute")
        if not idx_str or not hr_str or not min_str:
            continue
        
        drive_str = rec.attrib.get("Drive")
        drive = DriveState(drive_str) if drive_str in DriveState.__members__ else None
        
        mode_str = rec.attrib.get("Mode")
        mode = OperationMode(mode_str) if mode_str in OperationMode.__members__ else None
        
        st_str = rec.attrib.get("SetTemp")
        set_temp = float(st_str) if st_str and st_str.replace(".", "", 1).isdigit() else None
        
        ad_str = rec.attrib.get("AirDirection")
        air_dir = AirDirection(ad_str) if ad_str in AirDirection.__members__ else None
        
        fs_str = rec.attrib.get("FanSpeed")
        fan_spd = FanSpeed(fs_str) if fs_str in FanSpeed.__members__ else None

        items.append(
            ScheduleItem(
                index=int(idx_str),
                hour=int(hr_str),
                minute=int(min_str),
                drive=drive,
                mode=mode,
                set_temp_c=set_temp,
                air_direction=air_dir,
                fan_speed=fan_spd,
            )
        )
    return sorted(items, key=lambda x: (x.hour, x.minute))


def parse_all_today_schedules(xml_str: str) -> Dict[int, List[ScheduleItem]]:
    """Parse batch TodayList response into map of group_id -> List[ScheduleItem]."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    schedules: Dict[int, List[ScheduleItem]] = {}
    for tlist in root.iter("TodayList"):
        gid_str = tlist.attrib.get("Group")
        if not gid_str or not gid_str.isdigit():
            continue
        gid = int(gid_str)
        items: List[ScheduleItem] = []
        for rec in tlist.iter("TodayRecord"):
            idx_str = rec.attrib.get("Index")
            hr_str = rec.attrib.get("Hour")
            min_str = rec.attrib.get("Minute")
            if not idx_str or not hr_str or not min_str:
                continue
            drive_str = rec.attrib.get("Drive")
            drive = DriveState(drive_str) if drive_str in DriveState.__members__ else None
            mode_str = rec.attrib.get("Mode")
            mode = OperationMode(mode_str) if mode_str in OperationMode.__members__ else None
            st_str = rec.attrib.get("SetTemp")
            set_temp = float(st_str) if st_str and st_str.replace(".", "", 1).isdigit() else None
            ad_str = rec.attrib.get("AirDirection")
            air_dir = AirDirection(ad_str) if ad_str in AirDirection.__members__ else None
            fs_str = rec.attrib.get("FanSpeed")
            fan_spd = FanSpeed(fs_str) if fs_str in FanSpeed.__members__ else None

            items.append(
                ScheduleItem(
                    index=int(idx_str),
                    hour=int(hr_str),
                    minute=int(min_str),
                    drive=drive,
                    mode=mode,
                    set_temp_c=set_temp,
                    air_direction=air_dir,
                    fan_speed=fan_spd,
                )
            )
        schedules[gid] = sorted(items, key=lambda x: (x.hour, x.minute))
    return schedules


def parse_weekly_schedule(xml_str: str) -> Dict[int, List[ScheduleItem]]:
    """Parse WPatternList response into map of pattern_id (1=Mon..7=Sun) -> List[ScheduleItem]."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    patterns: Dict[int, List[ScheduleItem]] = {p: [] for p in range(1, 8)}
    for wlist in root.iter("WPatternList"):
        pat_str = wlist.attrib.get("Pattern")
        if not pat_str or not pat_str.isdigit():
            continue
        pat = int(pat_str)
        items: List[ScheduleItem] = []
        for rec in wlist.iter("WPatternRecord"):
            idx_str = rec.attrib.get("Index")
            hr_str = rec.attrib.get("Hour")
            min_str = rec.attrib.get("Minute")
            if not idx_str or not hr_str or not min_str:
                continue
            drive_str = rec.attrib.get("Drive")
            drive = DriveState(drive_str) if drive_str in DriveState.__members__ else None
            mode_str = rec.attrib.get("Mode")
            mode = OperationMode(mode_str) if mode_str in OperationMode.__members__ else None
            st_str = rec.attrib.get("SetTemp")
            set_temp = float(st_str) if st_str and st_str.replace(".", "", 1).isdigit() else None
            ad_str = rec.attrib.get("AirDirection")
            air_dir = AirDirection(ad_str) if ad_str in AirDirection.__members__ else None
            fs_str = rec.attrib.get("FanSpeed")
            fan_spd = FanSpeed(fs_str) if fs_str in FanSpeed.__members__ else None

            items.append(
                ScheduleItem(
                    index=int(idx_str),
                    hour=int(hr_str),
                    minute=int(min_str),
                    drive=drive,
                    mode=mode,
                    set_temp_c=set_temp,
                    air_direction=air_dir,
                    fan_speed=fan_spd,
                )
            )
        patterns[pat] = sorted(items, key=lambda x: (x.hour, x.minute))
    return patterns


MITSUBISHI_ERROR_INFO: Dict[str, Dict[str, str]] = {
    "0900": {
        "title": "Test Run Mode Active",
        "category": "Commissioning & Service",
        "description": "The unit was placed into manual test run mode for diagnostic verification.",
        "troubleshooting": "Normal status during system commissioning or routine maintenance test runs.",
    },
    "1102": {
        "title": "Discharge Temperature Overheat (TH4)",
        "category": "Refrigerant & Compressor",
        "description": "Compressor discharge pipe temperature exceeded safe operating limit (>115°C / 239°F).",
        "troubleshooting": "Check system refrigerant charge for leaks, verify electronic expansion valve (LEV) operation, and inspect discharge thermistor (TH4).",
    },
    "1301": {
        "title": "Low Pressure Fault (63L Trip)",
        "category": "Refrigerant & Pressure",
        "description": "Suction pressure dropped below safety threshold (vacuum or severe refrigerant undercharge).",
        "troubleshooting": "Inspect line set and flare connections for refrigerant leaks, check filter driers, and verify low pressure sensor (63LS).",
    },
    "1302": {
        "title": "High Pressure Switch Tripped (63H)",
        "category": "Refrigerant & Pressure",
        "description": "High pressure switch (63H) opened due to excessive condenser head pressure (>4.15 MPa / 601 PSI).",
        "troubleshooting": "Check for dirty outdoor condenser coils, failed outdoor condenser fan motor, closed service valves, or refrigerant overcharge.",
    },
    "1501": {
        "title": "Water Freeze Protection Tripped",
        "category": "Water & Hydro System",
        "description": "Plate heat exchanger water temperature dropped near freezing during cooling mode.",
        "troubleshooting": "Verify water flow rate, check circulating pump operation, clean water strainers, and test water temperature sensors.",
    },
    "2500": {
        "title": "Water Leakage Detected",
        "category": "Drain & Condensate",
        "description": "External water leak detector sensor triggered.",
        "troubleshooting": "Inspect indoor unit drain pan, condensate piping joints, and surrounding ceiling tiles.",
    },
    "2502": {
        "title": "Drain Pump / Float Switch Fault",
        "category": "Drain & Condensate",
        "description": "Condensate water level in indoor unit drain pan exceeded limit, opening the float safety switch.",
        "troubleshooting": "Inspect drain pump power and impeller, clear algae/sludge from condensate drain pipe, and verify float switch movement.",
    },
    "2503": {
        "title": "Drain Sensor (Float Switch) Disconnected",
        "category": "Drain & Condensate",
        "description": "Indoor unit drain pan float switch circuit is open or disconnected.",
        "troubleshooting": "Check wiring harness connection to CN4F / CN31 on indoor unit control board.",
    },
    "3602": {
        "title": "Refrigerant Cycle / Undercharge Warning",
        "category": "Refrigerant & Cycle",
        "description": "Abnormal subcooling / superheat cycle conditions detected during heating or cooling operation.",
        "troubleshooting": "Check for partial refrigerant undercharge, inspect LEV valve metering, and verify indoor/outdoor heat exchanger thermistors.",
    },
    "4100": {
        "title": "Compressor Overcurrent Trip",
        "category": "Power & Inverter",
        "description": "Inverter output instantaneous overcurrent protection activated on the compressor.",
        "troubleshooting": "Check compressor winding resistance and insulation resistance to ground; inspect inverter power module (IPM).",
    },
    "4102": {
        "title": "Open Phase / Power Phase Loss",
        "category": "Power & Electrical",
        "description": "3-phase power supply to outdoor unit has a missing, disconnected, or reversed phase (L1, L2, or L3).",
        "troubleshooting": "Check building electrical panel circuit breakers, test 3-phase line-to-line AC voltages (460V / 208V), and verify line fuses.",
    },
    "4115": {
        "title": "Power Supply Frequency Sync Error",
        "category": "Power & Electrical",
        "description": "Outdoor control board failed to synchronize with AC mains line frequency (50/60 Hz).",
        "troubleshooting": "Check electrical grid power quality, voltage waveform distortion, and outdoor power board noise filter.",
    },
    "4200": {
        "title": "Inverter DC Bus Voltage Error",
        "category": "Power & Inverter",
        "description": "Inverter DC bus voltage is abnormally high (overvoltage) or low (undervoltage).",
        "troubleshooting": "Measure incoming AC line voltage under load, inspect pre-charge resistor and smoothing electrolytic capacitors.",
    },
    "4220": {
        "title": "Inverter Bus Voltage Drop / IPM Error",
        "category": "Power & Inverter",
        "description": "Inverter DC link voltage dropped during compressor acceleration, or Intelligent Power Module (IPM) signaled a fault.",
        "troubleshooting": "Inspect inverter power module (IPM) heatsink thermal paste, check DC bus capacitor bank, and test input supply stability.",
    },
    "4230": {
        "title": "Inverter Radiator Fin Overheat (THHS)",
        "category": "Power & Inverter",
        "description": "Inverter heatsink temperature exceeded safe limits (>105°C / 221°F).",
        "troubleshooting": "Inspect outdoor electrical box cooling fan, clean heatsink cooling fins, and verify heatsink thermistor (THHS).",
    },
    "4250": {
        "title": "Inverter Output Short Circuit / IPM Trip",
        "category": "Power & Inverter",
        "description": "Inverter power module detected an instantaneous short circuit or grounding on compressor U/V/W terminals.",
        "troubleshooting": "Disconnect compressor leads and measure resistance phase-to-phase and phase-to-ground (megger test). Inspect IPM module.",
    },
    "5101": {
        "title": "Inlet Air Temperature Thermistor (TH21) Fault",
        "category": "Sensors & Thermistors",
        "description": "Indoor unit return / room intake air temperature thermistor (TH21) is open circuit or shorted.",
        "troubleshooting": "Inspect sensor plug on indoor unit PCB; test thermistor resistance (~15 kΩ at 25°C / 77°F). Replace sensor if out of spec.",
    },
    "5102": {
        "title": "Liquid Pipe Temperature Thermistor (TH22) Fault",
        "category": "Sensors & Thermistors",
        "description": "Indoor coil liquid refrigerant pipe temperature thermistor (TH22) open or short circuit.",
        "troubleshooting": "Check sensor lead on indoor coil, measure resistance (~15 kΩ at 25°C / 77°F), and replace sensor if damaged.",
    },
    "5103": {
        "title": "Gas Pipe Temperature Thermistor (TH23) Fault",
        "category": "Sensors & Thermistors",
        "description": "Indoor coil gas refrigerant pipe temperature thermistor (TH23) open or short circuit.",
        "troubleshooting": "Check sensor harness and probe clip on indoor suction/gas pipe; test resistance (~15 kΩ at 25°C / 77°F).",
    },
    "5106": {
        "title": "Outdoor Heat Exchanger Thermistor (TH3) Fault",
        "category": "Sensors & Thermistors",
        "description": "Outdoor condenser heat exchanger coil / defrost thermistor (TH3) is open circuit or shorted.",
        "troubleshooting": "Inspect outdoor coil thermistor clip, wiring harness, and measure sensor resistance (~15 kΩ at 25°C / 77°F).",
    },
    "5107": {
        "title": "Outdoor Ambient Temperature Thermistor (TH7) Fault",
        "category": "Sensors & Thermistors",
        "description": "Outdoor ambient air temperature thermistor (TH7) open or short circuit.",
        "troubleshooting": "Check outdoor ambient sensor located on rear of outdoor unit chassis.",
    },
    "5201": {
        "title": "High Pressure Sensor (63HS) Fault",
        "category": "Sensors & Thermistors",
        "description": "Analog high pressure transducer (63HS) voltage output is out of valid range (<0.1V or >4.9V).",
        "troubleshooting": "Verify 5V DC supply to sensor on outdoor control board; test pressure transducer output signal.",
    },
    "5202": {
        "title": "Low Pressure Sensor (63LS) Fault",
        "category": "Sensors & Thermistors",
        "description": "Analog low pressure transducer (63LS) voltage output is out of valid range (<0.1V or >4.9V).",
        "troubleshooting": "Verify 5V DC supply to sensor; test pressure transducer output voltage against manifold gauge reading.",
    },
    "6600": {
        "title": "Duplicate M-Net Hardware Address Conflict",
        "category": "M-Net Communication",
        "description": "Two or more units on the M-Net transmission bus are configured with the identical rotary dial address.",
        "troubleshooting": "Inspect rotary address switches on indoor units, outdoor units, and LOSSNAY controllers to ensure each address is unique.",
    },
    "6601": {
        "title": "M-Net Transmission Line Polarity / Open Circuit",
        "category": "M-Net Communication",
        "description": "M-Net transmission bus signal line voltage is missing or has an open circuit.",
        "troubleshooting": "Check 2-wire shielded transmission cable continuity between central controller (GB-50) and outdoor/indoor units.",
    },
    "6602": {
        "title": "M-Net Transmission Bus Collision / Busy Timeout",
        "category": "M-Net Communication",
        "description": "Central controller transmission collision or bus busy timeout due to electromagnetic noise or corrupted packets.",
        "troubleshooting": "Verify shield grounding on M-Net line (single-point ground at outdoor unit); inspect cable for damage or AC power induction.",
    },
    "6603": {
        "title": "M-Net Transmission ACK Timeout",
        "category": "M-Net Communication",
        "description": "Central controller transmitted a command to an M-Net address, but no acknowledgment was received.",
        "troubleshooting": "Verify target unit is powered on, inspect wiring terminals at TB7 / TB3, and test M-Net bus DC voltage (24-30V DC).",
    },
    "6606": {
        "title": "M-Net Transmission Processor Communication Error",
        "category": "M-Net Communication",
        "description": "Internal communication failure between central controller main CPU and M-Net transmission IC.",
        "troubleshooting": "Power cycle the GB-50 controller; inspect internal hardware connections.",
    },
    "6607": {
        "title": "No ACK from Outdoor Master Unit",
        "category": "M-Net Communication",
        "description": "Central controller lost communication with the outdoor condenser master unit.",
        "troubleshooting": "Check if outdoor unit circuit breaker is tripped, verify TB7 transmission terminals, and check outdoor main control board LED status.",
    },
    "6608": {
        "title": "No ACK from Indoor Unit / Device Offline",
        "category": "M-Net Communication",
        "description": "Indoor fan coil unit or LOSSNAY stopped responding to central controller polling.",
        "troubleshooting": "Check local disconnect switch and power to the indoor unit; inspect M-Net communication wiring at unit terminal block.",
    },
    "7100": {
        "title": "Indoor Unit Connected Capacity Exceeded",
        "category": "System Configuration",
        "description": "Total indoor unit horsepower capacity index exceeds 130% of the outdoor condensing unit rating.",
        "troubleshooting": "Review indoor unit capacity dip switch settings and ensure total connected load matches outdoor unit engineering limits.",
    },
    "7101": {
        "title": "Indoor Unit Capacity Setting Error",
        "category": "System Configuration",
        "description": "Indoor unit capacity dip switch setting (SW2) is invalid or unconfigured.",
        "troubleshooting": "Set model capacity code on indoor unit dip switches according to unit service manual table.",
    },
    "7102": {
        "title": "Connected Indoor Units Exceed Maximum Count",
        "category": "System Configuration",
        "description": "Number of connected indoor units exceeds maximum supported count for this refrigerant circuit.",
        "troubleshooting": "Verify total number of indoor units and branch controllers on this M-Net system.",
    },
    "7105": {
        "title": "M-Net Hardware Address Setting Error",
        "category": "System Configuration",
        "description": "Unit address switch is set out of allowable range (valid indoor range: 1 to 50).",
        "troubleshooting": "Adjust rotary address switches (tens and units) to a valid address between 01 and 50.",
    },
    "7110": {
        "title": "LOSSNAY Interlock Configuration Error",
        "category": "System Configuration",
        "description": "Interlocked indoor unit address or LOSSNAY address is non-existent or misconfigured.",
        "troubleshooting": "Verify LOSSNAY ventilation interlock pairings in Admin & Diagnostics settings.",
    },
}


def _parse_alarm_timestamp(rec: ET.Element, prefix: str = "") -> Optional[datetime]:
    """Parse Year..Second or RecovYear..RecovSecond into a datetime object."""
    y_str = rec.attrib.get(f"{prefix}Year")
    m_str = rec.attrib.get(f"{prefix}Month")
    d_str = rec.attrib.get(f"{prefix}Day")
    h_str = rec.attrib.get(f"{prefix}Hour")
    mn_str = rec.attrib.get(f"{prefix}Minute")
    s_str = rec.attrib.get(f"{prefix}Second")
    if not y_str or not m_str or not d_str or y_str in ("0", ""):
        return None
    try:
        y, m, d = int(y_str), int(m_str), int(d_str)
        h = int(h_str) if h_str and h_str.isdigit() else 0
        mn = int(mn_str) if mn_str and mn_str.isdigit() else 0
        s = int(s_str) if s_str and s_str.isdigit() else 0
        if y < 1990 or m < 1 or m > 12 or d < 1 or d > 31:
            return None
        return datetime(y, m, d, h, mn, s)
    except Exception:
        return None


def parse_alarms(
    xml_str: str,
    topology: Optional[Dict[int, Dict[str, Any]]] = None,
) -> List[AlarmRecord]:
    """Parse AlarmList response into rich AlarmRecord objects with timestamps and Mitsubishi diagnostics."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    
    top_map = topology or {}
    addr_to_name: Dict[int, str] = {}
    for gid, meta in top_map.items():
        addr = meta.get("address", gid)
        addr_to_name[addr] = meta.get("name", f"Group {gid}")
        for slave in meta.get("slaves", []):
            addr_to_name[slave] = f"{meta.get('name', f'Group {gid}')} (Slave {slave})"

    alarms: List[AlarmRecord] = []
    for idx, rec in enumerate(root.iter("AlarmRecord")):
        addr_str = rec.attrib.get("Address", "0") or "0"
        addr = int(addr_str) if addr_str.isdigit() else 0
        
        detect_str = rec.attrib.get("Detect", "0") or "0"
        detect_addr = int(detect_str) if detect_str.isdigit() else addr

        err_code = rec.attrib.get("AlarmCode") or rec.attrib.get("Code", "0000")
        model = rec.attrib.get("Model", "")
        if not model and addr in (51, 52, 65, 66):
            model = "OC"
        elif not model:
            model = "IC"

        level_str = rec.attrib.get("PriorityLevel", "2") or "2"
        level = int(level_str) if level_str.isdigit() else 2

        # Parse timestamps
        occurred_dt = _parse_alarm_timestamp(rec, prefix="")
        recovered_dt = _parse_alarm_timestamp(rec, prefix="Recov")

        is_active = recovered_dt is None
        duration_str: Optional[str] = None
        if occurred_dt and recovered_dt:
            diff = recovered_dt - occurred_dt
            total_sec = max(0, int(diff.total_seconds()))
            hrs = total_sec // 3600
            mins = (total_sec % 3600) // 60
            secs = total_sec % 60
            if hrs > 0:
                duration_str = f"{hrs}h {mins}m {secs}s"
            elif mins > 0:
                duration_str = f"{mins}m {secs}s"
            else:
                duration_str = f"{secs}s"
        elif is_active and occurred_dt:
            duration_str = "Active / Ongoing"

        # Diagnostic info lookup
        diag = MITSUBISHI_ERROR_INFO.get(
            err_code,
            {
                "title": f"Mitsubishi Error Code {err_code}",
                "category": "Hardware & System",
                "description": f"Fault code {err_code} reported on M-Net address {addr}.",
                "troubleshooting": "Refer to Mitsubishi City Multi service manual for detailed component test procedures.",
            },
        )

        unit_name = addr_to_name.get(addr)
        if not unit_name:
            if addr in (51, 52):
                unit_name = f"Outdoor Condenser Unit A (Addr {addr})"
            elif addr in (65, 66):
                unit_name = f"Outdoor Condenser Unit B (Addr {addr})"
            else:
                unit_name = f"Unit Addr {addr}"

        detect_name = addr_to_name.get(detect_addr) if detect_addr != addr else None

        msg = f"{diag['title']} on {unit_name}"

        alarms.append(
            AlarmRecord(
                index=int(rec.attrib.get("Index", idx + 1) or idx + 1),
                address=addr,
                unit_name=unit_name,
                unit_model=model,
                detect_address=detect_addr,
                detect_name=detect_name,
                error_code=err_code,
                priority_level=level,
                occurred_at=occurred_dt.isoformat() if occurred_dt else None,
                recovered_at=recovered_dt.isoformat() if recovered_dt else None,
                is_active=is_active,
                duration_str=duration_str,
                title=diag["title"],
                category=diag["category"],
                description=diag["description"],
                troubleshooting=diag["troubleshooting"],
                message=msg,
            )
        )

    # Sort most recent first
    alarms.sort(key=lambda a: a.occurred_at or "", reverse=True)
    return alarms


def parse_datetime(xml_str: str) -> datetime:
    """Parse Clock response into a Python datetime object."""
    root = ET.fromstring(xml_str)
    check_error_response(root, raw_xml=xml_str)
    
    clock = root.find(".//Clock")
    if clock is None:
        raise GB50ProtocolError("Missing Clock element in response", raw_xml=xml_str)
    
    year = int(clock.attrib.get("Year", "2026"))
    month = int(clock.attrib.get("Month", "1"))
    day = int(clock.attrib.get("Day", "1"))
    hour = int(clock.attrib.get("Hour", "0"))
    minute = int(clock.attrib.get("Minute", "0"))
    second = int(clock.attrib.get("Second", "0"))
    return datetime(year, month, day, hour, minute, second)
