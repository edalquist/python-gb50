"""Tests for GB50Client topology isolation, state manager lifecycle, and schedule constraints."""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from gb50.client import GB50Client, _redact_xml
from gb50.state_manager import StateManager
from gb50.exceptions import GB50ProtocolError, GB50TransportError
from gb50.protocol import (
    build_set_today_schedule_request,
    build_set_weekly_schedule_request,
)
from gb50.models import GroupControlRequest, GroupStatus, SystemInfo
from gb50.constants import DriveState, OperationMode, ModelType


def test_redact_xml():
    xml = '<UserAuth User="admin" Password="secretpassword" PasswordKey="1234" AuthKey="9999" AuthID="abcd" />'
    redacted = _redact_xml(xml)
    assert 'secretpassword' not in redacted
    assert '1234' not in redacted
    assert '9999' not in redacted
    assert 'abcd' not in redacted
    assert '***REDACTED***' in redacted


def test_schedule_request_builder_validations():
    # Valid schedule
    events = [{"hour": 8, "minute": 0, "drive": "ON", "mode": "AUTO", "set_temp_c": 21.0}]
    xml = build_set_today_schedule_request([1], events)
    assert 'Group="1"' in xml
    assert 'Hour="8"' in xml

    # Over 16 events rejected
    with pytest.raises(ValueError, match="Maximum 16 timer events"):
        build_set_today_schedule_request([1], [{"hour": 8, "minute": 0}] * 17)

    # Invalid hour
    with pytest.raises(ValueError, match="Invalid event hour"):
        build_set_today_schedule_request([1], [{"hour": 25, "minute": 0}])

    # Invalid minute
    with pytest.raises(ValueError, match="Invalid event minute"):
        build_set_today_schedule_request([1], [{"hour": 8, "minute": 60}])

    # Invalid group ID
    with pytest.raises(ValueError, match="Invalid group ID"):
        build_set_today_schedule_request([0], events)
    with pytest.raises(ValueError, match="Invalid group ID"):
        build_set_today_schedule_request([51], events)

    # Invalid day of week for weekly schedule
    with pytest.raises(ValueError, match="Invalid day_of_week"):
        build_set_weekly_schedule_request([1], 8, events)


@pytest.mark.asyncio
async def test_empty_telemetry_guard():
    client = GB50Client(host="127.0.0.1")
    # Empty list must return empty list immediately without calling _send_xml
    with patch.object(client, "_send_xml", new_callable=AsyncMock) as mock_send:
        result = await client.get_groups_telemetry([])
        assert result == []
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_topology_cache_isolation_on_failure():
    client = GB50Client(host="127.0.0.1")
    initial_topology = {
        1: {"name": "Room 101", "address": 1, "model": "IC", "slaves": [], "rcs": []},
        2: {"name": "Room 102", "address": 2, "model": "IC", "slaves": [], "rcs": []},
    }
    client._topology_cache = initial_topology

    # Simulate write failure in _send_xml
    with patch.object(client, "_send_xml", side_effect=Exception("Network connection timeout")):
        with pytest.raises(Exception, match="Network connection timeout"):
            await client.set_group_topology(group_id=1, name="Malicious Rename", primary_ic=99)

    # Cached topology MUST remain unchanged
    cached = await client.get_topology()
    assert cached[1]["name"] == "Room 101"
    assert cached[1]["address"] == 1


@pytest.mark.asyncio
async def test_state_manager_idempotent_start_and_listener_safety():
    mock_client = AsyncMock()
    mock_client.host = "127.0.0.1"
    mock_client.get_system_info.return_value = SystemInfo(
        version="2.80",
        model="GB-50ADA-A",
        ip_address="127.0.0.1",
        subnet_mask="255.255.255.0",
        gateway="127.0.0.1",
    )
    mock_client.get_all_groups.return_value = [
        GroupStatus(group_id=1, name="Zone 1", address=1, model=ModelType.IC)
    ]

    mgr = StateManager(client=mock_client, poll_interval_sec=0.1)

    # 1. Start twice - should be idempotent
    await mgr.start()
    task1 = mgr._poll_task
    await mgr.start()
    assert mgr._poll_task == task1  # Did not spawn a second concurrent poll task

    # 2. Add failing sync listener and failing async listener
    def bad_sync_listener(g):
        raise RuntimeError("Sync listener crashed")

    async def bad_async_listener(g):
        raise ValueError("Async listener crashed")

    mgr.add_listener(bad_sync_listener)
    mgr.add_listener(bad_async_listener)

    # Broadcast update - must not crash state manager or raise unhandled exceptions
    groups = [GroupStatus(group_id=1, name="Zone 1", address=1, model=ModelType.IC)]
    await mgr._broadcast_update(groups)
    await asyncio.sleep(0.05)  # Allow async tasks to complete

    await mgr.stop()


@pytest.mark.asyncio
async def test_get_users_password_privacy():
    client = GB50Client(host="127.0.0.1")
    xml_resp = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>getResponse</Command>
  <DatabaseManager>
    <UserAuth>
      <UserList UserCategory="Administrator">
        <UserRecord User="admin" Password="82808080808080808080" PasswordKey="1234" AvailableGroup="FFFFFFFFFFFFFFFF" />
      </UserList>
    </UserAuth>
  </DatabaseManager>
</Packet>"""

    with patch.object(client, "get_auth_key", new_callable=AsyncMock, return_value="1234"):
        with patch.object(client, "_send_xml", new_callable=AsyncMock, return_value=xml_resp):
            # Default: passwords omitted
            users_default = await client.get_users(category="Administrator")
            assert len(users_default) == 1
            assert users_default[0]["user"] == "admin"
            assert "password" not in users_default[0]

            # Explicit include_passwords=True
            users_with_pw = await client.get_users(category="Administrator", include_passwords=True)
            assert len(users_with_pw) == 1
            assert "password" in users_with_pw[0]
