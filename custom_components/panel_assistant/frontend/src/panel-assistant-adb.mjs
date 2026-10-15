import { AdbCommand, AdbTcpIpService } from '@yume-chan/adb';

// What a USB install leaves behind so Panel Assistant can reach the panel over
// network ADB afterwards, as a network install does: its own key trusted, and
// adbd listening on the port Panel Assistant connects to.

export const PANEL_ASSISTANT_ADB_PORT = 5555;

// Answers the panel's first authentication token with Panel Assistant's public
// key, so the owner's "Allow" on the panel approves Panel Assistant itself and
// this session opens on that approval. The browser holds no private key to sign
// with, so nothing else is offered.
export function panelAssistantKeyAuthenticator(key) {
  const payload = new TextEncoder().encode(`${key}\0`);
  return async function* authenticate(_credentialStore, getNextRequest) {
    const packet = await getNextRequest();
    if (packet.arg0 !== 1) return;
    yield { command: AdbCommand.Auth, arg0: 3, arg1: 0, payload };
  };
}

// The port adbd listens on when it starts, as adbd itself reads it.
function listening({ serviceListenAddresses, servicePort, persistPort }) {
  return serviceListenAddresses.includes(`tcp:${PANEL_ASSISTANT_ADB_PORT}`) ||
    (servicePort ?? persistPort) === PANEL_ASSISTANT_ADB_PORT;
}

// Turns network ADB on when it is off. adbd restarts to do it, which ends this
// USB session, so it is the last thing a USB install does. Never throws: the
// app is installed whatever happens here, and the outcome goes to the support log.
export async function ensureNetworkAdb(adb, { beforeRestart = () => {} } = {}) {
  const tcpip = new AdbTcpIpService(adb);
  try {
    if (listening(await tcpip.getListenAddresses())) return 'already_on';
    beforeRestart();
    await tcpip.setPort(PANEL_ASSISTANT_ADB_PORT);
    return 'turned_on';
  } catch {
    return 'failed';
  }
}
