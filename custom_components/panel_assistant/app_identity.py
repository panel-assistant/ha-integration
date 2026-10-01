"""The panel application ids this integration accepts while the identity moves.

ha-paneld is changing its Android application id. A new application id is a new
app to Android, so a panel crosses over by running both packages for one
handover rather than by an in-place update. Both ids are therefore installable,
observable and updatable panel apps at the same time, and every verifier here
accepts the pair rather than one constant.

Two things do not move with the application id, and getting either wrong names
something that does not exist on the panel:

* The manifest classes live in the Gradle namespace, which stays
  ``io.github.maxlyth.hapaneld`` for both builds. Android resolves the
  ``<id>/.Class`` shorthand against the *application id*, so the shorthand is
  correct only for the legacy id; the successor needs the fully qualified class.
  The accessibility component has the same problem. Both installers write it,
  so it is defined here and in the browser installer's ``app-identity.mjs``, and
  a test compares the two copies by value.
* The schema identifier strings, the database compatibility pattern and the
  MQTT identifiers are frozen on the legacy spelling on purpose, because
  released integrations compare them byte for byte. They are not derived from
  anything here.

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

#: The launcher activity, flattened per id. Never derive this from the package
#: id: ``.MainActivity`` resolves against the Gradle namespace.
LAUNCH_COMPONENTS: Mapping[str, str] = MappingProxyType(
    {
        LEGACY_PACKAGE_ID: "io.github.maxlyth.hapaneld/.MainActivity",
        SUCCESSOR_PACKAGE_ID: (
            "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity"
        ),
    }
)


def is_accepted_package_id(package_id: object) -> bool:
    """Return whether this is one of the two installable panel application ids."""
    return isinstance(package_id, str) and package_id in ACCEPTED_PACKAGE_IDS


#: The accessibility service written into the device-wide list, per id. The
#: successor's class keeps the legacy namespace, as with the launcher.
ACCESSIBILITY_COMPONENTS: Mapping[str, str] = MappingProxyType(
    {
        LEGACY_PACKAGE_ID: (
            "io.github.maxlyth.hapaneld/.input.PanelAccessibilityService"
        ),
        SUCCESSOR_PACKAGE_ID: (
            "io.panelassistant.android/"
            "io.github.maxlyth.hapaneld.input.PanelAccessibilityService"
        ),
    }
)

#: Every spelling Android reads back for an enabled service of this id: for the
#: legacy id the class lives in the id's own package, so both forms name it.
EQUIVALENT_ACCESSIBILITY_COMPONENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        LEGACY_PACKAGE_ID: (
            "io.github.maxlyth.hapaneld/.input.PanelAccessibilityService",
            "io.github.maxlyth.hapaneld/"
            "io.github.maxlyth.hapaneld.input.PanelAccessibilityService",
        ),
        SUCCESSOR_PACKAGE_ID: (
            "io.panelassistant.android/"
            "io.github.maxlyth.hapaneld.input.PanelAccessibilityService",
        ),
    }
)


def launch_component_for(package_id: str) -> str:
    """Return the exact flattened launcher component for one accepted id."""
    return LAUNCH_COMPONENTS[package_id]


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
