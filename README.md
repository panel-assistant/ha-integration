<h1 align="center">
  <a href="https://panel-assistant.io"><picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://panel-assistant.io/brand/wordmark-on-dark.svg">
    <img src="https://panel-assistant.io/brand/wordmark-on-light.svg" width="500" alt="Panel Assistant">
  </picture></a>
</h1>

<h1 align="center">Home Assistant, on the wall, done properly.</h1>

<p align="center">
  <a href="https://github.com/panel-assistant/ha-integration/releases/latest"><img src="https://img.shields.io/github/v/release/panel-assistant/ha-integration?label=release" alt="Latest release"></a>
  <a href="https://github.com/panel-assistant/ha-integration/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/panel-assistant/ha-integration/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <a href="https://github.com/panel-assistant/ha-integration/blob/main/LICENSE"><img src="https://img.shields.io/badge/licence-Apache%202.0-blue.svg" alt="Apache 2.0 licence"></a>
  <a href="https://panel-assistant.io/go/discord"><img src="https://img.shields.io/badge/Discord-join-5865F2.svg" alt="Panel Assistant on Discord"></a>
</p>
<p align="center">
  <a href="https://my.home-assistant.io/redirect/marketplace_repository/?owner=panel-assistant&repository=ha-integration&category=integration"><img src="https://img.shields.io/badge/Marketplace-Custom-41BDF5.svg" alt="Marketplace custom repository"></a>
  <a href="https://github.com/panel-assistant/ha-integration/actions/workflows/hacs.yml"><img src="https://img.shields.io/github/actions/workflow/status/panel-assistant/ha-integration/hacs.yml?branch=main&label=HACS%20validation" alt="HACS validation"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.8.3%2B-18BCF2.svg" alt="Home Assistant 2026.8.3 or newer">
</p>

Panel Assistant turns an Android wall panel into a fast, dependable home for your Home Assistant dashboards. You install and manage every panel from Home Assistant itself: add a panel the way you add any other device, from Settings, in your browser, without a command line in sight.

Android wall panels are genuinely good hardware wrapped in genuinely bad software. The dashboard lags, taps vanish, the vendor's own app elbows its way onto the screen, and every make demands a different app and a different ritual to set it up. Panel Assistant is the free and open-source cure. It does one job: run your Home Assistant dashboards on the wall, quickly and without fuss. Every panel you own is set up and managed the same way, from Home Assistant, with nothing to nurse, tune or tweak by hand.

This repository is the Home Assistant integration. The panel app it installs is [ha-paneld](https://github.com/panel-assistant/android). Everything else, from choosing a panel to keeping it running, is at [panel-assistant.io](https://panel-assistant.io).

## Everything happens in Home Assistant

- **Install, update and repair from Home Assistant.** You add a panel from **Settings**, update it from its update entity, and put problems right from the Repairs Home Assistant raises, which name the panel and say what to do.
- **Nothing to install on your computer.** No Android developer tools and no command line. A USB install runs from Home Assistant in Chrome or Edge, and a network install needs only the panel's address. The one step on the panel itself is switching on debugging in its own Settings; the [hardware page](https://panel-assistant.io/hardware/) for your model says where that switch is. Two vendor limits remain: some [Sonoff NSPanel Pro](https://panel-assistant.io/hardware/panels/sonoff/#gaining-adb-and-root-access) firmware hides the switch, and a [Tuya TPA10](https://panel-assistant.io/hardware/panels/tuya/tpa10/#webview-update-this-first) needs its WebView replaced once by hand; both can still call for a computer.
- **Repeatable results.** An interrupted install is safe to run again: it checks what already happened on the panel before it changes anything, and carries on from there. Every update arrives the same way, through Panel Assistant, which backs up the panel's settings first and confirms the panel came back healthy.

## What you get

- **Performance you can feel.** Your existing dashboards, fed only the data they actually use, so the panel answers the moment you touch it. Panel Assistant's entity filter learns which entities your dashboard depends on and asks Home Assistant to send only those.
- **Set up from Home Assistant.** Give the integration a panel's address and it checks the panel, installs the app, starts it and creates the device. A brand-new panel can be installed over USB from your browser before it ever goes on the wall.
- **Safe to install.** Checked before anything changes, refuses to overwrite what it should not, and picks up where it left off if interrupted.
- **The panel's hardware, in Home Assistant.** Screen, LEDs, buttons, relays, light and proximity sensors appear as entities automatically.
- **A voice assistant on the wall.** On a panel with a microphone, turn on the voice assistant from the panel's Configure page and the panel becomes a Home Assistant [Assist satellite](https://panel-assistant.io/manage/voice-assistant/), as an entity on the panel's existing device with nothing extra to add. The wake word is detected on the panel, audio goes to Home Assistant only after it hears one, and each wake word can use its own Assist pipeline.
- **Panel and house, working together.** Dims with the room and wakes as you approach. Where the panel lacks a sensor, Home Assistant drives the screen instead.
- **Every panel, one system.** Different makes and models look and behave identically in Home Assistant. Another panel joins the same setup, not a second app.
- **All your panels in the sidebar.** A Panel Assistant entry in the Home Assistant sidebar opens each connected panel's own dashboard, settings and logs.
- **Know before you buy.** The [hardware pages](https://panel-assistant.io/hardware/) say what works on each model, how far, and what to expect, before you spend the money or the evenings.

## Install the integration

You need Home Assistant 2026.8.3 or newer. On Home Assistant 2026.11 or newer, Panel Assistant comes from the Marketplace, which is built in; on older versions it comes from [HACS](https://hacs.xyz/).

[![Open your Home Assistant instance and open a repository inside the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=panel-assistant&repository=ha-integration&category=integration)

That button opens this repository in the Marketplace on your own Home Assistant, or in HACS on an older one. If you would rather do it by hand:

1. Open **Settings**, **Marketplace**, choose **Add from link** and enter `https://github.com/panel-assistant/ha-integration` (the Marketplace asks you to connect a GitHub account first). In HACS, add the same address as a custom repository with the category **Integration**.
2. Download **Panel Assistant**. HACS then asks for a restart of Home Assistant.
3. Go to **Settings**, **Devices and services**, **Add integration**, and choose **Panel Assistant**.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=panel_assistant)

The Marketplace and HACS offer the latest release. To try new versions before they are released, turn on **Pre-release** for Panel Assistant.

## Add a panel

The hard part of any panel is the first hour. Panel Assistant does that hour for you, from inside Home Assistant.

**Over the network.** Switch on network debugging in the panel's own Settings ([how](https://panel-assistant.io/go/panel-access)), add the integration and enter the panel's address. Panel Assistant checks the panel, offers the ha-paneld versions it can install, and connects the panel when its setup is done. A panel that already runs ha-paneld is connected rather than reinstalled.

**Over USB.** Choose **Add panel** in the **Panel Assistant** sidebar page (or add the integration), pick **Install using USB on this computer**, then **Open the USB installer** and follow the wizard. Use Chrome or Edge on a computer, and plug the panel into that computer, not into the Home Assistant server. Home Assistant itself does not need a certificate. Either way you approve one prompt on the panel and watch it through.

**What setup changes on the panel.** Panel Assistant makes its app the panel's home screen, and each install or update sets it again. Installing also gives the app the camera, microphone and notification permissions where the panel has that hardware, and lets it modify system settings, display over other apps, install its own updates and change secure settings; a permission you turned off is left off. Panel Assistant updates the app over network debugging (ADB on port 5555): a USB install turns it on, and on rooted panels installing keeps it on across restarts. Rooted panels keep that port closed while the app's own connection to Home Assistant is working, and the app can open it again if that connection is lost. Once the panel's own setup is finished, the install or update makes the app a device administrator, which it uses only to lock the screen where Android's screen timeout does not turn it off. It also turns on the app's accessibility service. The app uses it to send hardware button presses to Home Assistant, to show Back and Recents on panels without navigation keys, to take screenshots (on Android 11 or later) and taps from the panel page on panels without root access, to choose itself when Android asks which app is the home screen, to press Install when Android asks to confirm the app's own update, and on a Meta Portal to close Meta's overlay request at startup and set the device name. On models that need it, installing also sets the display and text size once. On a Meta Portal it also turns off Meta's screensaver and stops Meta's control centre drawing its swipe-up bar over other apps, and it turns off Android's package verifier so that its app can update itself. That last one is a security change: the Portal stops checking that new apps were signed by Meta before installing them. On a Shelly Wall Display it stops Shelly's launcher drawing its corner overlay over other apps. These changes are made only as part of an install or update you start, or when you confirm the Repairs issue that finishes one. Home Assistant's regular check of the panel, a reconnect or a restart never makes them again, so a setting you change back on the panel stays as you left it until the next update. The exceptions are the accessibility service, which the app turns back on each time it starts, and network debugging on rooted panels, as described above. Two things do run when a panel reconnects, without changing its settings: on a rooted panel Home Assistant restarts the app's root helper, and on a Shelly Wall Display it checks the panel over network debugging and, if Shelly's firmware has stopped the app, presses Home once to bring it back. On its first start the app also mutes the ring and notification volumes, which silences the boot chime, and sets Android's screen timeout to its longest so the firmware does not dim the screen; both can be turned off in the app's settings. Removing Panel Assistant from the panel puts back the home screen, display and text size, and vendor settings it changed; network debugging stays as it is.

**What Panel Assistant looks for on your network.** Any Android device that announces network debugging is offered as a panel you can install onto. Panels that Fully Kiosk, Shelly, ESPHome or the Companion app already know are looked for only when you open Add panel, which briefly checks their network debugging and app ports. The USB installer page is loaded from install.panel-assistant.io, and Home Assistant checks GitHub for new Panel Assistant releases every six hours.

Once a panel is running, point it at the dashboard you want on the wall. The four steps from a bare panel to your dashboard are on the [Getting started](https://panel-assistant.io/start/getting-started/) page, and [Choose a panel](https://panel-assistant.io/install/supported-panels/) says what to expect from each model before you buy one. If an installation stops, see [Updates and recovery](https://panel-assistant.io/manage/updates-and-recovery/).

## New, and moving fast

Panel Assistant 1.0 is the stable release line, and it is still moving quickly. A panel that is already installed is updated from its update entity in Home Assistant. Getting every Android wall panel worth mounting onto the supported list takes real hardware in real houses, which is where you come in.

- **Star this repository.** The simplest way to tell other Home Assistant users this exists.
- **Report your panel.** Model name and what worked, in the [issues](https://github.com/panel-assistant/ha-integration/issues).
- **Chat on [Discord](https://panel-assistant.io/go/discord).** Questions, panel-specific tips, panels in the wild, and what to build next.

## Licence

Free and open-source software under the [Apache License 2.0](LICENSE), with no account, no cloud service and no subscription.

The icon includes the Home Assistant mark, which remains the property of the Home Assistant project and is not covered by this project's licence. Panel Assistant is an independent project and is not affiliated with or endorsed by Home Assistant or the Open Home Foundation. All product names, trademarks and registered trademarks are the property of their respective owners.
