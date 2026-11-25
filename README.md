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
  <a href="https://my.home-assistant.io/redirect/marketplace_repository/?owner=panel-assistant&repository=ha-integration&category=integration"><img src="https://img.shields.io/badge/Marketplace-Default-41BDF5.svg" alt="Listed in Marketplace"></a>
  <a href="https://github.com/panel-assistant/ha-integration/actions/workflows/hacs.yml"><img src="https://img.shields.io/github/actions/workflow/status/panel-assistant/ha-integration/hacs.yml?branch=main&label=HACS%20validation" alt="HACS validation"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.8.3%2B-18BCF2.svg" alt="Home Assistant 2026.8.3 or newer">
</p>


Panel Assistant transforms a clunky Android wall panel into a fast, dependable home for your Home Assistant dashboards. Add a panel from **Settings** like any other device, then update and look after it from Home Assistant too.

It is two halves designed together: this Home Assistant integration and a purpose-built Android app, [ha-paneld](https://github.com/panel-assistant/android), which the integration installs and keeps up to date for you. They talk over a connection made for panels, so every feature is built for a screen on the wall, not squeezed into a protocol designed for sensors.

Other panel apps have longer feature lists, but reliability is what turns a panel into an appliance that works every time you touch it. That is why reliability, security and openness come first, and why we publish the whole codebase with full build instructions. If a feature you want is missing, it is usually because we are still finding a secure way to build it: ask, and we will talk through the trade-offs in the open.

Panel Assistant runs on almost any Android wall panel with Android 8.0 or newer, and has tuned support for the [Sonoff NSPanel Pro](https://panel-assistant.io/hardware/panels/sonoff/), [Shelly Wall Display](https://panel-assistant.io/hardware/panels/shelly/), [Meta Portal](https://panel-assistant.io/hardware/panels/meta/) and panels from Smatek, Lenovo, Tuya, Amazon and [others](https://panel-assistant.io/install/supported-panels/), so their screen, sensors, buttons, relays and LEDs work too.

## Everything happens in Home Assistant

- **Install, update and repair from Home Assistant.** Add a panel from **Settings**, update it from its update entity, and put problems right from Repairs that name the panel and say what to do.
- **Nothing to install on your computer.** No Android developer tools and no command line: install over WiFi or USB direct from Home Assistant in your browser.
- **Safe to install.** You are right to be cautious about sideloading an app onto a panel. The installer is thoroughly tested never to brick a panel, even if it is interrupted, and every update starts with a restore point.

## What you get

- **Fast dashboards.** Panel Assistant learns which entities each dashboard really uses and keeps the rest of your home's chatter away from the panel, so it responds the moment you touch it. Built-in tools show exactly what is slowing a dashboard down.
- **The panel's hardware in Home Assistant.** Its screen, sensors, buttons, relays, LEDs, camera, speaker and microphone all work in Home Assistant, set up automatically.
- **Every panel from one page.** However many panels you own, of whatever make, you run them all from one page in the Home Assistant sidebar, where they look and behave the same: no addresses to remember, no browser tab for each panel, and settings kept consistent across them.
- **A voice assistant on the wall.** A panel with a microphone becomes an Assist satellite with several wake words, even one of your own, and sends no audio until it hears one.
- **Panel and house, working together.** The panel knows which room it is in and uses that room's sensors to wake and dim its screen, with no scripts to write.
- **Open and local.** Free and open source under Apache 2.0, working securely with your own Home Assistant on your own network. Read [our approach to security](https://panel-assistant.io/start/research-and-interoperability/).

## Install the integration

[![Open your Home Assistant instance and open a repository inside the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=panel-assistant&repository=ha-integration&category=integration)

1. Open Panel Assistant with the button above, or search for it in Marketplace ([HACS](https://hacs.xyz/) before Home Assistant 2026.11), and download it.
2. Add the integration from **Settings**, **Devices and services**.

## Add a panel

**Over the network.** Switch on network debugging in the panel's own Settings ([how](https://panel-assistant.io/go/panel-access)) and enter its address. A panel that already runs ha-paneld is connected, not reinstalled.

**Over USB.** Choose **Add panel** on the Panel Assistant sidebar page, pick **Install using USB on this computer**, and follow the wizard in Chrome or Edge with the panel plugged into that computer.

[Getting started](https://panel-assistant.io/start/getting-started/) takes you from a bare panel to your dashboard. [What setup changes](https://panel-assistant.io/install/installing-ha-paneld/#what-setup-changes) lists everything an install changes on a panel. Updates and recovery are built in, and [Updates and recovery](https://panel-assistant.io/manage/updates-and-recovery/) explains how they work.

## New, and moving fast

Panel Assistant is stable and still moving quickly. Bringing every Android wall panel worth mounting in your home onto the supported list takes real hardware in real houses, which is where you come in.

- **Star this repository** to help other Home Assistant users find it.
- **Report your panel**, with its model and what worked, in the [issues](https://github.com/panel-assistant/ha-integration/issues).
- **Chat on [Discord](https://panel-assistant.io/go/discord)** about panels, tips and what to build next.
- **Try new versions first** by turning on **Pre-release** for Panel Assistant in the Marketplace.

## Licence

Free and open-source software under the [Apache License 2.0](LICENSE), with no account, no cloud service and no subscription.

The icon includes the Home Assistant mark, which remains the property of the Home Assistant project and is not covered by this project's licence. Panel Assistant is an independent project and is not affiliated with or endorsed by Home Assistant or the Open Home Foundation. All product names, trademarks and registered trademarks are the property of their respective owners.
