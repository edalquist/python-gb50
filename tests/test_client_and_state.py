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
from gb50.constants import (
    DriveState,
    OperationMode,
    ModelType,
    FanSpeed,
    AirDirection,
    RemoteControlPermission,
)


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

            # Explicit get_user_decrypted_passwords
            users_with_pw = await client.get_user_decrypted_passwords(category="Administrator")
            assert len(users_with_pw) == 1
            assert "password" in users_with_pw[0]


@pytest.mark.asyncio
async def test_state_manager_refresh_system_info():
    mock_client = AsyncMock()
    mock_client.host = "127.0.0.1"
    sys_info_1 = SystemInfo(
        version="2.80",
        model="GB-50ADA-A",
        system_name="Initial Name",
        ip_address="127.0.0.1",
        subnet_mask="255.255.255.0",
        gateway="127.0.0.1",
    )
    sys_info_2 = SystemInfo(
        version="2.80",
        model="GB-50ADA-A",
        system_name="Updated Name",
        ip_address="127.0.0.1",
        subnet_mask="255.255.255.0",
        gateway="127.0.0.1",
    )
    mock_client.get_system_info.side_effect = [sys_info_1, sys_info_2]

    mgr = StateManager(client=mock_client)

    # Initially None
    assert mgr.system_info is None

    # First get_system_info call fetches from client
    res1 = await mgr.get_system_info()
    assert res1.system_name == "Initial Name"
    assert mgr.system_info.system_name == "Initial Name"
    assert mock_client.get_system_info.call_count == 1

    # Second get_system_info call returns cached
    res_cached = await mgr.get_system_info()
    assert res_cached.system_name == "Initial Name"
    assert mock_client.get_system_info.call_count == 1

    # refresh_system_info forces a fresh fetch and updates cache
    res2 = await mgr.refresh_system_info()
    assert res2.system_name == "Updated Name"
    assert mgr.system_info.system_name == "Updated Name"
    assert mock_client.get_system_info.call_count == 2


def test_state_manager_has_changed_schedule_and_remote_lock():
    mgr = StateManager(client=AsyncMock())

    base = GroupStatus(
        group_id=1,
        name="Zone 1",
        address=1,
        model=ModelType.IC,
        drive=DriveState.OFF,
        mode=OperationMode.HEAT,
        set_temp_c=20.0,
        inlet_temp_c=22.0,
        fan_speed=FanSpeed.AUTO,
        air_direction=AirDirection.HORIZONTAL,
        filter_dirty=False,
        error_active=False,
        schedule_enabled=True,
        remote_lock=RemoteControlPermission.PERMIT,
    )

    # Identical state
    identical = base.model_copy()
    assert mgr._has_changed(base, identical) is False

    # schedule_enabled change
    changed_schedule = base.model_copy(update={"schedule_enabled": False})
    assert mgr._has_changed(base, changed_schedule) is True

    # remote_lock change
    changed_lock = base.model_copy(update={"remote_lock": RemoteControlPermission.PROHIBIT})
    assert mgr._has_changed(base, changed_lock) is True

    # drive change
    changed_drive = base.model_copy(update={"drive": DriveState.ON})
    assert mgr._has_changed(base, changed_drive) is True

    # mode change
    changed_mode = base.model_copy(update={"mode": OperationMode.COOL})
    assert mgr._has_changed(base, changed_mode) is True

    # set_temp_c change
    changed_temp = base.model_copy(update={"set_temp_c": 22.0})
    assert mgr._has_changed(base, changed_temp) is True

    # inlet_temp_c change
    changed_inlet = base.model_copy(update={"inlet_temp_c": 24.0})
    assert mgr._has_changed(base, changed_inlet) is True

    # filter_dirty change
    changed_filter = base.model_copy(update={"filter_dirty": True})
    assert mgr._has_changed(base, changed_filter) is True

    # error_active change
    changed_error = base.model_copy(update={"error_active": True})
    assert mgr._has_changed(base, changed_error) is True


@pytest.mark.asyncio
async def test_http_500_html_raises_transport_error():
    from gb50.exceptions import GB50TransportError
    import aiohttp
    from unittest.mock import MagicMock

    client = GB50Client(host="127.0.0.1")
    
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text.return_value = "<html><body>500 Internal Server Error (Apache)</body></html>"

    mock_session = MagicMock()
    mock_session.closed = False
    
    # context manager for session.post
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_session.post.return_value = mock_cm

    client._session = mock_session
    client._owns_session = False

    with pytest.raises(GB50TransportError, match="HTTP 500 from controller: <html><body>500 Internal Server Error"):
        await client._send_xml("<Packet/>", max_retries=0)


@pytest.mark.asyncio
async def test_external_session_preservation_on_retry():
    from unittest.mock import MagicMock
    from gb50.exceptions import GB50TransportError
    import aiohttp

    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.post.side_effect = aiohttp.ClientConnectionError("Connection reset")

    client = GB50Client(host="127.0.0.1", session=mock_session)
    assert client._owns_session is False

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(GB50TransportError, match="Failed to communicate with GB-50"):
            await client._send_xml("<Packet/>", max_retries=1)

    # The external session was not closed or replaced
    assert client._session is mock_session
    mock_session.close.assert_not_called()


@pytest.mark.asyncio
async def test_set_group_floor_invalidates_topology_cache():
    client = GB50Client(host="127.0.0.1")
    client._topology_cache = {1: {"name": "Zone 1", "floor": 1}}

    xml_resp = """<?xml version="1.0" encoding="UTF-8"?>
<Packet>
  <Command>setResponse</Command>
  <DatabaseManager>
    <MnetGroupFloor Group="1" Floor="2" />
  </DatabaseManager>
</Packet>"""

    with patch.object(client, "_send_xml", new_callable=AsyncMock, return_value=xml_resp):
        await client.set_group_floor(1, 2)
        assert client._topology_cache is None


def test_schedule_event_validation_negative():
    from gb50.protocol import build_set_weekly_schedule_request

    # Invalid drive
    with pytest.raises(ValueError, match="Invalid drive state 'UNKNOWN'"):
        build_set_weekly_schedule_request([1], 1, [{"hour": 8, "minute": 0, "drive": "UNKNOWN"}])

    # Invalid mode
    with pytest.raises(ValueError, match="Invalid operation mode 'TURBO'"):
        build_set_weekly_schedule_request([1], 1, [{"hour": 8, "minute": 0, "mode": "TURBO"}])

    # Invalid fan speed
    with pytest.raises(ValueError, match="Invalid fan speed 'HYPER'"):
        build_set_weekly_schedule_request([1], 1, [{"hour": 8, "minute": 0, "fan_speed": "HYPER"}])

    # Invalid air direction
    with pytest.raises(ValueError, match="Invalid air direction 'DIAGONAL'"):
        build_set_weekly_schedule_request([1], 1, [{"hour": 8, "minute": 0, "air_direction": "DIAGONAL"}])

    # Setpoint out of bounds
    with pytest.raises(ValueError, match="Schedule temperature setpoint 45.0°C out of allowable range"):
        build_set_weekly_schedule_request([1], 1, [{"hour": 8, "minute": 0, "set_temp_c": 45.0}])


@pytest.mark.asyncio
async def test_http_500_transient_retried_up_to_max_retries():
    """Verify GB50Client retries transient HTTP 500 responses without XML up to max_retries."""
    client = GB50Client(host="127.0.0.1")

    call_count = 0
    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.text.return_value = "Internal Server Error: Simulated Transient Hardware Failure"

    mock_session = MagicMock()
    mock_session.closed = False

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp

    def post_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return mock_cm

    mock_session.post.side_effect = post_side_effect
    client._session = mock_session
    client._owns_session = False

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(GB50TransportError, match="HTTP 500 from controller"):
            await client._send_xml("<Packet/>", max_retries=2)

    assert call_count == 3  # 1 initial + 2 retries
    assert mock_sleep.call_count == 2  # 2 backoff sleeps


@pytest.mark.asyncio
async def test_http_500_transient_recovers_on_retry():
    """Verify GB50Client recovers when a transient HTTP 500 is followed by an HTTP 200 response."""
    client = GB50Client(host="127.0.0.1")

    call_count = 0
    mock_resp_500 = AsyncMock()
    mock_resp_500.status = 500
    mock_resp_500.text.return_value = "<html><body>Transient Gateway Error</body></html>"

    mock_resp_200 = AsyncMock()
    mock_resp_200.status = 200
    mock_resp_200.text.return_value = '<?xml version="1.0"?><Packet><Command>getResponse</Command></Packet>'

    mock_session = MagicMock()
    mock_session.closed = False

    cm_500 = AsyncMock()
    cm_500.__aenter__.return_value = mock_resp_500
    cm_200 = AsyncMock()
    cm_200.__aenter__.return_value = mock_resp_200

    def post_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return cm_500
        return cm_200

    mock_session.post.side_effect = post_side_effect
    client._session = mock_session
    client._owns_session = False

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client._send_xml("<Packet/>", max_retries=2)

    assert call_count == 2
    assert "getResponse" in resp
    assert mock_sleep.call_count == 1



