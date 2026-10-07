import { buildPosture, parseObservedPosture } from './posture.mjs';
import { readShell } from './shell-session.mjs';
import { deviceKeyForIdentity } from './job-store.mjs';
import { TransactionError } from './transaction.mjs';
import { newNonce } from './shared.mjs';

// Observe identity before receipt lookup. Do not demand absence here: a saved
// install may already have succeeded. Controller/ports perform phase admission.
export async function inspectSessionTarget(adb, descriptor, usbDevice, ensureCurrent = () => {}) {
  ensureCurrent();
  const nonce = newNonce();
  const target = Object.freeze({
    ...parseObservedPosture(await readShell(adb, buildPosture(nonce)), nonce),
    usbVendorId: usbDevice.vendorId,
    usbProductId: usbDevice.productId,
    usbSerial: usbDevice.serialNumber ?? '',
  });
  ensureCurrent();
  if (!descriptor.supportedAbis.includes(target.primaryAbi) || descriptor.minSdk > target.androidSdk) {
    throw new TransactionError('target_incompatible');
  }
  await deviceKeyForIdentity(target);
  ensureCurrent();
  return target;
}
