import { readUsbWebView } from './usb-health.mjs';

const ACTIVE = new Set(['downloading', 'verified']);

// The app owns every binary and the durable receipt. A receipt found on a
// resumed installation is only observed; a fresh retry belongs to HA Repairs.
export async function provisionUsbWebView(adb, requestId, options, {
  read = readUsbWebView, pause = ms => new Promise(resolve => setTimeout(resolve, ms)),
  attempts = 120,
} = {}) {
  try {
    const deadline = performance.now() + 120000;
    let status = await read(adb, options);
    if (status?.schema !== 1 || status.supported !== true) return 'unsupported';
    if (status.transaction == null && status.eligible === true &&
        /^[0-9a-f]{64}$/.test(status.candidate?.sha256 ?? '')) {
      status = await read(adb, { ...options, request: {
        request_id: requestId, artifact_sha256: status.candidate.sha256,
      } });
    }
    for (let attempt = 0; ACTIVE.has(status?.transaction?.state) && attempt < attempts && performance.now() < deadline; attempt++) {
      await pause(1000);
      status = await read(adb, options);
    }
    return status?.transaction?.state ?? status?.reason ?? 'deferred';
  } catch {
    // The reply may have been lost after admission. Never submit it twice.
    return 'unknown';
  }
}
