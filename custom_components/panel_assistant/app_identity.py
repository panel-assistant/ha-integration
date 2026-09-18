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

#: The accessibility service, flattened per id, in the spelling the provisioner
#: writes into ``enabled_accessibility_services``.
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

#: Android accepts either spelling of a component whose class lives in the
#: application id's own package, and the setting reads back whichever was
#: written, so both are the same enabled service for the legacy id.
_EQUIVALENT_ACCESSIBILITY_COMPONENTS: Mapping[str, tuple[str, ...]] = MappingProxyType(
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


def is_accepted_package_id(package_id: object) -> bool:
    """Return whether this is one of the two installable panel application ids."""
    return isinstance(package_id, str) and package_id in ACCEPTED_PACKAGE_IDS


def launch_component_for(package_id: str) -> str:
    """Return the exact flattened launcher component for one accepted id."""
    return LAUNCH_COMPONENTS[package_id]


def accessibility_component_for(package_id: str) -> str:
    """Return the exact flattened accessibility component for one accepted id."""
    return ACCESSIBILITY_COMPONENTS[package_id]


def accessibility_components_for(package_id: str) -> tuple[str, ...]:
    """Return every spelling that names this id's accessibility service."""
    return _EQUIVALENT_ACCESSIBILITY_COMPONENTS[package_id]


def counterpart_of(package_id: str) -> str:
    """Return the other accepted id: the package handed over to, or from."""
    if package_id == LEGACY_PACKAGE_ID:
        return SUCCESSOR_PACKAGE_ID
    if package_id == SUCCESSOR_PACKAGE_ID:
        return LEGACY_PACKAGE_ID
    raise KeyError(package_id)
