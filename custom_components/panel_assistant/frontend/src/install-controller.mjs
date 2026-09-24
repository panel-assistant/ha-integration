import { deviceKeyForIdentity } from './job-store.mjs';
import { advanceTransaction, TransactionError } from './transaction.mjs';
import { recoverTransaction } from './recovery-transaction.mjs';

const fail = code => { throw new TransactionError(code); };
const RECOVERING = ['recovery_required', 'cleanup_pending'];
// A fresh install is three steps (copy, install, start); resuming adds one
// reconciliation and each recovery a cleanup before starting again. The bound
// ends a job that can never settle in an error instead of spinning forever.
const MAX_STEPS = 12;
const canonical = value => JSON.stringify(value, Object.keys(value).sort());

// UI controller for an already authenticated, identity-checked connection.
// The caller supplies actual usb-transaction-ports, storage, and a session guard.
// No jobs or mutations are created during preview. No receipt deletion exists.
export function createInstallController({ store, ports, locks = globalThis.navigator?.locks,
  ensureCurrent = () => {}, onReceipt = () => {}, onSetAside = () => {} }) {
  let preview, expectedJobId, busy = false;
  const guard = () => { ensureCurrent(); };
  // Grant the app its permissions under the device lock, from a healthy job
  // for exactly this release, with the release bytes checked again first.
  const grantPermissions = () => locks.request(`ha-paneld-usb:${preview.deviceKey}`,
    {mode: 'exclusive', ifAvailable: true}, async lock => {
      if (!lock) fail('transaction_busy');
      guard();
      const receipt = await store.load(preview.deviceKey);
      if (!receipt || receipt.phase !== 'healthy' ||
          canonical(receipt.artifact) !== canonical(preview.descriptor)) fail('transaction_invalid');
      if (receipt.id !== expectedJobId) fail('job_conflict');
      const release = await ports.authenticate();
      guard();
      if (release?.kind !== 'authenticated-apk-bytes' ||
          canonical(release.descriptor) !== canonical(receipt.artifact)) fail('artifact_changed');
      const granted = await ports.commissionPermissions(receipt, release);
      guard();
      return granted;
    });
  return Object.freeze({
    async preview(target) {
      if (busy) fail('transaction_busy');
      busy = true;
      preview = undefined;
      try {
        guard();
        const snapshot = Object.freeze({ ...target });
        const deviceKey = await deviceKeyForIdentity(snapshot);
        const release = await ports.authenticate();
        guard();
        if (release?.kind !== 'authenticated-apk-bytes') fail('artifact_changed');
        let receipt = await store.load(deviceKey);
        let discarded = null;
        // The job whose record the preview lets go. Its staged copy is left
        // for the Install press, which removes it before the new job exists.
        let setAside = null;
        if (receipt?.phase === 'healthy') {
          // A finished job only resumes (permissions, then setup) while the
          // panel still runs exactly its app. If another release was chosen,
          // or the app has changed since, the job is history: retire it so it
          // can never lock the panel out of a new install.
          const current = canonical(receipt.artifact) === canonical(release.descriptor) &&
            (await ports.inspect(receipt, release))?.installed === true;
          guard();
          if (!current) {
            const finished = receipt;
            await locks.request(`ha-paneld-usb:${deviceKey}`, {mode: 'exclusive', ifAvailable: true},
              async lock => {
                if (!lock) fail('transaction_busy');
                await store.retire(deviceKey, finished.revision);
              });
            guard();
            receipt = null;
          }
        }
        // A job that ended in recovery, on a panel that already runs exactly
        // that job's own release, has nothing left to recover: the app is
        // installed. Its recovery observation would demand a clean panel and
        // refuse this one as unclean, from an error screen whose only button
        // reproduces it. Discard the record and adopt the panel below instead,
        // so the retry converges on the install the person asked for.
        if (receipt && RECOVERING.includes(receipt.phase) &&
            canonical(receipt.artifact) === canonical(release.descriptor)) {
          const stalled = receipt;
          const running = await ports.inspect(
            { phase: 'installed', target: snapshot, artifact: release.descriptor }, release);
          guard();
          if (running?.installed === true) {
            await locks.request(`ha-paneld-usb:${deviceKey}`, {mode: 'exclusive', ifAvailable: true},
              async lock => {
                if (!lock) fail('transaction_busy');
                await store.discard(deviceKey, stalled.revision);
              });
            guard();
            setAside = Object.freeze({ jobId: stalled.id, phase: stalled.phase });
            receipt = null;
          }
        }
        // An unfinished job for a different release used to refuse here, and
        // the only button on that error reloads into the same refusal. The
        // person has since chosen another version in Home Assistant, so set
        // the old job aside and install what they chose. This discards one
        // saved record under the device lock and touches nothing on the panel;
        // a job whose release is unchanged still resumes exactly as before,
        // and every later guard still refuses an artifact that changes under a
        // job already running.
        if (receipt && canonical(receipt.artifact) !== canonical(release.descriptor)) {
          const stale = receipt;
          await locks.request(`ha-paneld-usb:${deviceKey}`, {mode: 'exclusive', ifAvailable: true},
            async lock => {
              if (!lock) fail('transaction_busy');
              await store.discard(deviceKey, stale.revision);
            });
          guard();
          discarded = Object.freeze({ versionName: stale.artifact.versionName,
            versionCode: stale.artifact.versionCode, releaseTag: stale.artifact.releaseTag });
          setAside = Object.freeze({ jobId: stale.id, phase: stale.phase });
          receipt = null;
        }
        // Actual ports validate the live target and installed/clean state. A
        // not-yet-created job may use the target only for read-only inspection.
        // A panel already running exactly this signed build is adopted, not
        // refused: installing it again must converge. This read comes first
        // because a refused clean check closes the session.
        let adopt = false;
        if (!receipt) {
          const existing = await ports.inspect(
            { phase: 'installed', target: snapshot, artifact: release.descriptor }, release);
          guard();
          adopt = existing?.installed === true;
        }
        const proposed = receipt ?? { phase: adopt ? 'installed' : 'prepared', target: snapshot,
          artifact: release.descriptor };
        if (RECOVERING.includes(proposed.phase)) await ports.inspectRecovery(proposed, release);
        else if (!adopt) await ports.inspect(proposed, release);
        guard();
        preview = Object.freeze({ target: snapshot, descriptor: release.descriptor, deviceKey,
          receipt, adopt, discarded, setAside });
        expectedJobId = receipt?.id;
        return preview;
      } finally { busy = false; }
    },
    // The one Install press: run the saved job to a healthy app, recovering
    // on the way when an observation says a step did not take, then grant the
    // app its permissions. Every step is its own locked, durable transaction
    // that observes the panel before it acts; nothing is replayed.
    async install(confirmed = false) {
      if (busy) fail('transaction_busy');
      if (!preview || confirmed !== true) fail('confirmation_required');
      busy = true;
      try {
        guard();
        const selected = preview;
        // Verify selection again before first durable job creation; changed
        // release selection requires a new preview and confirmation.
        const release = await ports.authenticate();
        guard();
        if (release?.kind !== 'authenticated-apk-bytes' ||
            canonical(release.descriptor) !== canonical(selected.descriptor)) fail('artifact_changed');
        let receipt = await store.load(selected.deviceKey);
        if (receipt && (!selected.receipt || receipt.id !== selected.receipt.id)) fail('job_conflict');
        if (!receipt) {
          if (selected.receipt) fail('transaction_missing');
          // Up to 64 MiB of a set-aside job can sit in /data/local/tmp, and
          // its release bytes went with the job. Only its own path is asked
          // about, under the lock and while no saved job carries its id.
          if (selected.setAside) {
            const { jobId, phase } = selected.setAside;
            const outcome = await locks.request(`ha-paneld-usb:${selected.deviceKey}`,
              {mode: 'exclusive', ifAvailable: true}, async lock => {
                if (!lock) fail('transaction_busy');
                guard();
                if ((await store.load(selected.deviceKey))?.id === jobId) fail('job_conflict');
                return ports.removeSetAside(jobId, selected.target, release);
              });
            guard();
            onSetAside(Object.freeze({ jobId, phase, outcome }));
          }
          receipt = selected.adopt ? await store.adopt(selected.target, release.descriptor)
            : await store.create(selected.target, release.descriptor);
        }
        // From here on this operation owns exactly this job, including one it
        // has just created.
        expectedJobId = receipt.id;
        onReceipt(receipt);
        for (let step = 0; step < MAX_STEPS && receipt.phase !== 'healthy'; step++) {
          guard();
          const transaction = { store, ports, locks, deviceKey: selected.deviceKey, ensureCurrent: guard };
          receipt = RECOVERING.includes(receipt.phase)
            ? await recoverTransaction({ ...transaction, confirmed: true, expectedJobId })
            : await advanceTransaction(transaction);
          guard();
          onReceipt(receipt);
        }
        if (receipt.phase !== 'healthy') fail('install_incomplete');
        const granted = await grantPermissions();
        if (granted?.permissionsVerified !== true) fail('permissions_unverified');
        return Object.freeze({ receipt, permissions: granted });
      } finally { busy = false; }
    },
    // Cancellation of active I/O belongs to the owning session's guard/close.
    invalidate() { if (busy) fail('transaction_busy'); preview = undefined; },
  });
}
