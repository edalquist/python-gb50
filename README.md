# Python GB-50 Client Library (`gb50`)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Asynchronous Python client library for communicating with **Mitsubishi Electric GB-50**, **AG-150**, and **G-50** Central HVAC Controllers over LAN.

This library is pure async Python, designed to be embedded directly into **Home Assistant custom components**, IoT bridges, automation daemons, or CLI management tools with **zero web framework dependencies**.

---

## Features

- **Full Async / Non-Blocking**: Built on `aiohttp` and `asyncio`.
- **High-Speed Batch Telemetry**: Unpacks the proprietary 65-byte `Bulk` hex status payload to monitor up to 50 indoor units and LOSSNAYs in a single HTTP request.
- **Proprietary Cryptographic Engine**: Built-in DES / XOR cipher for hardware password authentication and privilege elevation (`UserList`, `admin`).
- **Real-Time State Manager & Polling Engine**: In-memory caching, delta state computation, and asynchronous pub-sub event listener callbacks.
- **Weekly & Daily Scheduling**: View and set daily timer events (`TodayList`) and 7-day weekly schedule patterns (`WPatternList`) with single-group or multi-group bitmasks.
- **Mitsubishi City Multi Diagnostics**: Full 4-digit error code knowledge base with plain-English causes, timestamps, outage durations, and troubleshooting steps.

---

## Installation

```bash
pip install gb50
```

Or install in editable development mode:
```bash
pip install -e ./python-gb50
```

---

## Quickstart: Basic Direct Client

```python
import asyncio
from gb50 import GB50Client, DriveState, OperationMode, GroupControlRequest

async def main():
    async with GB50Client(host="192.0.2.90") as client:
        # 1. Discover hardware and system info
        sys_info = await client.get_system_info()
        print(f"Connected to: {sys_info.system_name} ({sys_info.model})")

        # 2. Get all zones and live telemetry
        groups = await client.get_groups_telemetry()
        for g in groups:
            temp_f = g.inlet_temp_f or "N/A"
            set_f = g.set_temp_f or "N/A"
            print(f"[{g.group_id}] {g.name} ({g.model}): {g.drive} | Mode: {g.mode} | Room: {temp_f}°F | Set: {set_f}°F")

        # 3. Control a single zone
        await client.set_group(
            group_id=1,
            update=GroupControlRequest(
                drive=DriveState.ON,
                mode=OperationMode.HEAT,
                set_temp_f=70.0,
            ),
        )

        # 4. Batch control multiple zones simultaneously
        await client.set_groups_batch({
            1: GroupControlRequest(set_temp_f=70.0),
            2: GroupControlRequest(set_temp_f=70.0),
        })

asyncio.run(main())
```

---

## Real-Time State Manager & Event Subscriptions

The `StateManager` continuously polls the GB-50 controller (default: every 3 seconds), maintains an in-memory cache of all 50 groups, and triggers asynchronous callback listeners whenever unit states change:

```python
import asyncio
from gb50 import GB50Client, StateManager, GroupStatus

async def on_climate_change(group: GroupStatus):
    print(f"Zone {group.name} state updated: Drive={group.drive}, RoomTemp={group.inlet_temp_f}°F")

async def main():
    client = GB50Client(host="192.0.2.90")
    state_mgr = StateManager(client=client, poll_interval_sec=3.0)
    
    # Register subscriber
    state_mgr.add_listener(on_climate_change)
    
    # Start polling task in the background
    await state_mgr.start()
    
    try:
        # Let it run
        await asyncio.sleep(60)
    finally:
        await state_mgr.stop()
        await client.close()

asyncio.run(main())
```

---

## Home Assistant Integration Example

In a Home Assistant custom component (`custom_components/mitsubishi_gb50/climate.py`):

```python
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature, ATTR_TEMPERATURE
from gb50 import GB50Client, StateManager, DriveState, OperationMode, GroupControlRequest

async def async_setup_entry(hass, entry, async_add_entities):
    host = entry.data["host"]
    client = GB50Client(host=host)
    state_mgr = StateManager(client=client, poll_interval_sec=3.0)
    await state_mgr.start()

    entities = [GB50ClimateEntity(g, state_mgr) for g in state_mgr.groups.values()]
    async_add_entities(entities)

class GB50ClimateEntity(ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | 
        ClimateEntityFeature.FAN_MODE
    )

    def __init__(self, group, state_mgr: StateManager):
        self._group_id = group.group_id
        self._state_mgr = state_mgr
        self._attr_name = group.name
        self._attr_unique_id = f"gb50_{state_mgr.system_info.serial_number}_{group.group_id}"

    async def async_added_to_hass(self):
        # Register listener with StateManager to push updates immediately
        self._state_mgr.add_listener(self._on_state_update)

    async def _on_state_update(self, group):
        if group.group_id == self._group_id:
            self.async_write_ha_state()

    @property
    def current_temperature(self):
        return self._state_mgr.groups[self._group_id].inlet_temp_f

    @property
    def target_temperature(self):
        return self._state_mgr.groups[self._group_id].set_temp_f

    @property
    def hvac_mode(self):
        g = self._state_mgr.groups[self._group_id]
        if g.drive == DriveState.OFF:
            return HVACMode.OFF
        if g.mode == OperationMode.COOL:
            return HVACMode.COOL
        if g.mode == OperationMode.HEAT:
            return HVACMode.HEAT
        if g.mode == OperationMode.FAN:
            return HVACMode.FAN_ONLY
        return HVACMode.AUTO

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp:
            await self._state_mgr.client.set_group(
                self._group_id, 
                GroupControlRequest(set_temp_f=temp)
            )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        if hvac_mode == HVACMode.OFF:
            await self._state_mgr.client.set_group(
                self._group_id, 
                GroupControlRequest(drive=DriveState.OFF)
            )
        else:
            mode_map = {
                HVACMode.COOL: OperationMode.COOL,
                HVACMode.HEAT: OperationMode.HEAT,
                HVACMode.FAN_ONLY: OperationMode.FAN,
                HVACMode.AUTO: OperationMode.AUTO,
            }
            await self._state_mgr.client.set_group(
                self._group_id, 
                GroupControlRequest(drive=DriveState.ON, mode=mode_map.get(hvac_mode, OperationMode.AUTO))
            )
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
