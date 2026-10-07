/** Fixed package-manager/activity-manager commands and bounded response contracts. */
export const MAX_INSTALL_RESPONSE_BYTES = 32 * 1024;
import { isLaunchComponent } from './app-identity.mjs';
import { decodeLines } from './shell-session.mjs';

export class InstallContractError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function fail(code = 'target_response_invalid') { throw new InstallContractError(code); }
function checkId(value) {
  if (typeof value !== 'string' || value.length !== 32 || !/^[0-9a-f]{32}$/.test(value)) fail('invalid_request');
}

/** Only the job-owned staging pathname is representable; never accepts shell text. */
export function buildInstall(nonce, jobId, sdk) {
  checkId(nonce);
  checkId(jobId);
  if (!Number.isInteger(sdk) || sdk < 1 || sdk > 100) fail('invalid_request');
  const path = `/data/local/tmp/ha-paneld-install-${jobId}.apk`;
  return `echo HAPANELD_INSTALL_BEGIN:${nonce}; pm install ${sdk >= 28 ? '-R ' : ''}${path}; echo HAPANELD_INSTALL_END:${nonce}:$?`;
}

/**
 * Start the package this descriptor installs, by its own exact component. The
 * `<id>/.Class` shorthand resolves against the application id while the classes
 * stay in the legacy namespace, so the component is looked up, never built.
 *
 * From Android 13 the notification permission is granted here, before the
 * first start, whatever a person once answered: the service notification is part
 * of keeping the panel working, and a "Don't allow" may have been a mis-tap or a
 * prompt nobody saw. The start never waits on the grant: its output is discarded
 * and its status is not the launch's, because the permission step after
 * `healthy` reads the result back and fails the install if the panel refused it.
 */
export function buildLaunch(nonce, packageId, launchComponent, sdk) {
  checkId(nonce);
  if (!isLaunchComponent(packageId, launchComponent)) fail('invalid_request');
  if (!Number.isInteger(sdk) || sdk < 1 || sdk > 100) fail('invalid_request');
  const permission = 'android.permission.POST_NOTIFICATIONS';
  const grant = sdk >= 33 ? `pm grant ${packageId} ${permission} >/dev/null 2>&1; ` : '';
  return `echo HAPANELD_LAUNCH_BEGIN:${nonce}; ${grant}am start -W -n ${launchComponent} -p ${packageId}; echo HAPANELD_LAUNCH_END:${nonce}:$?`;
}

function parseSection(body, nonce, prefix) {
  checkId(nonce);
  // Match Python str.splitlines(), preserving empty interior lines only.
  const lines = decodeLines(body, MAX_INSTALL_RESPONSE_BYTES, fail, 'splitlines');
  if (lines[0] !== `HAPANELD_${prefix}_BEGIN:${nonce}`) fail();
  const match = new RegExp(`^HAPANELD_${prefix}_END:${nonce}:([0-9]{1,3})$`).exec(lines.at(-1));
  if (!match || lines.slice(1, -1).some(line => line.startsWith('HAPANELD_'))) fail();
  return { lines: lines.slice(1, -1), status: Number(match[1]) };
}

export function parseInstall(body, nonce) {
  const { lines, status } = parseSection(body, nonce, 'INSTALL');
  if (status === 0 && lines.length === 1 && lines[0] === 'Success') return 'installed';
  if (status === 1 && lines.length === 1 && /^Failure \[INSTALL_[A-Z0-9_]+(?:: [ -~]{1,1024})?\]$/.test(lines[0])) return 'refused';
  fail();
}

/** As in the reference, activity-manager diagnostic text is never returned. */
// A first launch on a slow panel can take well over five seconds to bring its
// web server up, and a refused connection meanwhile is expected, not a fault.
// Wait here, read-only, for the app to be listening before the one strict
// health read. The loop is bounded: 30 checks two seconds apart.
export function buildAppReady(nonce) {
  checkId(nonce);
  return `echo HAPANELD_READY_BEGIN:${nonce}; i=0; r=1; while [ $i -lt 30 ]; do ` +
    `if { ss -ltn 2>/dev/null; netstat -ltn 2>/dev/null; } | grep -q ':8888 '; then r=0; break; fi; ` +
    `i=$((i+1)); sleep 2; done; echo HAPANELD_READY_END:${nonce}:$r`;
}
export function parseAppReady(body, nonce) {
  const { lines, status } = parseSection(body, nonce, 'READY');
  if (lines.length !== 0 || ![0, 1].includes(status)) fail();
  return status === 0;
}
export function parseLaunch(body, nonce) {
  const { status } = parseSection(body, nonce, 'LAUNCH');
  if (status === 0) return 'started';
  if (status === 1) return 'refused';
  fail();
}
