"""Wake words imported once in the sidebar and installed on every panel.

An administrator imports a microWakeWord model on one panel's Configure page in
the sidebar. That request passes through the relay (``ui_relay.py``), and once
that panel has installed it, so its wake-word engine has accepted it, Panel
Assistant keeps the model and installs it on every other panel with a
microphone, and on each panel that connects later. A panel reports the
SHA-256 of each imported model it holds in its voice configuration, so Panel
Assistant sends a model only to a panel that does not already hold that copy.

The model moves as a blob the panel fetches and checks against its size and
SHA-256. A panel that holds a model of its owner's own under the same name
keeps it: only a copy Panel Assistant sent before (named in ``replaces``) is
replaced. A panel that cannot take the model is tried again on its next
session; a refusal the owner must act on is a Repair, anything else a warning.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .blob import async_get_blobs
from .const import DOMAIN
from .transport_protocol import OUTCOME_APPLIED
from .transport_sessions import (
    PanelSession,
    async_get_sessions,
    async_manage_outcome,
    session_manages,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

DATA_SHARED_WAKE_WORDS: Final = "shared_wake_words"
STORAGE_KEY: Final = f"{DOMAIN}.wake_words"
STORAGE_VERSION: Final = 1
# The panel's Configure import, relayed from the sidebar.
OP_RELAYED_IMPORT: Final = "voice.wake_words.set"
# What Panel Assistant asks each other panel.
OP_SHARED_IMPORT: Final = "voice.wake_words.import"
IMPORT_TIMEOUT: Final = 60.0
MODEL_TYPE: Final = "application/octet-stream"
# The panel's own bounds (WakeWordCatalog); the panel checks them again.
MAX_MANIFEST_BYTES: Final = 16 * 1024
MAX_MODEL_BYTES: Final = 2 * 1024 * 1024
WAKE_WORD_ID: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Earlier copies a newer model of the same name may replace on a panel.
MAX_EARLIER: Final = 8
ISSUE_PREFIX: Final = "wake_word"
ISSUE_KEPT: Final = "wake_word_kept"
ISSUE_REFUSED: Final = "wake_word_refused"
CODE_UNKNOWN_COMMAND: Final = "unknown_command"
CODE_INVALID_VALUE: Final = "invalid_value"


@dataclass(frozen=True, slots=True)
class SharedWakeWord:
    """One model Panel Assistant installs on every panel."""

    id: str
    phrase: str
    manifest: str
    sha256: str
    # Copies sent before under this id, which this one may replace.
    earlier: tuple[str, ...] = ()


def issue_id(entry_id: str, wake_word_id: str) -> str:
    """One issue per panel and shared wake word."""
    return f"{ISSUE_PREFIX}_{entry_id}_{wake_word_id}"


class SharedWakeWords:
    """The shared models, their files, and each panel's convergence on them."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start with nothing loaded."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # Beside the index Store, never at its own path.
        self._models = Path(hass.config.path(".storage", f"{DOMAIN}.wake_word_models"))
        self._words: dict[str, SharedWakeWord] | None = None
        self._load_lock = asyncio.Lock()
        # Entries converging now, and those asked again meanwhile.
        self._running: set[str] = set()
        self._again: set[str] = set()
        # Per entry, the session tried and the models it did not take: each
        # session gets one attempt per model.
        self._tried: dict[str, tuple[PanelSession, set[str]]] = {}

    async def _async_words(self) -> dict[str, SharedWakeWord]:
        async with self._load_lock:
            if self._words is None:
                data = await self._store.async_load()
                self._words = {}
                for item in data.get("words", []) if isinstance(data, dict) else []:
                    try:
                        word = SharedWakeWord(
                            id=item["id"],
                            phrase=item["phrase"],
                            manifest=item["manifest"],
                            sha256=item["sha256"],
                            earlier=tuple(item.get("earlier", ())),
                        )
                    except KeyError, TypeError:
                        continue
                    if WAKE_WORD_ID.match(word.id):
                        self._words[word.id] = word
            return self._words

    def _model_path(self, wake_word_id: str) -> Path:
        return self._models / f"{wake_word_id}.tflite"

    async def async_record(
        self, source_entry_id: str, args: dict[str, Any], result: Any
    ) -> None:
        """Keep a model one panel installed from the sidebar, then share it.

        The panel it was imported on already holds it, so it is not asked.
        """
        wake_word_id = result.get("id") if isinstance(result, dict) else None
        phrase = result.get("wake_word") if isinstance(result, dict) else None
        manifest = args.get("manifest")
        encoded = args.get("model")
        if (
            not isinstance(wake_word_id, str)
            or not WAKE_WORD_ID.match(wake_word_id)
            or not isinstance(phrase, str)
            or not isinstance(manifest, str)
            or len(manifest.encode()) > MAX_MANIFEST_BYTES
            or not isinstance(encoded, str)
        ):
            return
        try:
            model = base64.b64decode(encoded, validate=True)
        except binascii.Error:
            return
        if not 0 < len(model) <= MAX_MODEL_BYTES:
            return
        sha256 = hashlib.sha256(model).hexdigest()
        words = await self._async_words()
        held = words.get(wake_word_id)
        if held is not None and (held.sha256, held.manifest) == (sha256, manifest):
            return
        earlier: tuple[str, ...] = ()
        if held is not None:
            earlier = tuple(
                dict.fromkeys(
                    item for item in (*held.earlier, held.sha256) if item != sha256
                )
            )[-MAX_EARLIER:]
        await self._hass.async_add_executor_job(
            _write_atomically, self._model_path(wake_word_id), model
        )
        words[wake_word_id] = SharedWakeWord(
            wake_word_id, phrase, manifest, sha256, earlier
        )
        await self._store.async_save(
            {
                "words": [
                    {
                        "id": word.id,
                        "phrase": word.phrase,
                        "manifest": word.manifest,
                        "sha256": word.sha256,
                        "earlier": list(word.earlier),
                    }
                    for word in words.values()
                ]
            }
        )
        _LOGGER.info("Wake word %s is now installed on every panel", wake_word_id)
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id != source_entry_id:
                self.converge(entry.entry_id)

    @callback
    def converge(self, entry_id: str) -> None:
        """Bring one panel to the shared models, in the background."""
        if entry_id in self._running:
            self._again.add(entry_id)
            return
        self._running.add(entry_id)
        self._hass.async_create_background_task(
            self._async_converge(entry_id), f"{DOMAIN} shared wake words"
        )

    async def _async_converge(self, entry_id: str) -> None:
        try:
            while True:
                self._again.discard(entry_id)
                await self._async_converge_once(entry_id)
                if entry_id not in self._again:
                    return
        finally:
            self._running.discard(entry_id)

    async def _async_converge_once(self, entry_id: str) -> None:
        session = async_get_sessions(self._hass).get(entry_id)
        voice = session.voice if session is not None else None
        if session is None or voice is None or not session_manages(session):
            return
        words = await self._async_words()
        if not words:
            return
        tried_session, tried = self._tried.get(entry_id, (None, set()))
        if tried_session is not session:
            tried = set()
            self._tried[entry_id] = (session, tried)
        reported = {word.id: word.sha256 for word in voice.wake_words}
        for word in words.values():
            if word.id in reported:
                held = reported[word.id]
                if held == word.sha256:
                    ir.async_delete_issue(
                        self._hass, DOMAIN, issue_id(entry_id, word.id)
                    )
                    continue
                if held is None:
                    # A bundled wake word of that name, or an app that does not
                    # report what it holds: nothing to compare, so nothing sent.
                    continue
                if held not in word.earlier:
                    self._raise(entry_id, word, ISSUE_KEPT)
                    continue
            if word.sha256 in tried:
                continue
            tried.add(word.sha256)
            if not await self._async_send(session, word):
                return

    async def _async_send(self, session: PanelSession, word: SharedWakeWord) -> bool:
        """Ask the panel to install one model; False when it can take none now."""
        entry_id = session.entry_id
        try:
            model = await self._hass.async_add_executor_job(
                self._model_path(word.id).read_bytes
            )
        except OSError:
            _LOGGER.warning("The shared wake word %s could not be read", word.id)
            return True
        _LOGGER.debug(
            "Installing the shared wake word %s on panel %s", word.id, entry_id
        )
        blobs = async_get_blobs(self._hass)
        token, reference = blobs.download(entry_id, model, MODEL_TYPE)
        try:
            outcome = await async_manage_outcome(
                self._hass,
                session,
                OP_SHARED_IMPORT,
                IMPORT_TIMEOUT,
                args={
                    "name": word.id,
                    "manifest": word.manifest,
                    "blob": reference,
                    "replaces": list(word.earlier),
                },
            )
        except HomeAssistantError:
            _LOGGER.warning(
                "Panel %s did not answer while installing the shared wake word %s;"
                " it is tried again when it next connects",
                entry_id,
                word.id,
            )
            return False
        finally:
            blobs.discard(token)
        if outcome.outcome == OUTCOME_APPLIED:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id(entry_id, word.id))
            return True
        if outcome.code == CODE_UNKNOWN_COMMAND:
            _LOGGER.warning(
                "Panel %s cannot take shared wake words until its app is updated",
                entry_id,
            )
            return False
        reason = (
            outcome.result.get("error") if isinstance(outcome.result, dict) else None
        )
        if outcome.code == CODE_INVALID_VALUE and isinstance(reason, str):
            self._raise(entry_id, word, ISSUE_REFUSED, reason[:200])
            return True
        _LOGGER.warning(
            "Panel %s could not install the shared wake word %s (%s);"
            " it is tried again when it next connects",
            entry_id,
            word.id,
            outcome.code or outcome.outcome,
        )
        return True

    def _raise(
        self, entry_id: str, word: SharedWakeWord, key: str, reason: str = ""
    ) -> None:
        entry = self._hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return
        from .device import panel_display_name

        placeholders = {
            "panel": panel_display_name(self._hass, entry),
            "wake_word": word.phrase,
        }
        if key == ISSUE_REFUSED:
            placeholders["reason"] = reason
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id(entry_id, word.id),
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders=placeholders,
            data={"entry_id": entry_id},
        )


def _write_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    with temporary.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def async_get_shared_wake_words(hass: HomeAssistant) -> SharedWakeWords:
    """Return the domain's shared wake words."""
    data = hass.data.setdefault(DOMAIN, {})
    shared: SharedWakeWords | None = data.get(DATA_SHARED_WAKE_WORDS)
    if shared is None:
        shared = data[DATA_SHARED_WAKE_WORDS] = SharedWakeWords(hass)
    return shared


@callback
def async_delete_wake_word_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Withdraw a panel's issues when it is unloaded or removed."""
    prefix = f"{ISSUE_PREFIX}_{entry.entry_id}_"
    for domain, key in list(ir.async_get(hass).issues):
        if domain == DOMAIN and key.startswith(prefix):
            ir.async_delete_issue(hass, DOMAIN, key)
