# Changelog

All notable changes to this project will be documented in this file.

## 0.6.0 - 2026-09-26

This one has been a long time coming, and we're really pleased with where it has landed. Panel Assistant 0.6.0 is the first release that can run your panels without MQTT: each panel talks to Home Assistant over its own connection, and Panel Assistant looks after its entities directly. MQTT keeps working exactly as before, and nothing changes until you choose to switch a panel over.

### Moving a panel off MQTT

**How it works.** Your panel already keeps its own signed-in connection to Home Assistant. Once you switch it over, its sensors, controls and commands travel over that connection instead of through your MQTT broker. Panel Assistant takes over the panel's existing entities with their entity IDs, history and customisations intact, so your dashboards and automations carry on as they were. The panel then stops announcing itself over MQTT.

**What you need to do.**

1. Update the panel to ha-paneld 0.9.8 or later.
2. Add `native_entities: true` under `panel_assistant:` in `configuration.yaml` and restart Home Assistant. This makes the choice available; on its own it changes nothing.
3. In Settings, Devices & services, Panel Assistant, choose **Configure** on the panel and set **Control** to **Panel Assistant**. We'd suggest trying one panel first.

**Why switch.** One less moving part between your panels and Home Assistant, and it's where Panel Assistant is heading. Commands still follow the same approval rules, and anything sensitive still needs approval on the panel's own screen.

**What to watch for.** Each of these shows up in Repairs with the fix spelled out:

- An MQTT entity you've customised (a name, icon, area, label or alias of your own) that has no native equivalent keeps the panel's MQTT entities in place until you delete it or clear those settings.
- A panel still on an older ha-paneld keeps announcing over MQTT. Panel Assistant disables the duplicates until you update it.
- If Home Assistant has merged two panels' MQTT devices, the switch is refused until they're separated.

**Changing your mind.** Set **Control** back to **MQTT** and the entities move back. If you'd like to look before you leap, **MQTT, with native reports for comparison** keeps MQTT in charge while the panel reports over both.

### New

- **Updates for panels without internet access.** Home Assistant downloads and verifies each ha-paneld release, then sends it to the panel over your local network. Panels that can't accept an uploaded app (older than 0.8.6, or with APK upload turned off) still download it themselves. Home Assistant needs to reach GitHub for this.
- **A clearer device page.** It shows the panel's real model, such as Shelly Wall Display X2i, the app version with its build, and a new sensor showing which Panel Assistant build the panel is connected through.
- **Dutch, Polish and Ukrainian.** Setup, repair and error text is machine translated and checked against the English, which stays authoritative.
- **IPv6-only networks.** Home Assistant now discovers panels that have only IPv6 addresses.
- **Better diagnostics.** A panel's diagnostics download now names the database operation that failed.

### Improved

- **Panels stay online when their address changes.** A panel connected to Home Assistant is never shown as unavailable. If its stored address stops answering, Home Assistant confirms it's the same panel and switches to the address it's connecting from, and the Status sensor reads "Connected, address unreachable" until then. Re-adding a moved panel updates it instead of creating a duplicate.
- **New panels finish setup in one go.** When you add a panel that has already signed in, you confirm its account right there in the add flow, instead of finding a repair later.
- **No notification prompt on new panels.** Both installers grant the app's notification permission before its first start on Android 13 and later.
- **More forgiving installs.** Installing the version a panel already runs just finishes. Picking a different version after an interrupted attempt carries on with your new choice. The USB installer tidies up its copy of the app once the install works, and no longer stops with a false "couldn't save its progress" error.
- **Tidier native entities.** With a matching ha-paneld release, a panel can say which sensors it doesn't have, and their entities are removed instead of sitting unavailable.

### Fixed

- Panels whose MQTT devices Home Assistant had merged can no longer disable or delete each other's entities.
- Panel Assistant is ready for ha-paneld's new app id: releases under either id install from Home Assistant, the USB installer and the manual file route, and a panel isn't marked as moved to the new app until the new app answers.
- A successful tap on the panel screenshot in the sidebar is no longer reported as refused.
- Choosing a third version in the browser installer is no longer refused as busy.
- Files copied over USB carry the right date instead of 1970.
- Panels running development builds can be read.

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
