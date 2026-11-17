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

Panel Assistant turns an Android wall panel into a fast, dependable home for your Home Assistant dashboards. Add a panel from **Settings** like any other device, then update and look after it from Home Assistant too. We recognise that other projects make grander claims on features but we take security and openness seriously which is why we publish our entire codebase with full build instructions. If we don't have a feature yet then it is likely because we are still looking for a secure way to implement it but if you want it just ask and we can have an open conversation about the compromises.

Panel Assistant runs on almost any Android wall panel with Android 8.0 or newer, and has tuned support for the [Sonoff NSPanel Pro](https://panel-assistant.io/hardware/panels/sonoff/), [Shelly Wall Display](https://panel-assistant.io/hardware/panels/shelly/), [Meta Portal](https://panel-assistant.io/hardware/panels/meta/) and panels from Smatek, Lenova, Amazon, Tuya and [others](https://panel-assistant.io/install/supported-panels/), so their screen, sensors, buttons, relays and LEDs work too.

- **Fast dashboards.** Each panel receives only the entities its dashboard uses, so it responds the moment you touch it.
- **Set up in minutes.** Enter the panel's address, or plug it into your computer and install from your browser.
- **Every panel, one system.** Every make and model looks and behaves the same in Home Assistant.
- **Hardware and voice in Home Assistant.** What the panel has appears as entities, and a panel with a microphone becomes an Assist voice satellite.
- **Safe to install.** You are right to be cautious about sideloading an app onto a panel. The installer is thoroughly tested never to brick a panel, even if it is interrupted, and every update starts with a backup.
- **Open and local.** Free and open source under Apache 2.0, working with your own Home Assistant on your own network.

## Get started

[![Open your Home Assistant instance and open a repository inside the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=panel-assistant&repository=ha-integration&category=integration)

1. Open Panel Assistant with the button above and download it. On Home Assistant before 2026.11, add `https://github.com/panel-assistant/ha-integration` to [HACS](https://hacs.xyz/) as a custom repository.
2. Add the integration from **Settings**, **Devices and services**.
3. Add your first panel over USB, or over the network once its debugging switch is on ([how](https://panel-assistant.io/go/panel-access)). [Getting started](https://panel-assistant.io/start/getting-started/) walks you through it.

## Learn more

Everything else lives at [panel-assistant.io](https://panel-assistant.io):

- [Choose a panel](https://panel-assistant.io/install/supported-panels/) for what each model can do.
- [Add a panel](https://panel-assistant.io/install/installing-ha-paneld/) for both install routes, and [what setup changes](https://panel-assistant.io/install/installing-ha-paneld/#what-setup-changes) on a panel, including the accessibility service, the device administrator and, on a Meta Portal, its package verifier.
- [How it works](https://panel-assistant.io/start/what-it-is/) and [our approach to security](https://panel-assistant.io/start/research-and-interoperability/).

This repository is the Home Assistant integration; the panel app it installs is [ha-paneld](https://github.com/panel-assistant/android).

## Join in

- **Star this repository** to help other Home Assistant users find it.
- **Report your panel**, with its model and what worked, in the [issues](https://github.com/panel-assistant/ha-integration/issues).
- **Chat on [Discord](https://panel-assistant.io/go/discord)** about panels, tips and what to build next.

## Licence

Free and open-source software under the [Apache License 2.0](LICENSE), with no account, no cloud service and no subscription.

The icon includes the Home Assistant mark, which remains the property of the Home Assistant project and is not covered by this project's licence. Panel Assistant is an independent project and is not affiliated with or endorsed by Home Assistant or the Open Home Foundation. All product names, trademarks and registered trademarks are the property of their respective owners.
