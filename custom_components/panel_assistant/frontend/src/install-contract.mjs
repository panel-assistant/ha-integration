/** Fixed package-manager/activity-manager commands and bounded response contracts. */
export const MAX_INSTALL_RESPONSE_BYTES = 32 * 1024;
import { isLaunchComponent } from './app-identity.mjs';
import { runtimePermissionRepair } from './permission-contract.mjs';
import { snapshotPreviousHome, transferPreviousHome } from './previous-home.mjs';
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
  return `echo HAPANELD_INSTALL_BEGIN:${nonce}; if ${snapshotPreviousHome(path)}; then pm install ${sdk >= 28 ? '-R ' : ''}${path}; hp_install=$?; if [ "$hp_install" -eq 0 ]; then ${transferPreviousHome(path)}; else (exit "$hp_install"); fi; else false; fi; echo HAPANELD_INSTALL_END:${nonce}:$?`;
}

/**
 * Start the package this descriptor installs, by its own exact component. The
 * `<id>/.Class` shorthand resolves against the application id while the classes
 * stay in the legacy namespace, so the component is looked up, never built.
 *
 * From Android 13 the notification permission is granted here, before the
 * first start, when nobody has answered it: the service notification is part
 * of keeping the panel working. A "Don't allow" the owner gave (or a device
 * policy) is kept, never granted over, as the network installer does; the
 * permission step after `healthy` reports it. The start never waits on the
 * grant: its output is discarded and its status is not the launch's, and that
 * step fails the install only when the panel refused a grant nobody decided.
 */
export function buildLaunch(nonce, packageId, launchComponent, sdk, previousHomeJobId = null) {
  checkId(nonce);
  if (!isLaunchComponent(packageId, launchComponent)) fail('invalid_request');
  if (!Number.isInteger(sdk) || sdk < 1 || sdk > 100) fail('invalid_request');
  if (previousHomeJobId !== null) checkId(previousHomeJobId);
  const transfer = previousHomeJobId === null ? '' : `${transferPreviousHome(`/data/local/tmp/ha-paneld-install-${previousHomeJobId}.apk`)} && `;
  const grant = sdk >= 33 ? `${runtimePermissionRepair(packageId, 'android.permission.POST_NOTIFICATIONS')}; ` : '';
  return `echo HAPANELD_LAUNCH_BEGIN:${nonce}; ${grant}${transfer}am start -W -n ${launchComponent} -p ${packageId}; echo HAPANELD_LAUNCH_END:${nonce}:$?`;
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
// The app takes 8888 when it is free and otherwise the next free port, which it
// keeps, and another app may hold 8888. So the app is the listening socket owned
// by its own user id, 8888 first among its sockets. Only where the socket table
// cannot be read does any listener on 8888 count. The port found is echoed.
export function buildAppReady(nonce, packageId) {
  checkId(nonce);
  if (typeof packageId !== 'string' || !/^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$/.test(packageId)) fail('invalid_request');
  return `echo HAPANELD_READY_BEGIN:${nonce}; i=0; r=1; while [ $i -lt 30 ]; do p=; ` +
    `u=$(stat -c %u /data/data/${packageId} 2>/dev/null); ` +
    `[ -n "$u" ] || u=$(dumpsys package ${packageId} 2>/dev/null | sed -n 's/.*userId=\\([0-9]*\\).*/\\1/p' | head -n 1); ` +
    `if [ -n "$u" ] && [ -r /proc/net/tcp ]; then p=$(cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | { f=; ` +
    `while read -r _ l _ s _ _ _ o _; do if [ "$s" = 0A ] && [ "$o" = "$u" ]; then q=$((0x\${l##*:})); ` +
    `if [ "$q" = 8888 ]; then f=8888; break; fi; [ -n "$f" ] || f=$q; fi; done; echo "$f"; }); ` +
    `elif { ss -ltn 2>/dev/null; netstat -ltn 2>/dev/null; } | grep -q ':8888 '; then p=8888; fi; ` +
    `if [ -n "$p" ]; then echo "$p"; r=0; break; fi; ` +
    `i=$((i+1)); sleep 2; done; echo HAPANELD_READY_END:${nonce}:$r`;
}
/** The port the app listens on, or null while it is not listening yet. */
export function parseAppReady(body, nonce) {
  const { lines, status } = parseSection(body, nonce, 'READY');
  if (status === 1 && lines.length === 0) return null;
  if (status === 0 && lines.length === 1 && /^[1-9][0-9]{0,4}$/.test(lines[0]) && Number(lines[0]) <= 65535) {
    return Number(lines[0]);
  }
  fail();
}
export function parseLaunch(body, nonce) {
  const { status } = parseSection(body, nonce, 'LAUNCH');
  if (status === 0) return 'started';
  if (status === 1) return 'refused';
  fail();
}
