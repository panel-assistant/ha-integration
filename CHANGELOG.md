# Changelog

All notable changes to this project will be documented in this file.

## 0.7.0-rc1 - 2026-09-30

This release pairs with ha-paneld v0.9.9-rc1, the last panel release that supports MQTT and the last one that can move an older MQTT panel over to Panel Assistant's own connection. If your panels still use MQTT, please move them now. Future feature releases will build on Panel Assistant and its connection, so MQTT set-ups will not get new features after this. MQTT still works in this release.

**Trying this release candidate through HACS:** open **Settings, Devices & services, HACS**, then the **Panel Assistant** device. Its **Pre-release** switch is under **Diagnostic** and disabled by default: enable it, turn it on, and HACS offers release candidates as updates.

### Moving off MQTT

- **Move your panels now.** Update each panel to ha-paneld v0.9.9-rc1, add it to Panel Assistant, then choose **Configure** on the panel and set **Control** to **Panel Assistant**. No YAML is needed.

### New

- **Update panels that have no internet access.** Home Assistant now downloads and checks the signed release itself and hands it to the panel over your local network.
- **Restart notices.** While a panel restarts, its status and the sidebar say so for a short time instead of it just going quiet.
- **Pick a known panel when adding one.** The add-panel flow now offers panels it already knows about.
- **Readable support reports.** Installer reports are laid out for people to read, and installer failures show up in Repairs.
- **Builds on the device card.** You can see which build each panel and the integration are running.
- **Voice assistant preview.** A panel that offers voice appears as an Assist satellite on its own device, with a listening colour per pipeline. This is an early preview that may change.

### Improved

- **Hands-free identity upgrade.** Panels moving to the new app identity finish the handover on their own, even without internet access, and keep their device and entities.
- **Panels that move address are followed.** A moved panel is verified at its new address and keeps its device and entities.
- **IPv6-only panels can be added.** Panels found over IPv6 alone are now offered.
- **Clearer ADB update approval.** Approving an update over ADB is easier to find, names the panel and clears itself once done.

### Fixed

- **Update repairs clear themselves.** A failed update repair retries the current release and disappears once the panel is on the right build.
- **Panels no longer show a "null" area.** Panels stuck with a literal null area are repaired.
- **Installs are more reliable.** An install only completes once the panel is confirmed as the home screen, and your existing setup is kept.

## 0.6.3 - 2026-09-27

### Fixed

- **New panels talk to Home Assistant directly.** A panel added to a Home Assistant without MQTT came up with almost nothing, because it still started with MQTT in charge. Panels you add now use Panel Assistant's own connection from the start, and their sensors and controls appear straight away.
- **Switching a panel off MQTT no longer needs YAML.** Choose **Configure** on the panel and set **Control** to **Panel Assistant**. The `native_entities` line in `configuration.yaml` is no longer needed; if you set it, `true` or `false` still decides for every panel.

Panels you added before this release keep their current setting.

## 0.6.2 - 2026-09-27

### Fixed

- **You can add a new panel again.** Choosing the recommended ha-paneld version left the field looking empty, and Home Assistant refused to continue with "Not all required fields are filled in". Once ha-paneld 0.9.8 replaced its test versions, that left no way to install on a new panel from the setup screen. The recommended version now fills in and the form moves on.

## 0.6.1 - 2026-09-26

### New

- **Offline panels can move to the new app.** A panel that can't reach the internet can now move to ha-paneld's new app: Home Assistant downloads and checks the new app and hands it to the panel over your local network, the same way it already delivers updates. Needs ha-paneld 0.9.8-rc2 or later.

### Improved

- **An unfinished move stays visible.** A panel that hasn't finished moving to the new app shows as a pending update, so you can see it and try again.
- **Retrying a move picks up where it left off**, reusing the new app if it's already on the panel.
- **Updates match the app each panel runs**, old or new, and a panel partway through the move is only offered releases that are genuinely newer.

### Fixed

- **Safety wording in Dutch, Polish and Ukrainian** is corrected throughout setup, approval and recovery text.

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

### New

- **Ready for ha-paneld's new app id.** Panel Assistant installs and checks releases under either the old or the new id, and prefers the new one when a release offers both. Install this before the ha-paneld release that changes the id; older Panel Assistant versions refuse it.
- **Smooth move to the new app.** A panel running the old app is no longer refused. The new app installs beside it, then the panel moves its own settings across and removes the old app by itself. The install waits for that to finish, and if it's still going when Panel Assistant stops watching, a repair lets you check again later.
- **No more typing Home Assistant's address into a panel.** When Panel Assistant installs or adopts a panel, it tells the panel where Home Assistant is. The panel checks the address works from its own network first, and only a local address is ever passed on. Needs a matching ha-paneld release.

### Improved

- The panel's settings backup is verified before an update replaces the app, and a record of it is kept alongside. An unreadable backup stops the update.
- Release lookups follow ha-paneld to its new repository address.

## 0.4.1 - 2026-09-16

### Fixed

- The sidebar's panel picker no longer logs a deprecation warning when it looks up a panel's name.

## 0.4.0 - 2026-09-16

### New

- **Fewer approval prompts from the sidebar.** When a panel offers it, Home Assistant gives the sidebar a signing key, and signed requests for lower-impact settings, such as display and power, no longer need approval on the panel. Everything else still does. Hardened mode requires physical access to the panel. High-impact remote actions cannot proceed until someone approves them on the panel's screen; they cannot be approved remotely. Needs a matching ha-paneld release.
- **A device page button in the sidebar** that opens the selected panel's own device page.

### Improved

- The sidebar's top bar has proper icon buttons matching Home Assistant's own header: GitHub, add a panel, integration settings and the new device page.
- The panel picker shows each panel's real name instead of its raw identifier.
- The picker keeps its contrast in dark mode, and opening a panel shows a spinner.

## 0.3.1 - 2026-09-16

### Improved

- HACS now shows the Panel Assistant icon and the same introduction as panel-assistant.io, with one-click buttons to open it in HACS and start setup. No code changes.

## 0.3.0 - 2026-09-14

### New

- **Your panel's own interface in the sidebar.** Pick a panel from the top menu and you get its full web interface, with the same dashboard, settings and logs as its `:8888` page. Adding a panel and the integration's settings are in the same bar.
- **Native connection, for testing.** Setting `native_entities: true` under `panel_assistant:` in `configuration.yaml` lets a panel report and take commands over its own connection instead of MQTT, chosen per panel in the integration's options. Off by default; MQTT is unaffected.

### Improved

- The sidebar shows the installed Panel Assistant version and build, so you no longer need a diagnostics download to check it.

### Fixed

- Renaming a panel, retrying a failed handover or losing power partway through no longer leaves stray or duplicate entities.

## 0.2.1 - 2026-09-13

The first full release, so HACS offers it without turning on beta versions.

### Improved

- The version list only offers ha-paneld releases with a signed install description. Until the next stable ha-paneld ships one, that means release candidates, marked as test versions.

### Fixed

- The sidebar's Install link opens the installer again.

## 0.2.0b1 - 2026-09-12

### New

- **A new name.** The integration is now Panel Assistant, with proper version numbers. Earlier installs won't be offered this as an update, so follow "Updating from 0.1.x" in the README.
- **Install over USB.** Plug the panel into the computer you're browsing on and press Install once. The installer copies, installs and starts the app, grants what it needs and hands over to the panel's own setup, with plain progress and technical detail tucked behind "Details for support". Home Assistant doesn't need a TLS certificate for this.
- **Choose your version,** with the newest stable release picked for you and release candidates marked for testing.
- **One way to add a panel.** Start with its address and Home Assistant works out whether to connect it, offer an install, or explain what's missing. A panel already running ha-paneld offers to open its setup if that isn't finished.
- **Helpful errors** when a panel can't be reached, with per-model help on panel-assistant.io.
- **Device details** for each panel: manufacturer, model, Android version and area.
- **Internal build feed,** optional, set with `build_feed` under `panel_assistant:` in `configuration.yaml`.

### Improved

- Reinstalling the version a panel already runs finishes its setup instead of stopping with an error.
- The panel isn't offered the same update twice once Home Assistant shows it.
- The Home Assistant pages and the installer match the panel's own setup wizard.

### Fixed

- USB installs work on panels whose debug bridge refused the app's port, wait for the app to start before checking it, and set the right file permissions. The installer never tells you to unplug a panel powered by that cable.

## 0.1.0 - Unreleased

### New

- **Add a panel** by hostname or IP address, checked through its health endpoint, with one device, a status sensor and downloadable diagnostics.
- **Install over the network.** Home Assistant checks the panel and the signed release, installs and starts the app, and adds the panel only once it answers as expected. Panels that need debug approval ask for it once on screen.
- **Try a release candidate** while keeping the latest stable release as the default.

### Improved

- An install carries on if its dialog closes, resumes safely after a reload, and never repeats a step whose outcome is unclear.
- Existing installs are never upgraded or overwritten by this release.
