"""One fleet log policy and one bounded delivery per panel, without a queue."""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from yarl import URL

from .const import DOMAIN
from .device import panel_display_name

if TYPE_CHECKING:
    from .transport_sessions import PanelSession

DATA_PANEL_LOGS = "panel_logs"
_LOGGER = logging.getLogger(__name__)
_LEVELS = {
    2: logging.DEBUG,
    3: logging.DEBUG,
    4: logging.INFO,
    5: logging.WARNING,
    6: logging.ERROR,
    7: logging.CRITICAL,
}
_SEVERITIES = {2: 7, 3: 7, 4: 6, 5: 4, 6: 3, 7: 2}


def validate_policy(value: dict[str, Any]) -> dict[str, Any]:
    """Validate persisted and administrator-supplied fleet policy alike."""
    sources = value.get("sources")
    if not isinstance(sources, dict) or any(
        source not in {"app", "system"}
        or type(level) is not int
        or level not in _LEVELS
        for source, level in sources.items()
    ):
        raise vol.Invalid("invalid log sources")
    destination = value.get("destination")
    if destination is not None:
        if not isinstance(destination, dict):
            raise vol.Invalid("invalid log destination")
        protocol = destination.get("protocol")
        if protocol == "home_assistant":
            destination = {"protocol": "home_assistant"}
        elif isinstance(protocol, str) and protocol in {"udp", "tcp", "http"}:
            host, port = destination.get("host"), destination.get("port")
            if (
                not isinstance(host, str)
                or not host.strip()
                or len(host) > 2048
                or any(ord(c) < 32 for c in host)
                or type(port) is not int
                or not 1 <= port <= 65535
            ):
                raise vol.Invalid("invalid log endpoint")
            destination = {"protocol": protocol, "host": host.strip(), "port": port}
            if protocol == "http":
                _http_url(destination)
            elif any(c in host for c in "/@?# "):
                raise vol.Invalid("expected a syslog host")
        else:
            raise vol.Invalid("unknown log destination")
    result: dict[str, Any] = {"destination": destination, "sources": dict(sources)}
    also_home_assistant = value.get("also_home_assistant", False)
    if type(also_home_assistant) is not bool:
        raise vol.Invalid("invalid Home Assistant log choice")
    if (
        also_home_assistant
        and destination
        and destination["protocol"] != "home_assistant"
    ):
        result["also_home_assistant"] = True
    return result


def _http_url(destination: dict[str, Any]) -> URL:
    host = destination["host"]
    try:
        url = URL(host) if "://" in host else URL.build(scheme="http", host=host)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.user
            or url.fragment
        ):
            raise ValueError
        return (
            url if url.explicit_port is not None else url.with_port(destination["port"])
        )
    except (ValueError, TypeError) as err:
        raise vol.Invalid("expected an HTTP endpoint") from err


class PanelLogRouter:
    """Store fleet settings; delivery tasks retain no queue or retry state."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.panel_logs")
        self.policy: dict[str, Any] = {"destination": None, "sources": {"app": 5}}
        self.busy: set[str] = set()

    async def async_load(self) -> None:
        """Restore the single policy before a panel can obtain a grant."""
        raw = await self.store.async_load()
        if raw is not None:
            try:
                self.policy = validate_policy(raw)
            except vol.Invalid, AttributeError:
                _LOGGER.warning("Ignoring invalid saved panel log policy")

    async def async_configure(self, policy: dict[str, Any]) -> None:
        """Save once for the fleet, then revoke old grants for renegotiation."""
        from .transport_sessions import async_get_sessions

        policy = validate_policy(policy)
        await self.store.async_save(policy)
        self.policy = deepcopy(policy)
        sessions = async_get_sessions(self.hass)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if (
                session := sessions.get(entry.entry_id)
            ) is not None and "logs" in session.capabilities:
                sessions.close(session, "log_policy_changed")

    @callback
    def sources(self) -> dict[str, int]:
        """An unset destination never asks a panel to send logs."""
        return dict(self.policy["sources"]) if self.policy["destination"] else {}

    @callback
    def saved_sinks(self) -> dict[str, dict[str, Any]]:
        """Offer only live granted panels' saved destinations while unset."""
        from .transport_sessions import async_get_sessions

        if self.policy["destination"] is not None:
            return {}
        sessions = async_get_sessions(self.hass)
        return {
            session.did: dict(session.saved_log_sink)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if (session := sessions.get(entry.entry_id)) is not None
            and "logs" in session.capabilities
            and session.saved_log_sink is not None
        }

    @callback
    def accept(self, session: PanelSession, msg: dict[str, Any]) -> None:
        """Claim custody synchronously so successive socket callbacks cannot queue."""
        connection = session.connection
        if "logs" not in session.capabilities:
            connection.send_error(
                msg["id"], "logs_not_granted", "Logs were not granted."
            )
            return
        if session.did in self.busy:
            connection.send_error(msg["id"], "log_busy", "A log batch is outstanding.")
            return
        self.busy.add(session.did)
        self.hass.async_create_task(
            self._deliver(session, msg, deepcopy(self.policy)),
            f"{DOMAIN} panel logs",
            eager_start=False,
        )

    def _current(self, session: PanelSession) -> bool:
        from .transport_sessions import async_get_sessions

        return (
            async_get_sessions(self.hass).for_request(session.token, session.connection)
            is session
        )

    async def _deliver(
        self,
        session: PanelSession,
        msg: dict[str, Any],
        policy: dict[str, Any],
    ) -> None:
        destination = policy["destination"]
        records = [
            record
            for record in msg["records"]
            if record["source"] in session.log_sources
            and record["priority"] >= session.log_sources[record["source"]]
        ]
        delivery_records = records
        if msg["dropped"]:
            delivery_records = [
                *records,
                {
                    "timestamp": dt_util.utcnow().isoformat(),
                    "source": "app",
                    "priority": 5,
                    "tag": "PanelLogs",
                    "pid": 0,
                    "message": (
                        f"Panel dropped {msg['dropped']} log records before this batch."
                    ),
                },
            ]
        try:
            async with asyncio.timeout(10):
                if not self._current(session):
                    return
                if delivery_records and destination is not None:
                    if destination["protocol"] == "home_assistant" or policy.get(
                        "also_home_assistant"
                    ):
                        self._write_ha(session, delivery_records)
                    if destination["protocol"] != "home_assistant":
                        await self._forward(session, destination, delivery_records)
            if self._current(session):
                session.connection.send_result(
                    msg["id"], {"accepted": len(records), "dropped": msg["dropped"]}
                )
        except Exception:
            # The target may include credentials; never log its URL or exception.
            if self._current(session):
                session.connection.send_error(
                    msg["id"],
                    "log_delivery_failed",
                    "The log destination did not accept this batch.",
                )
        finally:
            self.busy.discard(session.did)

    def _write_ha(self, session: PanelSession, records: list[dict[str, Any]]) -> None:
        logger = logging.getLogger(
            f"custom_components.panel_assistant.panel.{session.did}"
        )
        entry = self.hass.config_entries.async_get_entry(session.entry_id)
        name = (
            panel_display_name(self.hass, entry) if entry is not None else session.did
        )
        for item in records:
            level = _LEVELS[item["priority"]]
            if not logger.isEnabledFor(level):
                continue
            record = logger.makeRecord(
                logger.name,
                level,
                __file__,
                0,
                f"[{name}] {item['timestamp']} {item['source']} "
                f"{item['tag']}({item['pid']}): {item['message']}",
                (),
                None,
                extra={
                    f"panel_{key}": item[key]
                    for key in ("timestamp", "source", "tag", "pid")
                },
            )
            timestamp = dt_util.parse_datetime(item["timestamp"])
            assert timestamp is not None
            record.created = timestamp.timestamp()
            record.msecs = (record.created - int(record.created)) * 1000
            logger.handle(record)

    async def _forward(
        self,
        session: PanelSession,
        destination: dict[str, Any],
        records: list[dict[str, Any]],
    ) -> None:
        protocol = destination["protocol"]
        if protocol == "http":
            data = "".join(
                json.dumps(
                    {
                        "timestamp": item["timestamp"],
                        "host": session.did,
                        "app": "ha-paneld",
                        "versionCode": session.app_version_code,
                        "package": session.app_package,
                        "message": _forward_message(item),
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for item in records
            ).encode()
            async with async_get_clientsession(self.hass).post(
                _http_url(destination),
                data=data,
                headers={"Content-Type": "application/x-ndjson"},
                allow_redirects=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise OSError("log receiver refused")
            return
        frames = [_syslog(session, item).encode() for item in records]
        if protocol == "udp":
            transport, _ = await self.hass.loop.create_datagram_endpoint(
                asyncio.DatagramProtocol,
                remote_addr=(destination["host"], destination["port"]),
            )
            try:
                if self._current(session):
                    for frame in frames:
                        transport.sendto(frame)
            finally:
                transport.close()
        else:
            _, writer = await asyncio.open_connection(
                destination["host"], destination["port"]
            )
            try:
                if self._current(session):
                    writer.writelines(frames)
                    await writer.drain()
            finally:
                writer.close()
                async with asyncio.timeout(1):
                    await writer.wait_closed()


def _forward_message(item: dict[str, Any]) -> str:
    letter = {2: "V", 3: "D", 4: "I", 5: "W", 6: "E", 7: "F"}[item["priority"]]
    return f"{item['source']} {letter}/{item['tag']}({item['pid']}): {item['message']}"


def _syslog(session: PanelSession, item: dict[str, Any]) -> str:
    message = _forward_message(item)
    if "\n" in message or "\r" in message:
        message = (
            message.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
        )
    package = (
        session.app_package.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("]", "\\]")
    )
    return (
        f"<{8 + _SEVERITIES[item['priority']]}>1 {item['timestamp']} "
        f"{session.did} ha-paneld - - "
        f'[hapaneld@32473 versionCode="{session.app_version_code}" '
        f'package="{package}"] {message}\n'
    )


@callback
def async_get_log_router(hass: HomeAssistant) -> PanelLogRouter:
    """Return the installation-wide router initialized during domain setup."""
    router: PanelLogRouter = hass.data[DOMAIN][DATA_PANEL_LOGS]
    return router


async def async_setup_panel_logs(hass: HomeAssistant) -> None:
    """Load fleet settings before the transport accepts hello."""
    router = PanelLogRouter(hass)
    await router.async_load()
    hass.data.setdefault(DOMAIN, {})[DATA_PANEL_LOGS] = router
