"""The panel application id this integration installs, observes and updates.

ha-paneld changed its Android application id. A new id is a new app to
Android, and Panel Assistant 0.9.0 was the last release that moved a panel
from the old app to the new one. This release installs, observes and updates
only the new app (``PACKAGE_ID``); the old id is kept only so that a panel
still running it is recognised and refused (``repairs_upgrade.runs_old_app``),
never given the new app beside it, and so that records written before the
move still read.

Two things do not follow the application id, and getting either wrong names
something that does not exist on the panel:

* The manifest classes live in the Kotlin package, which is not the
  application id. Builds of the new id up to app 0.9.9 keep every class in
  ``io.github.maxlyth.hapaneld``; from 0.9.10 they live in
  ``io.panelassistant.android``. So the one application id names two
  different sets of classes, and a signed descriptor's ``launchComponent``
  says which set that exact build carries. Every other component is looked up
  from it, never from the application id. Android resolves the
  ``<id>/.Class`` shorthand against the *application id*, so the shorthand
  appears only where the class lives in the id's own package. Both installers
  write these components, so they are defined here and in the browser
  installer's ``app-identity.mjs``, and a test compares the two copies by
  value.
* The schema identifier strings moved to the new id with app 0.9.11 and this
  integration's 0.9.0, and every reader here accepts both spellings (see
  ``INSTALL_DESCRIPTOR_SCHEMAS`` below). The database compatibility pattern and
  the MQTT identifiers keep their spelling. None of them is derived from the
  application id.

Components are written out rather than computed so that a grep for the exact
string a panel will be sent finds it, and so that a change to the Android
namespace cannot silently rewrite them.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: The panel app's application id.
PACKAGE_ID = "io.panelassistant.android"
#: The old app's id: recognised only to refuse a panel still running it, and to
#: read the records an earlier release wrote about it. Nothing installs it.
LEGACY_PACKAGE_ID = "io.github.maxlyth.hapaneld"
#: The old app's one launcher, which install receipts stored by an earlier
#: release may name. It is read back, never sent to a panel.
LEGACY_LAUNCH_COMPONENT = "io.github.maxlyth.hapaneld/.MainActivity"

#: Signed release and record schemas. App releases from 0.9.11, and the records
#: this integration writes from 0.9.0, use the new id; app releases up to
#: 0.9.10, install jobs written before 0.9.0 and the signed build feed use
#: the legacy spelling. Readers accept every entry; writers use the first. Both
#: spellings are kept: they are byte-compared in signed documents and stored
#: records that still exist.
INSTALL_DESCRIPTOR_SCHEMAS: tuple[str, ...] = (
    "io.panelassistant.android.install.v1",
    "io.github.maxlyth.hapaneld.install.v1",
)
PROTOCOL_METADATA_SCHEMAS: tuple[str, ...] = (
    "io.panelassistant.android.protocol.v1",
    "io.github.maxlyth.hapaneld.protocol.v1",
)
BUILD_FEED_SCHEMAS: tuple[str, ...] = (
    "io.panelassistant.android.buildfeed.v1",
    "io.github.maxlyth.hapaneld.buildfeed.v1",
)
INSTALL_PLAN_SCHEMAS: tuple[str, ...] = (
    "io.panelassistant.android.install-plan.v1",
    "io.github.maxlyth.hapaneld.install-plan.v1",
)

#: The app's device admin, holding only the right to lock the screen: the
#: screen-off falls back to it where Android's screen timeout does not take.
#: Panel Assistant makes the new app id one at install and after each update.
DEVICE_ADMIN_RECEIVER = "io.panelassistant.android.platform.PanelDeviceAdminReceiver"

#: The receiver that makes the app give up its device admin, which Android
#: refuses to uninstall and the panel's shell cannot remove. Builds that have it
#: keep it under the new classes; one without it ignores the ask.
RELEASE_DEVICE_ADMIN_RECEIVER = (
    "io.panelassistant.android.platform.ReleaseDeviceAdminReceiver"
)

_OLD_CLASSES_SUCCESSOR_LAUNCH = (
    "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity"
)
_NEW_CLASSES_SUCCESSOR_LAUNCH = (
    "io.panelassistant.android/io.panelassistant.android.MainActivity"
)

#: Every launcher activity a signed build of the app may name, in the order
#: they shipped. Never derive one from the package id, and never replace the
#: one a descriptor names: a stage that recomputed it would start a class the
#: installed build does not have.
LAUNCH_COMPONENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {PACKAGE_ID: (_OLD_CLASSES_SUCCESSOR_LAUNCH, _NEW_CLASSES_SUCCESSOR_LAUNCH)}
)


def is_launch_component(package_id: object, component: object) -> bool:
    """Return whether a build of the app may name this launcher."""
    return package_id == PACKAGE_ID and component in LAUNCH_COMPONENTS[PACKAGE_ID]


#: The dashboard activity that answers HOME, by the build's launcher: the
#: activity a kiosk panel boots to lives in the same package as the launcher.
HOME_COMPONENTS: Mapping[str, str] = MappingProxyType(
    {
        _OLD_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/io.github.maxlyth.hapaneld.DashboardActivity"
        ),
        _NEW_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/io.panelassistant.android.DashboardActivity"
        ),
    }
)

#: Every spelling Android reads back for that HOME activity. HOME resolution
#: prints the short form where the class lives in the id's own package.
EQUIVALENT_HOME_COMPONENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        _OLD_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/io.github.maxlyth.hapaneld.DashboardActivity",
        ),
        _NEW_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/.DashboardActivity",
            "io.panelassistant.android/io.panelassistant.android.DashboardActivity",
        ),
    }
)

#: The accessibility service written into the device-wide list, by the build's
#: launcher.
ACCESSIBILITY_COMPONENTS: Mapping[str, str] = MappingProxyType(
    {
        _OLD_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/"
            "io.github.maxlyth.hapaneld.input.PanelAccessibilityService"
        ),
        _NEW_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/"
            "io.panelassistant.android.input.PanelAccessibilityService"
        ),
    }
)

#: Every spelling Android reads back for that enabled service: where the class
#: lives in the id's own package, both the shorthand and the full form name it.
EQUIVALENT_ACCESSIBILITY_COMPONENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        _OLD_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/"
            "io.github.maxlyth.hapaneld.input.PanelAccessibilityService",
        ),
        _NEW_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/.input.PanelAccessibilityService",
            "io.panelassistant.android/"
            "io.panelassistant.android.input.PanelAccessibilityService",
        ),
    }
)


#: The service class the app's 0.9.10 in-place update removed, by the launcher
#: that replaced it. Both installers drop it from the device-wide list.
OBSOLETE_ACCESSIBILITY_COMPONENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        _NEW_CLASSES_SUCCESSOR_LAUNCH: (
            ACCESSIBILITY_COMPONENTS[_OLD_CLASSES_SUCCESSOR_LAUNCH],
        ),
    }
)


def reports_package(reported: str | None, package_id: str) -> bool:
    """Return whether a panel reporting ``reported`` in health runs ``package_id``.

    The app always reports its own application id, so it is never assumed.
    Builds older than the move to the new id report no package at all, and
    every one of them is the old app.
    """
    if package_id == PACKAGE_ID:
        return reported == package_id
    return reported is None or reported == package_id
