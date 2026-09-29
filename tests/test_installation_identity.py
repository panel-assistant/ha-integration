"""Installation identity through discovery, polling, and administrator Repairs."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panel_assistant.client import parse_health_response
from custom_components.panel_assistant.const import DOMAIN

from .test_availability import STORED, _load, _poll
from .test_config_flow import _zeroconf_info
from .test_transport import DID, OTHER_DID, STATUS, _send


def _health(did: str, legacy: str = DID):
    return parse_health_response(
        f"ha-paneld 0.9.8-rc2 panel=alpha build=1000 cfg=1a2b3c4d "
        f"did={did} identity=install legacy_did={legacy}"
    )


async def test_changed_health_cannot_replace_a_panels_state(hass, hass_read_only_user):
    """A wrong panel at the saved endpoint does not become health authority."""
    entry = await _load(hass, hass_read_only_user.id)
    original = entry.runtime_data.coordinator.data
    await _poll(hass, entry, {STORED: _health(OTHER_DID)})
    assert entry.runtime_data.coordinator.last_update_success is False
    assert entry.runtime_data.coordinator.data == original
    assert entry.unique_id == DID


async def test_two_clones_create_separate_entries_and_sessions(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    """Two installations sharing one factory identity have independent controls."""
    from .test_transport import _hello

    entries = []
    for index, did in enumerate((DID, OTHER_DID)):
        health = _health(did, "c" * 64)
        with (
            patch(
                "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
                AsyncMock(return_value=health),
            ),
            patch(
                "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
                AsyncMock(return_value=STATUS),
            ),
        ):
            info = _zeroconf_info()
            from dataclasses import replace
            from ipaddress import ip_address

            info = replace(
                info,
                ip_address=ip_address(f"192.168.1.{23 + index}"),
                properties={**info.properties, "did": did},
            )
            form = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
            )
            assert form["type"] is FlowResultType.FORM
            result = await hass.config_entries.flow.async_configure(form["flow_id"], {})
            assert result["type"] is FlowResultType.CREATE_ENTRY
            entry = result["result"]
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "transport_user_id": hass_read_only_user.id}
            )
            await hass.async_block_till_done()
            entries.append(entry)
    assert len({entry.entry_id for entry in entries}) == 2
    for entry in entries:
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(
            client, _hello(did=entry.unique_id, protocol={"min": 3, "max": 3})
        )
        assert response["success"], response


async def test_repair_preserves_entry_device_and_entity_ids(hass: HomeAssistant):
    """Confirmation rekeys the existing registry records and rejects stale retry."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.panel_assistant.repairs import async_create_fix_flow

    entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", unique_id=DID, data={CONF_ADDRESS: STORED}
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}
    )
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{DID}_relay1",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="alpha_relay",
    )
    values = {
        "entry_id": entry.entry_id,
        "did": OTHER_DID,
        "legacy_did": DID,
        "address": STORED,
    }
    flow = await async_create_fix_flow(
        hass, f"panel_identity_confirmation_{entry.entry_id}", values
    )
    flow.hass = hass
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=_health(OTHER_DID)),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        shown = await flow.async_step_init()
        assert shown["type"] is FlowResultType.FORM
        result = await flow.async_step_confirm_identity({})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    updated = registry.async_get(entity.entity_id)
    assert updated.id == entity.id
    assert updated.device_id == device.id
    assert updated.unique_id == f"{OTHER_DID}_relay1"
    assert entry.unique_id == OTHER_DID
    assert entry.data["installation_identity"] is True
    assert (await flow.async_step_confirm_identity({}))["type"] is FlowResultType.ABORT


@pytest.mark.parametrize("conflict", ["entry", "entity", "changed"])
async def test_repair_rechecks_conflicts_and_the_shown_panel(
    hass: HomeAssistant, conflict
):
    from custom_components.panel_assistant.repairs import async_create_fix_flow

    entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", unique_id=DID, data={CONF_ADDRESS: STORED}
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "switch", DOMAIN, f"{DID}_relay1", config_entry=entry
    )
    values = {
        "entry_id": entry.entry_id,
        "did": OTHER_DID,
        "legacy_did": DID,
        "address": STORED,
    }
    flow = await async_create_fix_flow(hass, "panel_identity_confirmation_test", values)
    flow.hass = hass
    await flow.async_step_init()
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id=OTHER_DID if conflict == "entry" else "f" * 64,
        data={CONF_ADDRESS: "elsewhere.local"},
    )
    other.add_to_hass(hass)
    if conflict == "entity":
        registry.async_get_or_create(
            "switch", DOMAIN, f"{OTHER_DID}_relay1", config_entry=other
        )
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(
            return_value=_health("f" * 64 if conflict == "changed" else OTHER_DID)
        ),
    ):
        assert (await flow.async_step_confirm_identity({}))[
            "type"
        ] is FlowResultType.ABORT
    assert entry.unique_id == DID
    assert registry.async_get(entity.entity_id).unique_id == entity.unique_id


@pytest.mark.parametrize(
    "proof",
    [
        "exclusive",
        "merged",
        "deleted",
        "deleted_merged",
        "pruned",
        "shared_legacy",
        "previous_legacy",
    ],
)
async def test_automatic_upgrade_requires_surviving_exclusive_installation_proof(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token, proof
):
    from hashlib import sha256

    from homeassistant.helpers import device_registry as dr

    entry = await _load(hass, hass_read_only_user.id)
    uid = "a" * 32
    new_did = sha256(("panel-assistant-mdns-v1\0" + uid).encode()).hexdigest()
    mqtt = MockConfigEntry(domain="mqtt", data={})
    mqtt.add_to_hass(hass)
    identifiers = {("mqtt", "ha-paneld-alpha"), ("mqtt", f"ha-paneld-uid-{uid}")}
    if proof in ("merged", "deleted_merged"):
        identifiers.add(("mqtt", "ha-paneld-beta"))
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt.entry_id, identifiers=identifiers
    )
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "switch", DOMAIN, f"{DID}_relay1", config_entry=entry
    )
    data = {
        **entry.data,
        "installation_identity": False,
        "cutover": {
            "did": DID,
            "panel_id": "alpha",
            "state": "complete",
            "entities": {entity.id: {"mqtt_device_id": device.id}},
        },
        "supported_channels": {"did": DID, "channels": ["relay1"]},
    }
    hass.config_entries.async_update_entry(entry, data=data)
    if proof in ("deleted", "deleted_merged", "pruned"):
        dr.async_get(hass).async_remove_device(device.id)
        if proof == "pruned":
            from freezegun import freeze_time

            dr.async_get(hass).async_clear_config_entry(mqtt.entry_id)
            with freeze_time("2099-01-01"):
                dr.async_get(hass).async_purge_expired_orphaned_devices()
    if proof in ("shared_legacy", "previous_legacy"):
        other = MockConfigEntry(
            domain=DOMAIN,
            unique_id=DID if proof == "shared_legacy" else OTHER_DID,
            title="beta",
            data={
                CONF_ADDRESS: "other.local",
                **(
                    {
                        "previous_installation_identity": DID,
                        "installation_identity": True,
                    }
                    if proof == "previous_legacy"
                    else {}
                ),
            },
        )
        other.add_to_hass(hass)
        from custom_components.panel_assistant.identity import accept_health

        if proof == "shared_legacy":
            assert not accept_health(hass, other, _health(OTHER_DID))
    with patch.object(hass.config_entries, "async_reload", AsyncMock()):
        await _poll(hass, entry, {STORED: _health(new_did)})
        await hass.async_block_till_done()
    if proof in ("exclusive", "deleted"):
        assert entry.unique_id == new_did
        assert registry.async_get(entity.entity_id).id == entity.id
        assert registry.async_get(entity.entity_id).unique_id == f"{new_did}_relay1"
        assert entry.data["cutover"]["did"] == new_did
        assert entry.data["supported_channels"]["did"] == new_did
        from homeassistant.helpers import issue_registry as ir

        from .test_transport import _hello

        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN, f"panel_identity_confirmation_{entry.entry_id}"
            )
            is None
        )
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(client, _hello(did=new_did))
        assert response["success"], response
    else:
        assert entry.unique_id == DID
        assert registry.async_get(entity.entity_id).unique_id == f"{DID}_relay1"
        from homeassistant.helpers import issue_registry as ir

        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN, f"panel_identity_confirmation_{entry.entry_id}"
            )
            is not None
        )


@pytest.mark.parametrize("interrupted_after", [0, 1])
async def test_interrupted_confirmation_retries_without_duplicate_entities(
    hass, interrupted_after
):
    from custom_components.panel_assistant.repairs import async_create_fix_flow

    entry = MockConfigEntry(
        domain=DOMAIN, title="alpha", unique_id=DID, data={CONF_ADDRESS: STORED}
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    entities = [
        registry.async_get_or_create(
            "switch", DOMAIN, f"{DID}_relay{i}", config_entry=entry
        )
        for i in (1, 2)
    ]
    values = {
        "entry_id": entry.entry_id,
        "did": OTHER_DID,
        "legacy_did": DID,
        "address": STORED,
    }
    flow = await async_create_fix_flow(hass, "panel_identity_confirmation_test", values)
    flow.hass = hass
    update = registry.async_update_entity
    calls = 0

    def interrupted(entity_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls == interrupted_after + 1:
            raise RuntimeError("interrupted")
        return update(entity_id, **kwargs)

    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=_health(OTHER_DID)),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        with (
            patch.object(registry, "async_update_entity", interrupted),
            pytest.raises(RuntimeError, match="interrupted"),
        ):
            await flow.async_step_confirm_identity({})
        assert entry.unique_id == DID
        assert entry.data["identity_pending"]["did"] == OTHER_DID
        different = await async_create_fix_flow(
            hass, "panel_identity_confirmation_test", {**values, "did": "f" * 64}
        )
        different.hass = hass
        with patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=_health("f" * 64)),
        ):
            assert (await different.async_step_confirm_identity({}))[
                "type"
            ] is FlowResultType.ABORT
        assert entry.data["identity_pending"]["did"] == OTHER_DID
        assert (await flow.async_step_confirm_identity({}))[
            "type"
        ] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 2
    assert [registry.async_get(item.entity_id).id for item in entities] == [
        item.id for item in entities
    ]
    assert all(
        registry.async_get(item.entity_id).unique_id.startswith(OTHER_DID)
        for item in entities
    )
    assert "identity_pending" not in entry.data


async def test_legacy_clone_cannot_take_another_panels_session(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    """A clone's legacy did and shared account cannot redirect panel controls."""
    from custom_components.panel_assistant.transport import async_get_sessions

    from .test_transport import _hello

    entry = await _load(hass, hass_read_only_user.id, address="192.0.2.10")
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    client = await hass_ws_client(hass, hass_read_only_access_token)
    # Both endpoints still report the shared legacy identity: this is a clone,
    # not the original panel moving away from a dead address.
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(return_value=entry.runtime_data.coordinator.data.health),
    ):
        response = await _send(client, _hello(protocol={"min": 1, "max": 1}))
    assert response["success"] is False
    assert response["error"]["code"] == "unknown_panel"
    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert entry.data[CONF_ADDRESS] == "192.0.2.10"


async def test_confirmation_closes_old_session_before_rekey(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    from custom_components.panel_assistant.repairs import async_create_fix_flow

    from .test_transport import _hello, _receive

    entry = await _load(hass, hass_read_only_user.id, address="127.0.0.1")
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    client = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _send(client, _hello(protocol={"min": 1, "max": 1}))
    assert response["success"]
    token = response["result"]["session"]
    flow = await async_create_fix_flow(
        hass,
        "panel_identity_confirmation_test",
        {
            "entry_id": entry.entry_id,
            "did": OTHER_DID,
            "legacy_did": DID,
            "address": "127.0.0.1",
        },
    )
    flow.hass = hass
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=_health(OTHER_DID)),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        assert (await flow.async_step_confirm_identity({}))[
            "type"
        ] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    closed = await _receive(client)
    assert closed["event"]["kind"] == "session_closed"
    late = await _send(
        client,
        {
            "type": "panel_assistant/report_state",
            "session": token,
            "sync": "delta",
            "observations": [],
        },
    )
    assert late["error"]["code"] == "session_unknown"


async def test_restart_finishes_registry_save_that_lagged_identity_commit(
    hass, hass_read_only_user
):
    """Independent HA stores may save the entry before its entity rekeys."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=OTHER_DID,
        data={
            CONF_ADDRESS: STORED,
            "installation_identity": True,
            "previous_installation_identity": DID,
            "transport_user_id": hass_read_only_user.id,
        },
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch", DOMAIN, f"{DID}_relay1", config_entry=entry
    )
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=_health(OTHER_DID)),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant._async_reconcile_install_receipt",
            AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert registry.async_get(old.entity_id).id == old.id
    assert registry.async_get(old.entity_id).unique_id == f"{OTHER_DID}_relay1"


@pytest.mark.parametrize(
    "extra",
    [
        "identity=install identity=install",
        "identity=unknown",
        "legacy_did=bad",
        f"legacy_did={DID} legacy_did={OTHER_DID}",
    ],
)
def test_malformed_identity_hints_do_not_establish_authority(extra):
    from custom_components.panel_assistant.client import InvalidResponseError

    with pytest.raises(InvalidResponseError):
        parse_health_response(
            "ha-paneld 0.9.8-rc2 panel=alpha build=1000 cfg=1a2b3c4d "
            f"did={OTHER_DID} {extra}"
        )


@pytest.mark.parametrize(
    "peer,changed,accepted",
    [
        ("127.0.0.1", False, True),
        ("::ffff:127.0.0.1", False, True),
        ("192.0.2.10", False, False),
        ("127.0.0.1", True, False),
    ],
)
async def test_legacy_hostname_uses_only_the_verified_saved_http_peer(
    hass,
    hass_ws_client,
    hass_read_only_user,
    hass_read_only_access_token,
    peer,
    changed,
    accepted,
):
    from .test_transport import _hello

    entry = await _load(hass, hass_read_only_user.id)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    entry.runtime_data.client.health_peer = (
        str(entry.runtime_data.client.health_url),
        peer,
    )
    if changed:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ADDRESS: "changed.local"}
        )
    # A different peer cannot move an endpoint that still answers this DID.
    # Recovery now performs fresh health I/O instead of refusing every new peer.
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(
                return_value=parse_health_response(
                    f"ha-paneld 0.9.8-rc2 panel=alpha build=1000 cfg=1a2b3c4d did={DID}"
                )
            ),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
    ):
        client = await hass_ws_client(hass, hass_read_only_access_token)
        response = await _send(client, _hello(protocol={"min": 1, "max": 1}))
        assert response["success"] is accepted
        await hass.async_block_till_done()


async def test_discovery_mismatch_immediately_withdraws_existing_authority(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    from dataclasses import replace

    from custom_components.panel_assistant.transport import async_get_sessions

    from .test_transport import _hello, _receive

    entry = await _load(hass, hass_read_only_user.id, address="192.168.1.23")
    client = await hass_ws_client(hass, hass_read_only_access_token)
    assert (await _send(client, _hello()))["success"]
    info = _zeroconf_info()
    info = replace(info, properties={**info.properties, "did": OTHER_DID})
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(return_value=_health(OTHER_DID)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=info
        )
    assert result["type"] is FlowResultType.ABORT
    assert entry.unique_id == DID
    assert entry.runtime_data.coordinator.identity_mismatch
    assert not entry.runtime_data.coordinator.last_update_success
    assert async_get_sessions(hass).get(entry.entry_id) is None
    assert (await _receive(client))["event"]["kind"] == "session_closed"


async def test_restart_with_new_registry_and_old_entry_cannot_admit_old_session(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    from custom_components.panel_assistant.repairs import async_create_fix_flow

    from .test_transport import _hello

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="alpha",
        unique_id=DID,
        data={CONF_ADDRESS: "127.0.0.1", "transport_user_id": hass_read_only_user.id},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    moved = registry.async_get_or_create(
        "switch", DOMAIN, f"{OTHER_DID}_relay1", config_entry=entry
    )
    old_health = parse_health_response(
        f"ha-paneld 0.9.8-rc2 panel=alpha build=1000 cfg=1a2b3c4d did={DID}"
    )
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=old_health),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    response = await _send(client, _hello(protocol={"min": 1, "max": 1}))
    assert response["success"] is False
    assert response["error"]["code"] == "unknown_panel"
    wrong = await async_create_fix_flow(
        hass,
        "panel_identity_confirmation_wrong",
        {
            "entry_id": entry.entry_id,
            "did": "f" * 64,
            "legacy_did": DID,
            "address": "127.0.0.1",
        },
    )
    wrong.hass = hass
    with patch(
        "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
        AsyncMock(return_value=_health("f" * 64)),
    ):
        assert (await wrong.async_step_confirm_identity({}))[
            "type"
        ] is FlowResultType.ABORT
    assert entry.unique_id == DID
    assert registry.async_get(moved.entity_id).unique_id == f"{OTHER_DID}_relay1"
    flow = await async_create_fix_flow(
        hass,
        "panel_identity_confirmation_test",
        {
            "entry_id": entry.entry_id,
            "did": OTHER_DID,
            "legacy_did": DID,
            "address": "127.0.0.1",
        },
    )
    flow.hass = hass
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=_health(OTHER_DID)),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        assert (await flow.async_step_confirm_identity({}))[
            "type"
        ] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert registry.async_get(moved.entity_id).id == moved.id
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    assert entry.unique_id == OTHER_DID


async def test_old_address_entry_keeps_identity_from_its_owned_entities(
    hass, hass_read_only_user
):
    """Entries predating persisted did still retain their existing entity setup."""
    entry = await _load(hass, hass_read_only_user.id)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "switch", DOMAIN, f"{DID}_relay1", config_entry=entry
    )
    hass.config_entries.async_update_entry(
        entry, unique_id=None, data={**entry.data, "installation_identity": False}
    )
    await _poll(hass, entry, {STORED: _health(OTHER_DID)})
    assert entry.unique_id == DID
    assert registry.async_get(old.entity_id).unique_id == old.unique_id
    assert not entry.runtime_data.coordinator.last_update_success
    from homeassistant.helpers import issue_registry as ir

    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"panel_identity_confirmation_{entry.entry_id}"
        )
        is not None
    )


async def test_identity_repair_admits_the_panels_next_connection(
    hass, hass_ws_client, hass_read_only_user, hass_read_only_access_token
):
    """Confirmation admits the next hello without changing the account binding."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.panel_assistant.repairs import async_create_fix_flow
    from custom_components.panel_assistant.transport import async_get_sessions

    from .test_transport import _hello

    entry = await _load(hass, hass_read_only_user.id, address="127.0.0.1")
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "installation_identity": False}
    )
    await _poll(hass, entry, {"127.0.0.1": _health(OTHER_DID)})
    client = await hass_ws_client(hass, hass_read_only_access_token)
    refused = await _send(client, _hello(did=OTHER_DID))
    assert refused["error"]["code"] == "unknown_panel"
    assert async_get_sessions(hass).get(entry.entry_id) is None
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"panel_identity_confirmation_{entry.entry_id}"
    )
    flow = await async_create_fix_flow(hass, issue.issue_id, issue.data)
    flow.hass = hass
    await flow.async_step_init()
    with (
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_health",
            AsyncMock(return_value=_health(OTHER_DID)),
        ),
        patch(
            "custom_components.panel_assistant.client.HaPaneldClient.async_get_status",
            AsyncMock(return_value=STATUS),
        ),
        patch(
            "custom_components.panel_assistant.async_resume_loaded_install_jobs",
            AsyncMock(return_value=()),
        ),
        patch(
            "custom_components.panel_assistant._async_reconcile_install_receipt",
            AsyncMock(),
        ),
    ):
        assert (await flow.async_step_confirm_identity({}))[
            "type"
        ] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    accepted = await _send(client, _hello(did=OTHER_DID))
    assert accepted["success"], accepted
    assert async_get_sessions(hass).get(entry.entry_id).did == OTHER_DID
