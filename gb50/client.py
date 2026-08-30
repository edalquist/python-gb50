"""Asynchronous client for communicating with the Mitsubishi GB-50 central controller."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import aiohttp
import asyncio
import xml.etree.ElementTree as ET

from .constants import (
    DriveState,
    OperationMode,
    AirDirection,
    FanSpeed,
    RemoteControlPermission,
)
from .models import (
    GroupStatus,
    SystemInfo,
    ScheduleItem,
    AlarmRecord,
    GroupControlRequest,
)
from .protocol import (
    GB50ProtocolError,
    build_get_system_info_request,
    build_set_system_data_request,
    build_get_topology_request,
    build_set_group_name_request,
    build_set_group_topology_request,
    build_get_groups_telemetry_request,
    build_set_group_request,
    build_set_groups_batch_request,
    build_reset_filter_request,
    build_get_today_schedule_request,
    build_get_all_schedules_request,
    build_get_weekly_schedule_request,
    build_set_today_schedule_request,
    build_set_weekly_schedule_request,
    build_get_alarms_request,
    build_delete_alarm_history_request,
    build_get_datetime_request,
    build_set_datetime_request,
    build_get_interlocks_request,
    build_set_interlocks_batch_request,
    build_get_summertime_request,
    build_set_summertime_request,
    build_get_setback_request,
    build_set_setback_request,
    build_register_option_request,
    parse_system_info,
    parse_topology,
    parse_interlocks_list,
    parse_summertime,
    parse_setback,
    parse_groups_telemetry,
    parse_today_schedule,
    parse_all_today_schedules,
    parse_weekly_schedule,
    parse_alarms,
    parse_datetime,
    wrap_packet,
    check_error_response,
)
from .crypto import encrypt, decrypt, create_key

logger = logging.getLogger("gb50.client")


class GB50Client:
    """Asynchronous client for Mitsubishi GB-50 series HVAC central controllers."""

    def __init__(
        self,
        host: str,
        port: int = 80,
        session: Optional[aiohttp.ClientSession] = None,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}/servlet/MIMEReceiveServlet"
        self._session = session
        self._owns_session = session is None
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._topology_cache: Optional[Dict[int, Dict[str, Any]]] = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> GB50Client:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP session if owned by this client."""
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    async def _send_xml(self, xml_payload: str) -> str:
        """Post XML packet to the controller and return response text."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "Connection": "close",
        }
        
        logger.debug("Sending XML to %s:\n%s", self.url, xml_payload)

        try:
            async with self._session.post(self.url, data=xml_payload.encode("utf-8"), headers=headers) as resp:
                body = await resp.text(encoding="utf-8", errors="replace")
                logger.debug("Received XML (HTTP %s) from %s:\n%s", resp.status, self.url, body)
                
                if resp.status not in (200, 500):
                    logger.error("Unexpected HTTP status %s from controller %s. Response body:\n%s", resp.status, self.url, body)
                    resp.raise_for_status()
                return body
        except Exception as ex:
            logger.error("HTTP error communicating with GB-50 at %s: %s\nRequest payload was:\n%s", self.url, ex, xml_payload)
            raise

    async def get_system_info(self) -> SystemInfo:
        """Fetch controller hardware metadata, firmware version, and licensed functions."""
        xml_req = build_get_system_info_request()
        xml_resp = await self._send_xml(xml_req)
        return parse_system_info(xml_resp)

    async def set_system_info(self, settings: Dict[str, Any]) -> bool:
        """Update controller SystemData settings (name, IP, mask, gateway, formats, etc.)."""
        xml_req = build_set_system_data_request(settings)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def get_topology(self, force_refresh: bool = False) -> Dict[int, Dict[str, Any]]:
        """Discover all configured groups, web display names, and M-Net hardware addresses."""
        if self._topology_cache is not None and not force_refresh:
            return self._topology_cache
        
        async with self._lock:
            if self._topology_cache is not None and not force_refresh:
                return self._topology_cache
            xml_req = build_get_topology_request()
            xml_resp = await self._send_xml(xml_req)
            self._topology_cache = parse_topology(xml_resp)
            return self._topology_cache

    async def set_group_topology(
        self,
        group_id: int,
        name: str,
        primary_ic: int,
        model: str = "IC",
        slave_ics: Optional[List[int]] = None,
        rcs: Optional[List[int]] = None,
    ) -> bool:
        """Configure group name, primary unit address, slave units, and remote controllers."""
        xml_req = build_set_group_topology_request(
            group_id=group_id,
            name=name,
            primary_ic=primary_ic,
            model=model,
            slave_ics=slave_ics,
            rcs=rcs,
        )
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        self._topology_cache = None
        return True

    async def get_all_groups(self, refresh_topology: bool = False) -> List[GroupStatus]:
        """Fetch real-time telemetry and state for all configured HVAC groups in a single batch."""
        topology = await self.get_topology(force_refresh=refresh_topology)
        if not topology:
            return []
        
        group_ids = sorted(topology.keys())
        xml_req = build_get_groups_telemetry_request(group_ids)
        xml_resp = await self._send_xml(xml_req)
        return parse_groups_telemetry(xml_resp, topology=topology)

    async def get_groups_telemetry(self, group_ids: Optional[List[int]] = None) -> List[GroupStatus]:
        """Fetch real-time telemetry for specified group IDs (or all groups if None)."""
        topology = await self.get_topology()
        if not topology:
            return []
        target_ids = group_ids if group_ids is not None else sorted(topology.keys())
        xml_req = build_get_groups_telemetry_request(target_ids)
        xml_resp = await self._send_xml(xml_req)
        return parse_groups_telemetry(xml_resp, topology=topology)

    async def get_group(self, group_id: int) -> GroupStatus:
        """Fetch real-time status for a single HVAC group."""
        topology = await self.get_topology()
        xml_req = build_get_groups_telemetry_request([group_id])
        xml_resp = await self._send_xml(xml_req)
        groups = parse_groups_telemetry(xml_resp, topology=topology)
        if not groups:
            raise GB50ProtocolError(f"Group {group_id} not found in controller response")
        return groups[0]

    async def set_group(
        self,
        group_id: int,
        drive: Optional[DriveState] = None,
        mode: Optional[OperationMode] = None,
        set_temp_c: Optional[float] = None,
        set_temp_f: Optional[float] = None,
        air_direction: Optional[AirDirection] = None,
        fan_speed: Optional[FanSpeed] = None,
        remote_lock: Optional[RemoteControlPermission] = None,
    ) -> bool:
        """Send command to control an HVAC group."""
        req = GroupControlRequest(
            drive=drive,
            mode=mode,
            set_temp_c=set_temp_c,
            set_temp_f=set_temp_f,
            air_direction=air_direction,
            fan_speed=fan_speed,
            remote_lock=remote_lock,
        )
        xml_req = build_set_group_request(group_id, req)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def set_group_name(self, group_id: int, name: str) -> bool:
        """Rename an HVAC group web display name."""
        xml_req = build_set_group_name_request(group_id, name)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        self._topology_cache = None
        return True

    async def set_groups_batch(self, updates: Dict[int, GroupControlRequest]) -> bool:
        """Send batch command to control multiple HVAC groups in a single HTTP request."""
        if not updates:
            return True
        xml_req = build_set_groups_batch_request(updates)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def reset_filter(self, group_id: int) -> bool:
        """Reset the dirty air filter indicator sign for a group."""
        xml_req = build_reset_filter_request(group_id)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def get_interlocks(self) -> List[Dict[str, int]]:
        """Retrieve list of LOSSNAY interlocked pairings (IC -> LC)."""
        xml_req = build_get_interlocks_request()
        xml_resp = await self._send_xml(xml_req)
        return parse_interlocks_list(xml_resp)

    async def set_interlocks(self, pairings: List[Dict[str, int]]) -> bool:
        """Set LOSSNAY interlocked pairings."""
        xml_req = build_set_interlocks_batch_request(pairings)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        self._topology_cache = None
        return True

    async def get_today_schedule(self, group_id: int) -> List[ScheduleItem]:
        """Fetch the programmed timer events for today for a single group."""
        xml_req = build_get_today_schedule_request(group_id)
        xml_resp = await self._send_xml(xml_req)
        return parse_today_schedule(xml_resp)

    async def get_all_today_schedules(self, group_ids: Optional[List[int]] = None) -> Dict[int, List[ScheduleItem]]:
        """Fetch programmed timer events for today across multiple/all groups in a single call."""
        if group_ids is None:
            topology = await self.get_topology()
            group_ids = sorted(topology.keys()) if topology else list(range(1, 31))
        if not group_ids:
            return {}
        xml_req = build_get_all_schedules_request(group_ids)
        xml_resp = await self._send_xml(xml_req)
        return parse_all_today_schedules(xml_resp)

    async def get_weekly_schedule(self, group_id: int, season: int = 1) -> Dict[int, List[ScheduleItem]]:
        """Fetch full 7-day weekly schedule pattern for a group (keys 1=Mon .. 7=Sun)."""
        xml_req = build_get_weekly_schedule_request(group_id, season=season)
        xml_resp = await self._send_xml(xml_req)
        return parse_weekly_schedule(xml_resp)

    async def set_today_schedule(self, group_ids: List[int], events: List[Dict[str, Any]]) -> bool:
        """Update today's programmed timer events for one or more groups simultaneously."""
        if not group_ids:
            return True
        xml_req = build_set_today_schedule_request(group_ids, events)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def set_weekly_schedule(
        self,
        group_ids: List[int],
        day_of_week: int,
        events: List[Dict[str, Any]],
        season: int = 1,
    ) -> bool:
        """Update weekly schedule pattern (day 1=Mon .. 7=Sun) for one or more groups."""
        if not group_ids:
            return True
        xml_req = build_set_weekly_schedule_request(group_ids, day_of_week, events, season=season)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def get_alarms(self, priority_level: Optional[int] = None) -> List[AlarmRecord]:
        """Retrieve unit malfunction alarms and communication error history."""
        topology = await self.get_topology()
        if priority_level is not None:
            xml_req = build_get_alarms_request(priority_level=priority_level)
            xml_resp = await self._send_xml(xml_req)
            return parse_alarms(xml_resp, topology=topology)
        
        # Query unit errors (2) and comm errors (0)
        alarms_all: List[AlarmRecord] = []
        for p in (2, 0):
            try:
                xml_req = build_get_alarms_request(priority_level=p)
                xml_resp = await self._send_xml(xml_req)
                parsed = parse_alarms(xml_resp, topology=topology)
                alarms_all.extend(parsed)
            except Exception as e:
                logger.warning("Failed querying alarms for priority %s: %s", p, e)
        
        alarms_all.sort(key=lambda a: a.occurred_at or "", reverse=True)
        return alarms_all

    async def clear_alarm_history(self, priority_level: int = 2) -> bool:
        """Clear resolved alarm history records from controller memory."""
        xml_req = build_delete_alarm_history_request(priority_level=priority_level)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def get_datetime(self) -> datetime:
        """Fetch controller real-time clock."""
        xml_req = build_get_datetime_request()
        xml_resp = await self._send_xml(xml_req)
        return parse_datetime(xml_resp)

    async def set_datetime(self, dt: datetime) -> bool:
        """Synchronize controller real-time clock."""
        xml_req = build_set_datetime_request(dt)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def get_summertime(self) -> Dict[str, Any]:
        """Fetch Daylight Saving Time / Summer Time configuration."""
        xml_req = build_get_summertime_request()
        xml_resp = await self._send_xml(xml_req)
        return parse_summertime(xml_resp)

    async def set_summertime(self, config: Dict[str, Any]) -> bool:
        """Update Daylight Saving Time / Summer Time configuration."""
        xml_req = build_set_summertime_request(config)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def get_setback(self) -> Dict[str, Any]:
        """Fetch Night Setback schedule and per-group drift boundaries."""
        xml_req = build_get_setback_request()
        xml_resp = await self._send_xml(xml_req)
        return parse_setback(xml_resp)

    async def set_setback(
        self,
        enabled: bool,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        group_records: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Update Night Setback configuration."""
        xml_req = build_set_setback_request(
            enabled=enabled,
            start_hour=start_hour,
            start_minute=start_minute,
            end_hour=end_hour,
            end_minute=end_minute,
            group_records=group_records,
        )
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def register_option(self, func_index: int, key_code: str) -> bool:
        """Activate an optional software function license on the controller."""
        func_names = [
            None, "WebBrowse", "Schedule", "Account", "BeforeMal", "SendMail",
            "SaveEnergy", "MainteTool", "UserWeb", "Bacnet", "MainteFull",
            "PeakCut", "PLCIo", "GB50LP", "OCMeasure", "AG150ALP", "EnergyLP",
            "GB24LP", "Interlock", "TG2000", "GB50ADALP", "Ecocute", "ETShiftLP",
            "ETShift", "ETAccount"
        ]
        func_id = func_names[func_index] if 1 <= func_index < len(func_names) else f"Func_{func_index}"
        auth_key = await self.get_auth_key()
        encrypted_func_id = encrypt(func_id, auth_key)
        xml_req = build_register_option_request(func_index, encrypted_func_id, key_code)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def get_auth_key(self) -> str:
        """Fetch fresh dynamic authentication nonce key."""
        body = '    <UserAuth AuthKey="*" />\r\n'
        xml_req = wrap_packet("getRequest", body)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        ua = root.find(".//UserAuth")
        if ua is not None and "AuthKey" in ua.attrib:
            return ua.attrib["AuthKey"]
        raise GB50ProtocolError("Missing AuthKey in controller response", raw_xml=xml_resp)

    async def get_users(self, category: str = "Administrator") -> List[Dict[str, Any]]:
        """Retrieve user accounts for a given category (Administrator, Maintenance, PublicUser)."""
        auth_key = await self.get_auth_key()
        auth_id = encrypt("UserList", auth_key)
        body = (
            f'    <UserAuth>\r\n'
            f'      <UserList AuthID="{auth_id}" UserCategory="{category}">\r\n'
            f'        <UserRecord />\r\n'
            f'      </UserList>\r\n'
            f'    </UserAuth>\r\n'
        )
        xml_req = wrap_packet("getRequest", body)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        
        users: List[Dict[str, Any]] = []
        for rec in root.iter("UserRecord"):
            u = rec.attrib.get("User", "")
            p_enc = rec.attrib.get("Password", "")
            pk_str = rec.attrib.get("PasswordKey", "")
            ag = rec.attrib.get("AvailableGroup", "FFFFFFFFFFFFFFFF")
            dec_pw = decrypt(p_enc, int(pk_str)) if p_enc and pk_str and pk_str.isdigit() else ""
            users.append({
                "user": u,
                "password": dec_pw,
                "category": category,
                "available_group": ag,
            })
        return users

    async def set_user_password(self, user: str, new_password: str) -> bool:
        """Update password for a user account using Mitsubishi Crypt cipher."""
        key_str = create_key()
        enc_pw = encrypt(new_password, key_str)
        body = f'    <UserAuth User="{user}" Password="{enc_pw}" PasswordKey="{key_str}" />\r\n'
        xml_req = wrap_packet("setRequest", body)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True

    async def set_user_permissions(self, user: str, available_groups_mask: str) -> bool:
        """Update accessible groups bitmask for a user account."""
        body = f'    <UserAuth User="{user}" AvailableGroup="{available_groups_mask}" />\r\n'
        xml_req = wrap_packet("setRequest", body)
        xml_resp = await self._send_xml(xml_req)
        root = ET.fromstring(xml_resp)
        check_error_response(root, raw_xml=xml_resp)
        return True
