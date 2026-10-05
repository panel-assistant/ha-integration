# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

**Update Panel Assistant before your panels take ha-paneld v0.9.10.** v0.9.10 moves the app's code to its new name, and only this Panel Assistant release knows how to install it, start it and grant its permissions. Panels connected to an older Panel Assistant are not offered v0.9.10: they stay on the build they have and keep working, and they get the update once you install this release.

**Panels Home Assistant already knows now show up under Discovered.** If a panel runs the Home Assistant Companion app or Fully Kiosk, or reaches Home Assistant through ESPHome or a Shelly Wall Display, Panel Assistant finds it on its own and lists it under **Settings, Devices & services, Discovered**, even before you have added a panel. Choose **Add** to install Panel Assistant's app on it; the old app and its device stay as they are. **Ignore** keeps it out of the list for good, and phones running the Companion app are never offered. A Companion panel is found only while its Wi-Fi IP address sensor is enabled, and discovery relies on Home Assistant's standard DHCP discovery.

**Voice that says why it is not listening.** A panel whose microphone has not yet been proven to work checks it before listening. If the check hears only silence or gets no audio, Home Assistant shows a Repair naming the panel and what the check found, instead of the voice assistant quietly not answering. Turning the voice assistant off and on again in the panel's settings checks it again.

**A fuller device card.** A panel's device card in Home Assistant shows its vendor firmware and Android release on the Hardware line, for example `1.11.0 · Android 8.1.0`, and its serial number: the panel's own hardware serial where the app can read it, otherwise its Android ID. The product name and app build still lead the card. Panels need an app build that reports these; on older builds the card stays as it was.

**Voice timers.** Say "set a timer for one minute" to a panel whose voice assistant is on, and the panel rings when the time is up, then says the timer's name if you gave it one. Cancelling, pausing, adding time and asking how long is left work too. Home Assistant keeps the timers, as it does for a Voice Preview Edition; the panel only rings.

## 0.7.0 - 2026-10-04

Panel Assistant 0.7.0 takes over the jobs panels used to do for themselves. It is the last Panel Assistant release that supports MQTT panels and the last that moves them over to its own connection, so if your panels still use MQTT, please move them now. It also moves panels to the new app from Home Assistant with one click. Sorry, Sonoff NSPanel Pro owners: before this, each panel had to hand itself over, which needed a root helper that some NSPanel Pros have no room for. If yours got stuck, or 0.6.3 told you "The release did not match its signature, so nothing was installed", that was my approach, not your panel, and you should never need Magisk or to delete anything from the system partition.

Pairs with ha-paneld v0.9.9.

### Moving off MQTT

**Move your panels now.** Each panel that has used MQTT gets a Repair that moves it to Panel Assistant's own connection; panels that never used MQTT never see it. You can also choose **Configure** on the panel and set **Control** to **Panel Assistant**. No YAML is needed. MQTT still works in this release, but future feature releases build on Panel Assistant's connection, so MQTT set-ups will not get new features after this.

### Moving from the old app

**The panel app has a new Android app id, `io.panelassistant.android`.** A panel still running the old ha-paneld app, including one whose earlier move was refused or stopped partway, gets a "Move to the new app" Repair under **Settings, Repairs**. One click backs the panel up into Home Assistant, installs the new app over your network and carries its settings, device and entities across. You don't need to root the panel or type any commands. If the panel asks whether to allow USB debugging, tap Allow. Once the new app is clearly running the panel, Panel Assistant removes the old app automatically, and if it can't, a Repair says why. Updating a panel that still runs the old app points you to this Repair instead of starting a move of its own.

**Move before v1.0.** Moving panels to the new app is supported through the 0.x releases only. v1.0 will not move panels, so please move yours before then.

### New

- **Voice assistant preview.** A panel that offers voice appears as an Assist satellite on its own device, with a listening colour for each pipeline. It is an early preview and may change.
- **Panel cameras and speakers as native entities.** A panel with a camera appears as a camera, and its speaker as a media player with its volume, ready for announcements. No MQTT involved.
- **Panels hear about Home Assistant restarts.** Panel Assistant tells each panel when Home Assistant is shutting down and when it is ready again, and, once it has timed a restart, how long one usually takes on your system. While a panel itself restarts, its status and the sidebar say so.
- **Repairs that tell you what needs doing.** You get one when Home Assistant needs a restart to load a newer Panel Assistant, when a panel is missing an Android permission it needs, and when a panel can no longer be updated at all, which used to go unnoticed because the panel still looked healthy. Installer failures show up in Repairs too, with support reports laid out for people to read.
- **Pick a known panel when adding one.** The add-panel flow offers panels it already knows about.
- **Builds on the device card.** You can see which build each panel and the integration are running.

### Improved

- **Panel updates are easier to follow.** The update dialog shows one steady step line, and an update is only marked done once the restarted panel is back on its dashboard. A retried update finishes when the panel has already updated.
- **Panels follow Panel Assistant's update channel.** A panel is offered only builds that match the channel Panel Assistant is on, and pre-releases only when that panel's pre-release switch is on.
- **Network installs match USB installs.** A panel installed over the network gets the same Android permissions as one installed over USB, so touch sounds and similar controls work straight away. Panel Assistant also puts back any permission Android shows as missing, and if a panel refuses one, the log says so and the install still finishes.
- **The sidebar works on a phone**, with Home Assistant's menu button and an overflow menu, and panel settings open there too.
- **Panels that move address are followed**, verified at the new address and kept with their device and entities.
- **Network debugging asks first.** Approving an update over ADB is easier to find and names the panel, a new key is only offered to the panel when you ask, background checks never pop up an approval prompt, and Panel Assistant rechecks what you agreed to just before it installs.

### Fixed

- **No more false or stuck update Repairs.** A failed update Repair retries the current release and clears once the panel is on the right build, and a panel is never asked to update to the version it already runs.
- **Installs only complete once the panel is confirmed as the home screen**, and your existing setup is kept.
- **A panel that already moved itself to the new app is adopted** instead of getting a misleading Repair about its address, and an adoption interrupted by a restart finishes at start-up.
- **Adding a panel whose old app is not answering no longer installs the new app beside it.** Home Assistant asks you to open the old app or restart the panel first.
- **No alarming messages while a panel is getting ready**, and USB-installed panels skip asking where Home Assistant is.
- **Panels no longer show a "null" area.**
- **Media permission Repairs only appear when you use a feature that needs them.**
- **Translation corrections** from a review of every language.

## 0.7.0-rc5 - 2026-10-04

Pairs with ha-paneld v0.9.9-rc5.

### New

- **Each panel's speaker shows up as a media player**, with its volume, ready for announcements.
- **Panel Assistant removes the old app when it is left beside the new one.** Once the new app is clearly running the panel, Panel Assistant backs the panel up and uninstalls the old app with nothing for you to click. If it can't, a Repair says why: network debugging is off or refused, or it couldn't confirm the new app is the one in charge.

### Fixed

- **Moving a panel to the new app takes one run.** Before, the move restored the panel's settings twice and the second restore could fail, so the Repair said the move had not finished and had to be run again.
- **A panel that already moved itself to the new app is adopted** instead of getting a misleading Repair about its address.
- **Background checks never ask the panel to trust Home Assistant's debugging key.** An adoption interrupted by a restart now finishes at start-up.
- **The restart Repair appears promptly after an update is installed.**
- **Media permission Repairs only appear when you use a feature that needs them.**
- **A failed update-route check waits before trying again** instead of retrying in a tight loop.

## 0.7.0-rc4 - 2026-10-03

Pairs with ha-paneld v0.9.9-rc4.

### New

- **A Repair when a panel can no longer be updated.** A panel gets app updates either by installing them itself or through Home Assistant over network debugging. A panel that loses both still looks perfectly healthy, so until now nobody noticed it had stopped updating. Panel Assistant now raises a Repair that explains what happened and links to the fix. It does not raise one for a dropped connection or a panel that is just restarting.

### Improved

- **Panels follow Panel Assistant's update channel.** A panel is offered only builds that match the channel Panel Assistant itself is on and that it can talk to, and pre-releases only when that panel has its pre-release switch on. The newest eligible build wins across all release sources.
- **Panel settings open in the Home Assistant sidebar.**

### Fixed

- **The move Repair finishes panels caught halfway.** That covers three cases:
  - a panel whose new app only ever waited beside the old one;
  - a move that was accepted but not completed;
  - a move whose identity reached Home Assistant only partly.

  If the new app's records can't be read, the Repair now treats them as unknown rather than empty.
- **Adding a panel whose old app is not answering no longer installs the new app beside it.** Home Assistant asks you to open the old app or restart the panel first.
- **A retried update finishes when the panel has already updated** instead of waiting forever.
- **Network debugging asks before it offers a new key to the panel.** Passive checks no longer pop up an approval prompt on the panel.
- **Updates and the move Repair recheck what you agreed to just before installing**, so a setting you changed in the meantime is respected.

## 0.7.0-rc3 - 2026-10-02

**Sorry, Sonoff NSPanel Pro owners.** The move to the app's new name was first built the way ha-paneld did everything in its MQTT-only days: entirely on the panel. The panel had to hand itself over to the new app, and that needed the root helper, which since version 0.9.4 has to be registered in the panel's system partition. On some NSPanel Pros that partition is full, so the helper could not go in, even though the app on these panels can use root by itself and doesn't need the helper to run. If your panel got stuck partway through the move, refused to install, or Panel Assistant 0.6.3 told you "The release did not match its signature, so nothing was installed", that was down to this approach, not your panel. Please don't install Magisk or delete anything from the system partition to get round it; you should never need to.

The fix plays to what Panel Assistant is there for. Instead of asking each panel to move itself, Panel Assistant 0.7.0-rc3 does the heavy lifting from Home Assistant: it shows a Repair for each panel that still needs to move, and one click backs the panel up, moves it to the new app over your network and keeps its settings. If the panel asks whether to allow USB debugging, tap Allow. Having one central Panel Assistant looking after the panels has already made this move far simpler, and it is how future moves will be done. One case isn't handled yet: if the new app has already run beside the old app, for example after an earlier attempt, the Repair says so and changes nothing; a later release will cover it. The ha-paneld 0.9.9-rc3 installer also stops failing on these panels: when there is nowhere to put the helper, it warns and finishes without it. Thank you to everyone who reported this and sent logs.

Pairs with ha-paneld v0.9.9-rc3.

### New

- **Move a panel to the new app from Home Assistant.** A panel still running the old ha-paneld app, including one whose move to the new app was refused or stopped partway, gets a Repair called "Move (panel name) to the new app". One click backs the panel up into Home Assistant, installs the new app over your network, carries its settings across and removes the old app. You don't need to root the panel or type any commands. If the panel asks whether to allow USB debugging, tap Allow. If the new app has already run beside the old app, the Repair says so and changes nothing for now.
- **A Repair when a panel is missing an Android permission.** When Panel Assistant installs or updates the app on a panel it is allowed to reach over network debugging, it puts back any permission the app needs that Android shows as missing, and leaves the rest alone. If the panel still reports a missing permission, a Repair shows the steps to take on the panel.
- **Panels hear about Home Assistant restarts.** Panel Assistant tells each panel when Home Assistant is shutting down and when it is ready again, and, once it has timed a restart, how long one usually takes on your system.

### Improved

- **Updates no longer start a panel's own move.** Updating a panel that still runs the old app now points you to its Repair instead, so the two cannot get in each other's way.

### Fixed

- **Translations.** Corrections from the quarterly review of the translated text in every language.

## 0.7.0-rc2 - 2026-10-01

The second release candidate for Panel Assistant 0.7.0 brings smoother panel updates, helpful Repairs and a native camera. Reminder: 0.7.0 is the last Panel Assistant release that supports MQTT panels and the last that moves them over to its own connection. Pairs with ha-paneld v0.9.9-rc2.

### New

- **Panel cameras as native camera entities.** A panel with a camera now appears in Home Assistant as a camera, with no MQTT involved.
- **A Repair to move off MQTT.** Each panel that has used MQTT gets a Repairs item that moves it to Panel Assistant's own connection. Panels that never used MQTT never see it.
- **A Repair when Home Assistant needs a restart.** After you install a newer Panel Assistant, Repairs tells you a restart is needed to load it.

### Improved

- **Panel updates are easier to follow.** The update dialog shows one steady step line, moves at a pace that matches what the panel is doing, and finishes only once the panel is showing its dashboard again.
- **An update stays offered until the panel is back.** The update is only marked done once the restarted panel has proven it is running.
- **Network installs match USB installs.** A panel installed over the network gets the same Android permissions as one installed over USB, so touch sounds and similar controls work straight away. If a panel refuses one, the log says so and the install still finishes.
- **The sidebar works on a phone.** The Panel Assistant header now has Home Assistant's menu button and an overflow menu, stays on one row, and shows a dot only for notifications that are really there.

### Fixed

- **No more false update Repairs.** Panel Assistant no longer asks a panel to update to a version it already runs, such as the stray "update to 0.9.8-rc1" Repair.
- **New panels finish setup without MQTT.** Panels added on a system without MQTT no longer get stuck partway through setup.
- **No alarming messages while a panel is getting ready.** A panel that cannot hand over to Panel Assistant yet now waits quietly instead of showing an error.
- **USB-installed panels know Home Assistant set them up.** These panels now skip asking where Home Assistant is.

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
- A panel installed over the network gets the same Android permissions as one installed over USB: changing system settings, showing over other apps and its accessibility service. Touch sounds and other controls that need them no longer stay unavailable after a network install. If the panel refuses any of them, the Home Assistant log reports it and the install still finishes.

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
