"""Shared real-Store race scenarios for pytest and the native HA 2026.11 check."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.discovery_monitor.notifications import NotificationHistory


async def check_reset_and_restart(  # noqa: PLR0915 - explicit interleaving scenario
    *, in_flight: bool, emit_during_reset: bool, resets: int
) -> None:
    """Interleave real disk writes, resets and new timestamps, then restart HA."""
    with TemporaryDirectory(prefix="discovery-reset-") as directory:
        hass = HomeAssistant(directory)
        history = NotificationHistory(hass)
        await history.async_load()
        key, other_key = "a" * 64, "b" * 64
        writes_started = asyncio.Event()
        finish_write = asyncio.Event()
        reset_tasks: list[asyncio.Task[None]] = []
        pending: asyncio.Task[None] | None = None
        store = history._store
        real_write = store._async_write_data
        write_count = 0

        async def controlled_write(data: dict[str, Any]) -> None:
            nonlocal write_count
            # Freeze the delayed data before yielding, as the native Store does
            # before awaiting disk I/O. Keep the real Store lock and disk write.
            data = dict(data)
            if "data_func" in data:
                data["data"] = data.pop("data_func")()
            write_count += 1
            if write_count == 1:
                writes_started.set()
                await finish_write.wait()
            await real_write(data)

        try:
            with patch.object(store, "_async_write_data", side_effect=controlled_write):
                history.async_event_emitted(key)
                history.async_event_emitted(other_key)
                if in_flight:
                    pending = asyncio.create_task(
                        history._store._async_callback_delayed_write()
                    )
                    await asyncio.wait_for(writes_started.wait(), 5)
                for _ in range(resets):
                    reset_tasks.append(asyncio.create_task(history.async_reset()))
                    await asyncio.sleep(0)
                if not in_flight:
                    await asyncio.wait_for(writes_started.wait(), 5)
                assert history.last_emitted == {}
                assert any(not task.done() for task in reset_tasks)
                if emit_during_reset:
                    history.async_event_emitted(key)
                expected = dict(history.last_emitted)
                finish_write.set()
                await asyncio.gather(*reset_tasks, *([pending] if pending else []))
                # Execute a queued delayed callback, including a redundant old
                # callback: neither may restore data consumed before the reset.
                await history._store._async_callback_delayed_write()
                await history._store._async_callback_delayed_write()
                assert history.last_emitted == expected
        finally:
            finish_write.set()
            await asyncio.gather(*reset_tasks, *([pending] if pending else []))
            await hass.async_stop(force=True)

        # A new HA instance avoids Store's read cache and actually reads disk.
        disk_data = json.loads(await asyncio.to_thread(Path(store.path).read_text))
        assert disk_data["data"] == expected
        restarted = HomeAssistant(directory)
        try:
            restored = NotificationHistory(restarted)
            await restored.async_load()
            assert restored.last_emitted == expected
            assert restored.is_paused(key, 24) is emit_during_reset
            assert not restored.is_paused(other_key, 24)
        finally:
            await restarted.async_stop(force=True)
