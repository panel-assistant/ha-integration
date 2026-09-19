# Changelog

All notable changes to this project will be documented in this file.

## 0.5.0 - 2026-09-19

- ha-paneld is changing the application id it installs under. Panel Assistant now accepts either id, so it can install and verify both the release that keeps the old id and the one that carries the new one. This release has to be installed before the ha-paneld release that makes the change; an older Panel Assistant refuses the new release outright.
- A panel that already runs the old app is no longer refused as an unclean target. The new app installs beside it, and the panel then moves its own settings across and removes the old app by itself. Panel Assistant only watches: it never touches the panel beyond installing and starting the new app.
- Installing onto such a panel now waits through that handover, including the moment when the panel's own page is briefly unreachable, and finishes only when the new app answers for itself rather than on the first reply.
- If the handover has not finished by the time Panel Assistant stops watching, it now says so as a repairable issue that re-checks the panel when you ask it to. The panel carries on by itself either way.
- The panel's settings backup is now verified before an update replaces the app that produced it, and a receipt recording its size, digest and contents is kept beside it. An unreadable backup stops the update instead of being written and trusted.
- When an ha-paneld release offers an APK under each application id, Panel Assistant installs the new one. A release with a single APK, which is every release published so far, resolves exactly as it did before.
- Release lookups follow ha-paneld to its new repository address.
- Panel Assistant now tells a panel where Home Assistant is when it installs or adopts one, so the panel's setup wizard no longer asks for an address Home Assistant already knows. The panel checks the address from its own network before using it, and asks as it always did if it does not answer, showing the address that was tried rather than an empty box. This needs a matching ha-paneld release that accepts the address; older panels are unaffected and behave exactly as before.
- Only a local address is ever handed over. If Home Assistant has no internal URL (which is what happens when it terminates HTTPS itself and only its external address is set), nothing is sent and the panel asks, rather than being given a public address that would send its dashboard out to the internet and back.

## 0.4.1 - 2026-09-16

- Stop logging a deprecation warning when the sidebar's panel picker looks up a panel's device name, ahead of Home Assistant removing the old lookup in a future release.

## 0.4.0 - 2026-09-16

- The sidebar's top bar now has real icon buttons, right-justified and sized to match Home Assistant's own header: GitHub, add a panel, integration settings, and a new button that opens the selected panel's own device page. The panel picker shows each panel's real device name, its own reported name or a rename from the Devices page, rather than the panel's raw identifier.
- The panel picker and its controls keep their contrast in dark mode. The "opening a panel" wait now shows an animated spinner, not static text.
- Home Assistant now issues the sidebar's session a signing key when a panel offers it, and a signed request exempts a fixed set of lower-impact operations, such as display and power settings, from on-panel approval; every other operation still needs it. Hardened mode requires physical access to the panel. High-impact remote actions cannot proceed until someone approves them on the panel's screen; they cannot be approved remotely. This needs a matching ha-paneld release that offers signing; older panels are unaffected and behave exactly as before.

## 0.3.1 - 2026-09-16

- Rewrite the README so HACS shows the icon and the same introduction as panel-assistant.io, with badges and one-click buttons to open the repository in HACS and to start setup. No code changes.

## 0.3.0 - 2026-09-14

- The sidebar now shows each connected panel's own web interface (the same tab bar, dashboard, settings and logs as its `:8888` page) instead of a separate fleet-card list. Pick a panel from the new top menu; add a panel or open the integration's own settings from the same bar. The old placeholder sidebar UI is gone.
- The sidebar's top menu shows the integration's installed version and build number, so checking it no longer needs a diagnostics download.
- Renaming a panel, retrying a failed handover, or losing power partway through no longer leaves a stray or duplicated entity behind.
- Advanced/testing: an optional native transport, set with `native_entities: true` under `panel_assistant` in `configuration.yaml`, lets a panel report state and accept commands over its own authenticated Home Assistant connection instead of MQTT, chosen per panel from the integration's options once turned on. Off by default; MQTT is unaffected either way.

## 0.2.1 - 2026-09-13

- First full release, so HACS offers it without Show beta versions.
- Fix the Install link in the Panel Assistant sidebar, which in 0.2.0b1 opened nothing, or the old integration's installer where that was still installed.
- Panel Assistant lists only ha-paneld releases that carry a signed installation descriptor. The current stable ha-paneld, v0.9.6, has none, so until the next stable release the version list offers only release candidates, marked as test versions.

## 0.2.0b1 - 2026-09-12

- Rename the integration to Panel Assistant and move to semantic versions. Earlier installs tracked a moving branch and will not be offered this as an update, so follow "Updating from 0.1.x" in the README.
- Install ha-paneld over USB with a guided wizard: plug the panel into the computer you are browsing on, press Install once, and the installer copies, installs, starts the app, grants what it needs and hands over to the panel's own setup. It shows plain progress rather than a log, and technical detail stays behind "Details for support".
- Home Assistant does not need a TLS certificate for USB installation. The installer runs on its own secure page and Home Assistant passes it the signed release.
- Choose which ha-paneld version to install, with the newest stable preselected and release candidates marked for testing.
- Add a panel from one path that starts with its address. Home Assistant looks at the panel and then either connects it, offers to install, or explains what is missing.
- A panel that already runs ha-paneld says so, and offers to open its setup wizard when that is unfinished. Home Assistant connects the panel by itself once setup is done, or connects it straight away if you skip.
- Say what to do when a panel cannot be reached: a panel whose Android Debug Bridge is off is told to turn it on or to install over USB instead, a silent address is told to check the address, and the screen links to per-model help on panel-assistant.io.
- Reinstalling the version a panel already runs finishes its setup instead of stopping with an error, and a finished install no longer blocks the next one.
- Fill in each panel's device details: manufacturer, model, Android version and area.
- Tell the panel when Home Assistant shows its ha-paneld update, so a panel does not offer the same update twice once both are updated.
- Optionally read internal builds from a signed build feed, set with `build_feed` under `panel_assistant` in `configuration.yaml`. Without it, only published GitHub releases are used.
- Fix USB installation on panels whose Android Debug Bridge refused the app's port, wait for the app to start before checking it, correct the staged file's permissions, and never advise unplugging a panel that is powered over that cable.
- Give the Home Assistant page and the installer the same look as the panel's own setup wizard.

## 0.1.0 - Unreleased

- Allow advanced users to test one exact published Android release candidate while keeping the latest stable release as the default. RC installs retain the signed descriptor, exact consent, durable receipt and recovery checks; existing installations are never upgraded by this option.
- Add a clean first-install workflow over network ADB while keeping connection to an existing installation as a separate path. Home Assistant verifies the panel and signed stable-release descriptor, downloads the exact APK, installs and launches it, then creates the config entry only after final identity and health checks pass.
- On panels requiring ADB authorization, request approval on the panel for one persistent credential stored in Home Assistant's private storage. Unreachable targets and the initial authorization probe do not create or offer a key.
- Keep the installation transaction running if its setup dialog closes, resume safe phases after the integration loads again and refuse to replay any operation with an ambiguous outcome.
- Keep older stable releases without a signed installation descriptor preview-only. Installed, retained, ambiguous and incompatible targets are refused, and this release does not upgrade or overwrite an existing installation.
- Add manual setup by panel hostname or IP address.
- Validate panels through the stable read-only `/api/v1/health` endpoint.
- Add bounded, privacy-safe `/api/v1/status` data to downloadable diagnostics.
- Create one Home Assistant device with a diagnostic status sensor and downloadable redacted diagnostics.
- Support config-entry setup, unload and reload.
