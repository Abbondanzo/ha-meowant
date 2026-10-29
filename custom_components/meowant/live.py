"""Live work state and commands over the IM (MQTT) channel.

Runs alongside the REST coordinator: one MQTT connection per config entry,
kept up in the background with backoff. The box pushes its state on every
change (commands, and a clean finishing by itself); a slow all-state poll is
only a backstop for missed pushes and for the state after (re)connecting.

Live updates reach entities through a dispatcher signal, not the coordinator.
async_update_listeners() would re-fire the activity event for the last poll's
records, and async_set_updated_data() would keep pushing back the REST poll.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util.ssl import client_context

from .api import MeowantApiClient, MeowantApiError, MeowantAuthError, MeowantRateLimitError
from .const import (
    DOMAIN,
    LIVE_POLL_INTERVAL,
    LIVE_RETRY_MAX,
    LIVE_RETRY_MIN,
    LIVE_STABLE_AFTER,
    LIVE_STATE_MAX_AGE,
)
from .im import MeowantImSession
from .im_proto import OP_PAUSE, OP_RESUME, WORK_MODE_TO_OP, WorkState
from .mqtt import MqttAuthError, MqttError

_LOGGER = logging.getLogger(__name__)


class MeowantLive:
    """Keeps the IM connection up and tracks each box's latest work state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: MeowantApiClient,
        serials: Callable[[], list[str]],
        on_finished: Callable[[str], None],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._api = api
        self._serials = serials
        # Called with a box's serial when it goes from working back to idle.
        self._on_finished = on_finished
        self.signal = f"{DOMAIN}_{entry.entry_id}_live"
        self._session: MeowantImSession | None = None
        self._states: dict[str, tuple[WorkState, float]] = {}  # serial -> (state, monotonic time received)

    @property
    def connected(self) -> bool:
        return self._session is not None and self._session.connected

    def state(self, serial: str) -> WorkState | None:
        """The box's work state, or None if not connected or it hasn't answered lately."""
        entry = self._states.get(serial)
        if not self.connected or entry is None:
            return None
        state, received = entry
        return state if time.monotonic() - received < LIVE_STATE_MAX_AGE.total_seconds() else None

    def start(self) -> None:
        # Cancelled automatically when the entry unloads; _run closes the connection.
        self._entry.async_create_background_task(self._hass, self._run(), f"{DOMAIN} live connection")

    async def async_operate(self, serial: str, op_mode: int, op_state: int) -> None:
        session = self._session
        if session is None or not session.connected:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="live_not_connected")
        try:
            await session.send_operation(serial, op_mode, op_state)
        except (MeowantApiError, MqttError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_failed", translation_placeholders={"error": str(err)}
            ) from err
        # No follow-up state request needed: the box pushes its new state.

    async def async_pause(self, serial: str) -> None:
        await self.async_operate(serial, self._current_op(serial, "nothing_to_pause"), OP_PAUSE)

    async def async_resume(self, serial: str) -> None:
        await self.async_operate(serial, self._current_op(serial, "nothing_to_resume"), OP_RESUME)

    def _current_op(self, serial: str, error_key: str) -> int:
        """The op mode matching what the box is doing: pause/resume must name it."""
        state = self.state(serial)
        if state is None or (op := WORK_MODE_TO_OP.get(state.work_mode)) is None:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key=error_key)
        return op

    async def _run(self) -> None:
        delay = LIVE_RETRY_MIN.total_seconds()
        while True:
            session = MeowantImSession(self._api, ssl_context=client_context(), on_state=self._on_state)
            connected_at: float | None = None
            try:
                await session.connect()
                _LOGGER.debug("Live connection up")
                connected_at = time.monotonic()
                self._session = session
                self._notify()
                await self._serve(session)
            except (MeowantAuthError, MeowantRateLimitError, MqttAuthError) as err:
                # Retrying could mean more password logins toward the lockout. The
                # coordinator raises reauth for bad credentials; reload to retry.
                _LOGGER.warning("Live state and commands disabled: %s", err)
                return
            except (MeowantApiError, MqttError) as err:
                _LOGGER.info("Live connection unavailable, retrying in %d s: %s", delay, err)
            finally:
                self._session = None
                await session.close()
                self._notify()
            # Only a connection that held up resets the backoff. One that's dropped
            # right after connecting (e.g. another client taking over the session)
            # must not turn into a reconnect, and two REST calls, every 30 s.
            if connected_at is not None and time.monotonic() - connected_at >= LIVE_STABLE_AFTER.total_seconds():
                delay = LIVE_RETRY_MIN.total_seconds()
            await asyncio.sleep(delay)
            delay = min(delay * 2, LIVE_RETRY_MAX.total_seconds())

    async def _serve(self, session: MeowantImSession) -> None:
        """Poll state until the connection drops (raises MqttError) or is cancelled."""
        poller = asyncio.create_task(self._poll(session))
        try:
            await session.wait_closed()
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
        raise MqttError("Connection closed")

    async def _poll(self, session: MeowantImSession) -> None:
        while True:
            # Only boxes in the IM contacts: asking for one that isn't would make
            # the session re-fetch the contact list over REST on every poll.
            for serial in (s for s in self._serials() if s in session.contacts):
                try:
                    await session.request_state(serial)
                except (MeowantApiError, MqttError) as err:
                    _LOGGER.debug("State request for %s failed: %s", serial, err)
            # Lets entities notice states that went stale without a new reply.
            self._notify()
            await asyncio.sleep(LIVE_POLL_INTERVAL.total_seconds())

    @callback
    def _on_state(self, serial: str, state: WorkState) -> None:
        previous = self._states.get(serial)
        self._states[serial] = (state, time.monotonic())
        self._notify()
        if previous is not None and not previous[0].is_idle and state.is_idle:
            self._on_finished(serial)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self._hass, self.signal)

    def diagnostics(self) -> dict:
        now = time.monotonic()
        return {
            "connected": self.connected,
            "boxes": [
                {"serialNumber": serial, "age_s": round(now - received), **state.as_dict()}
                for serial, (state, received) in self._states.items()
            ],
        }
