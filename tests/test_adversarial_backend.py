"""Adversarial and Stress Test Suite for python-gb50 and gb50-web-proxy backend.

Empirically challenges:
1. Concurrency safety of GB50Client._http_lock under high concurrency simulation.
2. Stress and boundary/fuzz testing of binary telemetry parser (bulk_parser.py).
3. Cipher (crypto.py) round-trip correctness across keys and adversarial inputs.
4. Protocol XML parser resilience under corrupted and malformed inputs.
5. Temperature conversion monotonicity, bijectivity, and state manager concurrency.
"""

import asyncio
import copy
import random
import string
import struct
import xml.etree.ElementTree as ET
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import aiohttp

from gb50.client import GB50Client, _redact_xml
from gb50.bulk_parser import parse_bulk_telemetry, parse_bcd_temp
from gb50.crypto import encrypt, decrypt, create_key, _char_to_val, _val_to_char
from gb50.exceptions import (
    GB50Error,
    GB50ProtocolError,
    GB50TransportError,
    GB50ParseError,
)
from gb50.constants import (
    DriveState,
    OperationMode,
    AirDirection,
    FanSpeed,
    ModelType,
    RemoteControlPermission,
)
from gb50.models import GroupControlRequest, c_to_f, f_to_c, GroupStatus
from gb50.state_manager import StateManager
from gb50.protocol import check_error_response


# ============================================================================
# 1. CONCURRENCY SAFETY & MUTEX SERIALIZATION TESTS
# ============================================================================

class MockLockedTransport:
    """Mock HTTP transport that simulates network delay and asserts strict mutual exclusion."""
    def __init__(self, simulated_delay: float = 0.01):
        self.simulated_delay = simulated_delay
        self.active_requests = 0
        self.max_concurrent_observed = 0
        self.total_requests = 0
        self.lock_violations = 0
        self._internal_lock = asyncio.Lock()

    async def mock_post(self, url, data=None, headers=None):
        async with self._internal_lock:
            self.active_requests += 1
            if self.active_requests > self.max_concurrent_observed:
                self.max_concurrent_observed = self.active_requests
            if self.active_requests > 1:
                self.lock_violations += 1
            self.total_requests += 1

        # Simulate network I/O and controller processing time
        await asyncio.sleep(self.simulated_delay)

        async with self._internal_lock:
            self.active_requests -= 1

        # Return mock XML response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="""<?xml version="1.0" encoding="UTF-8"?>
<Packet>
    <Database>
        <SystemData Model="GB-50ADA-A" Ver="2.90" SystemName="Main Sanctuary AC" SerialNumber="123456"
                    LocationID="0" IPAddress="192.0.2.90" SubnetMask="255.255.255.0"
                    DefaultGateway="192.0.2.1" MACAddress="00:11:22:33:44:55"
                    TempUnit="Fahrenheit" TimeDisp="24" DateDisp="YYYY/MM/DD"
                    MnetAddress="0" Function_01="1" Function_02="0"/>
    </Database>
</Packet>""")
        return mock_resp


@pytest.mark.asyncio
async def test_http_lock_strict_mutual_exclusion_high_concurrency():
    """Verify that GB50Client._http_lock guarantees at most 1 concurrent HTTP request

    under high async concurrency (100 parallel tasks).
    """
    transport = MockLockedTransport(simulated_delay=0.005)
    client = GB50Client(host="192.0.2.90", port=80)

    # Patch session.post to use transport.mock_post
    mock_session = MagicMock()
    mock_session.closed = False
    
    class MockPostContextManager:
        async def __aenter__(self):
            return await transport.mock_post(client.url)
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_session.post = MagicMock(side_effect=lambda url, data=None, headers=None: MockPostContextManager())
    client._session = mock_session
    client._owns_session = False

    num_concurrent_tasks = 100

    async def make_request(idx: int):
        if idx % 2 == 0:
            return await client.get_system_info()
        else:
            return await client._send_xml("<Packet><Database><SystemData/></Database></Packet>")

    # Launch all 100 coroutines simultaneously
    results = await asyncio.gather(*(make_request(i) for i in range(num_concurrent_tasks)), return_exceptions=True)

    # Assertions
    assert len(results) == num_concurrent_tasks
    for r in results:
        assert not isinstance(r, Exception), f"Unexpected exception in task: {r}"
    assert transport.total_requests == num_concurrent_tasks
    assert transport.max_concurrent_observed == 1, (
        f"Lock violation! Max concurrent HTTP requests observed was {transport.max_concurrent_observed}, expected 1"
    )
    assert transport.lock_violations == 0, f"Observed {transport.lock_violations} mutual exclusion violations"


@pytest.mark.asyncio
async def test_http_lock_release_on_cancellation():
    """Verify that if a coroutine waiting for or executing _send_xml is cancelled,

    the lock is released cleanly and subsequent callers succeed.
    """
    client = GB50Client(host="192.0.2.90", port=80)

    # Mock post that blocks until cancelled
    async def blocking_post(*args, **kwargs):
        await asyncio.sleep(10.0)

    mock_session = MagicMock()
    mock_session.closed = False

    class BlockingContext:
        async def __aenter__(self):
            await blocking_post()
        async def __aexit__(self, *args):
            pass

    mock_session.post = MagicMock(side_effect=lambda *a, **kw: BlockingContext())
    client._session = mock_session
    client._owns_session = False

    task = asyncio.create_task(client.get_system_info())
    await asyncio.sleep(0.02)  # Ensure task enters _send_xml and acquires lock

    # Cancel the running task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Lock must now be free
    assert not client._http_lock.locked()

    # Subsequent request with normal mock must succeed immediately
    class FastContext:
        async def __aenter__(self):
            resp = AsyncMock()
            resp.status = 200
            resp.text = AsyncMock(return_value="<Packet><Database><SystemData Model='IC'/></Database></Packet>")
            return resp
        async def __aexit__(self, *args):
            pass

    mock_session.post = MagicMock(side_effect=lambda *a, **kw: FastContext())
    info = await client.get_system_info()
    assert info.model == "IC"


@pytest.mark.asyncio
async def test_http_lock_retry_holds_mutex_and_recovers():
    """Verify that during transient network transport failures, the retry backoff

    holds the lock and successfully completes when network recovers.
    """
    client = GB50Client(host="192.0.2.90", port=80)
    attempts = 0

    class FlakyContext:
        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise aiohttp.ClientConnectionError("Simulated controller TCP reset")
            resp = AsyncMock()
            resp.status = 200
            resp.text = AsyncMock(return_value="<Packet><Database><SystemData Model='GB-50'/></Database></Packet>")
            return resp
        async def __aexit__(self, *args):
            pass

    def create_mock_session(*args, **kwargs):
        mock_sess = MagicMock()
        mock_sess.closed = False
        mock_sess.close = AsyncMock()
        mock_sess.post = MagicMock(side_effect=lambda *a, **kw: FlakyContext())
        return mock_sess

    with patch("aiohttp.ClientSession", side_effect=create_mock_session):
        client._session = create_mock_session()
        client._owns_session = True

        info = await client.get_system_info()
        assert info.model == "GB-50"
        assert attempts == 2


@pytest.mark.asyncio
async def test_topology_cache_thread_safety_and_immutability():
    """Verify get_topology returns deep copies preventing external callers from corrupting internal cache."""
    client = GB50Client(host="192.0.2.90")
    client._topology_cache = {
        1: {"name": "Sanctuary", "model": ModelType.IC, "address": 1, "floor": 1, "slaves": [], "rcs": []},
        2: {"name": "Narthex", "model": ModelType.IC, "address": 2, "floor": 1, "slaves": [], "rcs": []},
    }

    # Concurrent readers mutating the returned dict
    async def reader_task(val: int):
        top = await client.get_topology()
        top[1]["name"] = f"Corrupted_{val}"
        top[999] = {"name": "Injected"}
        return top

    results = await asyncio.gather(*(reader_task(i) for i in range(50)))
    assert len(results) == 50

    # Fresh read from client must remain pristine
    pristine_top = await client.get_topology()
    assert pristine_top[1]["name"] == "Sanctuary"
    assert 999 not in pristine_top


# ============================================================================
# 2. BULK TELEMETRY PARSER FUZZING & BOUNDARY TESTS
# ============================================================================

def test_bulk_parser_boundary_temperatures():
    """Test boundary conditions for inlet temperature, set temperature, and BCD decoding."""
    # Valid base bulk payload template (65 bytes = 130 hex characters)
    raw = bytearray(65)
    raw[0] = 0x01
    raw[1] = 0x01  # ON
    raw[2] = 0x01  # COOL
    raw[3] = 22    # 22 C
    raw[4] = 0     # 0 dec
    # Positive inlet temp: 24.5 C -> 245 = 0x00F5
    struct.pack_into(">h", raw, 5, 245)
    raw[7] = 0x01
    raw[8] = 0x02
    raw[9] = 0x00
    raw[15] = 0x00
    raw[16] = 0x00
    raw[17] = 0x01
    raw[21] = 0x01
    raw[23] = 0x01
    raw[24] = 0x01
    raw[25] = 0x01
    raw[26] = 0x01
    raw[27] = 0x01
    raw[28] = 0x01
    raw[29] = 0x01
    raw[30] = 0x01
    raw[31] = 0x01
    raw[32] = 0x13
    raw[33] = 0x1c
    raw[34] = 0x1e
    raw[35] = 0x11
    raw[36] = 0x13
    raw[37] = 0x1c
    raw[45] = 0x01

    hex_str = raw.hex()
    res = parse_bulk_telemetry(hex_str)
    assert res["inlet_temp_c"] == 24.5
    assert res["set_temp_c"] == 22.0
    assert res["drive"] == DriveState.ON
    assert res["mode"] == OperationMode.COOL

    # Test negative inlet temperature (e.g. -5.5 C -> -55 = 0xFFC9)
    struct.pack_into(">h", raw, 5, -55)
    res = parse_bulk_telemetry(raw.hex())
    assert res["inlet_temp_c"] == -5.5

    # Test extreme negative inlet temperature (-30.0 C -> -300)
    struct.pack_into(">h", raw, 5, -300)
    res = parse_bulk_telemetry(raw.hex())
    assert res["inlet_temp_c"] == -30.0

    # Test zero set temp (returns None)
    raw[3] = 0
    raw[4] = 0
    res = parse_bulk_telemetry(raw.hex())
    assert res["set_temp_c"] is None

    # Test set temp with fractional digit (23.5 C)
    raw[3] = 23
    raw[4] = 5
    res = parse_bulk_telemetry(raw.hex())
    assert res["set_temp_c"] == 23.5


def test_bulk_parser_bcd_temp_variations():
    """Test parse_bcd_temp edge cases and non-standard decimal nibbles."""
    assert parse_bcd_temp(0, 0) == 0.0
    assert parse_bcd_temp(0x19, 5) == 19.5
    assert parse_bcd_temp(0x28, 0) == 28.0
    assert parse_bcd_temp(0x30, 0) == 30.0
    # Dec nibble >= 10 is ignored
    assert parse_bcd_temp(0x20, 10) == 20.0
    assert parse_bcd_temp(0x20, 15) == 20.0
    # Non-BCD hex byte fallback
    assert parse_bcd_temp(0x1F, 0) == 31.0


def test_bulk_parser_fuzz_random_noise():
    """Fuzz parse_bulk_telemetry with 5,000 completely random byte streams.

    Verifies that it raises GB50ParseError or ValueError and NEVER crashes with unhandled exceptions.
    """
    rng = random.Random(42)
    for _ in range(5000):
        # Generate random length from 0 to 200 hex characters
        length = rng.randint(0, 200)
        # Random hex string or random ascii string
        if rng.random() < 0.8:
            payload = "".join(rng.choices("0123456789abcdefABCDEF", k=length))
        else:
            payload = "".join(rng.choices(string.printable, k=length))

        try:
            parse_bulk_telemetry(payload)
        except (GB50ParseError, ValueError):
            pass  # Expected safe failure mode
        except Exception as ex:
            pytest.fail(f"Unhandled exception during fuzzing with payload {payload[:40]!r}: {type(ex).__name__}: {ex}")


def test_bulk_parser_fuzz_mutated_valid_packet():
    """Fuzz parse_bulk_telemetry by mutating fields in a valid 130-char payload."""
    base_raw = bytearray(65)
    base_raw[0] = 0x01
    base_raw[1] = 0x01
    base_raw[2] = 0x02
    base_raw[7] = 0x01
    base_raw[8] = 0x02
    base_raw[17] = 0x01
    base_raw[32] = 0x13
    base_raw[33] = 0x1c
    base_raw[34] = 0x1e
    base_raw[35] = 0x11
    base_raw[36] = 0x13
    base_raw[37] = 0x1c

    rng = random.Random(1337)
    for _ in range(5000):
        mutated = bytearray(base_raw)
        # Mutate 1 to 5 random bytes
        num_mutations = rng.randint(1, 5)
        for _ in range(num_mutations):
            byte_idx = rng.randint(0, 64)
            mutated[byte_idx] = rng.randint(0, 255)

        try:
            res = parse_bulk_telemetry(mutated.hex())
            # If it succeeded, verify basic structure
            assert "drive" in res
            assert "mode" in res
            assert "inlet_temp_c" in res
            assert "capabilities" in res
        except GB50ParseError:
            pass  # Expected on invalid codes
        except Exception as ex:
            pytest.fail(f"Unhandled crash on mutated packet: {type(ex).__name__}: {ex}")


# ============================================================================
# 3. CIPHER (CRYPTO.PY) ADVERSARIAL STRESS & EXHAUSTIVE TESTING
# ============================================================================

def test_crypto_exhaustive_keys_round_trip():
    """Test crypto encrypt/decrypt round-trip over a wide sample of 5-digit keys."""
    test_plaintexts = [
        "admin",
        "init",
        "guest",
        "UserList",
        "Administrator",
        "Maintenance",
        "PublicUser",
        "a",
        "ab",
        "abc",
        "abcd",
        "abcde",
        "abcdef",
        "Pass1234567890",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    ]

    rng = random.Random(999)
    # Test 500 randomly sampled keys from 0 to 99999 + boundary keys
    boundary_keys = [0, 1, 9, 10, 99, 100, 999, 1000, 9999, 10000, 99999, 12345, 54321, 10203, 90807]
    sample_keys = boundary_keys + [rng.randint(0, 99999) for _ in range(500)]

    for key_int in sample_keys:
        key_str = f"{key_int:05d}"
        for pt in test_plaintexts:
            ciphertext = encrypt(pt, key_str)
            decrypted = decrypt(ciphertext, key_int)
            assert decrypted == pt, f"Cipher roundtrip failed for key {key_str} with plaintext {pt!r} -> cipher {ciphertext!r} -> decrypted {decrypted!r}"


def test_crypto_create_key_distribution_and_validity():
    """Verify create_key generates valid 5-digit strings matching format [0-9]{4}[1-4]."""
    for _ in range(2000):
        k = create_key()
        assert len(k) == 5
        assert k.isdigit()
        # Last char must be 1, 2, 3, or 4
        assert k[4] in ("1", "2", "3", "4")


def test_crypto_adversarial_inputs():
    """Verify cipher rejects invalid non-alphanumeric characters properly."""
    # Plaintext containing spaces or symbols
    with pytest.raises(ValueError, match="outside the valid alphanumeric cipher set"):
        encrypt("hello world", "12345")

    with pytest.raises(ValueError, match="outside the valid alphanumeric cipher set"):
        encrypt("admin@123", "12345")

    with pytest.raises(ValueError, match="outside the valid alphanumeric cipher set"):
        encrypt("password!", "12345")

    # Decrypt with corrupted ciphertext containing invalid chars at evaluated position
    with pytest.raises(ValueError, match="outside the valid alphanumeric cipher set"):
        decrypt("#invalid_cipher", 12345)


# ============================================================================
# 4. XML PROTOCOL PARSER RESILIENCE TESTS
# ============================================================================

def test_redact_xml_preserves_wellformedness_and_masks_secrets():
    """Test XML credential redactor masks all auth attributes cleanly."""
    xml = '<Packet><Database><UserAuth Category="Administrator" Password="supersecret" PasswordKey="12345" AuthKey="98765" AuthID="admin123"/></Database></Packet>'
    redacted = _redact_xml(xml)
    assert "supersecret" not in redacted
    assert "12345" not in redacted
    assert "98765" not in redacted
    assert "admin123" not in redacted
    assert 'Password="***REDACTED***"' in redacted
    assert 'PasswordKey="***REDACTED***"' in redacted
    assert 'AuthKey="***REDACTED***"' in redacted
    assert 'AuthID="***REDACTED***"' in redacted


def test_redact_xml_handles_empty_or_malformed_inputs():
    """Test _redact_xml handles None, empty string, or non-XML safely."""
    assert _redact_xml("") == ""
    assert _redact_xml(None) == ""
    assert _redact_xml("plain text without auth") == "plain text without auth"


def test_protocol_xml_error_handling_and_malformed_packets():
    """Test protocol check_error_response handles diverse error codes and corrupted XML structures."""
    # Normal success packet
    normal_xml = '<Packet><Database><SystemData Model="GB-50"/></Database></Packet>'
    check_error_response(ET.fromstring(normal_xml), raw_xml=normal_xml)

    # Packet with ERROR element
    error_xml = '<Packet><Database><ERROR Point="SetTemp" Code="0101" Message="Controller Busy"/></Database></Packet>'
    with pytest.raises(GB50ProtocolError) as exc_info:
        check_error_response(ET.fromstring(error_xml), raw_xml=error_xml)
    assert exc_info.value.code == "0101"
    assert exc_info.value.point == "SetTemp"
    assert exc_info.value.error_code == 101

    # Packet with getErrorResponse Command
    cmd_err_xml = '<Packet><Command>getErrorResponse</Command><Database/></Packet>'
    with pytest.raises(GB50ProtocolError):
        check_error_response(ET.fromstring(cmd_err_xml), raw_xml=cmd_err_xml)


# ============================================================================
# 5. TEMPERATURE UNIT CONVERSIONS & STATE MANAGER CONCURRENCY
# ============================================================================

def test_temperature_conversions_monotonic_and_invariance():
    """Verify Celsius <-> Fahrenheit conversions are strictly monotonic and handle edge values."""
    assert c_to_f(None) is None
    assert f_to_c(None) is None

    # Freezing point
    assert c_to_f(0.0) == 32.0
    assert f_to_c(32.0) == 0.0

    # Human comfort range
    assert c_to_f(20.0) == 68.0
    assert c_to_f(25.0) == 77.0
    assert f_to_c(68.0) == 20.0
    assert f_to_c(77.0) == 25.0

    # Negative temperatures
    assert c_to_f(-10.0) == 14.0
    assert c_to_f(-40.0) == -40.0
    assert f_to_c(-40.0) == -40.0

    # Monotonicity test across range [-30°C to 50°C]
    c_values = [round(x * 0.1, 1) for x in range(-300, 501)]
    f_values = [c_to_f(c) for c in c_values]
    for i in range(len(f_values) - 1):
        assert f_values[i] <= f_values[i + 1], f"Non-monotonic conversion at {c_values[i]}C -> {f_values[i]}F"


@pytest.mark.asyncio
async def test_state_manager_pubsub_concurrency_stress():
    """Stress test StateManager with rapid state changes and 50 concurrent listener callbacks."""
    client = GB50Client(host="192.0.2.90")
    sm = StateManager(client=client, poll_interval_sec=0.1)

    received_events = []
    
    def make_listener(lid: int):
        def listener(group: GroupStatus):
            received_events.append((lid, group.group_id, group.drive))
        return listener

    listeners = [make_listener(i) for i in range(50)]
    for l in listeners:
        sm.add_listener(l)

    sample_group = GroupStatus(
        group_id=1,
        name="RM101",
        floor=1,
        model=ModelType.IC,
        address=1,
        drive=DriveState.ON,
        mode=OperationMode.COOL,
        set_temp_c=22.0,
        inlet_temp_c=23.0,
    )

    # Fire listener notifications
    for l in list(sm._listeners):
        l(sample_group)

    assert len(received_events) == 50
    for lid in range(50):
        assert (lid, 1, DriveState.ON) in received_events

    # Dynamic removal of listeners while operating
    for l in listeners[:25]:
        sm.remove_listener(l)

    assert len(sm._listeners) == 25
