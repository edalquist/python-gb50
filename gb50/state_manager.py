"""State manager with background polling, in-memory caching, presets, and pub-sub listener broadcasting."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Set, Any, Callable, Coroutine
from datetime import datetime

from .client import GB50Client
from .models import GroupStatus, SystemInfo, GroupControlRequest, ScheduleItem, AlarmRecord
from .constants import DriveState, OperationMode, FanSpeed, AirDirection, ModelType

logger = logging.getLogger("gb50.state_manager")


class StateManager:
    """Manages cached controller state, quick presets, and real-time subscriber updates."""

    def __init__(self, client: GB50Client, poll_interval_sec: float = 3.0) -> None:
        self.client = client
        self.poll_interval = poll_interval_sec
        self._system_info: Optional[SystemInfo] = None
        self._groups_cache: Dict[int, GroupStatus] = {}
        self._listeners: Set[Callable[[GroupStatus], Any]] = set()
        self._raw_subscribers: Set[Any] = set()
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start background polling worker."""
        self._running = True
        logger.info(f"Starting GB-50 state manager (target: {self.client.host}, poll: {self.poll_interval}s)")
        
        # Initial synchronous fetch
        try:
            self._system_info = await self.client.get_system_info()
            groups = await self.client.get_all_groups(refresh_topology=True)
            async with self._lock:
                for g in groups:
                    self._groups_cache[g.group_id] = g
            logger.info(f"Initial discovery loaded {len(groups)} groups from {self._system_info.system_name}")
        except Exception as ex:
            logger.error(f"Initial GB-50 state discovery failed: {ex}")

        self._poll_task = asyncio.create_task(self._poll_loop(), name="gb50_poller")

    async def stop(self) -> None:
        """Stop background polling worker."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("GB-50 state manager stopped")

    @property
    def system_info(self) -> Optional[SystemInfo]:
        return self._system_info

    @property
    def groups(self) -> Dict[int, GroupStatus]:
        return dict(self._groups_cache)

    async def get_system_info(self) -> SystemInfo:
        """Fetch or return cached controller system info."""
        if self._system_info is None:
            self._system_info = await self.client.get_system_info()
        return self._system_info

    async def get_all_groups(self, refresh: bool = False) -> List[GroupStatus]:
        """Fetch or return cached group status list."""
        if refresh or not self._groups_cache:
            groups = await self.client.get_all_groups(refresh_topology=refresh)
            async with self._lock:
                for g in groups:
                    self._groups_cache[g.group_id] = g
        return list(self._groups_cache.values())

    async def get_group(self, group_id: int) -> GroupStatus:
        """Fetch or return cached group status for a single group."""
        if group_id not in self._groups_cache:
            telemetry = await self.client.get_groups_telemetry([group_id])
            if telemetry:
                async with self._lock:
                    self._groups_cache[group_id] = telemetry[0]
        return self._groups_cache[group_id]

    def add_listener(self, callback: Callable[[GroupStatus], Any]) -> None:
        """Register a callback invoked when an HVAC zone state updates."""
        self._listeners.add(callback)

    def remove_listener(self, callback: Callable[[GroupStatus], Any]) -> None:
        """Unregister a state change listener."""
        self._listeners.discard(callback)

    def add_subscriber(self, ws: Any) -> None:
        """Register a raw WebSocket or client for broadcast JSON events."""
        self._raw_subscribers.add(ws)

    def remove_subscriber(self, ws: Any) -> None:
        """Unregister a raw WebSocket client."""
        self._raw_subscribers.discard(ws)

    def register_ws(self, ws: Any) -> None:
        """Register a WebSocket connection."""
        self._raw_subscribers.add(ws)

    def unregister_ws(self, ws: Any) -> None:
        """Unregister a WebSocket connection."""
        self._raw_subscribers.discard(ws)

    async def get_schedule(self, group_id: int) -> List[ScheduleItem]:
        """Fetch today's scheduled events for a group."""
        return await self.client.get_today_schedule(group_id)

    async def get_alarms(self) -> List[AlarmRecord]:
        """Fetch active system alarms and error history."""
        return await self.client.get_alarms()

    async def control_group(self, group_id: int, request: GroupControlRequest) -> GroupStatus:
        """Mutate a single group and trigger instant local cache update + broadcast."""
        await self.client.set_group(group_id, request)
        updated_list = await self.client.get_groups_telemetry([group_id])
        if updated_list:
            updated = updated_list[0]
            async with self._lock:
                self._groups_cache[group_id] = updated
            await self._broadcast_update([updated])
            return updated
        return self._groups_cache[group_id]

    async def control_batch(self, updates: Dict[int, GroupControlRequest]) -> List[GroupStatus]:
        """Batch mutate multiple groups in one call."""
        return await self.control_groups_batch(updates)

    async def control_groups_batch(self, updates: Dict[int, GroupControlRequest]) -> List[GroupStatus]:
        """Batch mutate multiple groups in one call."""
        await self.client.set_groups_batch(updates)
        updated_list = await self.client.get_groups_telemetry(list(updates.keys()))
        async with self._lock:
            for g in updated_list:
                self._groups_cache[g.group_id] = g
        await self._broadcast_update(updated_list)
        return updated_list

    async def rename_group(self, group_id: int, new_name: str) -> GroupStatus:
        """Rename group display name."""
        await self.client.set_group_name(group_id, new_name)
        groups = await self.client.get_all_groups(refresh_topology=True)
        async with self._lock:
            for g in groups:
                self._groups_cache[g.group_id] = g
        updated = self._groups_cache.get(group_id)
        if updated:
            await self._broadcast_update([updated])
            return updated
        return await self.get_group(group_id)

    async def reset_filter(self, group_id: int) -> GroupStatus:
        """Clear dirty air filter flag."""
        await self.client.reset_filter(group_id)
        updated_list = await self.client.get_groups_telemetry([group_id])
        if updated_list:
            updated = updated_list[0]
            async with self._lock:
                self._groups_cache[group_id] = updated
            await self._broadcast_update([updated])
            return updated
        return self._groups_cache[group_id]

    async def apply_preset(self, preset_name: str) -> List[GroupStatus]:
        """Apply batch presets to indoor and ventilation units."""
        updates: Dict[int, GroupControlRequest] = {}
        all_ids = list(self._groups_cache.keys())

        if preset_name in ("all_on", "sunday"):
            for gid, g in self._groups_cache.items():
                if g.model == ModelType.IC:
                    updates[gid] = GroupControlRequest(
                        drive=DriveState.ON,
                        mode=OperationMode.AUTO,
                        set_temp_f=70.0,
                        fan_speed=FanSpeed.AUTO,
                    )
                elif g.model == ModelType.LC:
                    updates[gid] = GroupControlRequest(
                        drive=DriveState.ON,
                        fan_speed=FanSpeed.AUTO,
                    )
        elif preset_name == "all_off":
            for gid in all_ids:
                updates[gid] = GroupControlRequest(drive=DriveState.OFF)
        elif preset_name == "occupied":
            for gid, g in self._groups_cache.items():
                if g.model == ModelType.IC:
                    updates[gid] = GroupControlRequest(
                        drive=DriveState.ON,
                        mode=OperationMode.AUTO,
                        set_temp_f=70.0,
                    )
        elif preset_name in ("unoccupied", "night"):
            for gid, g in self._groups_cache.items():
                if g.model == ModelType.IC:
                    updates[gid] = GroupControlRequest(
                        drive=DriveState.OFF,
                        set_temp_f=64.0,
                    )
        elif preset_name == "office":
            for gid, g in self._groups_cache.items():
                if getattr(g, 'floor', 1) == 1:
                    updates[gid] = GroupControlRequest(
                        drive=DriveState.ON,
                        mode=OperationMode.AUTO,
                        set_temp_f=71.0,
                    )
                else:
                    updates[gid] = GroupControlRequest(drive=DriveState.OFF)
        else:
            raise ValueError(f"Unknown preset: {preset_name}")

        return await self.control_batch(updates)

    async def poll_now(self) -> List[GroupStatus]:
        """Force an immediate poll cycle, refresh topology/groups, and broadcast updates."""
        try:
            new_groups = await self.client.get_all_groups(refresh_topology=True)
            async with self._lock:
                self._groups_cache = {g.group_id: g for g in new_groups}
            if new_groups:
                await self._broadcast_update(new_groups)
            return new_groups
        except Exception as ex:
            logger.warning(f"Error during poll_now: {ex}")
            return list(self._groups_cache.values())

    async def _poll_loop(self) -> None:
        """Continuous background polling loop."""
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                new_groups = await self.client.get_groups_telemetry()
                
                changed_groups: List[GroupStatus] = []
                async with self._lock:
                    for g in new_groups:
                        prev = self._groups_cache.get(g.group_id)
                        if prev is None or self._has_changed(prev, g):
                            self._groups_cache[g.group_id] = g
                            changed_groups.append(g)
                
                if changed_groups:
                    await self._broadcast_update(changed_groups)

            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.warning(f"Error during GB-50 background polling: {ex}")
                await asyncio.sleep(2.0)

    def _has_changed(self, a: GroupStatus, b: GroupStatus) -> bool:
        return (
            a.drive != b.drive or
            a.mode != b.mode or
            a.set_temp_c != b.set_temp_c or
            a.inlet_temp_c != b.inlet_temp_c or
            a.fan_speed != b.fan_speed or
            a.air_direction != b.air_direction or
            a.filter_dirty != b.filter_dirty or
            a.error_active != b.error_active
        )

    async def _broadcast_update(self, groups: List[GroupStatus]) -> None:
        """Broadcast state updates to callback listeners and raw WebSocket connections."""
        # 1. Fire programmatic listeners
        for listener in list(self._listeners):
            for g in groups:
                try:
                    res = listener(g)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                except Exception as e:
                    logger.error(f"Error in state listener: {e}")

        # 2. Fire WebSocket subscribers
        if not self._raw_subscribers:
            return

        payload = {
            "type": "groups_update",
            "timestamp": datetime.now().isoformat(),
            "groups": [g.model_dump() for g in groups],
        }

        dead_subs = set()
        for ws in self._raw_subscribers:
            try:
                await ws.send_json(payload)
            except Exception:
                dead_subs.add(ws)

        for ws in dead_subs:
            self._raw_subscribers.discard(ws)
