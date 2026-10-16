"""Tests for the private durable install job store and its secure reader.

These tests pin how the install job manager loads, writes and verifies its
private Home Assistant Store file: loads and post-save checks use only the
secure reader, cancelled or failing saves drain and fail closed, a backward
clock or revision overflow never reaches the writer, corrupt documents are
refused, and the direct reader refuses broad modes, links, hardlinks, inode or
metadata drift, duplicate keys and excessive files. They share the receipt
helpers and Store emulation of tests/test_install_jobs.py, which they import
from there as the sibling install modules do.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import threading
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.panel_assistant import install_jobs
from custom_components.panel_assistant.const import DOMAIN
from custom_components.panel_assistant.install_jobs import (
    InstallJobManager,
    InstallJobReceipt,
    InstallJobRevisionError,
    InstallJobStoreError,
    InstallPhase,
)
from tests.test_install_jobs import (
    _REAL_DURABLE_JOBS_READER,
    _REAL_PARSE_STORE_DOCUMENT,
    _REAL_STORE_PRESENCE,
    Clock,
    create,
    durable_receipt,
    durable_store_document,
    emulate_home_assistant_store_file,
    target,
    transition_to_staging,
    write_durable_store,
)

__all__ = ["emulate_home_assistant_store_file"]


async def test_existing_store_load_uses_secure_reader_only(
    hass: HomeAssistant,
) -> None:
    """An existing Store is loaded from exact fd-bound bytes, never async_load."""
    receipt = durable_receipt()
    store = MagicMock()
    store.path = "/secure/install_jobs"
    store.async_load = AsyncMock(side_effect=AssertionError("unsafe Store read"))
    detail_store = MagicMock()
    detail_store.path = "/secure/install_jobs.details"

    with (
        patch.object(install_jobs, "Store", side_effect=[store, detail_store]),
        patch.object(
            install_jobs,
            "_store_presence",
            side_effect=lambda path: (path == store.path, False),
        ),
        patch.object(
            install_jobs,
            "_read_durable_jobs",
            return_value={receipt.job_id: receipt},
        ) as reader,
    ):
        loaded = await InstallJobManager(hass, now=Clock()).async_list()

    assert loaded == (receipt,)
    assert reader.call_count == 1
    store.async_load.assert_not_awaited()


async def test_absent_store_loads_empty_without_a_path_read(
    hass: HomeAssistant,
) -> None:
    """A genuinely absent non-corrupt Store retains new-install semantics."""
    store = MagicMock()
    store.path = "/secure/install_jobs"
    store.async_load = AsyncMock(side_effect=AssertionError("unsafe Store read"))

    with (
        patch.object(install_jobs, "Store", return_value=store),
        patch.object(install_jobs, "_store_presence", return_value=(False, False)),
        patch.object(install_jobs, "_read_durable_jobs") as reader,
    ):
        loaded = await InstallJobManager(hass, now=Clock()).async_list()

    assert loaded == ()
    reader.assert_not_called()
    store.async_load.assert_not_awaited()


async def test_post_save_verification_uses_secure_reader_only(
    hass: HomeAssistant,
) -> None:
    """Store remains the writer while exact fd-bound bytes verify its result."""
    persisted: dict[str, InstallJobReceipt] = {}
    writer = MagicMock()
    writer.path = "/secure/install_jobs"
    writer.async_load = AsyncMock(side_effect=AssertionError("unsafe Store read"))
    detail_store = MagicMock()
    detail_store.path = "/secure/install_jobs.details"
    primary_presence = iter(((False, False), (True, False)))

    async def save(document: dict[str, Any]) -> None:
        persisted.update(install_jobs._parse_document(document))

    writer.async_save = AsyncMock(side_effect=save)

    with (
        patch.object(install_jobs, "Store", side_effect=[writer, detail_store]),
        patch.object(
            install_jobs,
            "_store_presence",
            side_effect=lambda path: (
                next(primary_presence) if path == writer.path else (False, False)
            ),
        ),
        patch.object(
            install_jobs, "_read_durable_jobs", side_effect=lambda _path: persisted
        ) as reader,
    ):
        receipt, created = await create(InstallJobManager(hass, now=Clock()))

    assert created
    assert persisted == {receipt.job_id: receipt}
    assert reader.call_count == 1
    writer.async_save.assert_awaited_once()
    writer.async_load.assert_not_awaited()


async def test_swallowed_store_write_failure_fails_closed(hass: HomeAssistant) -> None:
    """A save is never accepted without a matching secure fd-bound read."""
    writer = MagicMock()
    writer.path = "/not/read/by/this/test"
    writer.async_save = AsyncMock(return_value=None)
    detail_store = MagicMock()
    detail_store.path = "/not/read/by/this/test.details"
    primary_presence = iter(((False, False), (True, False)))

    with (
        patch.object(install_jobs, "Store", side_effect=[writer, detail_store]),
        patch.object(
            install_jobs,
            "_store_presence",
            side_effect=lambda path: (
                next(primary_presence) if path == writer.path else (False, False)
            ),
        ),
        patch.object(install_jobs, "_read_durable_jobs", return_value={}) as reader,
        pytest.raises(InstallJobStoreError),
    ):
        await create(InstallJobManager(hass, now=Clock()))

    assert reader.call_count == 1


async def test_cancelled_save_drains_before_a_new_writer_can_start(
    hass: HomeAssistant,
) -> None:
    """Cancellation cannot release receipt authority ahead of Store's writer."""
    persisted: dict[str, InstallJobReceipt] = {}
    persisted_lock = threading.Lock()
    release_old_writer = threading.Event()
    release_presence = threading.Event()
    release_readback = threading.Event()
    old_writer_started = asyncio.Event()
    new_writer_started = asyncio.Event()
    post_save_presence_started = threading.Event()
    post_save_readback_started = threading.Event()
    old_writer_finished = threading.Event()
    blocked_presence = False
    blocked_readback = False
    writes: list[dict[str, InstallJobReceipt]] = []
    writer = MagicMock()
    writer.path = "/secure/install_jobs"

    def commit(
        snapshot: dict[str, InstallJobReceipt],
        release: threading.Event | None,
    ) -> None:
        if release is not None and not release.wait(timeout=5):
            raise RuntimeError("timed out waiting to release old writer")
        with persisted_lock:
            persisted.clear()
            persisted.update(snapshot)
        if release is not None:
            old_writer_finished.set()

    async def save(document: dict[str, Any]) -> None:
        snapshot = install_jobs._parse_document(copy.deepcopy(document))
        writes.append(snapshot)
        if len(writes) == 1:
            old_writer_started.set()
            release = release_old_writer
        else:
            new_writer_started.set()
            release = None
        await hass.async_add_executor_job(commit, snapshot, release)

    def presence(_path: str) -> tuple[bool, bool]:
        nonlocal blocked_presence
        if _path.endswith(".details"):
            return False, False
        if old_writer_finished.is_set() and not blocked_presence:
            blocked_presence = True
            post_save_presence_started.set()
            if not release_presence.wait(timeout=5):
                raise RuntimeError("timed out waiting to release store presence")
        with persisted_lock:
            return bool(persisted), False

    def read(_path: str) -> dict[str, InstallJobReceipt]:
        nonlocal blocked_readback
        if not blocked_readback:
            blocked_readback = True
            post_save_readback_started.set()
            if not release_readback.wait(timeout=5):
                raise RuntimeError("timed out waiting to release store readback")
        with persisted_lock:
            return persisted.copy()

    writer.async_save = AsyncMock(side_effect=save)
    detail_store = MagicMock()
    detail_store.path = "/secure/install_jobs.details"
    first_task: asyncio.Task[tuple[InstallJobReceipt, bool]] | None = None
    second_task: asyncio.Task[tuple[InstallJobReceipt, bool]] | None = None
    try:
        with (
            patch.object(
                install_jobs,
                "Store",
                side_effect=lambda _hass, _version, key, **_kwargs: (
                    writer if key == f"{DOMAIN}.install_jobs" else detail_store
                ),
            ),
            patch.object(install_jobs, "_store_presence", side_effect=presence),
            patch.object(install_jobs, "_read_durable_jobs", side_effect=read),
            patch.object(
                hass,
                "async_add_executor_job",
                side_effect=lambda target, *args: asyncio.to_thread(target, *args),
            ),
        ):
            manager = InstallJobManager(hass, now=Clock())
            first_task = asyncio.create_task(create(manager))
            await asyncio.wait_for(old_writer_started.wait(), timeout=1)

            first_task.cancel()
            await asyncio.sleep(0)
            first_task.cancel()
            await asyncio.sleep(0)
            assert not first_task.done()

            second_task = asyncio.create_task(
                create(
                    manager,
                    install_target=target(
                        "panel-two.local", "SERIAL-2", "192.168.1.24"
                    ),
                )
            )
            await asyncio.sleep(0)
            assert not new_writer_started.is_set()
            assert len(writes) == 1

            release_old_writer.set()
            assert await asyncio.to_thread(post_save_presence_started.wait, 1)
            first_task.cancel()
            await asyncio.sleep(0)
            assert not first_task.done()
            assert not new_writer_started.is_set()

            release_presence.set()
            assert await asyncio.to_thread(post_save_readback_started.wait, 1)
            first_task.cancel()
            await asyncio.sleep(0)
            assert not first_task.done()
            assert not new_writer_started.is_set()

            release_readback.set()
            with pytest.raises(asyncio.CancelledError):
                await first_task
            newest, created = await asyncio.wait_for(second_task, timeout=1)

            assert created
            assert new_writer_started.is_set()
            assert len(writes) == 2
            durable = await InstallJobManager(hass, now=Clock()).async_list()
            assert newest in durable
            assert len(durable) == 2
            with persisted_lock:
                assert persisted == writes[-1]
    finally:
        release_old_writer.set()
        release_presence.set()
        release_readback.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def test_queued_cancel_never_reaches_store_writer(hass: HomeAssistant) -> None:
    """A caller cancelled behind the manager lock cannot enqueue a Store write."""
    release_writer = asyncio.Event()
    writer_started = asyncio.Event()
    persisted: dict[str, InstallJobReceipt] = {}
    writes: list[dict[str, InstallJobReceipt]] = []
    writer = MagicMock()
    writer.path = "/secure/install_jobs"
    detail_store = MagicMock()
    detail_store.path = "/secure/install_jobs.details"

    async def save(document: dict[str, Any]) -> None:
        snapshot = install_jobs._parse_document(copy.deepcopy(document))
        writes.append(snapshot)
        writer_started.set()
        await release_writer.wait()
        persisted.clear()
        persisted.update(snapshot)

    writer.async_save = AsyncMock(side_effect=save)
    with (
        patch.object(
            install_jobs,
            "Store",
            side_effect=lambda _hass, _version, key, **_kwargs: (
                writer if key == f"{DOMAIN}.install_jobs" else detail_store
            ),
        ),
        patch.object(
            install_jobs,
            "_store_presence",
            side_effect=lambda path: (
                (bool(persisted), False) if path == writer.path else (False, False)
            ),
        ),
        patch.object(
            install_jobs,
            "_read_durable_jobs",
            side_effect=lambda _path: persisted.copy(),
        ),
    ):
        manager = InstallJobManager(hass, now=Clock())
        first_task = asyncio.create_task(create(manager))
        await asyncio.wait_for(writer_started.wait(), timeout=1)
        queued_task = asyncio.create_task(
            create(
                manager,
                install_target=target("panel-two.local", "SERIAL-2", "192.168.1.24"),
            )
        )
        await asyncio.sleep(0)
        assert manager._lock._waiters is not None  # type: ignore[attr-defined]
        assert any(
            not waiter.done()
            for waiter in manager._lock._waiters  # type: ignore[attr-defined]
        )
        queued_task.cancel()
        queued_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued_task
        assert len(writes) == 1

        release_writer.set()
        first, created = await asyncio.wait_for(first_task, timeout=1)
        assert created
        assert (await InstallJobManager(hass, now=Clock()).async_list()) == (first,)
        assert len(writes) == 1


async def test_cancelled_claimed_save_blocks_barrier_and_clears_claim(
    hass: HomeAssistant,
) -> None:
    """A barrier cannot pass a cancelled write or retain its executor claim."""
    manager = InstallJobManager(hass, now=Clock())
    staging = await transition_to_staging(manager, (await create(manager))[0].job_id)
    assert manager._claimed_jobs == {
        staging.job_id: staging.executor_generation,
    }
    real_save = manager._store.async_save
    writer_started = asyncio.Event()
    release_writer = asyncio.Event()

    async def save(document: dict[str, Any]) -> None:
        writer_started.set()
        await release_writer.wait()
        await real_save(document)

    manager._store.async_save = AsyncMock(side_effect=save)  # type: ignore[method-assign]
    save_task = asyncio.create_task(
        manager.async_request_cancel(staging.job_id, staging.revision)
    )
    await asyncio.wait_for(writer_started.wait(), timeout=1)
    save_task.cancel()
    await asyncio.sleep(0)
    save_task.cancel()
    await asyncio.sleep(0)
    assert not save_task.done()

    barrier_task = asyncio.create_task(
        manager.async_verify_mutation_barrier(
            staging.job_id, staging.revision, InstallPhase.STAGING
        )
    )
    await asyncio.sleep(0)
    assert not barrier_task.done()
    assert manager._lock._waiters is not None  # type: ignore[attr-defined]
    assert any(
        not waiter.done()
        for waiter in manager._lock._waiters  # type: ignore[attr-defined]
    )

    release_writer.set()
    with pytest.raises(asyncio.CancelledError):
        await save_task
    assert manager._claimed_jobs == {}
    with pytest.raises(InstallJobRevisionError):
        await barrier_task

    durable = await InstallJobManager(hass, now=Clock()).async_get(staging.job_id)
    assert durable.revision == staging.revision + 1
    assert durable.cancel_requested


async def test_cancelled_failing_save_is_drained_and_invalidates_authority(
    hass: HomeAssistant,
) -> None:
    """Repeated cancellation retrieves a late writer error before lock release."""
    release_writer = asyncio.Event()
    writer_started = asyncio.Event()
    writer = MagicMock()
    writer.path = "/secure/install_jobs"

    async def save(_document: dict[str, Any]) -> None:
        writer_started.set()
        await release_writer.wait()
        raise RuntimeError("late writer failure")

    writer.async_save = AsyncMock(side_effect=save)
    with (
        patch.object(install_jobs, "Store", return_value=writer),
        patch.object(install_jobs, "_store_presence", return_value=(False, False)),
        patch.object(install_jobs, "_read_durable_jobs") as reader,
    ):
        manager = InstallJobManager(hass, now=Clock())
        task = asyncio.create_task(create(manager))
        await asyncio.wait_for(writer_started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        release_writer.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task

    assert manager._jobs is None
    assert manager._claimed_jobs == {}
    assert isinstance(raised.value.__cause__, RuntimeError)
    reader.assert_not_called()


async def test_stopping_core_cannot_defer_a_receipt_write(
    hass: HomeAssistant,
) -> None:
    """The manager never lets Store queue authority for final-write time."""
    manager = InstallJobManager(hass, now=Clock())
    receipt, _ = await create(manager)
    hass.set_state(CoreState.stopping)

    with pytest.raises(InstallJobStoreError):
        await manager.async_request_cancel(receipt.job_id, receipt.revision)

    assert manager._jobs is None
    assert manager._claimed_jobs == {}
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    durable = await InstallJobManager(hass, now=Clock()).async_get(receipt.job_id)
    assert durable == receipt


async def test_shutdown_background_cancellation_cannot_detach_store_writer(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
) -> None:
    """Core classifies Store's in-flight executor writer as tracked work."""
    manager = InstallJobManager(hass, now=Clock())
    receipt, _ = await create(manager)
    writer_started = threading.Event()
    release_writer = threading.Event()
    save_task: asyncio.Task[InstallJobReceipt] | None = None

    def write(document: dict[str, Any]) -> None:
        writer_started.set()
        if not release_writer.wait(timeout=5):
            raise RuntimeError("timed out waiting to release Store writer")
        hass_storage[f"{DOMAIN}.install_jobs"] = copy.deepcopy(document)

    async def write_data(document: dict[str, Any]) -> None:
        await hass.async_add_executor_job(write, document)

    manager._store._async_write_data = write_data  # type: ignore[method-assign]
    tracked_before = set(hass._tasks)
    background_before = set(hass._background_tasks)
    try:
        save_task = asyncio.create_task(
            manager.async_request_cancel(receipt.job_id, receipt.revision)
        )
        assert await asyncio.to_thread(writer_started.wait, 1)

        added_tracked = hass._tasks - tracked_before
        added_background = hass._background_tasks - background_before
        writer_futures = {
            future
            for future in added_tracked | added_background
            if not isinstance(future, asyncio.Task)
        }
        assert len(writer_futures) == 1

        for future in added_background:
            future.cancel("Home Assistant is stopping")
        hass.set_state(CoreState.stopping)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert not save_task.done()
        assert manager._lock.locked()
        assert writer_futures <= added_tracked
        assert not any(future.cancelled() for future in writer_futures)

        release_writer.set()
        updated = await asyncio.wait_for(save_task, timeout=1)
        assert updated.cancel_requested
        durable = await InstallJobManager(hass, now=Clock()).async_get(receipt.job_id)
        assert durable == updated
    finally:
        release_writer.set()
        hass.set_state(CoreState.running)
        if save_task is not None and not save_task.done():
            save_task.cancel()
            await asyncio.gather(save_task, return_exceptions=True)


async def test_backward_clock_is_rejected_before_store_write(
    hass: HomeAssistant,
) -> None:
    """A timestamp regression after an update cannot replace its receipt."""
    clock = Clock()
    manager = InstallJobManager(hass, now=clock)
    receipt, _ = await create(manager)
    clock.advance(timedelta(minutes=10))
    receipt = await manager.async_claim(receipt.job_id, receipt.revision)
    assert manager._claimed_jobs == {
        receipt.job_id: receipt.executor_generation,
    }
    clock.advance(timedelta(minutes=-5))
    writer = AsyncMock()
    manager._store.async_save = writer  # type: ignore[method-assign]

    with pytest.raises(InstallJobStoreError):
        await manager.async_transition(
            receipt.job_id, receipt.revision, InstallPhase.AUTHORIZING
        )

    writer.assert_not_awaited()
    assert manager._jobs is None
    assert manager._claimed_jobs == {}
    durable = await InstallJobManager(hass, now=Clock()).async_get(receipt.job_id)
    assert durable == receipt


async def test_revision_overflow_is_rejected_before_store_write(
    hass: HomeAssistant,
) -> None:
    """The maximum durable revision cannot wrap through a claim save."""
    receipt, _ = await create(InstallJobManager(hass, now=Clock()))
    raw_store: Store[dict[str, Any]] = Store(
        hass, 1, f"{DOMAIN}.install_jobs", private=True, atomic_writes=True
    )
    document = await raw_store.async_load()
    assert document is not None
    document["jobs"][0]["revision"] = 2**63 - 1
    await raw_store.async_save(document)
    maximum = replace(receipt, revision=2**63 - 1)

    manager = InstallJobManager(hass, now=Clock())
    writer = AsyncMock()
    manager._store.async_save = writer  # type: ignore[method-assign]
    with pytest.raises(InstallJobStoreError):
        await manager.async_claim(maximum.job_id, maximum.revision)

    writer.assert_not_awaited()
    assert manager._jobs is None
    assert manager._claimed_jobs == {}
    durable = await InstallJobManager(hass, now=Clock()).async_get(maximum.job_id)
    assert durable == maximum


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(extra=True),
        lambda document: document.update(format="future"),
        lambda document: document["jobs"][0].update(job_id="not-opaque"),
        lambda document: document["jobs"][0].update(result_code="raw traceback"),
        lambda document: document["jobs"][0]["artifact"].update(
            download_url="https://example.invalid/secret"
        ),
        lambda document: document["jobs"][0]["artifact"].update(
            apk_size=64 * 1024 * 1024 + 1
        ),
        lambda document: document["jobs"][0]["target"].update(address="HTTP://x"),
        lambda document: document["jobs"][0]["target"].update(
            pinned_address="panel.local"
        ),
        lambda document: document["jobs"][0]["target"].update(pinned_address="8.8.8.8"),
        lambda document: document["jobs"][0]["target"].update(
            pinned_address="192.168.1.23:9999"
        ),
        lambda document: document["jobs"][0]["target"].update(
            pinned_address="[fd00:0:0:0:0:0:0:1]"
        ),
        lambda document: document["jobs"][0].update(attempt=1),
        lambda document: document["jobs"][0].update(actual_apk_bytes=12_345),
        lambda document: document["jobs"][0].update(plan_sha256="c" * 64),
    ],
)
async def test_corrupt_or_excessive_stored_documents_fail_closed(
    hass: HomeAssistant, mutation
) -> None:
    """The versioned schema is closed and every retained value is bounded."""
    manager = InstallJobManager(hass, now=Clock())
    await create(manager)
    store: Store[dict[str, Any]] = Store(
        hass, 1, f"{DOMAIN}.install_jobs", private=True, atomic_writes=True
    )
    document = await store.async_load()
    assert document is not None
    mutation(document)
    await store.async_save(document)

    with pytest.raises(InstallJobStoreError):
        await InstallJobManager(hass, now=Clock()).async_list()


def test_store_file_must_be_small_regular_and_owner_only(tmp_path: Path) -> None:
    """Receipt persistence refuses broad modes, links, and excessive files."""
    store_path = tmp_path / "install-jobs"
    store_path.write_text("{}", encoding="utf-8")
    store_path.chmod(0o600)
    assert _REAL_STORE_PRESENCE(str(store_path)) == (True, False)

    hardlink_path = tmp_path / "install-jobs-hardlink"
    os.link(store_path, hardlink_path)
    with pytest.raises(InstallJobStoreError):
        _REAL_STORE_PRESENCE(str(store_path))
    hardlink_path.unlink()

    with (
        patch.object(install_jobs.os, "geteuid", return_value=os.geteuid() + 1),
        pytest.raises(InstallJobStoreError),
    ):
        _REAL_STORE_PRESENCE(str(store_path))

    store_path.chmod(0o644)
    with pytest.raises(InstallJobStoreError):
        _REAL_STORE_PRESENCE(str(store_path))

    store_path.unlink()
    os.symlink(tmp_path / "missing", store_path)
    with pytest.raises(InstallJobStoreError):
        _REAL_STORE_PRESENCE(str(store_path))

    store_path.unlink()
    store_path.write_bytes(b"x" * (128 * 1024 + 1))
    store_path.chmod(0o600)
    with pytest.raises(InstallJobStoreError):
        _REAL_STORE_PRESENCE(str(store_path))


def test_durable_job_reader_repeatedly_binds_one_private_store_inode(
    tmp_path: Path,
) -> None:
    """Repeated reads return only the exact validated Store receipt bytes."""
    receipt = durable_receipt()
    store_path = tmp_path / "panel_assistant.install_jobs"
    write_durable_store(store_path, durable_store_document(receipt))

    for _ in range(3):
        assert _REAL_DURABLE_JOBS_READER(str(store_path)) == {receipt.job_id: receipt}


def test_durable_job_reader_rejects_hardlinked_store_file(tmp_path: Path) -> None:
    """An alias cannot retain usable receipt authority after Store replacement."""
    store_path = tmp_path / "panel_assistant.install_jobs"
    write_durable_store(store_path)
    os.link(store_path, tmp_path / "install-jobs-hardlink")

    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))


def test_durable_job_reader_rejects_link_count_drift_during_read(
    tmp_path: Path,
) -> None:
    """A new alias invalidates the opened receipt file without other drift."""
    store_path = tmp_path / "panel_assistant.install_jobs"
    write_durable_store(store_path)
    actual = os.stat(store_path)

    def metadata(link_count: int) -> SimpleNamespace:
        return SimpleNamespace(
            st_dev=actual.st_dev,
            st_ino=actual.st_ino,
            st_mode=actual.st_mode,
            st_uid=actual.st_uid,
            st_nlink=link_count,
            st_size=actual.st_size,
            st_mtime_ns=actual.st_mtime_ns,
            st_ctime_ns=actual.st_ctime_ns,
        )

    before = metadata(1)
    linked = metadata(2)
    with (
        patch.object(install_jobs.os, "fstat", side_effect=[before, linked, linked]),
        patch.object(install_jobs.os, "lstat", side_effect=[linked, linked]),
        pytest.raises(InstallJobStoreError),
    ):
        _REAL_DURABLE_JOBS_READER(str(store_path))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda document: document.update(extra=True),
        lambda document: document.update(version=True),
        lambda document: document.update(version=2),
        lambda document: document.update(minor_version=False),
        lambda document: document.update(minor_version=2),
        lambda document: document.update(key="other"),
        lambda document: document.update(data={}),
    ],
)
def test_durable_job_reader_rejects_invalid_store_wrapper(
    tmp_path: Path, mutation
) -> None:
    """Direct reads preserve Home Assistant Store identity and version checks."""
    document = durable_store_document()
    mutation(document)
    store_path = tmp_path / "panel_assistant.install_jobs"
    write_durable_store(store_path, document)

    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))


@pytest.mark.parametrize("duplicate_field", ["version", "job_id"])
def test_durable_job_reader_rejects_duplicate_json_keys(
    tmp_path: Path, duplicate_field: str
) -> None:
    """Duplicate wrapper and nested receipt keys cannot override trusted bytes."""
    body = json.dumps(durable_store_document(), separators=(",", ":"))
    if duplicate_field == "version":
        body = body.replace('"version":1', '"version":1,"version":1', 1)
    else:
        body = body.replace(
            '"job_id":"11111111111111111111111111111111"',
            (
                '"job_id":"11111111111111111111111111111111",'
                '"job_id":"11111111111111111111111111111111"'
            ),
            1,
        )
    store_path = tmp_path / "panel_assistant.install_jobs"
    store_path.write_text(body, encoding="utf-8")
    store_path.chmod(0o600)

    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))


def test_durable_job_reader_rejects_path_replacement_during_read(
    tmp_path: Path,
) -> None:
    """An atomic same-content replacement cannot authorize a stale inode."""
    store_path = tmp_path / "panel_assistant.install_jobs"
    replacement_path = tmp_path / "replacement"
    write_durable_store(store_path)
    write_durable_store(replacement_path)
    real_read = os.read
    replaced = False

    def replace_after_read(file_fd: int, size: int) -> bytes:
        nonlocal replaced
        chunk = real_read(file_fd, size)
        if chunk and not replaced:
            replaced = True
            os.replace(replacement_path, store_path)
        return chunk

    with (
        patch.object(install_jobs.os, "read", side_effect=replace_after_read),
        pytest.raises(InstallJobStoreError),
    ):
        _REAL_DURABLE_JOBS_READER(str(store_path))

    assert replaced


def test_durable_job_reader_rejects_same_inode_overwrite_during_read(
    tmp_path: Path,
) -> None:
    """A same-size overwrite cannot authorize bytes from unstable metadata."""
    document = durable_store_document()
    original_body = json.dumps(document).encode("utf-8")
    replacement = copy.deepcopy(document)
    replacement["minor_version"] = 2
    replacement_body = json.dumps(replacement).encode("utf-8")
    assert len(original_body) == len(replacement_body)
    store_path = tmp_path / "panel_assistant.install_jobs"
    store_path.write_bytes(original_body)
    store_path.chmod(0o600)
    real_read = os.read
    overwritten = False

    def overwrite_after_read(file_fd: int, size: int) -> bytes:
        nonlocal overwritten
        chunk = real_read(file_fd, size)
        if chunk and not overwritten:
            overwritten = True
            overwrite_fd = os.open(store_path, os.O_WRONLY | os.O_TRUNC)
            try:
                os.write(overwrite_fd, replacement_body)
                os.fsync(overwrite_fd)
            finally:
                os.close(overwrite_fd)
        return chunk

    with (
        patch.object(install_jobs.os, "read", side_effect=overwrite_after_read),
        pytest.raises(InstallJobStoreError),
    ):
        _REAL_DURABLE_JOBS_READER(str(store_path))

    assert overwritten


def test_durable_job_reader_rejects_metadata_drift_after_parse(
    tmp_path: Path,
) -> None:
    """The descriptor and path metadata must remain stable through parsing."""
    store_path = tmp_path / "panel_assistant.install_jobs"
    write_durable_store(store_path)
    parsed = False

    def parse_then_change_mode(body: bytes) -> dict[str, InstallJobReceipt]:
        nonlocal parsed
        jobs = _REAL_PARSE_STORE_DOCUMENT(body)
        parsed = True
        store_path.chmod(0o400)
        return jobs

    with (
        patch.object(
            install_jobs,
            "_parse_store_document",
            side_effect=parse_then_change_mode,
        ),
        pytest.raises(InstallJobStoreError),
    ):
        _REAL_DURABLE_JOBS_READER(str(store_path))

    assert parsed


def test_durable_job_reader_rejects_foreign_or_nonregular_files(
    tmp_path: Path,
) -> None:
    """Only a current-UID regular file can supply mutation authority."""
    store_path = tmp_path / "panel_assistant.install_jobs"
    write_durable_store(store_path)
    with (
        patch.object(install_jobs.os, "geteuid", return_value=os.geteuid() + 1),
        pytest.raises(InstallJobStoreError),
    ):
        _REAL_DURABLE_JOBS_READER(str(store_path))

    store_path.unlink()
    os.mkfifo(store_path, mode=0o600)
    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))


@pytest.mark.parametrize("mode", [0o000, 0o400, 0o640])
def test_durable_job_reader_rejects_nonprivate_or_missing_file(
    tmp_path: Path, mode: int
) -> None:
    """Mutation authority requires one present owner-readable 0600 file."""
    store_path = tmp_path / "panel_assistant.install_jobs"
    write_durable_store(store_path)
    store_path.chmod(mode)
    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))

    store_path.unlink()
    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))


def test_durable_job_reader_rejects_symlink_and_excessive_file(
    tmp_path: Path,
) -> None:
    """Links and unbounded JSON cannot become receipt mutation authority."""
    target_path = tmp_path / "target"
    write_durable_store(target_path)
    store_path = tmp_path / "panel_assistant.install_jobs"
    store_path.symlink_to(target_path)
    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))

    store_path.unlink()
    store_path.write_bytes(b" " * (128 * 1024 + 1))
    store_path.chmod(0o600)
    with pytest.raises(InstallJobStoreError):
        _REAL_DURABLE_JOBS_READER(str(store_path))
