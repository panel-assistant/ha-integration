"""The panel application ids this integration accepts while the identity moves.

ha-paneld is changing its Android application id. A new application id is a new
app to Android, so a panel crosses over by running both packages for one
handover rather than by an in-place update. Both ids are therefore installable,
observable and updatable panel apps at the same time, and every verifier here
accepts the pair rather than one constant.

Two things do not move with the application id, and getting either wrong names
something that does not exist on the panel:

* The manifest classes live in the Kotlin package, which is not the
  application id. Builds up to app 0.9.9 keep every class in
  ``io.github.maxlyth.hapaneld``; from 0.9.10 the new app's classes live in
  ``io.panelassistant.android``. So one application id can name two different
  sets of classes, and a signed descriptor's ``launchComponent`` says which set
  that exact build carries. Every other component is looked up from it, never
  from the application id. Android resolves the ``<id>/.Class`` shorthand
  against the *application id*, so the shorthand appears only where the class
  lives in the id's own package. Both installers write these components, so
  they are defined here and in the browser installer's ``app-identity.mjs``,
  and a test compares the two copies by value.
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

LEGACY_PACKAGE_ID = "io.github.maxlyth.hapaneld"
SUCCESSOR_PACKAGE_ID = "io.panelassistant.android"

#: Both installable panel application ids, legacy first. Order is part of the
#: contract: preflight reports the legacy package first so an operator reading a
#: failure sees the package a migrating panel actually has.
ACCEPTED_PACKAGE_IDS: tuple[str, ...] = (LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID)

#: Signed release and record schemas. App releases from 0.9.11, and the records
#: this integration writes from 0.9.0, use the new id; app releases up to
#: 0.9.10, install jobs written before 0.9.0 and the signed build feed use
#: the legacy spelling. Readers accept every entry; writers use the first. The
#: legacy spellings go in 1.0, once no supported release or record carries them.
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

_OLD_CLASSES_SUCCESSOR_LAUNCH = (
    "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity"
)
_NEW_CLASSES_SUCCESSOR_LAUNCH = (
    "io.panelassistant.android/io.panelassistant.android.MainActivity"
)

#: Every launcher activity a signed build of each id may name, flattened, in
#: the order they shipped. Never derive one from the package id, and never
#: replace the one a descriptor names: a stage that recomputed it would start a
#: class the installed build does not have.
LAUNCH_COMPONENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        LEGACY_PACKAGE_ID: ("io.github.maxlyth.hapaneld/.MainActivity",),
        SUCCESSOR_PACKAGE_ID: (
            _OLD_CLASSES_SUCCESSOR_LAUNCH,
            _NEW_CLASSES_SUCCESSOR_LAUNCH,
        ),
    }
)

#: The one launcher component of the old app. No build of the old id moved its
#: classes that this integration installs or observes.
LEGACY_LAUNCH_COMPONENT = LAUNCH_COMPONENTS[LEGACY_PACKAGE_ID][0]


def is_launch_component(package_id: object, component: object) -> bool:
    """Return whether a build of this accepted id may name this launcher."""
    return isinstance(package_id, str) and component in LAUNCH_COMPONENTS.get(
        package_id, ()
    )


def is_accepted_package_id(package_id: object) -> bool:
    """Return whether this is one of the two installable panel application ids."""
    return isinstance(package_id, str) and package_id in ACCEPTED_PACKAGE_IDS


#: The dashboard activity that answers HOME, by the build's launcher: the
#: activity a kiosk panel boots to lives in the same package as the launcher.
HOME_COMPONENTS: Mapping[str, str] = MappingProxyType(
    {
        LEGACY_LAUNCH_COMPONENT: "io.github.maxlyth.hapaneld/.DashboardActivity",
        _OLD_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/io.github.maxlyth.hapaneld.DashboardActivity"
        ),
        _NEW_CLASSES_SUCCESSOR_LAUNCH: (
            "io.panelassistant.android/io.panelassistant.android.DashboardActivity"
        ),
    }
)

#: The accessibility service written into the device-wide list, by the build's
#: launcher.
ACCESSIBILITY_COMPONENTS: Mapping[str, str] = MappingProxyType(
    {
        LEGACY_LAUNCH_COMPONENT: (
            "io.github.maxlyth.hapaneld/.input.PanelAccessibilityService"
        ),
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
        LEGACY_LAUNCH_COMPONENT: (
            "io.github.maxlyth.hapaneld/.input.PanelAccessibilityService",
            "io.github.maxlyth.hapaneld/"
            "io.github.maxlyth.hapaneld.input.PanelAccessibilityService",
        ),
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


def counterpart_of(package_id: str) -> str:
    """Return the other accepted id: the package handed over to, or from."""
    if package_id == LEGACY_PACKAGE_ID:
        return SUCCESSOR_PACKAGE_ID
    if package_id == SUCCESSOR_PACKAGE_ID:
        return LEGACY_PACKAGE_ID
    raise KeyError(package_id)


def reports_package(reported: str | None, package_id: str) -> bool:
    """Return whether a panel reporting ``reported`` in health runs ``package_id``.

    The successor always reports its own application id, so it is never
    assumed. Builds older than the migration report no package at all, and every
    one of them is the legacy app.
    """
    if package_id == SUCCESSOR_PACKAGE_ID:
        return reported == package_id
    return reported is None or reported == package_id
