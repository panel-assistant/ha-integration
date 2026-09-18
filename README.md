<p align="center">
  <a href="https://panel-assistant.io"><img src="https://raw.githubusercontent.com/panel-assistant/ha-integration/main/custom_components/panel_assistant/static/icon.svg" width="160" height="160" alt="Panel Assistant"></a>
</p>

<h1 align="center">Panel Assistant</h1>

<p align="center"><strong>Home Assistant, on the wall, done properly.</strong></p>

<p align="center">
  <a href="https://github.com/panel-assistant/ha-integration/releases/latest"><img src="https://img.shields.io/github/v/release/panel-assistant/ha-integration?label=release" alt="Latest release"></a>
  <a href="https://hacs.xyz/"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS custom repository"></a>
  <a href="https://github.com/panel-assistant/ha-integration/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/panel-assistant/ha-integration/tests.yml?branch=main&label=tests" alt="Tests"></a>
  <a href="https://github.com/panel-assistant/ha-integration/actions/workflows/hacs.yml"><img src="https://img.shields.io/github/actions/workflow/status/panel-assistant/ha-integration/hacs.yml?branch=main&label=HACS%20validation" alt="HACS validation"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.8.3%2B-18BCF2.svg" alt="Home Assistant 2026.8.3 or newer">
  <a href="https://github.com/panel-assistant/ha-integration/blob/main/LICENSE"><img src="https://img.shields.io/github/license/panel-assistant/ha-integration" alt="Apache 2.0 licence"></a>
  <a href="https://panel-assistant.io/go/discord"><img src="https://img.shields.io/badge/Discord-join-5865F2.svg" alt="Panel Assistant on Discord"></a>
</p>

Panel Assistant turns an Android wall panel into a fast, dependable home for your Home Assistant dashboards. You install and manage every panel from Home Assistant itself: add a panel the way you add any other device, from Settings, in your browser, without a command line in sight.

Android wall panels are genuinely good hardware wrapped in genuinely bad software. The dashboard lags, taps vanish, the vendor's own app elbows its way onto the screen, and every make demands a different app and a different ritual to set it up. Panel Assistant is the free and open-source cure. It does one job: run your Home Assistant dashboards on the wall, quickly and without fuss. Every panel you own is set up and managed the same way, from Home Assistant, with nothing to nurse, tune or tweak by hand.

This repository is the Home Assistant integration. The panel app it installs is [ha-paneld](https://github.com/panel-assistant/android). Everything else, from choosing a panel to keeping it running, is at [panel-assistant.io](https://panel-assistant.io).

## What you get

- **Performance you can feel.** Your existing dashboards, fed only the data they actually use, so the panel answers the moment you touch it. Panel Assistant's entity filter learns which entities your dashboard depends on and asks Home Assistant to send only those.
- **Set up from Home Assistant.** Give the integration a panel's address and it checks the panel, installs the app, starts it and creates the device. A brand-new panel can be installed over USB from your browser before it ever goes on the wall.
- **Safe to install.** Checked before anything changes, refuses to overwrite what it should not, and picks up where it left off if interrupted.
- **The panel's hardware, in Home Assistant.** Screen, LEDs, buttons, relays, light and proximity sensors appear as entities automatically.
- **Panel and house, working together.** Dims with the room and wakes as you approach. Where the panel lacks a sensor, Home Assistant drives the screen instead.
- **Every panel, one system.** Different makes and models look and behave identically in Home Assistant. Another panel joins the same setup, not a second app.
- **All your panels in the sidebar.** A Panel Assistant entry in the Home Assistant sidebar opens each connected panel's own dashboard, settings and logs.
- **Know before you buy.** The [hardware pages](https://panel-assistant.io/hardware/) say what works on each model, how far, and what to expect, before you spend the money or the evenings.

## Install the integration

You need Home Assistant 2026.8.3 or newer and [HACS](https://hacs.xyz/).

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=panel-assistant&repository=ha-integration&category=integration)

That button opens this repository in the HACS on your own Home Assistant, ready to download. If you would rather do it by hand:

1. In HACS, add `https://github.com/panel-assistant/ha-integration` as a custom repository with the category **Integration**.
2. Download **Panel Assistant**, then restart Home Assistant.
3. Go to **Settings**, **Devices and services**, **Add integration**, and choose **Panel Assistant**.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=panel_assistant)

HACS offers the latest release. To try new versions before they are released, turn on **Show beta versions** for Panel Assistant in HACS.

## Add a panel

The hard part of any panel is the first hour. Panel Assistant does that hour for you, from inside Home Assistant.

**Over the network.** [Enable ADB on your panel](https://panel-assistant.io/go/panel-access), add the integration and enter the panel's address. Panel Assistant checks the panel, offers the ha-paneld versions it can install, and connects the panel when its setup is done. A panel that already runs ha-paneld is connected rather than reinstalled.

**Over USB.** Open **Panel Assistant** in the Home Assistant sidebar, choose a version and follow the wizard. Use Chrome or Edge on a computer, and plug the panel into that computer, not into the Home Assistant server. Home Assistant itself does not need a certificate. Either way you approve one prompt on the panel and watch it through.

Once a panel is running, point it at the dashboard you want on the wall. The four steps from a bare panel to your dashboard are on the [Getting started](https://panel-assistant.io/start/getting-started/) page, and [Choose a panel](https://panel-assistant.io/install/supported-panels/) says what to expect from each model before you buy one. If an installation stops, see [Updates and recovery](https://panel-assistant.io/manage/updates-and-recovery/).

## New, and moving fast

Panel Assistant is still in 0.x: the direction is settled, and the internals are still changing. A panel that is already installed is updated from its update entity in Home Assistant. Getting every Android wall panel worth mounting onto the supported list takes real hardware in real houses, which is where you come in.

- **Star this repository.** The simplest way to tell other Home Assistant users this exists.
- **Report your panel.** Model name and what worked, in the [issues](https://github.com/panel-assistant/ha-integration/issues).
- **Chat on [Discord](https://panel-assistant.io/go/discord).** Questions, panel-specific tips, panels in the wild, and what to build next.

## Licence

Free and open-source software under the [Apache License 2.0](LICENSE), with no account, no cloud service and no subscription.

The icon includes the Home Assistant mark, which remains the property of the Home Assistant project and is not covered by this project's licence. Panel Assistant is an independent project and is not affiliated with or endorsed by Home Assistant or the Open Home Foundation. All product names, trademarks and registered trademarks are the property of their respective owners.
