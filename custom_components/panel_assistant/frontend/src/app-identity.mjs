/**
 * The panel application ids this installer accepts while the identity moves.
 *
 * A new application id is a new app to Android, so a panel crosses over by
 * running both packages for one handover. Both ids are therefore installable
 * and observable at the same time, and every verifier accepts the pair.
 *
 * Components are written out per id rather than built from it: the manifest
 * classes stay in the legacy namespace, which does not move with the
 * application id, so `<id>/.Class` names a real class only for the legacy id.
 * The schema identifier strings and the database pattern are frozen on the
 * legacy spelling and are never derived from these values.
 */
export const LEGACY_PACKAGE_ID = 'io.github.maxlyth.hapaneld';
export const SUCCESSOR_PACKAGE_ID = 'io.panelassistant.android';

/** Both installable panel application ids, legacy first. */
export const ACCEPTED_PACKAGE_IDS = Object.freeze([LEGACY_PACKAGE_ID, SUCCESSOR_PACKAGE_ID]);

const LAUNCH_COMPONENTS = Object.freeze({
  [LEGACY_PACKAGE_ID]: 'io.github.maxlyth.hapaneld/.MainActivity',
  [SUCCESSOR_PACKAGE_ID]: 'io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity',
});

const ACCESSIBILITY_COMPONENTS = Object.freeze({
  [LEGACY_PACKAGE_ID]: 'io.github.maxlyth.hapaneld/.input.PanelAccessibilityService',
  [SUCCESSOR_PACKAGE_ID]:
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService',
});

/**
 * Android accepts either spelling of a component whose class lives in the
 * application id's own package, and the setting reads back whichever was
 * written, so both name the same enabled service for the legacy id.
 */
const EQUIVALENT_ACCESSIBILITY_COMPONENTS = Object.freeze({
  [LEGACY_PACKAGE_ID]: Object.freeze([
    'io.github.maxlyth.hapaneld/.input.PanelAccessibilityService',
    'io.github.maxlyth.hapaneld/io.github.maxlyth.hapaneld.input.PanelAccessibilityService',
  ]),
  [SUCCESSOR_PACKAGE_ID]: Object.freeze([
    'io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService',
  ]),
});

export function isAcceptedPackageId(packageId) {
  return ACCEPTED_PACKAGE_IDS.includes(packageId);
}

export function launchComponentFor(packageId) {
  if (!isAcceptedPackageId(packageId)) throw new RangeError('unknown application id');
  return LAUNCH_COMPONENTS[packageId];
}

export function accessibilityComponentFor(packageId) {
  if (!isAcceptedPackageId(packageId)) throw new RangeError('unknown application id');
  return ACCESSIBILITY_COMPONENTS[packageId];
}

export function accessibilityComponentsFor(packageId) {
  if (!isAcceptedPackageId(packageId)) throw new RangeError('unknown application id');
  return EQUIVALENT_ACCESSIBILITY_COMPONENTS[packageId];
}

export function counterpartOf(packageId) {
  if (packageId === LEGACY_PACKAGE_ID) return SUCCESSOR_PACKAGE_ID;
  if (packageId === SUCCESSOR_PACKAGE_ID) return LEGACY_PACKAGE_ID;
  throw new RangeError('unknown application id');
}
