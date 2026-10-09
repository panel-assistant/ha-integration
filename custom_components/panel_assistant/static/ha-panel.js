const he = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Open navigation", choosePanel: "Panel", more: "More options", addPanel: "Add panel", integrationSettings: "Integration settings", device: "This panel's Home Assistant device", showDevice: "Show device", github: "GitHub", unreachable: "unreachable", restarting: "Restarting ({reason})", not_loaded: "not loaded", opening: "Opening {panel}…", loadingHint: "Usually takes a few seconds", empty: "No panels are attached yet.", failed: "The panel could not be opened. It will be tried again shortly.", admin: "An administrator must open this page.", unreachableBody: "Home Assistant cannot reach this panel right now.", notLoadedBody: "This panel is not loaded in Home Assistant.", closed: "This panel was closed.", frameTitle: "Panel interface", picklesStory: "Pickles the panda has escaped and is causing havoc. He’s slow and stubborn, so getting him back may take a moment.", unreachableNext: "Check that the panel is powered on and connected to your network.", notLoadedNext: "Open Integration settings to check this panel’s connection.", failedNext: "Wait a moment while we try again, or choose another panel.", closedNext: "Choose another panel, or wait for this panel to reconnect." }, me = { title: "Install ha-paneld on a panel", introduction: "Plug the panel into this computer with a USB cable. A new window will find it and install the app.", release: "Version", loading: "Loading versions…", catalogError: "The list of versions couldn’t be loaded.", empty: "No versions are available yet. Try again later.", choose: "Follow Panel Assistant’s channel (recommended)", testing: "test version", devBuild: "dev build", retry: "Try again", start: "Continue", cancel: "Cancel", ready: "", unavailable: "The installer isn’t available. Update Panel Assistant, then try again.", admin: "Ask a Home Assistant administrator to install panels.", waiting: "Continue in the new window.", preparing: "Getting the app ready…", downloading: "Getting the app ready…", verifying: "Getting the app ready…", verified: "Continue in the new window.", cancelled: "Cancelled.", popup_blocked: "Your browser blocked the new window. Allow pop-ups for this page, then press Continue.", invalid_request: "Choose a version first.", failed: "That didn’t work. Press Continue to try again." }, be = { title: "Set up your panel", preparing: "Getting the app ready…", preparingSlow: "Still getting the app ready. Keep the Home Assistant tab open.", connectHeading: "Connect your panel", connectBody: "Plug the panel into this computer with a USB cable, then press Find my panel.", connect: "Find my panel", allowHeading: "Allow this computer", allowBody: "Look at the panel’s screen and tap Allow.", checkingPanel: "Checking your panel…", confirmHeading: "Ready to install", confirmBody: "This installs the app and makes it your panel’s home screen. Your other apps are not touched.", install: "Install", restartedDifferentVersion: "An earlier installation attempt used a different release. That attempt has been set aside.", alreadyInstalledHeading: "Already installed", alreadyInstalledBody: "This version is already on your panel. Continue to finish setting it up; nothing is copied or reinstalled.", continueSetup: "Continue", progressHeading: "Installing", stepCopying: "Copying the app to your panel…", stepFinishingCopy: "Finishing the copy…", stepInstalling: "Installing…", stepStarting: "Starting the app…", stepPermissions: "Giving the app what it needs to run…", stepOpening: "Opening your panel’s setup…", keepConnected: "Keep the cable plugged in until this finishes.", doneHeading: "Installed", doneOpening: "Taking you to your panel’s setup…", doneManual: "Finish setting up on the panel’s screen.", openSetup: "Open panel setup", errorHeading: "That didn’t work", tryAgain: "Try again", backToHa: "Back to Home Assistant", details: "Details for support", unsupported: "This browser can’t talk to USB devices. Open this page in Chrome or Edge on a computer.", handoffFailure: "The app couldn’t be fetched from Home Assistant. Go back to Home Assistant and start again.", noSelection: "No panel was chosen. Press Find my panel and pick it from the list, or try another cable: some cables power the panel but carry no data.", connectionTimeout: "The panel didn’t answer. Check the cable is firmly in, or swap it: a cable can power the panel and still carry no data.", disconnected: "The panel was disconnected. Plug it back in, wait for it to start, then press Try again.", cancelled: "Cancelled.", pageClosed: "The page was closed.", progress: "Progress", installProgress: "Installation progress", licenses: "Third-party licenses" }, fe = { installErrorGeneric: "Something went wrong. Keep the panel plugged in and press Try again.", installErrorBusy: "The panel is busy with another install. Wait a minute, then try again.", installErrorStorage: "This browser couldn’t save its progress. Allow this site to store data, then try again.", installErrorTarget: "A different panel was connected. Plug in the same panel and try again.", installErrorArtifact: "The app download couldn’t be checked. Go back to Home Assistant and start again.", installErrorNotClean: "This panel already has the app. Update it from Home Assistant instead.", installErrorIncompatible: "This panel can’t run this version of the app.", installErrorConnection: "The connection to the panel dropped. Keep it plugged in and press Try again.", installErrorHealth: "The app is installed but hasn’t started yet. Wait a moment, then try again." }, ve = { version: "Version", connect: "Connect", install: "Install", setup: "Set up" }, ye = { update: "update", settings: "settings", recovery: "recovery", reboot: "reboot" }, ke = { stopped: "Stopped", cause: "Cause", error: "Error", release: "Release", panel: "Panel", copied: "Copied", savedProgress: "Saved progress", earlierCopy: "Earlier copy", stagedCopy: "Staged copy", setAside: "Set aside", alreadyInstalled: "Already installed", permissions: "Permissions", setupAddress: "Setup address", setupHandover: "Setup handover", copiedDetail: "{bytes} bytes sent; waiting for the panel to confirm", none: "none", setupMissing: "not found; setup continues on the panel" }, we = {
  sidebar: he,
  haInstall: me,
  installer: be,
  errors: fe,
  journey: ve,
  sidebarReason: ye,
  support: ke
}, Ae = { title: "Panel Assistant", versionLabel: "{version}, sestavení {build}", menu: "Otevřít navigaci", choosePanel: "Panel", more: "Další možnosti", addPanel: "Přidat panel", integrationSettings: "Nastavení integrace", device: "Zařízení tohoto panelu v Home Assistant", showDevice: "Zobrazit zařízení", github: "GitHub", unreachable: "nedostupný", restarting: "Restartování ({reason})", not_loaded: "nenačtený", opening: "Otevírání panelu {panel}…", loadingHint: "Obvykle to trvá několik sekund", empty: "Zatím nejsou připojené žádné panely.", failed: "Panel se nepodařilo otevřít. Brzy proběhne další pokus.", admin: "Tuto stránku musí otevřít správce.", unreachableBody: "Home Assistant se k tomuto panelu momentálně nedostane.", notLoadedBody: "Tento panel není v Home Assistant načtený.", closed: "Tento panel byl zavřen.", frameTitle: "Rozhraní panelu", picklesStory: "Panda Pickles utekl a tropí neplechu. Je pomalý a tvrdohlavý, takže dostat ho zpátky může chvíli trvat.", unreachableNext: "Zkontrolujte, že je panel zapnutý a připojený k vaší síti.", notLoadedNext: "Otevřete Nastavení integrace a zkontrolujte připojení tohoto panelu.", failedNext: "Chvíli počkejte, než to zkusíme znovu, nebo vyberte jiný panel.", closedNext: "Vyberte jiný panel nebo počkejte, až se tento panel znovu připojí." }, ze = { title: "Nainstalovat ha-paneld na panel", introduction: "Připojte panel k tomuto počítači kabelem USB. Nové okno ho najde a nainstaluje aplikaci.", release: "Verze", loading: "Načítání verzí…", catalogError: "Seznam verzí se nepodařilo načíst.", empty: "Zatím nejsou dostupné žádné verze. Zkuste to znovu později.", choose: "Podle kanálu Panel Assistant (doporučeno)", testing: "testovací verze", devBuild: "vývojové sestavení", retry: "Zkusit znovu", start: "Pokračovat", cancel: "Zrušit", ready: "", unavailable: "Instalátor není dostupný. Aktualizujte Panel Assistant a pak to zkuste znovu.", admin: "Požádejte správce Home Assistant o instalaci panelů.", waiting: "Pokračujte v novém okně.", preparing: "Příprava aplikace…", downloading: "Příprava aplikace…", verifying: "Příprava aplikace…", verified: "Pokračujte v novém okně.", cancelled: "Zrušeno.", popup_blocked: "Prohlížeč zablokoval nové okno. Povolte pro tuto stránku vyskakovací okna a pak stiskněte Pokračovat.", invalid_request: "Nejprve vyberte verzi.", failed: "To se nepodařilo. Stiskněte Pokračovat a zkuste to znovu." }, Ce = { title: "Nastavte svůj panel", preparing: "Příprava aplikace…", preparingSlow: "Aplikace se stále připravuje. Nechte kartu Home Assistant otevřenou.", connectHeading: "Připojte svůj panel", connectBody: "Připojte panel k tomuto počítači kabelem USB a pak stiskněte Najít můj panel.", connect: "Najít můj panel", allowHeading: "Povolit tento počítač", allowBody: "Podívejte se na obrazovku panelu a klepněte na Povolit.", checkingPanel: "Kontrola panelu…", confirmHeading: "Připraveno k instalaci", confirmBody: "Tímto nainstalujete aplikaci a nastavíte ji jako domovskou obrazovku panelu. Vašich ostatních aplikací se to nedotkne.", install: "Nainstalovat", restartedDifferentVersion: "Předchozí pokus o instalaci používal jiné vydání. Ten pokus byl ponechán stranou.", alreadyInstalledHeading: "Už nainstalováno", alreadyInstalledBody: "Tato verze už na vašem panelu je. Pokračujte a dokončete nastavení; nic se nekopíruje ani znovu neinstaluje.", continueSetup: "Pokračovat", progressHeading: "Instalace", stepCopying: "Kopírování aplikace do panelu…", stepFinishingCopy: "Dokončování kopírování…", stepInstalling: "Instalování…", stepStarting: "Spouštění aplikace…", stepPermissions: "Poskytování toho, co aplikace potřebuje k běhu…", stepOpening: "Otevírání nastavení panelu…", keepConnected: "Nechte kabel připojený, dokud se proces nedokončí.", doneHeading: "Nainstalováno", doneOpening: "Přesměrování na nastavení panelu…", doneManual: "Dokončete nastavení na obrazovce panelu.", openSetup: "Otevřít nastavení panelu", errorHeading: "To se nepodařilo", tryAgain: "Zkusit znovu", backToHa: "Zpět do Home Assistant", details: "Podrobnosti pro podporu", unsupported: "Tento prohlížeč nedokáže komunikovat se zařízeními USB. Otevřete tuto stránku v prohlížeči Chrome nebo Edge na počítači.", handoffFailure: "Aplikaci se nepodařilo získat z Home Assistant. Vraťte se do Home Assistant a začněte znovu.", noSelection: "Nebyl vybrán žádný panel. Stiskněte Najít můj panel a vyberte ho ze seznamu nebo zkuste jiný kabel: některé kabely panel napájejí, ale nepřenášejí data.", connectionTimeout: "Panel neodpověděl. Zkontrolujte, že je kabel pevně připojený, nebo ho vyměňte: kabel může panel napájet a přitom nepřenášet data.", disconnected: "Panel byl odpojen. Znovu ho připojte, počkejte, až se spustí, a pak stiskněte Zkusit znovu.", cancelled: "Zrušeno.", pageClosed: "Stránka byla zavřena.", progress: "Průběh", installProgress: "Průběh instalace", licenses: "Licence třetích stran" }, Se = { installErrorGeneric: "Něco se pokazilo. Nechte panel připojený a stiskněte Zkusit znovu.", installErrorBusy: "Panel je zaneprázdněný jinou instalací. Počkejte minutu a pak to zkuste znovu.", installErrorStorage: "Tento prohlížeč nedokázal uložit průběh. Povolte tomuto webu ukládání dat a pak to zkuste znovu.", installErrorTarget: "Byl připojen jiný panel. Připojte stejný panel a zkuste to znovu.", installErrorArtifact: "Staženou aplikaci se nepodařilo ověřit. Vraťte se do Home Assistant a začněte znovu.", installErrorNotClean: "Tento panel už aplikaci má. Aktualizujte ji z Home Assistant.", installErrorIncompatible: "Tento panel nedokáže spustit tuto verzi aplikace.", installErrorConnection: "Spojení s panelem se přerušilo. Nechte ho připojený a stiskněte Zkusit znovu.", installErrorHealth: "Aplikace je nainstalovaná, ale ještě se nespustila. Chvíli počkejte a pak to zkuste znovu." }, Pe = { version: "Verze", connect: "Připojit", install: "Nainstalovat", setup: "Nastavit" }, xe = { update: "aktualizace", settings: "nastavení", recovery: "obnova", reboot: "restart" }, je = { stopped: "Zastaveno", cause: "Příčina", error: "Chyba", release: "Vydání", panel: "Panel", copied: "Zkopírováno", savedProgress: "Uložený průběh", earlierCopy: "Dřívější kopie", stagedCopy: "Připravená kopie", setAside: "Odloženo", alreadyInstalled: "Už nainstalováno", permissions: "Oprávnění", setupAddress: "Adresa nastavení", setupHandover: "Předání nastavení", copiedDetail: "Odesláno {bytes} bajtů; čeká se na potvrzení panelu", none: "žádné", setupMissing: "nenalezeno; nastavení pokračuje na panelu" }, He = {
  sidebar: Ae,
  haInstall: ze,
  installer: Ce,
  errors: Se,
  journey: Pe,
  sidebarReason: xe,
  support: je
}, Ee = { title: "Panel Assistant", versionLabel: "{version} Build {build}", menu: "Navigation öffnen", choosePanel: "Panel", more: "Weitere Optionen", addPanel: "Panel hinzufügen", integrationSettings: "Integrationseinstellungen", device: "Home Assistant-Gerät dieses Panels", showDevice: "Gerät anzeigen", github: "GitHub", unreachable: "nicht erreichbar", restarting: "Neustart ({reason})", not_loaded: "nicht geladen", opening: "{panel} wird geöffnet…", loadingHint: "Dauert normalerweise nur wenige Sekunden", empty: "Es sind noch keine Panels verbunden.", failed: "Das Panel konnte nicht geöffnet werden. In Kürze wird es erneut versucht.", admin: "Diese Seite muss von einem Administrator geöffnet werden.", unreachableBody: "Home Assistant kann dieses Panel gerade nicht erreichen.", notLoadedBody: "Dieses Panel ist in Home Assistant nicht geladen.", closed: "Dieses Panel wurde geschlossen.", frameTitle: "Panel-Oberfläche", picklesStory: "Der Panda Pickles ist ausgebüxt und richtet Chaos an. Er ist langsam und stur, deshalb kann es einen Moment dauern, ihn zurückzuholen.", unreachableNext: "Prüfe, ob das Panel eingeschaltet und mit deinem Netzwerk verbunden ist.", notLoadedNext: "Öffne die Integrationseinstellungen, um die Verbindung dieses Panels zu prüfen.", failedNext: "Warte einen Moment, während wir es erneut versuchen, oder wähle ein anderes Panel.", closedNext: "Wähle ein anderes Panel oder warte, bis sich dieses Panel wieder verbindet." }, Le = { title: "ha-paneld auf einem Panel installieren", introduction: "Verbinde das Panel über ein USB-Kabel mit diesem Computer. Ein neues Fenster findet es und installiert die App.", release: "Version", loading: "Versionen werden geladen…", catalogError: "Die Versionsliste konnte nicht geladen werden.", empty: "Es sind noch keine Versionen verfügbar. Versuche es später erneut.", choose: "Dem Kanal von Panel Assistant folgen (empfohlen)", testing: "Testversion", devBuild: "Entwicklungsbuild", retry: "Erneut versuchen", start: "Weiter", cancel: "Abbrechen", ready: "", unavailable: "Das Installationsprogramm ist nicht verfügbar. Aktualisiere Panel Assistant und versuche es erneut.", admin: "Bitte einen Home Assistant-Administrator, Panels zu installieren.", waiting: "Fahre im neuen Fenster fort.", preparing: "Die App wird vorbereitet…", downloading: "Die App wird vorbereitet…", verifying: "Die App wird vorbereitet…", verified: "Fahre im neuen Fenster fort.", cancelled: "Abgebrochen.", popup_blocked: "Dein Browser hat das neue Fenster blockiert. Erlaube Pop-ups für diese Seite und drücke dann „Weiter“.", invalid_request: "Wähle zuerst eine Version.", failed: "Das hat nicht geklappt. Drücke „Weiter“, um es erneut zu versuchen." }, Qe = { title: "Dein Panel einrichten", preparing: "Die App wird vorbereitet…", preparingSlow: "Die App wird noch vorbereitet. Lass den Home Assistant-Tab geöffnet.", connectHeading: "Dein Panel verbinden", connectBody: "Verbinde das Panel über ein USB-Kabel mit diesem Computer und drücke dann „Mein Panel finden“.", connect: "Mein Panel finden", allowHeading: "Diesen Computer zulassen", allowBody: "Schau auf den Bildschirm des Panels und tippe auf „Zulassen“.", checkingPanel: "Dein Panel wird geprüft…", confirmHeading: "Bereit zur Installation", confirmBody: "Die App wird installiert und als Startbildschirm deines Panels eingerichtet. Deine anderen Apps bleiben unberührt.", install: "Installieren", restartedDifferentVersion: "Ein früherer Installationsversuch verwendete eine andere Version. Dieser Versuch wird nicht weiterverwendet.", alreadyInstalledHeading: "Bereits installiert", alreadyInstalledBody: "Diese Version ist bereits auf deinem Panel. Fahre fort, um die Einrichtung abzuschließen; es wird nichts kopiert oder neu installiert.", continueSetup: "Weiter", progressHeading: "Installation läuft", stepCopying: "Die App wird auf dein Panel kopiert…", stepFinishingCopy: "Der Kopiervorgang wird abgeschlossen…", stepInstalling: "Installation läuft…", stepStarting: "Die App wird gestartet…", stepPermissions: "Die App erhält alles, was sie zum Ausführen braucht…", stepOpening: "Die Einrichtung deines Panels wird geöffnet…", keepConnected: "Lass das Kabel angeschlossen, bis der Vorgang abgeschlossen ist.", doneHeading: "Installiert", doneOpening: "Du wirst zur Einrichtung deines Panels weitergeleitet…", doneManual: "Schließe die Einrichtung auf dem Bildschirm des Panels ab.", openSetup: "Panel-Einrichtung öffnen", errorHeading: "Das hat nicht geklappt", tryAgain: "Erneut versuchen", backToHa: "Zurück zu Home Assistant", details: "Details für den Support", unsupported: "Dieser Browser kann nicht mit USB-Geräten kommunizieren. Öffne diese Seite auf einem Computer in Chrome oder Edge.", handoffFailure: "Die App konnte nicht von Home Assistant abgerufen werden. Gehe zurück zu Home Assistant und beginne erneut.", noSelection: "Es wurde kein Panel ausgewählt. Drücke „Mein Panel finden“ und wähle es aus der Liste oder versuche ein anderes Kabel: Manche Kabel versorgen das Panel mit Strom, übertragen aber keine Daten.", connectionTimeout: "Das Panel hat nicht geantwortet. Prüfe, ob das Kabel fest sitzt, oder tausche es aus: Ein Kabel kann das Panel mit Strom versorgen, ohne Daten zu übertragen.", disconnected: "Die Verbindung zum Panel wurde getrennt. Schließe es wieder an, warte, bis es gestartet ist, und drücke dann „Erneut versuchen“.", cancelled: "Abgebrochen.", pageClosed: "Die Seite wurde geschlossen.", progress: "Fortschritt", installProgress: "Installationsfortschritt", licenses: "Lizenzen von Drittanbietern" }, Ie = { installErrorGeneric: "Etwas ist schiefgegangen. Lass das Panel angeschlossen und drücke „Erneut versuchen“.", installErrorBusy: "Das Panel ist mit einer anderen Installation beschäftigt. Warte eine Minute und versuche es dann erneut.", installErrorStorage: "Dieser Browser konnte den Fortschritt nicht speichern. Erlaube dieser Website, Daten zu speichern, und versuche es erneut.", installErrorTarget: "Ein anderes Panel wurde angeschlossen. Schließe dasselbe Panel an und versuche es erneut.", installErrorArtifact: "Die heruntergeladene App konnte nicht geprüft werden. Gehe zurück zu Home Assistant und beginne erneut.", installErrorNotClean: "Die App ist bereits auf diesem Panel. Aktualisiere sie über Home Assistant.", installErrorIncompatible: "Dieses Panel kann diese Version der App nicht ausführen.", installErrorConnection: "Die Verbindung zum Panel wurde unterbrochen. Lass es angeschlossen und drücke „Erneut versuchen“.", installErrorHealth: "Die App ist installiert, aber noch nicht gestartet. Warte einen Moment und versuche es dann erneut." }, Be = { version: "Version", connect: "Verbinden", install: "Installieren", setup: "Einrichten" }, Te = { update: "Aktualisierung", settings: "Einstellungen", recovery: "Wiederherstellung", reboot: "Systemneustart" }, _e = { stopped: "Gestoppt", cause: "Ursache", error: "Fehler", release: "Version", panel: "Panel", copied: "Kopiert", savedProgress: "Gespeicherter Fortschritt", earlierCopy: "Frühere Kopie", stagedCopy: "Bereitgestellte Kopie", setAside: "Zurückgestellt", alreadyInstalled: "Bereits installiert", permissions: "Berechtigungen", setupAddress: "Einrichtungsadresse", setupHandover: "Übergabe an die Einrichtung", copiedDetail: "{bytes} Bytes gesendet; Bestätigung des Panels ausstehend", none: "keine", setupMissing: "nicht gefunden; Einrichtung wird auf dem Panel fortgesetzt" }, Ne = {
  sidebar: Ee,
  haInstall: Le,
  installer: Qe,
  errors: Ie,
  journey: Be,
  sidebarReason: Te,
  support: _e
}, De = { title: "Panel Assistant", versionLabel: "{version}, compilación {build}", menu: "Abrir navegación", choosePanel: "Panel", more: "Más opciones", addPanel: "Añadir panel", integrationSettings: "Configuración de la integración", device: "Dispositivo de Home Assistant de este panel", showDevice: "Mostrar dispositivo", github: "GitHub", unreachable: "inaccesible", restarting: "Reiniciando ({reason})", not_loaded: "sin cargar", opening: "Abriendo {panel}…", loadingHint: "Suele tardar unos segundos", empty: "Aún no hay paneles vinculados.", failed: "No se pudo abrir el panel. Se volverá a intentar en breve.", admin: "Un administrador debe abrir esta página.", unreachableBody: "Home Assistant no puede acceder a este panel en este momento.", notLoadedBody: "Este panel no está cargado en Home Assistant.", closed: "Este panel se cerró.", frameTitle: "Interfaz del panel", picklesStory: "Pickles el panda se ha escapado y está causando estragos. Es lento y testarudo, así que traerlo de vuelta puede llevar un momento.", unreachableNext: "Comprueba que el panel esté encendido y conectado a tu red.", notLoadedNext: "Abre Configuración de la integración para comprobar la conexión de este panel.", failedNext: "Espera un momento mientras volvemos a intentarlo, o elige otro panel.", closedNext: "Elige otro panel o espera a que este panel vuelva a conectarse." }, Me = { title: "Instalar ha-paneld en un panel", introduction: "Conecta el panel a este ordenador con un cable USB. Una nueva ventana lo encontrará e instalará la aplicación.", release: "Versión", loading: "Cargando versiones…", catalogError: "No se pudo cargar la lista de versiones.", empty: "Aún no hay versiones disponibles. Vuelve a intentarlo más tarde.", choose: "Seguir el canal de Panel Assistant (recomendado)", testing: "versión de prueba", devBuild: "compilación de desarrollo", retry: "Volver a intentar", start: "Continuar", cancel: "Cancelar", ready: "", unavailable: "El instalador no está disponible. Actualiza Panel Assistant y vuelve a intentarlo.", admin: "Pide a un administrador de Home Assistant que instale los paneles.", waiting: "Continúa en la nueva ventana.", preparing: "Preparando la aplicación…", downloading: "Preparando la aplicación…", verifying: "Preparando la aplicación…", verified: "Continúa en la nueva ventana.", cancelled: "Cancelado.", popup_blocked: "Tu navegador bloqueó la nueva ventana. Permite las ventanas emergentes para esta página y pulsa Continuar.", invalid_request: "Elige primero una versión.", failed: "No ha funcionado. Pulsa Continuar para volver a intentarlo." }, Oe = { title: "Configura tu panel", preparing: "Preparando la aplicación…", preparingSlow: "La aplicación aún se está preparando. Mantén abierta la pestaña de Home Assistant.", connectHeading: "Conecta tu panel", connectBody: "Conecta el panel a este ordenador con un cable USB y pulsa Buscar mi panel.", connect: "Buscar mi panel", allowHeading: "Autoriza este ordenador", allowBody: "Mira la pantalla del panel y toca Permitir.", checkingPanel: "Comprobando tu panel…", confirmHeading: "Listo para instalar", confirmBody: "Esto instala la aplicación y la convierte en la pantalla de inicio de tu panel. Las demás aplicaciones no se modifican.", install: "Instalar", restartedDifferentVersion: "Un intento anterior de instalación usó una versión diferente. Ese intento se ha dejado de lado.", alreadyInstalledHeading: "Ya instalada", alreadyInstalledBody: "Esta versión ya está en tu panel. Continúa para terminar la configuración; no se copia ni se reinstala nada.", continueSetup: "Continuar", progressHeading: "Instalando", stepCopying: "Copiando la aplicación a tu panel…", stepFinishingCopy: "Terminando la copia…", stepInstalling: "Instalando…", stepStarting: "Iniciando la aplicación…", stepPermissions: "Dando a la aplicación lo que necesita para funcionar…", stepOpening: "Abriendo la configuración de tu panel…", keepConnected: "Mantén el cable conectado hasta que termine.", doneHeading: "Instalada", doneOpening: "Te estamos llevando a la configuración de tu panel…", doneManual: "Termina la configuración en la pantalla del panel.", openSetup: "Abrir la configuración del panel", errorHeading: "No ha funcionado", tryAgain: "Volver a intentar", backToHa: "Volver a Home Assistant", details: "Detalles para soporte", unsupported: "Este navegador no puede comunicarse con dispositivos USB. Abre esta página en Chrome o Edge en un ordenador.", handoffFailure: "No se pudo obtener la aplicación de Home Assistant. Vuelve a Home Assistant y empieza de nuevo.", noSelection: "No se eligió ningún panel. Pulsa Buscar mi panel y selecciónalo en la lista, o prueba con otro cable: algunos cables alimentan el panel, pero no transmiten datos.", connectionTimeout: "El panel no respondió. Comprueba que el cable esté bien conectado o cámbialo: un cable puede alimentar el panel sin transmitir datos.", disconnected: "El panel se desconectó. Vuelve a conectarlo, espera a que inicie y pulsa Volver a intentar.", cancelled: "Cancelado.", pageClosed: "La página se cerró.", progress: "Progreso", installProgress: "Progreso de la instalación", licenses: "Licencias de terceros" }, qe = { installErrorGeneric: "Algo salió mal. Mantén el panel conectado y pulsa Volver a intentar.", installErrorBusy: "El panel está ocupado con otra instalación. Espera un minuto y vuelve a intentarlo.", installErrorStorage: "Este navegador no pudo guardar el progreso. Permite que este sitio almacene datos y vuelve a intentarlo.", installErrorTarget: "Se conectó un panel diferente. Conecta el mismo panel y vuelve a intentarlo.", installErrorArtifact: "No se pudo comprobar la descarga de la aplicación. Vuelve a Home Assistant y empieza de nuevo.", installErrorNotClean: "Este panel ya tiene la aplicación. Actualízala desde Home Assistant.", installErrorIncompatible: "Este panel no puede ejecutar esta versión de la aplicación.", installErrorConnection: "Se perdió la conexión con el panel. Mantenlo conectado y pulsa Volver a intentar.", installErrorHealth: "La aplicación está instalada, pero aún no ha iniciado. Espera un momento y vuelve a intentarlo." }, Ve = { version: "Versión", connect: "Conectar", install: "Instalar", setup: "Configurar" }, Re = { update: "actualización", settings: "configuración", recovery: "recuperación", reboot: "reinicio del sistema" }, $e = { stopped: "Detenido", cause: "Causa", error: "Error", release: "Versión", panel: "Panel", copied: "Copiado", savedProgress: "Progreso guardado", earlierCopy: "Copia anterior", stagedCopy: "Copia preparada", setAside: "Dejado de lado", alreadyInstalled: "Ya instalada", permissions: "Permisos", setupAddress: "Dirección de configuración", setupHandover: "Transferencia a la configuración", copiedDetail: "{bytes} bytes enviados; esperando la confirmación del panel", none: "ninguno", setupMissing: "no encontrada; la configuración continúa en el panel" }, Ze = {
  sidebar: De,
  haInstall: Me,
  installer: Oe,
  errors: qe,
  journey: Ve,
  sidebarReason: Re,
  support: $e
}, Ue = { title: "Panel Assistant", versionLabel: "{version}, build {build}", menu: "Ouvrir la navigation", choosePanel: "Panneau", more: "Plus d’options", addPanel: "Ajouter un panneau", integrationSettings: "Paramètres de l’intégration", device: "Appareil Home Assistant de ce panneau", showDevice: "Afficher l’appareil", github: "GitHub", unreachable: "injoignable", restarting: "Redémarrage ({reason})", not_loaded: "non chargé", opening: "Ouverture de {panel}…", loadingHint: "Prend généralement quelques secondes", empty: "Aucun panneau n’est encore rattaché.", failed: "Impossible d’ouvrir le panneau. Une nouvelle tentative aura lieu sous peu.", admin: "Un administrateur doit ouvrir cette page.", unreachableBody: "Home Assistant ne peut pas joindre ce panneau pour le moment.", notLoadedBody: "Ce panneau n’est pas chargé dans Home Assistant.", closed: "Ce panneau a été fermé.", frameTitle: "Interface du panneau", picklesStory: "Pickles le panda s’est échappé et sème la pagaille. Il est lent et têtu, il faudra donc peut-être un moment pour le ramener.", unreachableNext: "Vérifiez que le panneau est allumé et connecté à votre réseau.", notLoadedNext: "Ouvrez les paramètres de l’intégration pour vérifier la connexion de ce panneau.", failedNext: "Patientez pendant la nouvelle tentative, ou choisissez un autre panneau.", closedNext: "Choisissez un autre panneau, ou attendez que celui-ci se reconnecte." }, Ge = { title: "Installer ha-paneld sur un panneau", introduction: "Branchez le panneau à cet ordinateur avec un câble USB. Une nouvelle fenêtre le trouvera et installera l’application.", release: "Version", loading: "Chargement des versions…", catalogError: "Impossible de charger la liste des versions.", empty: "Aucune version n’est encore disponible. Réessayez plus tard.", choose: "Suivre le canal de Panel Assistant (recommandé)", testing: "version de test", devBuild: "build de développement", retry: "Réessayer", start: "Continuer", cancel: "Annuler", ready: "", unavailable: "Le programme d’installation n’est pas disponible. Mettez à jour Panel Assistant, puis réessayez.", admin: "Demandez à un administrateur Home Assistant d’installer les panneaux.", waiting: "Continuez dans la nouvelle fenêtre.", preparing: "Préparation de l’application…", downloading: "Préparation de l’application…", verifying: "Préparation de l’application…", verified: "Continuez dans la nouvelle fenêtre.", cancelled: "Annulé.", popup_blocked: "Votre navigateur a bloqué la nouvelle fenêtre. Autorisez les fenêtres contextuelles pour cette page, puis appuyez sur « Continuer ».", invalid_request: "Choisissez d’abord une version.", failed: "Cela n’a pas fonctionné. Appuyez sur « Continuer » pour réessayer." }, Fe = { title: "Configurer votre panneau", preparing: "Préparation de l’application…", preparingSlow: "La préparation de l’application se poursuit. Gardez l’onglet Home Assistant ouvert.", connectHeading: "Connecter votre panneau", connectBody: "Branchez le panneau à cet ordinateur avec un câble USB, puis appuyez sur « Trouver mon panneau ».", connect: "Trouver mon panneau", allowHeading: "Autoriser cet ordinateur", allowBody: "Regardez l’écran du panneau et appuyez sur « Autoriser ».", checkingPanel: "Vérification de votre panneau…", confirmHeading: "Prêt à installer", confirmBody: "L’application sera installée et deviendra l’écran d’accueil de votre panneau. Vos autres applications ne seront pas modifiées.", install: "Installer", restartedDifferentVersion: "Une tentative d’installation précédente utilisait une autre version. Cette tentative a été mise de côté.", alreadyInstalledHeading: "Déjà installée", alreadyInstalledBody: "Cette version est déjà sur votre panneau. Continuez pour terminer sa configuration ; rien ne sera copié ni réinstallé.", continueSetup: "Continuer", progressHeading: "Installation en cours", stepCopying: "Copie de l’application sur votre panneau…", stepFinishingCopy: "Fin de la copie…", stepInstalling: "Installation…", stepStarting: "Démarrage de l’application…", stepPermissions: "L’application reçoit ce qu’il lui faut pour fonctionner…", stepOpening: "Ouverture de la configuration de votre panneau…", keepConnected: "Gardez le câble branché jusqu’à la fin.", doneHeading: "Installée", doneOpening: "Ouverture de la configuration de votre panneau…", doneManual: "Terminez la configuration sur l’écran du panneau.", openSetup: "Ouvrir la configuration du panneau", errorHeading: "Cela n’a pas fonctionné", tryAgain: "Réessayer", backToHa: "Retour à Home Assistant", details: "Détails pour l’assistance", unsupported: "Ce navigateur ne peut pas communiquer avec les appareils USB. Ouvrez cette page dans Chrome ou Edge sur un ordinateur.", handoffFailure: "Impossible de récupérer l’application depuis Home Assistant. Retournez dans Home Assistant et recommencez.", noSelection: "Aucun panneau n’a été choisi. Appuyez sur « Trouver mon panneau » et sélectionnez-le dans la liste, ou essayez un autre câble : certains alimentent le panneau sans transmettre de données.", connectionTimeout: "Le panneau n’a pas répondu. Vérifiez que le câble est bien branché, ou changez-le : un câble peut alimenter le panneau sans transmettre de données.", disconnected: "Le panneau a été déconnecté. Rebranchez-le, attendez qu’il démarre, puis appuyez sur « Réessayer ».", cancelled: "Annulé.", pageClosed: "La page a été fermée.", progress: "Progression", installProgress: "Progression de l’installation", licenses: "Licences tierces" }, Ke = { installErrorGeneric: "Une erreur s’est produite. Gardez le panneau branché et appuyez sur « Réessayer ».", installErrorBusy: "Le panneau est occupé par une autre installation. Attendez une minute, puis réessayez.", installErrorStorage: "Ce navigateur n’a pas pu enregistrer sa progression. Autorisez ce site à stocker des données, puis réessayez.", installErrorTarget: "Un autre panneau a été connecté. Branchez le même panneau et réessayez.", installErrorArtifact: "Le téléchargement de l’application n’a pas pu être vérifié. Retournez dans Home Assistant et recommencez.", installErrorNotClean: "L’application est déjà sur ce panneau. Mettez-la plutôt à jour depuis Home Assistant.", installErrorIncompatible: "Ce panneau ne peut pas exécuter cette version de l’application.", installErrorConnection: "La connexion au panneau a été interrompue. Gardez-le branché et appuyez sur « Réessayer ».", installErrorHealth: "L’application est installée mais n’a pas encore démarré. Patientez un moment, puis réessayez." }, We = { version: "Version", connect: "Connecter", install: "Installer", setup: "Configurer" }, Je = { update: "mise à jour", settings: "paramètres", recovery: "récupération", reboot: "redémarrage du système" }, Xe = { stopped: "Arrêté", cause: "Cause", error: "Erreur", release: "Version", panel: "Panneau", copied: "Copié", savedProgress: "Progression enregistrée", earlierCopy: "Copie précédente", stagedCopy: "Copie préparée", setAside: "Mis de côté", alreadyInstalled: "Déjà installée", permissions: "Autorisations", setupAddress: "Adresse de configuration", setupHandover: "Passage à la configuration", copiedDetail: "{bytes} octets envoyés ; en attente de confirmation du panneau", none: "aucun", setupMissing: "introuvable ; la configuration continue sur le panneau" }, Ye = {
  sidebar: Ue,
  haInstall: Ge,
  installer: Fe,
  errors: Ke,
  journey: We,
  sidebarReason: Je,
  support: Xe
}, en = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Apri la navigazione", choosePanel: "Pannello", more: "Altre opzioni", addPanel: "Aggiungi pannello", integrationSettings: "Impostazioni dell’integrazione", device: "Dispositivo Home Assistant di questo pannello", showDevice: "Mostra dispositivo", github: "GitHub", unreachable: "non raggiungibile", restarting: "Riavvio in corso ({reason})", not_loaded: "non caricato", opening: "Apertura di {panel}…", loadingHint: "Di solito bastano pochi secondi", empty: "Non è ancora collegato alcun pannello.", failed: "Non è stato possibile aprire il pannello. Riproveremo tra poco.", admin: "Questa pagina deve essere aperta da un amministratore.", unreachableBody: "Home Assistant non riesce a raggiungere questo pannello al momento.", notLoadedBody: "Questo pannello non è caricato in Home Assistant.", closed: "Questo pannello è stato chiuso.", frameTitle: "Interfaccia del pannello", picklesStory: "Pickles il panda è scappato e sta creando scompiglio. È lento e testardo, quindi potrebbe volerci un momento per riportarlo indietro.", unreachableNext: "Controlla che il pannello sia acceso e connesso alla tua rete.", notLoadedNext: "Apri Impostazioni dell’integrazione per controllare la connessione di questo pannello.", failedNext: "Attendi un momento mentre riproviamo, oppure scegli un altro pannello.", closedNext: "Scegli un altro pannello, oppure attendi che questo pannello si riconnetta." }, nn = { title: "Installa ha-paneld su un pannello", introduction: "Collega il pannello a questo computer con un cavo USB. Una nuova finestra lo troverà e installerà l’app.", release: "Versione", loading: "Caricamento delle versioni…", catalogError: "Non è stato possibile caricare l’elenco delle versioni.", empty: "Non ci sono ancora versioni disponibili. Riprova più tardi.", choose: "Segui il canale di Panel Assistant (consigliato)", testing: "versione di prova", devBuild: "build di sviluppo", retry: "Riprova", start: "Continua", cancel: "Annulla", ready: "", unavailable: "Il programma di installazione non è disponibile. Aggiorna Panel Assistant, poi riprova.", admin: "Chiedi a un amministratore di Home Assistant di installare i pannelli.", waiting: "Continua nella nuova finestra.", preparing: "Preparazione dell’app…", downloading: "Preparazione dell’app…", verifying: "Preparazione dell’app…", verified: "Continua nella nuova finestra.", cancelled: "Annullato.", popup_blocked: "Il browser ha bloccato la nuova finestra. Consenti i popup per questa pagina, poi premi Continua.", invalid_request: "Scegli prima una versione.", failed: "L’operazione non è riuscita. Premi Continua per riprovare." }, an = { title: "Configura il tuo pannello", preparing: "Preparazione dell’app…", preparingSlow: "La preparazione dell’app è ancora in corso. Tieni aperta la scheda di Home Assistant.", connectHeading: "Collega il tuo pannello", connectBody: "Collega il pannello a questo computer con un cavo USB, poi premi Trova il mio pannello.", connect: "Trova il mio pannello", allowHeading: "Autorizza questo computer", allowBody: "Guarda lo schermo del pannello e tocca Consenti.", checkingPanel: "Verifica del pannello…", confirmHeading: "Pronto per l’installazione", confirmBody: "Questa operazione installa l’app e la imposta come schermata iniziale del pannello. Le altre app non vengono modificate.", install: "Installa", restartedDifferentVersion: "Un precedente tentativo di installazione usava una versione diversa. Quel tentativo è stato accantonato.", alreadyInstalledHeading: "Già installata", alreadyInstalledBody: "Questa versione è già sul pannello. Continua per completare la configurazione; non viene copiato o reinstallato nulla.", continueSetup: "Continua", progressHeading: "Installazione in corso", stepCopying: "Copia dell’app sul pannello…", stepFinishingCopy: "Completamento della copia…", stepInstalling: "Installazione…", stepStarting: "Avvio dell’app…", stepPermissions: "Assegnazione all’app di ciò che le serve per funzionare…", stepOpening: "Apertura della configurazione del pannello…", keepConnected: "Lascia il cavo collegato fino al termine dell’operazione.", doneHeading: "Installata", doneOpening: "Apertura della configurazione del pannello…", doneManual: "Completa la configurazione sullo schermo del pannello.", openSetup: "Apri la configurazione del pannello", errorHeading: "L’operazione non è riuscita", tryAgain: "Riprova", backToHa: "Torna a Home Assistant", details: "Dettagli per l’assistenza", unsupported: "Questo browser non può comunicare con dispositivi USB. Apri questa pagina in Chrome o Edge su un computer.", handoffFailure: "Non è stato possibile recuperare l’app da Home Assistant. Torna a Home Assistant e ricomincia.", noSelection: "Non è stato scelto alcun pannello. Premi Trova il mio pannello e sceglilo dall’elenco, oppure prova un altro cavo: alcuni cavi alimentano il pannello ma non trasmettono dati.", connectionTimeout: "Il pannello non ha risposto. Controlla che il cavo sia ben inserito, oppure cambialo: un cavo può alimentare il pannello senza trasmettere dati.", disconnected: "Il pannello è stato scollegato. Ricollegalo, attendi che si avvii, poi premi Riprova.", cancelled: "Annullato.", pageClosed: "La pagina è stata chiusa.", progress: "Avanzamento", installProgress: "Avanzamento dell’installazione", licenses: "Licenze di terze parti" }, tn = { installErrorGeneric: "Si è verificato un problema. Lascia il pannello collegato e premi Riprova.", installErrorBusy: "Il pannello è impegnato in un’altra installazione. Attendi un minuto, poi riprova.", installErrorStorage: "Questo browser non è riuscito a salvare l’avanzamento. Consenti a questo sito di memorizzare dati, poi riprova.", installErrorTarget: "È stato collegato un pannello diverso. Collega lo stesso pannello e riprova.", installErrorArtifact: "Non è stato possibile verificare il download dell’app. Torna a Home Assistant e ricomincia.", installErrorNotClean: "L’app è già presente su questo pannello. Aggiornala da Home Assistant.", installErrorIncompatible: "Questo pannello non può eseguire questa versione dell’app.", installErrorConnection: "La connessione al pannello si è interrotta. Lascialo collegato e premi Riprova.", installErrorHealth: "L’app è installata ma non si è ancora avviata. Attendi un momento, poi riprova." }, on = { version: "Versione", connect: "Collega", install: "Installa", setup: "Configura" }, rn = { update: "aggiornamento", settings: "impostazioni", recovery: "ripristino", reboot: "riavvio di sistema" }, sn = { stopped: "Interrotto", cause: "Causa", error: "Errore", release: "Versione pubblicata", panel: "Pannello", copied: "Copiato", savedProgress: "Avanzamento salvato", earlierCopy: "Copia precedente", stagedCopy: "Copia preparata", setAside: "Accantonato", alreadyInstalled: "Già installata", permissions: "Autorizzazioni", setupAddress: "Indirizzo di configurazione", setupHandover: "Passaggio alla configurazione", copiedDetail: "{bytes} byte inviati; in attesa della conferma del pannello", none: "nessuno", setupMissing: "non trovato; la configurazione prosegue sul pannello" }, ln = {
  sidebar: en,
  haInstall: nn,
  installer: an,
  errors: tn,
  journey: on,
  sidebarReason: rn,
  support: sn
}, dn = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Navigatie openen", choosePanel: "Paneel", more: "Meer opties", addPanel: "Paneel toevoegen", integrationSettings: "Integratie-instellingen", device: "Het Home Assistant-apparaat van dit paneel", showDevice: "Apparaat tonen", github: "GitHub", unreachable: "onbereikbaar", restarting: "Opnieuw starten ({reason})", not_loaded: "niet geladen", opening: "{panel} openen…", loadingHint: "Duurt meestal enkele seconden", empty: "Er zijn nog geen panelen gekoppeld.", failed: "Het paneel kon niet worden geopend. Er wordt binnenkort opnieuw geprobeerd.", admin: "Een beheerder moet deze pagina openen.", unreachableBody: "Home Assistant kan dit paneel momenteel niet bereiken.", notLoadedBody: "Dit paneel is niet geladen in Home Assistant.", closed: "Dit paneel is gesloten.", frameTitle: "Paneelinterface", picklesStory: "Pickles de panda is ontsnapt en richt een ravage aan. Hij is traag en koppig, dus het kan even duren om hem terug te krijgen.", unreachableNext: "Controleer of het paneel aanstaat en is verbonden met je netwerk.", notLoadedNext: "Open Integratie-instellingen om de verbinding van dit paneel te controleren.", failedNext: "Wacht even terwijl we het opnieuw proberen, of kies een ander paneel.", closedNext: "Kies een ander paneel of wacht tot dit paneel opnieuw verbinding maakt." }, cn = { title: "ha-paneld op een paneel installeren", introduction: "Sluit het paneel met een USB-kabel aan op deze computer. Een nieuw venster zoekt het paneel en installeert de app.", release: "Versie", loading: "Versies laden…", catalogError: "De lijst met versies kon niet worden geladen.", empty: "Er zijn nog geen versies beschikbaar. Probeer het later opnieuw.", choose: "Kanaal van Panel Assistant volgen (aanbevolen)", testing: "testversie", devBuild: "ontwikkelbuild", retry: "Opnieuw proberen", start: "Doorgaan", cancel: "Annuleren", ready: "", unavailable: "Het installatieprogramma is niet beschikbaar. Werk Panel Assistant bij en probeer het opnieuw.", admin: "Vraag een Home Assistant-beheerder om panelen te installeren.", waiting: "Ga verder in het nieuwe venster.", preparing: "App voorbereiden…", downloading: "App voorbereiden…", verifying: "App voorbereiden…", verified: "Ga verder in het nieuwe venster.", cancelled: "Geannuleerd.", popup_blocked: "De browser heeft het nieuwe venster geblokkeerd. Sta pop-ups toe voor deze pagina en druk op Doorgaan.", invalid_request: "Kies eerst een versie.", failed: "Dat is niet gelukt. Druk op Doorgaan om het opnieuw te proberen." }, pn = { title: "Je paneel instellen", preparing: "App voorbereiden…", preparingSlow: "De app wordt nog voorbereid. Houd het Home Assistant-tabblad open.", connectHeading: "Je paneel verbinden", connectBody: "Sluit het paneel met een USB-kabel aan op deze computer en druk op Mijn paneel zoeken.", connect: "Mijn paneel zoeken", allowHeading: "Deze computer toestaan", allowBody: "Kijk op het scherm van het paneel en tik op Toestaan.", checkingPanel: "Je paneel controleren…", confirmHeading: "Klaar om te installeren", confirmBody: "Hiermee installeer je de app en maak je die het startscherm van je paneel. Je andere apps worden niet aangeraakt.", install: "Installeren", restartedDifferentVersion: "Een eerdere installatiepoging gebruikte een andere uitgave. Die poging is terzijde gelegd.", alreadyInstalledHeading: "Al geïnstalleerd", alreadyInstalledBody: "Deze versie staat al op je paneel. Ga door om het instellen af te ronden; er wordt niets gekopieerd of opnieuw geïnstalleerd.", continueSetup: "Doorgaan", progressHeading: "Installeren", stepCopying: "App naar je paneel kopiëren…", stepFinishingCopy: "Kopiëren afronden…", stepInstalling: "Installeren…", stepStarting: "App starten…", stepPermissions: "De app geven wat die nodig heeft om te werken…", stepOpening: "Paneelconfiguratie openen…", keepConnected: "Laat de kabel aangesloten totdat dit klaar is.", doneHeading: "Geïnstalleerd", doneOpening: "Je wordt naar de paneelconfiguratie geleid…", doneManual: "Rond het instellen af op het scherm van het paneel.", openSetup: "Paneelconfiguratie openen", errorHeading: "Dat is niet gelukt", tryAgain: "Opnieuw proberen", backToHa: "Terug naar Home Assistant", details: "Details voor ondersteuning", unsupported: "Deze browser kan niet communiceren met USB-apparaten. Open deze pagina in Chrome of Edge op een computer.", handoffFailure: "De app kon niet worden opgehaald uit Home Assistant. Ga terug naar Home Assistant en begin opnieuw.", noSelection: "Er is geen paneel gekozen. Druk op Mijn paneel zoeken en kies het in de lijst, of probeer een andere kabel: sommige kabels voeden het paneel zonder gegevens over te dragen.", connectionTimeout: "Het paneel antwoordde niet. Controleer of de kabel goed vastzit, of vervang die: een kabel kan het paneel voeden zonder gegevens over te dragen.", disconnected: "Het paneel is losgekoppeld. Sluit het weer aan, wacht tot het start en druk op Opnieuw proberen.", cancelled: "Geannuleerd.", pageClosed: "De pagina is gesloten.", progress: "Voortgang", installProgress: "Installatievoortgang", licenses: "Licenties van derden" }, un = { installErrorGeneric: "Er ging iets mis. Laat het paneel aangesloten en druk op Opnieuw proberen.", installErrorBusy: "Het paneel is bezig met een andere installatie. Wacht een minuut en probeer het opnieuw.", installErrorStorage: "Deze browser kon de voortgang niet opslaan. Sta toe dat deze site gegevens opslaat en probeer het opnieuw.", installErrorTarget: "Er is een ander paneel aangesloten. Sluit hetzelfde paneel aan en probeer het opnieuw.", installErrorArtifact: "De app-download kon niet worden gecontroleerd. Ga terug naar Home Assistant en begin opnieuw.", installErrorNotClean: "De app staat al op dit paneel. Werk de app in plaats daarvan bij vanuit Home Assistant.", installErrorIncompatible: "Dit paneel kan deze versie van de app niet uitvoeren.", installErrorConnection: "De verbinding met het paneel is verbroken. Laat het aangesloten en druk op Opnieuw proberen.", installErrorHealth: "De app is geïnstalleerd, maar nog niet gestart. Wacht even en probeer het opnieuw." }, gn = { version: "Versie", connect: "Verbinden", install: "Installeren", setup: "Instellen" }, hn = { update: "update", settings: "instellingen", recovery: "herstel", reboot: "herstart" }, mn = { stopped: "Gestopt", cause: "Oorzaak", error: "Fout", release: "Uitgave", panel: "Paneel", copied: "Gekopieerd", savedProgress: "Opgeslagen voortgang", earlierCopy: "Eerdere kopie", stagedCopy: "Klaargezette kopie", setAside: "Terzijde gelegd", alreadyInstalled: "Al geïnstalleerd", permissions: "Machtigingen", setupAddress: "Configuratieadres", setupHandover: "Configuratieoverdracht", copiedDetail: "{bytes} bytes verstuurd; wachten op bevestiging van het paneel", none: "geen", setupMissing: "niet gevonden; instellen gaat verder op het paneel" }, bn = {
  sidebar: dn,
  haInstall: cn,
  installer: pn,
  errors: un,
  journey: gn,
  sidebarReason: hn,
  support: mn
}, fn = { title: "Panel Assistant", versionLabel: "{version} kompilacja {build}", menu: "Otwórz nawigację", choosePanel: "Panel", more: "Więcej opcji", addPanel: "Dodaj panel", integrationSettings: "Ustawienia integracji", device: "Urządzenie tego panelu w Home Assistant", showDevice: "Pokaż urządzenie", github: "GitHub", unreachable: "nieosiągalny", restarting: "Ponowne uruchamianie ({reason})", not_loaded: "niewczytany", opening: "Otwieranie {panel}…", loadingHint: "Zwykle zajmuje to kilka sekund", empty: "Nie podłączono jeszcze żadnych paneli.", failed: "Nie udało się otworzyć panelu. Wkrótce nastąpi kolejna próba.", admin: "Tę stronę musi otworzyć administrator.", unreachableBody: "Home Assistant nie może teraz połączyć się z tym panelem.", notLoadedBody: "Ten panel nie jest wczytany w Home Assistant.", closed: "Ten panel został zamknięty.", frameTitle: "Interfejs panelu", picklesStory: "Panda Pickles uciekł i rozrabia. Jest powolny i uparty, więc sprowadzenie go z powrotem może chwilę potrwać.", unreachableNext: "Sprawdź, czy panel jest włączony i połączony z Twoją siecią.", notLoadedNext: "Otwórz Ustawienia integracji, aby sprawdzić połączenie tego panelu.", failedNext: "Poczekaj chwilę, aż spróbujemy ponownie, lub wybierz inny panel.", closedNext: "Wybierz inny panel lub poczekaj, aż ten panel połączy się ponownie." }, vn = { title: "Zainstaluj ha-paneld na panelu", introduction: "Podłącz panel do tego komputera kablem USB. Nowe okno znajdzie panel i zainstaluje aplikację.", release: "Wersja", loading: "Wczytywanie wersji…", catalogError: "Nie udało się wczytać listy wersji.", empty: "Nie ma jeszcze dostępnych wersji. Spróbuj ponownie później.", choose: "Używaj kanału Panel Assistant (zalecane)", testing: "wersja testowa", devBuild: "kompilacja deweloperska", retry: "Spróbuj ponownie", start: "Kontynuuj", cancel: "Anuluj", ready: "", unavailable: "Instalator jest niedostępny. Zaktualizuj Panel Assistant, a potem spróbuj ponownie.", admin: "Poproś administratora Home Assistant o zainstalowanie paneli.", waiting: "Kontynuuj w nowym oknie.", preparing: "Przygotowywanie aplikacji…", downloading: "Przygotowywanie aplikacji…", verifying: "Przygotowywanie aplikacji…", verified: "Kontynuuj w nowym oknie.", cancelled: "Anulowano.", popup_blocked: "Przeglądarka zablokowała nowe okno. Zezwól na wyskakujące okna dla tej strony, a potem naciśnij Kontynuuj.", invalid_request: "Najpierw wybierz wersję.", failed: "Nie udało się. Naciśnij Kontynuuj, aby spróbować ponownie." }, yn = { title: "Skonfiguruj swój panel", preparing: "Przygotowywanie aplikacji…", preparingSlow: "Aplikacja nadal jest przygotowywana. Zostaw otwartą kartę Home Assistant.", connectHeading: "Podłącz swój panel", connectBody: "Podłącz panel do tego komputera kablem USB, a potem naciśnij Znajdź mój panel.", connect: "Znajdź mój panel", allowHeading: "Zezwól temu komputerowi", allowBody: "Spójrz na ekran panelu i dotknij Zezwól.", checkingPanel: "Sprawdzanie panelu…", confirmHeading: "Gotowe do instalacji", confirmBody: "To zainstaluje aplikację i ustawi ją jako ekran główny panelu. Pozostałe aplikacje pozostaną nietknięte.", install: "Zainstaluj", restartedDifferentVersion: "Wcześniejsza próba instalacji używała innego wydania. Ta próba została odłożona na bok.", alreadyInstalledHeading: "Już zainstalowano", alreadyInstalledBody: "Ta wersja jest już na Twoim panelu. Kontynuuj, aby dokończyć konfigurację; nic nie zostanie skopiowane ani zainstalowane ponownie.", continueSetup: "Kontynuuj", progressHeading: "Instalowanie", stepCopying: "Kopiowanie aplikacji na panel…", stepFinishingCopy: "Kończenie kopiowania…", stepInstalling: "Instalowanie…", stepStarting: "Uruchamianie aplikacji…", stepPermissions: "Zapewnianie aplikacji warunków do działania…", stepOpening: "Otwieranie konfiguracji panelu…", keepConnected: "Zostaw kabel podłączony do zakończenia.", doneHeading: "Zainstalowano", doneOpening: "Przechodzenie do konfiguracji panelu…", doneManual: "Dokończ konfigurację na ekranie panelu.", openSetup: "Otwórz konfigurację panelu", errorHeading: "Nie udało się", tryAgain: "Spróbuj ponownie", backToHa: "Wróć do Home Assistant", details: "Szczegóły dla pomocy technicznej", unsupported: "Ta przeglądarka nie może komunikować się z urządzeniami USB. Otwórz tę stronę w Chrome lub Edge na komputerze.", handoffFailure: "Nie udało się pobrać aplikacji z Home Assistant. Wróć do Home Assistant i zacznij od nowa.", noSelection: "Nie wybrano panelu. Naciśnij Znajdź mój panel i wybierz go z listy lub spróbuj innego kabla: niektóre kable zasilają panel, ale nie przesyłają danych.", connectionTimeout: "Panel nie odpowiedział. Sprawdź, czy kabel jest dobrze wpięty, lub wymień go: kabel może zasilać panel, a mimo to nie przesyłać danych.", disconnected: "Panel został odłączony. Podłącz go ponownie, poczekaj na jego uruchomienie, a potem naciśnij Spróbuj ponownie.", cancelled: "Anulowano.", pageClosed: "Strona została zamknięta.", progress: "Postęp", installProgress: "Postęp instalacji", licenses: "Licencje innych podmiotów" }, kn = { installErrorGeneric: "Coś poszło nie tak. Zostaw panel podłączony i naciśnij Spróbuj ponownie.", installErrorBusy: "Panel jest zajęty inną instalacją. Poczekaj minutę, a potem spróbuj ponownie.", installErrorStorage: "Przeglądarka nie mogła zapisać postępu. Zezwól tej stronie na zapisywanie danych, a potem spróbuj ponownie.", installErrorTarget: "Podłączono inny panel. Podłącz ten sam panel co wcześniej i spróbuj ponownie.", installErrorArtifact: "Nie udało się sprawdzić pobranej aplikacji. Wróć do Home Assistant i zacznij od nowa.", installErrorNotClean: "Ten panel ma już aplikację. Zamiast instalować, zaktualizuj ją z Home Assistant.", installErrorIncompatible: "Ten panel nie może uruchomić tej wersji aplikacji.", installErrorConnection: "Połączenie z panelem zostało przerwane. Zostaw go podłączonego i naciśnij Spróbuj ponownie.", installErrorHealth: "Aplikacja jest zainstalowana, ale jeszcze się nie uruchomiła. Poczekaj chwilę, a potem spróbuj ponownie." }, wn = { version: "Wersja", connect: "Połącz", install: "Zainstaluj", setup: "Skonfiguruj" }, An = { update: "aktualizacja", settings: "ustawienia", recovery: "odzyskiwanie", reboot: "restart systemu" }, zn = { stopped: "Zatrzymano", cause: "Przyczyna", error: "Błąd", release: "Wydanie", panel: "Panel", copied: "Skopiowano", savedProgress: "Zapisany postęp", earlierCopy: "Wcześniejsza kopia", stagedCopy: "Przygotowana kopia", setAside: "Odłożono", alreadyInstalled: "Już zainstalowano", permissions: "Uprawnienia", setupAddress: "Adres konfiguracji", setupHandover: "Przekazanie konfiguracji", copiedDetail: "Wysłano {bytes} bajtów; oczekiwanie na potwierdzenie panelu", none: "brak", setupMissing: "nie znaleziono; konfiguracja trwa dalej na panelu" }, Cn = {
  sidebar: fn,
  haInstall: vn,
  installer: yn,
  errors: kn,
  journey: wn,
  sidebarReason: An,
  support: zn
}, Sn = { title: "Panel Assistant", versionLabel: "{version} compilação {build}", menu: "Abrir navegação", choosePanel: "Painel", more: "Mais opções", addPanel: "Adicionar painel", integrationSettings: "Configurações da integração", device: "Dispositivo deste painel no Home Assistant", showDevice: "Mostrar dispositivo", github: "GitHub", unreachable: "inacessível", restarting: "Reiniciando ({reason})", not_loaded: "não carregado", opening: "Abrindo {panel}…", loadingHint: "Geralmente leva alguns segundos", empty: "Ainda não há painéis vinculados.", failed: "Não foi possível abrir o painel. Uma nova tentativa será feita em breve.", admin: "Um administrador precisa abrir esta página.", unreachableBody: "O Home Assistant não consegue acessar este painel no momento.", notLoadedBody: "Este painel não está carregado no Home Assistant.", closed: "Este painel foi fechado.", frameTitle: "Interface do painel", picklesStory: "Pickles, o panda, escapou e está fazendo uma bagunça. Ele é lento e teimoso, então trazê-lo de volta pode levar um tempinho.", unreachableNext: "Verifique se o painel está ligado e conectado à sua rede.", notLoadedNext: "Abra Configurações da integração para verificar a conexão deste painel.", failedNext: "Aguarde um momento enquanto tentamos novamente ou escolha outro painel.", closedNext: "Escolha outro painel ou aguarde a reconexão deste painel." }, Pn = { title: "Instalar ha-paneld em um painel", introduction: "Conecte o painel a este computador com um cabo USB. Uma nova janela encontrará o painel e instalará o aplicativo.", release: "Versão", loading: "Carregando versões…", catalogError: "Não foi possível carregar a lista de versões.", empty: "Ainda não há versões disponíveis. Tente novamente mais tarde.", choose: "Seguir o canal do Panel Assistant (recomendado)", testing: "versão de teste", devBuild: "compilação de desenvolvimento", retry: "Tentar novamente", start: "Continuar", cancel: "Cancelar", ready: "", unavailable: "O instalador não está disponível. Atualize o Panel Assistant e tente novamente.", admin: "Peça a um administrador do Home Assistant para instalar os painéis.", waiting: "Continue na nova janela.", preparing: "Preparando o aplicativo…", downloading: "Preparando o aplicativo…", verifying: "Preparando o aplicativo…", verified: "Continue na nova janela.", cancelled: "Cancelado.", popup_blocked: "Seu navegador bloqueou a nova janela. Permita pop-ups nesta página e pressione Continuar.", invalid_request: "Escolha uma versão primeiro.", failed: "Não deu certo. Pressione Continuar para tentar novamente." }, xn = { title: "Configure seu painel", preparing: "Preparando o aplicativo…", preparingSlow: "Ainda estamos preparando o aplicativo. Mantenha a aba do Home Assistant aberta.", connectHeading: "Conecte seu painel", connectBody: "Conecte o painel a este computador com um cabo USB e pressione Encontrar meu painel.", connect: "Encontrar meu painel", allowHeading: "Permita este computador", allowBody: "Olhe a tela do painel e toque em Permitir.", checkingPanel: "Verificando seu painel…", confirmHeading: "Pronto para instalar", confirmBody: "Isso instala o aplicativo e o torna a tela inicial do seu painel. Seus outros aplicativos não são alterados.", install: "Instalar", restartedDifferentVersion: "Uma tentativa anterior de instalação usou uma versão diferente. Essa tentativa foi deixada de lado.", alreadyInstalledHeading: "Já instalado", alreadyInstalledBody: "Esta versão já está no seu painel. Continue para concluir a configuração; nada é copiado ou reinstalado.", continueSetup: "Continuar", progressHeading: "Instalando", stepCopying: "Copiando o aplicativo para seu painel…", stepFinishingCopy: "Concluindo a cópia…", stepInstalling: "Instalando…", stepStarting: "Iniciando o aplicativo…", stepPermissions: "Concedendo ao aplicativo o que ele precisa para funcionar…", stepOpening: "Abrindo a configuração do seu painel…", keepConnected: "Mantenha o cabo conectado até terminar.", doneHeading: "Instalado", doneOpening: "Levando você à configuração do seu painel…", doneManual: "Conclua a configuração na tela do painel.", openSetup: "Abrir configuração do painel", errorHeading: "Não deu certo", tryAgain: "Tentar novamente", backToHa: "Voltar ao Home Assistant", details: "Detalhes para suporte", unsupported: "Este navegador não consegue se comunicar com dispositivos USB. Abra esta página no Chrome ou no Edge em um computador.", handoffFailure: "Não foi possível obter o aplicativo do Home Assistant. Volte ao Home Assistant e recomece.", noSelection: "Nenhum painel foi escolhido. Pressione Encontrar meu painel e selecione-o na lista ou tente outro cabo: alguns cabos alimentam o painel, mas não transmitem dados.", connectionTimeout: "O painel não respondeu. Verifique se o cabo está bem conectado ou troque-o: um cabo pode alimentar o painel e ainda assim não transmitir dados.", disconnected: "O painel foi desconectado. Conecte-o novamente, aguarde a inicialização e pressione Tentar novamente.", cancelled: "Cancelado.", pageClosed: "A página foi fechada.", progress: "Progresso", installProgress: "Progresso da instalação", licenses: "Licenças de terceiros" }, jn = { installErrorGeneric: "Algo deu errado. Mantenha o painel conectado e pressione Tentar novamente.", installErrorBusy: "O painel está ocupado com outra instalação. Aguarde um minuto e tente novamente.", installErrorStorage: "Este navegador não conseguiu salvar o progresso. Permita que este site armazene dados e tente novamente.", installErrorTarget: "Outro painel foi conectado. Conecte o mesmo painel e tente novamente.", installErrorArtifact: "Não foi possível verificar o download do aplicativo. Volte ao Home Assistant e recomece.", installErrorNotClean: "Este painel já tem o aplicativo. Atualize-o pelo Home Assistant.", installErrorIncompatible: "Este painel não consegue executar esta versão do aplicativo.", installErrorConnection: "A conexão com o painel caiu. Mantenha-o conectado e pressione Tentar novamente.", installErrorHealth: "O aplicativo está instalado, mas ainda não iniciou. Aguarde um momento e tente novamente." }, Hn = { version: "Versão", connect: "Conectar", install: "Instalar", setup: "Configurar" }, En = { update: "atualização", settings: "configurações", recovery: "recuperação", reboot: "reinicialização" }, Ln = { stopped: "Interrompido", cause: "Causa", error: "Erro", release: "Versão", panel: "Painel", copied: "Copiado", savedProgress: "Progresso salvo", earlierCopy: "Cópia anterior", stagedCopy: "Cópia preparada", setAside: "Deixado de lado", alreadyInstalled: "Já instalado", permissions: "Permissões", setupAddress: "Endereço de configuração", setupHandover: "Transferência da configuração", copiedDetail: "{bytes} bytes enviados; aguardando a confirmação do painel", none: "nenhum", setupMissing: "não encontrado; a configuração continua no painel" }, Qn = {
  sidebar: Sn,
  haInstall: Pn,
  installer: xn,
  errors: jn,
  journey: Hn,
  sidebarReason: En,
  support: Ln
}, In = { title: "Panel Assistant", versionLabel: "{version}, збірка {build}", menu: "Відкрити навігацію", choosePanel: "Панель", more: "Інші параметри", addPanel: "Додати панель", integrationSettings: "Налаштування інтеграції", device: "Пристрій цієї панелі в Home Assistant", showDevice: "Показати пристрій", github: "GitHub", unreachable: "Недоступна", restarting: "Перезапускається ({reason})", not_loaded: "Не завантажено", opening: "Відкриття {panel}…", loadingHint: "Зазвичай це займає кілька секунд", empty: "Панелей ще не приєднано.", failed: "Не вдалося відкрити панель. Незабаром спробу буде повторено.", admin: "Цю сторінку має відкрити адміністратор.", unreachableBody: "Home Assistant зараз не може зв’язатися з цією панеллю.", notLoadedBody: "Цю панель не завантажено в Home Assistant.", closed: "Цю панель було закрито.", frameTitle: "Інтерфейс панелі", picklesStory: "Панда Pickles утік і спричиняє безлад. Він повільний та впертий, тому його повернення може зайняти трохи часу.", unreachableNext: "Перевірте, чи панель увімкнена та під’єднана до вашої мережі.", notLoadedNext: "Відкрийте «Налаштування інтеграції», щоб перевірити з’єднання з цією панеллю.", failedNext: "Зачекайте трохи, поки ми повторимо спробу, або виберіть іншу панель.", closedNext: "Виберіть іншу панель або зачекайте, поки ця панель під’єднається знову." }, Bn = { title: "Встановити ha-paneld на панель", introduction: "Під’єднайте панель до цього комп’ютера кабелем USB. Нове вікно знайде її та встановить програму.", release: "Версія", loading: "Завантаження версій…", catalogError: "Не вдалося завантажити список версій.", empty: "Поки що немає доступних версій. Спробуйте ще раз пізніше.", choose: "Дотримуватися каналу Panel Assistant (рекомендовано)", testing: "Тестова версія", devBuild: "Збірка для розробки", retry: "Спробувати ще раз", start: "Продовжити", cancel: "Скасувати", ready: "", unavailable: "Засіб встановлення недоступний. Оновіть Panel Assistant і повторіть спробу.", admin: "Попросіть адміністратора Home Assistant встановити панелі.", waiting: "Продовжуйте в новому вікні.", preparing: "Підготовка програми…", downloading: "Підготовка програми…", verifying: "Підготовка програми…", verified: "Продовжуйте в новому вікні.", cancelled: "Скасовано.", popup_blocked: "Браузер заблокував нове вікно. Дозвольте спливні вікна для цієї сторінки та натисніть «Продовжити».", invalid_request: "Спочатку виберіть версію.", failed: "Не вдалося. Натисніть «Продовжити», щоб спробувати ще раз." }, Tn = { title: "Налаштуйте панель", preparing: "Підготовка програми…", preparingSlow: "Підготовка програми ще триває. Залиште вкладку Home Assistant відкритою.", connectHeading: "Під’єднайте панель", connectBody: "Під’єднайте панель до цього комп’ютера кабелем USB, а потім натисніть «Знайти мою панель».", connect: "Знайти мою панель", allowHeading: "Дозволити цей комп’ютер", allowBody: "Подивіться на екран панелі та торкніться «Дозволити».", checkingPanel: "Перевірка панелі…", confirmHeading: "Готово до встановлення", confirmBody: "Це встановить програму й зробить її головним екраном панелі. Інші ваші програми не зачіпатимуться.", install: "Встановити", restartedDifferentVersion: "Попередня спроба встановлення використовувала інший випуск. Цю спробу відкладено вбік.", alreadyInstalledHeading: "Уже встановлено", alreadyInstalledBody: "Ця версія вже є на панелі. Продовжуйте, щоб завершити її налаштування; нічого не копіюється й не перевстановлюється.", continueSetup: "Продовжити", progressHeading: "Встановлення", stepCopying: "Копіювання програми на панель…", stepFinishingCopy: "Завершення копіювання…", stepInstalling: "Встановлення…", stepStarting: "Запуск програми…", stepPermissions: "Надання програмі всього необхідного для роботи…", stepOpening: "Відкриття налаштування панелі…", keepConnected: "Залиште кабель під’єднаним до завершення.", doneHeading: "Встановлено", doneOpening: "Перехід до налаштування панелі…", doneManual: "Завершіть налаштування на екрані панелі.", openSetup: "Відкрити налаштування панелі", errorHeading: "Не вдалося", tryAgain: "Спробувати ще раз", backToHa: "Назад до Home Assistant", details: "Подробиці для служби підтримки", unsupported: "Цей браузер не може взаємодіяти з пристроями USB. Відкрийте цю сторінку в Chrome або Edge на комп’ютері.", handoffFailure: "Не вдалося отримати програму з Home Assistant. Поверніться до Home Assistant і почніть знову.", noSelection: "Панель не вибрано. Натисніть «Знайти мою панель» і виберіть її зі списку або спробуйте інший кабель: деякі кабелі живлять панель, але не передають даних.", connectionTimeout: "Панель не відповіла. Перевірте, чи кабель щільно під’єднано, або замініть його: кабель може живити панель, але не передавати даних.", disconnected: "Панель було від’єднано. Під’єднайте її знову, дочекайтеся запуску й натисніть «Спробувати ще раз».", cancelled: "Скасовано.", pageClosed: "Сторінку було закрито.", progress: "Перебіг", installProgress: "Перебіг встановлення", licenses: "Ліцензії сторонніх компонентів" }, _n = { installErrorGeneric: "Щось пішло не так. Залиште панель під’єднаною та натисніть «Спробувати ще раз».", installErrorBusy: "На панелі вже триває інше встановлення. Зачекайте хвилину й повторіть спробу.", installErrorStorage: "Браузеру не вдалося зберегти перебіг роботи. Дозвольте цьому сайту зберігати дані й повторіть спробу.", installErrorTarget: "Було під’єднано іншу панель. Під’єднайте ту саму панель і повторіть спробу.", installErrorArtifact: "Не вдалося перевірити завантажену програму. Поверніться до Home Assistant і почніть знову.", installErrorNotClean: "На цій панелі вже є програма. Натомість оновіть її з Home Assistant.", installErrorIncompatible: "Ця панель не може запускати цю версію програми.", installErrorConnection: "З’єднання з панеллю перервалося. Залиште її під’єднаною та натисніть «Спробувати ще раз».", installErrorHealth: "Програму встановлено, але вона ще не запустилася. Зачекайте трохи й повторіть спробу." }, Nn = { version: "Версія", connect: "Під’єднатися", install: "Встановити", setup: "Налаштувати" }, Dn = { update: "Оновлення", settings: "Налаштування", recovery: "Відновлення", reboot: "Перезавантаження" }, Mn = { stopped: "Зупинено", cause: "Причина", error: "Помилка", release: "Випуск", panel: "Панель", copied: "Скопійовано", savedProgress: "Збережений прогрес", earlierCopy: "Попередня копія", stagedCopy: "Підготовлена копія", setAside: "Відкладено вбік", alreadyInstalled: "Уже встановлено", permissions: "Дозволи", setupAddress: "Адреса налаштування", setupHandover: "Передача до налаштування", copiedDetail: "Надіслано {bytes} байт; очікується підтвердження від панелі", none: "Немає", setupMissing: "Не знайдено; налаштування триває на панелі" }, On = {
  sidebar: In,
  haInstall: Bn,
  installer: Tn,
  errors: _n,
  journey: Nn,
  sidebarReason: Dn,
  support: Mn
}, qn = { title: "Panel Assistant", versionLabel: "{version} 构建 {build}", menu: "打开导航", choosePanel: "面板", more: "更多选项", addPanel: "添加面板", integrationSettings: "集成设置", device: "此面板的 Home Assistant 设备", showDevice: "显示设备", github: "GitHub", unreachable: "无法连接", restarting: "正在重启（{reason}）", not_loaded: "未加载", opening: "正在打开 {panel}…", loadingHint: "通常需要几秒钟", empty: "尚未接入任何面板。", failed: "无法打开面板。稍后会再次尝试。", admin: "此页面必须由管理员打开。", unreachableBody: "Home Assistant 目前无法连接到此面板。", notLoadedBody: "此面板尚未在 Home Assistant 中加载。", closed: "此面板已关闭。", frameTitle: "面板界面", picklesStory: "熊猫 Pickles 逃跑了，正在到处捣乱。他动作慢又固执，要把他带回来可能需要一点时间。", unreachableNext: "请检查面板是否已开机并连接到您的网络。", notLoadedNext: "打开集成设置，检查此面板的连接。", failedNext: "请稍候，我们正在重试；您也可以选择其他面板。", closedNext: "请选择其他面板，或等待此面板重新连接。" }, Vn = { title: "在面板上安装 ha-paneld", introduction: "用 USB 数据线将面板连接到此电脑。新窗口会查找面板并安装应用。", release: "版本", loading: "正在加载版本…", catalogError: "无法加载版本列表。", empty: "目前没有可用版本。请稍后重试。", choose: "跟随 Panel Assistant 的发布渠道（推荐）", testing: "测试版本", devBuild: "开发构建", retry: "重试", start: "继续", cancel: "取消", ready: "", unavailable: "安装程序不可用。请更新 Panel Assistant 后重试。", admin: "请让 Home Assistant 管理员安装面板。", waiting: "请在新窗口中继续。", preparing: "正在准备应用…", downloading: "正在准备应用…", verifying: "正在准备应用…", verified: "请在新窗口中继续。", cancelled: "已取消。", popup_blocked: "浏览器阻止了新窗口。请允许此页面弹出窗口，然后点击“继续”。", invalid_request: "请先选择版本。", failed: "未能完成。请点击“继续”重试。" }, Rn = { title: "设置您的面板", preparing: "正在准备应用…", preparingSlow: "仍在准备应用。请保持 Home Assistant 标签页打开。", connectHeading: "连接您的面板", connectBody: "用 USB 数据线将面板连接到此电脑，然后点击“查找我的面板”。", connect: "查找我的面板", allowHeading: "允许此电脑连接", allowBody: "请查看面板屏幕并点击“允许”。", checkingPanel: "正在检查您的面板…", confirmHeading: "可以开始安装", confirmBody: "这会安装应用，并将其设为面板的主屏幕。您的其他应用不会受到影响。", install: "安装", restartedDifferentVersion: "先前的一次安装尝试使用了另一个发行版本。那次尝试已搁置。", alreadyInstalledHeading: "已安装", alreadyInstalledBody: "您的面板上已有此版本。请继续完成设置；不会复制或重新安装任何内容。", continueSetup: "继续", progressHeading: "正在安装", stepCopying: "正在将应用复制到您的面板…", stepFinishingCopy: "正在完成复制…", stepInstalling: "正在安装…", stepStarting: "正在启动应用…", stepPermissions: "正在为应用提供运行所需的条件…", stepOpening: "正在打开面板设置…", keepConnected: "请保持数据线连接，直到完成。", doneHeading: "已安装", doneOpening: "正在带您进入面板设置…", doneManual: "请在面板屏幕上完成设置。", openSetup: "打开面板设置", errorHeading: "未能完成", tryAgain: "重试", backToHa: "返回 Home Assistant", details: "技术支持详细信息", unsupported: "此浏览器无法与 USB 设备通信。请在电脑上使用 Chrome 或 Edge 打开此页面。", handoffFailure: "无法从 Home Assistant 获取应用。请返回 Home Assistant 重新开始。", noSelection: "未选择面板。请点击“查找我的面板”并从列表中选择，或尝试另一根数据线：有些线缆只能给面板供电，无法传输数据。", connectionTimeout: "面板未响应。请检查线缆是否插紧，或更换线缆：有些线缆能给面板供电，却无法传输数据。", disconnected: "面板已断开连接。请重新连接，等待面板启动后点击“重试”。", cancelled: "已取消。", pageClosed: "页面已关闭。", progress: "进度", installProgress: "安装进度", licenses: "第三方许可" }, $n = { installErrorGeneric: "发生了错误。请保持面板连接，然后点击“重试”。", installErrorBusy: "面板正在进行另一项安装。请等待一分钟后重试。", installErrorStorage: "此浏览器无法保存进度。请允许此网站存储数据，然后重试。", installErrorTarget: "连接了另一个面板。请连接原来的面板后重试。", installErrorArtifact: "无法校验下载的应用。请返回 Home Assistant 重新开始。", installErrorNotClean: "此面板上已有该应用。请通过 Home Assistant 更新。", installErrorIncompatible: "此面板无法运行此版本的应用。", installErrorConnection: "与面板的连接已中断。请保持面板连接，然后点击“重试”。", installErrorHealth: "应用已安装，但尚未启动。请稍候再重试。" }, Zn = { version: "版本", connect: "连接", install: "安装", setup: "设置" }, Un = { update: "更新", settings: "设置", recovery: "恢复", reboot: "系统重启" }, Gn = { stopped: "已停止", cause: "原因", error: "错误", release: "发行版本", panel: "面板", copied: "已复制", savedProgress: "已保存的进度", earlierCopy: "先前的副本", stagedCopy: "暂存的副本", setAside: "已搁置", alreadyInstalled: "已安装", permissions: "权限", setupAddress: "设置地址", setupHandover: "设置交接", copiedDetail: "已发送 {bytes} 字节；正在等待面板确认", none: "无", setupMissing: "未找到；设置将在面板上继续" }, Fn = {
  sidebar: qn,
  haInstall: Vn,
  installer: Rn,
  errors: $n,
  journey: Zn,
  sidebarReason: Un,
  support: Gn
}, V = Object.freeze({ en: we, cs: He, de: Ne, es: Ze, fr: Ye, it: ln, nl: bn, pl: Cn, "pt-BR": Qn, uk: On, "zh-Hans": Fn }), Kn = V.en, Wn = Object.keys(V);
function E(a) {
  if (typeof a != "string") return "en";
  const e = a.trim().replaceAll("_", "-").toLowerCase();
  if (e === "pt-br" || e.startsWith("pt-br-")) return "pt-BR";
  if (e === "zh" || e === "zh-cn" || e.startsWith("zh-cn-") || e === "zh-sg" || e.startsWith("zh-sg-") || e === "zh-hans" || e.startsWith("zh-hans-")) return "zh-Hans";
  const n = e.split("-")[0];
  return Wn.includes(n) ? n : "en";
}
function C(a, e, n = V) {
  const t = Kn[a], i = n[E(e)]?.[a], s = (l) => [...l.matchAll(/\{([A-Za-z_]+)\}/g)].map((o) => o[1]).sort().join(",");
  return Object.fromEntries(Object.entries(t).map(([l, o]) => {
    const p = i?.[l];
    return [l, typeof p == "string" && (p || !o) && s(p) === s(o) ? p : o];
  }));
}
function D(a, e) {
  return a.replace(/\{([A-Za-z_]+)\}/g, (n, t) => Object.hasOwn(e, t) ? String(e[t]) : n);
}
const Jn = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="24 32 60 45">
<path d="M28,32 h52 a4,4 0 0 1 4,4 v37 a4,4 0 0 1 -4,4 h-52 a4,4 0 0 1 -4,-4 v-37 a4,4 0 0 1 4,-4 z" fill="#607D8B"/>
<path d="M29,35 h50 a2,2 0 0 1 2,2 v35 a2,2 0 0 1 -2,2 h-50 a2,2 0 0 1 -2,-2 v-35 a2,2 0 0 1 2,-2 z" fill="#10284A" stroke="#CFD8DC" stroke-width="1"/>
<g transform="translate(42.00,42.50) scale(0.1000)">
<path fill="#F2F4F9" d="M240 224.813C240 233.063 233.25 239.813 225 239.813H15C6.75 239.813 0 233.063 0 224.813V134.813C0 126.563 4.77 115.043 10.61 109.203L109.39 10.423C115.22 4.59304 124.77 4.59304 130.6 10.423L229.39 109.213C235.22 115.043 240 126.573 240 134.823V224.823V224.813Z"/>
<path fill="#18BCF2" d="M229.39 109.203L130.61 10.423C124.78 4.59304 115.23 4.59304 109.4 10.423L10.61 109.203C4.78 115.033 0 126.563 0 134.813V224.813C0 233.063 6.75 239.813 15 239.813H107.27L66.64 199.183C64.55 199.903 62.32 200.313 60 200.313C48.7 200.313 39.5 191.113 39.5 179.813C39.5 168.513 48.7 159.313 60 159.313C71.3 159.313 80.5 168.513 80.5 179.813C80.5 182.143 80.09 184.373 79.37 186.463L111 218.093V102.213C104.2 98.873 99.5 91.893 99.5 83.823C99.5 72.523 108.7 63.323 120 63.323C131.3 63.323 140.5 72.523 140.5 83.823C140.5 91.893 135.8 98.873 129 102.213V183.483L160.46 152.023C159.84 150.063 159.5 147.983 159.5 145.823C159.5 134.523 168.7 125.323 180 125.323C191.3 125.323 200.5 134.523 200.5 145.823C200.5 157.123 191.3 166.323 180 166.323C177.5 166.323 175.12 165.853 172.91 165.033L129 208.943V239.823H225C233.25 239.823 240 233.073 240 224.823V134.823C240 126.573 235.23 115.053 229.39 109.213V109.203Z"/>
</g>
</svg>
`, Xn = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -42.91 373.57 56">
<svg x="0" y="-42.91" width="74.67" height="56" viewBox="24 32 60 45">
<path d="M28,32 h52 a4,4 0 0 1 4,4 v37 a4,4 0 0 1 -4,4 h-52 a4,4 0 0 1 -4,-4 v-37 a4,4 0 0 1 4,-4 z" fill="#607D8B"/>
<path d="M29,35 h50 a2,2 0 0 1 2,2 v35 a2,2 0 0 1 -2,2 h-50 a2,2 0 0 1 -2,-2 v-35 a2,2 0 0 1 2,-2 z" fill="#10284A" stroke="#CFD8DC" stroke-width="1"/>
<g transform="translate(42.00,42.50) scale(0.1000)">
<path fill="#F2F4F9" d="M240 224.813C240 233.063 233.25 239.813 225 239.813H15C6.75 239.813 0 233.063 0 224.813V134.813C0 126.563 4.77 115.043 10.61 109.203L109.39 10.423C115.22 4.59304 124.77 4.59304 130.6 10.423L229.39 109.213C235.22 115.043 240 126.573 240 134.823V224.823V224.813Z"/>
<path fill="#18BCF2" d="M229.39 109.203L130.61 10.423C124.78 4.59304 115.23 4.59304 109.4 10.423L10.61 109.203C4.78 115.033 0 126.563 0 134.813V224.813C0 233.063 6.75 239.813 15 239.813H107.27L66.64 199.183C64.55 199.903 62.32 200.313 60 200.313C48.7 200.313 39.5 191.113 39.5 179.813C39.5 168.513 48.7 159.313 60 159.313C71.3 159.313 80.5 168.513 80.5 179.813C80.5 182.143 80.09 184.373 79.37 186.463L111 218.093V102.213C104.2 98.873 99.5 91.893 99.5 83.823C99.5 72.523 108.7 63.323 120 63.323C131.3 63.323 140.5 72.523 140.5 83.823C140.5 91.893 135.8 98.873 129 102.213V183.483L160.46 152.023C159.84 150.063 159.5 147.983 159.5 145.823C159.5 134.523 168.7 125.323 180 125.323C191.3 125.323 200.5 134.523 200.5 145.823C200.5 157.123 191.3 166.323 180 166.323C177.5 166.323 175.12 165.853 172.91 165.033L129 208.943V239.823H225C233.25 239.823 240 233.073 240 224.823V134.823C240 126.573 235.23 115.053 229.39 109.213V109.203Z"/>
</g>
</svg>
<path fill="currentColor" transform="translate(84.67 0)" d="M7.92 -9.56L7.92 -13.76L14 -13.76Q15.92 -13.76 17.18 -14.5Q18.44 -15.24 19.1 -16.54Q19.76 -17.84 19.76 -19.48Q19.76 -21.12 19.1 -22.42Q18.44 -23.72 17.18 -24.46Q15.92 -25.2 14 -25.2L7.92 -25.2L7.92 -29.4L13.44 -29.4Q17.08 -29.4 19.58 -28.22Q22.08 -27.04 23.38 -24.88Q24.68 -22.72 24.68 -19.8L24.68 -19.16Q24.68 -16.24 23.38 -14.08Q22.08 -11.92 19.58 -10.74Q17.08 -9.56 13.44 -9.56ZM3.92 0L3.92 -29.4L8.72 -29.4L8.72 0ZM40.64 0L40.64 -6.44L39.88 -6.44L39.88 -13.68Q39.88 -15.72 38.84 -16.74Q37.8 -17.76 35.76 -17.76Q34.64 -17.76 33.28 -17.72Q31.92 -17.68 30.6 -17.64Q29.28 -17.6 28.28 -17.52L28.28 -21.56Q29.2 -21.64 30.26 -21.7Q31.32 -21.76 32.46 -21.8Q33.6 -21.84 34.64 -21.84Q37.92 -21.84 40.08 -21Q42.24 -20.16 43.3 -18.34Q44.36 -16.52 44.36 -13.48L44.36 0ZM33.64 0.56Q31.28 0.56 29.52 -0.26Q27.76 -1.08 26.8 -2.66Q25.84 -4.24 25.84 -6.44Q25.84 -8.76 26.94 -10.28Q28.04 -11.8 30.08 -12.56Q32.12 -13.32 34.96 -13.32L40.36 -13.32L40.36 -10.24L34.84 -10.24Q32.72 -10.24 31.6 -9.22Q30.48 -8.2 30.48 -6.56Q30.48 -4.92 31.6 -3.94Q32.72 -2.96 34.84 -2.96Q36.08 -2.96 37.2 -3.42Q38.32 -3.88 39.06 -4.96Q39.8 -6.04 39.88 -8L41.08 -6.44Q40.88 -4.16 39.98 -2.62Q39.08 -1.08 37.5 -0.26Q35.92 0.56 33.64 0.56ZM49.35 0L49.35 -21.56L53.11 -21.56L53.11 -12.32L52.67 -12.32Q52.67 -15.52 53.53 -17.72Q54.39 -19.92 56.15 -21.06Q57.91 -22.2 60.51 -22.2L60.71 -22.2Q64.71 -22.2 66.67 -19.68Q68.63 -17.16 68.63 -12.32L68.63 0L63.91 0L63.91 -12.96Q63.91 -15.2 62.65 -16.56Q61.39 -17.92 59.15 -17.92Q56.87 -17.92 55.47 -16.52Q54.07 -15.12 54.07 -12.76L54.07 0ZM83.05 0.76Q80.25 0.76 78.17 -0.2Q76.09 -1.16 74.75 -2.76Q73.41 -4.36 72.75 -6.34Q72.09 -8.32 72.09 -10.4L72.09 -11.16Q72.09 -13.28 72.75 -15.26Q73.41 -17.24 74.75 -18.82Q76.09 -20.4 78.11 -21.34Q80.13 -22.28 82.81 -22.28Q86.29 -22.28 88.61 -20.76Q90.93 -19.24 92.09 -16.82Q93.25 -14.4 93.25 -11.6L93.25 -9.72L74.13 -9.72L74.13 -12.88L90.09 -12.88L88.77 -11.24Q88.77 -13.44 88.11 -15Q87.45 -16.56 86.13 -17.42Q84.81 -18.28 82.81 -18.28Q80.73 -18.28 79.33 -17.34Q77.93 -16.4 77.25 -14.72Q76.57 -13.04 76.57 -10.76Q76.57 -8.6 77.25 -6.9Q77.93 -5.2 79.37 -4.22Q80.81 -3.24 83.05 -3.24Q85.29 -3.24 86.73 -4.22Q88.17 -5.2 88.49 -6.52L92.85 -6.52Q92.41 -4.32 91.07 -2.68Q89.73 -1.04 87.69 -0.14Q85.65 0.76 83.05 0.76ZM97.03 0L97.03 -29.2L101.75 -29.2L101.75 0ZM94.19 -25.6L94.19 -29.2L101.75 -29.2L101.75 -25.6ZM111.97 0L122.21 -29.2L130.17 -29.2L140.73 0L135.77 0L126.53 -25.96L128.49 -25.04L123.65 -25.04L125.73 -25.96L116.77 0ZM118.61 -8.24L120.13 -12.4L132.37 -12.4L133.93 -8.24ZM150.93 0.68Q146.57 0.68 144.15 -1.18Q141.73 -3.04 141.61 -6.28L145.93 -6.28Q146.01 -5.08 147.17 -4.08Q148.33 -3.08 150.97 -3.08Q153.21 -3.08 154.49 -3.96Q155.77 -4.84 155.77 -6.16Q155.77 -7.36 154.81 -8.06Q153.85 -8.76 151.65 -8.96L149.53 -9.2Q146.21 -9.56 144.27 -11.14Q142.33 -12.72 142.33 -15.48Q142.33 -17.64 143.41 -19.12Q144.49 -20.6 146.39 -21.4Q148.29 -22.2 150.77 -22.2Q154.61 -22.2 156.97 -20.5Q159.33 -18.8 159.41 -15.52L155.09 -15.52Q155.05 -16.72 153.95 -17.58Q152.85 -18.44 150.77 -18.44Q148.77 -18.44 147.71 -17.64Q146.65 -16.84 146.65 -15.6Q146.65 -14.48 147.45 -13.84Q148.25 -13.2 150.05 -13L152.17 -12.8Q155.81 -12.4 157.95 -10.78Q160.09 -9.16 160.09 -6.28Q160.09 -4.2 158.93 -2.64Q157.77 -1.08 155.71 -0.2Q153.65 0.68 150.93 0.68ZM171.13 0.68Q166.77 0.68 164.35 -1.18Q161.93 -3.04 161.81 -6.28L166.13 -6.28Q166.21 -5.08 167.37 -4.08Q168.53 -3.08 171.17 -3.08Q173.41 -3.08 174.69 -3.96Q175.97 -4.84 175.97 -6.16Q175.97 -7.36 175.01 -8.06Q174.05 -8.76 171.85 -8.96L169.73 -9.2Q166.41 -9.56 164.47 -11.14Q162.53 -12.72 162.53 -15.48Q162.53 -17.64 163.61 -19.12Q164.69 -20.6 166.59 -21.4Q168.49 -22.2 170.97 -22.2Q174.81 -22.2 177.17 -20.5Q179.53 -18.8 179.61 -15.52L175.29 -15.52Q175.25 -16.72 174.15 -17.58Q173.05 -18.44 170.97 -18.44Q168.97 -18.44 167.91 -17.64Q166.85 -16.84 166.85 -15.6Q166.85 -14.48 167.65 -13.84Q168.45 -13.2 170.25 -13L172.37 -12.8Q176.01 -12.4 178.15 -10.78Q180.29 -9.16 180.29 -6.28Q180.29 -4.2 179.13 -2.64Q177.97 -1.08 175.91 -0.2Q173.85 0.68 171.13 0.68ZM184.93 0L184.93 -21.56L189.61 -21.56L189.61 0ZM181.77 -17.96L181.77 -21.56L189.61 -21.56L189.61 -17.96ZM186.45 -24.56Q185.01 -24.56 184.31 -25.32Q183.61 -26.08 183.61 -27.28Q183.61 -28.44 184.31 -29.22Q185.01 -30 186.45 -30Q187.93 -30 188.63 -29.22Q189.33 -28.44 189.33 -27.28Q189.33 -26.08 188.63 -25.32Q187.93 -24.56 186.45 -24.56ZM202.49 0.68Q198.13 0.68 195.71 -1.18Q193.29 -3.04 193.17 -6.28L197.49 -6.28Q197.57 -5.08 198.73 -4.08Q199.89 -3.08 202.53 -3.08Q204.77 -3.08 206.05 -3.96Q207.33 -4.84 207.33 -6.16Q207.33 -7.36 206.37 -8.06Q205.41 -8.76 203.21 -8.96L201.09 -9.2Q197.77 -9.56 195.83 -11.14Q193.89 -12.72 193.89 -15.48Q193.89 -17.64 194.97 -19.12Q196.05 -20.6 197.95 -21.4Q199.85 -22.2 202.33 -22.2Q206.17 -22.2 208.53 -20.5Q210.89 -18.8 210.97 -15.52L206.65 -15.52Q206.61 -16.72 205.51 -17.58Q204.41 -18.44 202.33 -18.44Q200.33 -18.44 199.27 -17.64Q198.21 -16.84 198.21 -15.6Q198.21 -14.48 199.01 -13.84Q199.81 -13.2 201.61 -13L203.73 -12.8Q207.37 -12.4 209.51 -10.78Q211.65 -9.16 211.65 -6.28Q211.65 -4.2 210.49 -2.64Q209.33 -1.08 207.27 -0.2Q205.21 0.68 202.49 0.68ZM224.09 0.28Q221.41 0.28 219.57 -0.46Q217.73 -1.2 216.77 -2.96Q215.81 -4.72 215.81 -7.72L215.85 -27.84L220.29 -27.84L220.29 -7.32Q220.29 -5.64 221.19 -4.74Q222.09 -3.84 223.73 -3.84L227.33 -3.84L227.33 0.28ZM212.25 -18.04L212.25 -21.56L227.33 -21.56L227.33 -18.04ZM243.81 0L243.81 -6.44L243.05 -6.44L243.05 -13.68Q243.05 -15.72 242.01 -16.74Q240.97 -17.76 238.93 -17.76Q237.81 -17.76 236.45 -17.72Q235.09 -17.68 233.77 -17.64Q232.45 -17.6 231.45 -17.52L231.45 -21.56Q232.37 -21.64 233.43 -21.7Q234.49 -21.76 235.63 -21.8Q236.77 -21.84 237.81 -21.84Q241.09 -21.84 243.25 -21Q245.41 -20.16 246.47 -18.34Q247.53 -16.52 247.53 -13.48L247.53 0ZM236.81 0.56Q234.45 0.56 232.69 -0.26Q230.93 -1.08 229.97 -2.66Q229.01 -4.24 229.01 -6.44Q229.01 -8.76 230.11 -10.28Q231.21 -11.8 233.25 -12.56Q235.29 -13.32 238.13 -13.32L243.53 -13.32L243.53 -10.24L238.01 -10.24Q235.89 -10.24 234.77 -9.22Q233.65 -8.2 233.65 -6.56Q233.65 -4.92 234.77 -3.94Q235.89 -2.96 238.01 -2.96Q239.25 -2.96 240.37 -3.42Q241.49 -3.88 242.23 -4.96Q242.97 -6.04 243.05 -8L244.25 -6.44Q244.05 -4.16 243.15 -2.62Q242.25 -1.08 240.67 -0.26Q239.09 0.56 236.81 0.56ZM252.52 0L252.52 -21.56L256.28 -21.56L256.28 -12.32L255.84 -12.32Q255.84 -15.52 256.7 -17.72Q257.56 -19.92 259.32 -21.06Q261.08 -22.2 263.68 -22.2L263.88 -22.2Q267.88 -22.2 269.84 -19.68Q271.8 -17.16 271.8 -12.32L271.8 0L267.08 0L267.08 -12.96Q267.08 -15.2 265.82 -16.56Q264.56 -17.92 262.32 -17.92Q260.04 -17.92 258.64 -16.52Q257.24 -15.12 257.24 -12.76L257.24 0ZM285.66 0.28Q282.98 0.28 281.14 -0.46Q279.3 -1.2 278.34 -2.96Q277.38 -4.72 277.38 -7.72L277.42 -27.84L281.86 -27.84L281.86 -7.32Q281.86 -5.64 282.76 -4.74Q283.66 -3.84 285.3 -3.84L288.9 -3.84L288.9 0.28ZM273.82 -18.04L273.82 -21.56L288.9 -21.56L288.9 -18.04Z"/>
</svg>
`, Yn = `data:image/svg+xml,${encodeURIComponent(Jn)}`, ea = "https://github.com/panel-assistant/ha-integration", na = "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12", ie = "panel_assistant.sidebar.entry", aa = "/config/integrations/dashboard/add?domain=panel_assistant", ta = "/config/integrations/integration/panel_assistant", ia = /* @__PURE__ */ new Set(["reachable", "unreachable", "not_loaded", "restarting"]), oa = /* @__PURE__ */ new Set(["update", "settings", "recovery", "reboot"]), ra = 5e3, sa = /* @__PURE__ */ new Set(["current", "added", "updated", "removed"]);
function la(a) {
  if (!a || !Array.isArray(a.panels) || a.panels.length > 200) throw Error("invalid panels");
  const e = /* @__PURE__ */ new Set();
  return a.panels.map((n) => {
    if (!n || typeof n.entry_id != "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(n.entry_id) || e.has(n.entry_id) || typeof n.title != "string" || n.title.length > 256 || !ia.has(n.state) || n.state === "restarting" && !oa.has(n.reason) || n.device_id !== null && n.device_id !== void 0 && typeof n.device_id != "string") throw Error("invalid panel");
    return e.add(n.entry_id), {
      entry_id: n.entry_id,
      title: n.title,
      state: n.state,
      device_id: n.device_id ?? null,
      ...n.state === "restarting" ? { reason: n.reason } : {}
    };
  });
}
function da(a) {
  return typeof a == "string" ? a.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}
function F(a, e = "en") {
  const n = a?.version, t = a?.build;
  return typeof n != "string" || !/^[0-9A-Za-z.+-]{1,32}$/.test(n) || !Number.isSafeInteger(t) || t < 0 ? "" : D(C("sidebar", e).versionLabel, { version: n, build: t });
}
function ca(a, e = "en") {
  return D(C("sidebar", e).opening, { panel: typeof a == "string" ? a : "" });
}
function K(a) {
  history.pushState(null, "", a), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
}
function pa() {
  try {
    return localStorage.getItem(ie);
  } catch {
    return null;
  }
}
function ua(a) {
  try {
    localStorage.setItem(ie, a);
  } catch {
  }
}
class ga extends HTMLElement {
  get #e() {
    return C("sidebar", this.#n?.language);
  }
  #n;
  #d;
  #a;
  #t = !1;
  #g;
  #i = null;
  #r = "loading";
  #s = 0;
  #u = "";
  #o = null;
  #l = null;
  #m = null;
  #b = null;
  #w = () => this.#E();
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>
      :host{display:block;height:100vh;height:100dvh;overflow:hidden;background:var(--primary-background-color,#fafafa);color:var(--primary-text-color,#212121)}
      [hidden]{display:none!important}
      .root{display:flex;flex-direction:column;height:100%;overflow:hidden}
      header{display:flex;flex-wrap:wrap;flex-shrink:0;align-items:center;gap:8px;min-height:56px;padding:4px 12px 4px 20px;font-size:1rem;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff);border-bottom:1px solid var(--divider-color,#e0e0e0);box-sizing:border-box}
      h1{font-size:1.25rem;font-weight:400;margin:0}
      #icon{width:36px;height:36px;border-radius:8px;flex-shrink:0}
      #version{margin:0 8px 0 0;font-size:.875rem;opacity:.8}
      button,select,a{font:inherit;font-size:1rem;min-height:44px;box-sizing:border-box;border-radius:6px}
      button{display:inline-flex;align-items:center;justify-content:center;min-width:44px;padding:0;color:inherit;background:transparent;border:0;cursor:pointer}
      button svg{width:24px;height:24px;fill:currentColor}
      #menu,#overflow{position:relative;flex-shrink:0;width:48px;height:48px;border-radius:50%}
      /* The alert dot is Home Assistant's own menu-button dot. */
      #dot{pointer-events:none;position:absolute;top:9px;inset-inline-end:7px;width:12px;height:12px;box-sizing:content-box;background:var(--accent-color,#ff9800);border-radius:50%;border:2px solid var(--app-header-background-color,var(--primary-color,#03a9f4))}
      label{display:flex;align-items:center;gap:8px;flex:0 0 auto;max-width:100%;min-width:0}
      select{flex:1 1 auto;width:auto;min-width:0;max-width:100%;text-overflow:ellipsis;padding:0 8px;color:#212121;background:#fff;border:1px solid rgba(0,0,0,.15)}
      #spacer{flex:1 1 auto}
      a{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:0 12px;color:inherit;text-decoration:none}
      a svg{width:20px;height:20px;fill:currentColor}
      #github,#add,#settings,#device{min-height:36px}
      #github{min-width:36px;padding:0;justify-content:center}
      #github svg{width:20px;height:20px}
      #add{background:#fff;color:#0288d1;padding:0 14px}
      #add svg{width:18px;height:18px}
      #settings{min-width:36px;padding:0;justify-content:center}
      #settings svg{width:22px;height:22px}
      #device{min-width:36px;padding:0;justify-content:center}
      #device svg{width:20px;height:20px}
      #slot,#more{display:contents}
      .menu-icon,.item-label,#backdrop{display:none}
      #add-label{display:inline}
      #failure{flex:1;min-height:0;overflow:auto;box-sizing:border-box;display:flex;flex-direction:column;align-items:center;justify-content:safe center;gap:12px;padding:20px;text-align:center}
      #pickles{width:180px;max-width:100%;height:180px;object-fit:contain;flex-shrink:0}
      #pickles-story,#next-step{margin:0;max-width:440px;line-height:1.5}
      #next-step{color:var(--secondary-text-color,#727272)}
      #failure-status{margin:0;max-width:440px;line-height:1.5;color:var(--primary-text-color,#212121)}
      #status{margin:0;padding:16px;color:var(--secondary-text-color,#727272)}
      #loading{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:16px;text-align:center;color:var(--secondary-text-color,#727272)}
      .spinner{animation:pa-spin .9s linear infinite}
      @keyframes pa-spin{to{transform:rotate(360deg)}}
      #loading-text{margin:0;max-width:100%;overflow-wrap:anywhere;font-size:.9375rem;color:var(--primary-text-color,#212121)}
      #loading-hint{margin:4px 0 0;font-size:.8125rem}
      iframe{flex:1;border:0;width:100%;display:block;background:var(--card-background-color,#fff)}
      /* On a phone the header is one row like Home Assistant's own panels: menu button, picker, and a
         vertical ellipsis that opens the remaining links as a menu. The picker caption stays for screen readers. */
      [data-narrow] header{position:relative;flex-wrap:nowrap;gap:4px;padding:3px 4px}
      [data-narrow] #icon,[data-narrow] #spacer{display:none}
      [data-narrow] #picker{flex:1 1 auto;min-width:0}
      [data-narrow] #picker>span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
      [data-narrow] select{flex:1 1 auto;width:100%;min-width:0;text-overflow:ellipsis}
      [data-narrow] #more{display:none}
      [data-narrow] #more[data-open]{display:flex;flex-direction:column;position:absolute;z-index:2;top:calc(100% - 4px);inset-inline-end:4px;min-width:200px;max-width:calc(100% - 8px);padding:8px 0;box-sizing:border-box;background:var(--card-background-color,#fff);color:var(--primary-text-color,#212121);border-radius:4px;box-shadow:0 2px 4px -1px rgba(0,0,0,.2),0 4px 5px rgba(0,0,0,.14),0 1px 10px rgba(0,0,0,.12)}
      [data-narrow] #more a{justify-content:flex-start;gap:16px;width:100%;min-height:48px;padding:0 16px;border-radius:0;background:none;color:inherit}
      [data-narrow] #more a:focus-visible,[data-narrow] #more a:hover{background:var(--secondary-background-color,rgba(0,0,0,.06))}
      [data-narrow] #more a svg{width:24px;height:24px;flex-shrink:0;fill:var(--secondary-text-color,#727272)}
      [data-narrow] #more .menu-icon{display:block}
      [data-narrow] #more .wide-icon{display:none}
      [data-narrow] #more .item-label{display:inline}
      [data-narrow] #device{order:1}
      [data-narrow] #add{order:2}
      [data-narrow] #settings{order:3}
      [data-narrow] #github{order:4}
      [data-narrow] #backdrop:not([hidden]){display:block;position:fixed;inset:0;z-index:1}
    </style><div class="root" id="root"><header>
      <button id="menu" type="button"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z"/></svg><span id="dot"></span></button>
      <img id="icon" src="${Yn}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <button id="overflow" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="more"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z"/></svg></button>
      <div id="backdrop"></div>
      <div id="more">
      <a id="device"><svg class="wide-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg><svg class="menu-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19,18H5V6H19M21,4H3C1.89,4 1,4.89 1,6V18A2,2 0 0,0 3,20H21A2,2 0 0,0 23,18V6C23,4.89 22.1,4 21,4Z"/></svg><span class="item-label" data-message="showDevice"></span></a>
      <div id="spacer"></div>
      <a id="github" href="${ea}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${na}"/></svg><span class="item-label" data-message="github"></span></a>
      <a id="add"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg><span id="add-label" class="item-label" data-message="addPanel"></span></a>
      <a id="settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z"/></svg><span class="item-label" data-message="integrationSettings"></span></a>
      </div>
      <div id="slot"></div>
    </header><div id="failure" hidden><img id="pickles" src="/panel_assistant/usb/pickles.svg" alt=""><p id="pickles-story" data-message="picklesStory"></p><p id="failure-status" role="status" aria-live="polite"></p><p id="next-step"></p></div><p id="status" role="status" aria-live="polite"></p><div id="loading" hidden><svg class="spinner" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="19" stroke="var(--divider-color,#e0e0e0)" stroke-width="4" fill="none"></circle><circle cx="24" cy="24" r="19" stroke="var(--app-header-background-color,var(--primary-color,#03a9f4))" stroke-width="4" stroke-linecap="round" stroke-dasharray="119.4" stroke-dashoffset="89.5" fill="none"></circle></svg><p id="loading-text" role="status" aria-live="polite"></p><p id="loading-hint" data-message="loadingHint"></p></div><iframe id="frame"></iframe></div>`;
    const e = this.shadowRoot;
    for (const o of e.querySelectorAll("[data-message]")) o.textContent = this.#e[o.dataset.message];
    const n = e.querySelector("#menu");
    n.setAttribute("aria-label", this.#e.menu), n.hidden = !0, n.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: !0, composed: !0 })));
    const t = e.querySelector("#overflow");
    t.setAttribute("aria-label", this.#e.more), t.setAttribute("title", this.#e.more), t.hidden = !0, e.querySelector("#backdrop").hidden = !0, e.querySelector("#dot").hidden = !0, t.addEventListener("click", () => this.#h(!this.#A())), e.querySelector("#backdrop").addEventListener("click", () => this.#h(!1)), e.querySelector("#root").addEventListener("keydown", (o) => {
      o.key !== "Escape" || !this.#A() || (this.#h(!1), t.focus?.());
    }), e.querySelector("#frame").setAttribute("title", this.#e.frameTitle);
    const i = e.querySelector("#settings");
    i.setAttribute("aria-label", this.#e.integrationSettings), i.setAttribute("title", this.#e.integrationSettings);
    const s = e.querySelector("#github");
    s.setAttribute("aria-label", this.#e.github), s.setAttribute("title", this.#e.github), s.addEventListener("click", () => this.#h(!1));
    const l = e.querySelector("#device");
    l.setAttribute("aria-label", this.#e.device), l.setAttribute("title", this.#e.device), l.addEventListener("click", (o) => {
      const p = l.getAttribute("href");
      this.#h(!1), !(!p || o.defaultPrevented || o.button !== 0 || o.metaKey || o.ctrlKey || o.shiftKey || o.altKey) && (o.preventDefault(), K(p));
    });
    for (const [o, p] of [["add", aa], ["settings", ta]]) {
      const u = e.querySelector(`#${o}`);
      u.setAttribute("href", p), u.addEventListener("click", (m) => {
        this.#h(!1), !(m.defaultPrevented || m.button !== 0 || m.metaKey || m.ctrlKey || m.shiftKey || m.altKey) && (m.preventDefault(), K(p));
      });
    }
    e.querySelector("#panels").addEventListener("change", (o) => this.#S(o.target.value)), this.#c();
  }
  get hass() {
    return this.#n;
  }
  set hass(e) {
    const n = this.#n;
    if (this.#n = e, !!this.isConnected) {
      if (this.#f(), n?.connection !== e?.connection || n?.user?.id !== e?.user?.id || n?.user?.is_admin !== e?.user?.is_admin) {
        this.#C();
        return;
      }
      (n?.language !== e?.language || !!n?.themes?.darkMode != !!e?.themes?.darkMode) && (this.#p(), this.#k());
    }
  }
  get panel() {
    return this.#d;
  }
  set panel(e) {
    this.#d = e, this.shadowRoot.querySelector("#version").textContent = F(e?.config, this.#n?.language);
  }
  get route() {
    return this.#a;
  }
  set route(e) {
    const n = this.#a?.path;
    this.#a = e, n !== e?.path && this.#S(e?.path?.slice(1));
  }
  get narrow() {
    return this.#t;
  }
  set narrow(e) {
    this.#t = e === !0;
    const n = this.shadowRoot, t = n.querySelector("#root");
    this.#t ? t.setAttribute("data-narrow", "") : t.removeAttribute("data-narrow"), n.querySelector("#menu").hidden = !this.#t, n.querySelector("#overflow").hidden = !this.#t, n.querySelector("#title").hidden = this.#t, n.querySelector("#version").hidden = this.#t;
    const i = n.querySelector("#more");
    this.#t ? i.setAttribute("role", "menu") : i.removeAttribute("role");
    for (const s of ["device", "github", "add", "settings"]) {
      const l = n.querySelector(`#${s}`);
      this.#t ? l.setAttribute("role", "menuitem") : l.removeAttribute("role");
    }
    this.#t || this.#h(!1), this.#f();
  }
  #A() {
    return this.shadowRoot.querySelector("#more").getAttribute("data-open") !== null;
  }
  #h(e) {
    const n = this.shadowRoot, t = n.querySelector("#more"), i = e && this.#t;
    i ? t.setAttribute("data-open", "") : t.removeAttribute("data-open"), n.querySelector("#backdrop").hidden = !i, n.querySelector("#overflow").setAttribute("aria-expanded", String(i));
  }
  // Home Assistant's menu button shows a dot while persistent notifications exist; on a phone this
  // header replaces it, so it keeps the dot from the same subscription.
  #f() {
    const e = this.isConnected && this.#t ? this.#n?.connection : void 0, n = this.#b;
    if (n?.connection === e || (n && (this.#b = null, n.unsubscribe?.then((i) => i()).catch(() => {
    }), this.shadowRoot.querySelector("#dot").hidden = !0), !e?.subscribeMessage)) return;
    const t = { connection: e, notifications: {}, unsubscribe: null };
    this.#b = t, t.unsubscribe = Promise.resolve().then(() => e.subscribeMessage((i) => this.#j(t, i), { type: "persistent_notification/subscribe" })), t.unsubscribe.catch(() => {
    });
  }
  #j(e, n) {
    if (!(this.#b !== e || !sa.has(n?.type) || !n.notifications || typeof n.notifications != "object")) {
      if (n.type === "current") e.notifications = { ...n.notifications };
      else if (n.type === "removed") for (const t of Object.keys(n.notifications)) delete e.notifications[t];
      else e.notifications = { ...e.notifications, ...n.notifications };
      this.shadowRoot.querySelector("#dot").hidden = Object.keys(e.notifications).length === 0;
    }
  }
  connectedCallback() {
    clearInterval(this.#m), this.#C(), this.#f(), this.#m = setInterval(() => this.#y(), ra);
  }
  disconnectedCallback() {
    clearInterval(this.#m), this.#m = null, this.#s++, this.#z(), this.#p(), this.#f();
  }
  #v() {
    return this.#n?.user?.is_admin === !0;
  }
  #z() {
    this.#g?.removeEventListener?.("ready", this.#w), this.#g = void 0;
  }
  #C() {
    this.#z(), this.#p(), this.#i = null, this.#u = "", this.#r = "loading", this.#v() && this.#n.connection && (this.#g = this.#n.connection, this.#g.addEventListener("ready", this.#w)), this.#c(), this.#y();
  }
  async #y() {
    const e = ++this.#s;
    if (!this.#v()) {
      this.#c();
      return;
    }
    let n;
    try {
      let t;
      try {
        t = await this.#n.callWS({ type: "panel_assistant/embed_panels" });
      } catch (i) {
        if (this.#i) return;
        throw i;
      }
      n = la(t);
    } catch {
      if (e !== this.#s) return;
      this.#i = null, this.#r = "failed", this.#p(), this.#c();
      return;
    }
    if (e === this.#s) {
      if (this.#i = n, this.#r = n.length ? "ready" : "empty", !n.some((t) => t.entry_id === this.#o)) {
        const t = this.#a?.path?.slice(1), i = n.some((s) => s.entry_id === t) ? t : pa();
        this.#o = n.some((s) => s.entry_id === i) ? i : n[0]?.entry_id ?? null;
      }
      this.#k();
    }
  }
  #S(e) {
    !this.#i?.some((n) => n.entry_id === e) || e === this.#o || (this.#o = e, ua(e), this.#p(), this.#k());
  }
  // Opens a session when the selected panel is reachable and none is live for it.
  #k() {
    const e = this.#i?.find((t) => t.entry_id === this.#o), n = this.#l;
    !e || e.state !== "reachable" ? n && !(n.state === "closed" && n.entryId === e?.entry_id) && this.#p() : (!n || n.entryId !== e.entry_id || !["opening", "open"].includes(n.state)) && (this.#p(), this.#P(e.entry_id, null, null)), this.#c();
  }
  #P(e, n, t) {
    const i = this.#n, s = { entryId: e, token: n, url: t, state: "opening", code: null, unsubscribe: null };
    this.#l = s;
    const l = {
      type: "panel_assistant/embed_session",
      entry_id: e,
      language: i.language,
      theme: i.themes?.darkMode ? "dark" : "light",
      ...n ? { resume: n } : {}
    };
    s.unsubscribe = Promise.resolve().then(() => i.connection.subscribeMessage((o) => this.#H(s, o), l, { resubscribe: !1 })), s.unsubscribe.catch((o) => {
      this.#l === s && (s.state = "failed", s.code = o?.code ?? null, this.#x(), this.#c());
    });
  }
  #H(e, n) {
    if (!(this.#l !== e || !n))
      if (n.kind === "opened") {
        const t = da(n.url);
        if (!t) {
          this.#p(), this.#l = { entryId: e.entryId, state: "failed", code: null, unsubscribe: null }, this.#c();
          return;
        }
        e.token = t, e.url = n.url, e.state = "open";
        const i = this.shadowRoot.querySelector("#frame");
        i.getAttribute("src") !== n.url && i.setAttribute("src", n.url), this.#c();
      } else n.kind === "closed" && (this.#p(), this.#l = { entryId: e.entryId, state: "closed", code: null, unsubscribe: null }, this.#c(), this.#y());
  }
  // The connection came back; subscriptions made with resubscribe:false are gone, and
  // their unsubscribe functions must not be called: command ids restart per socket.
  #E() {
    const e = this.#l;
    !this.isConnected || !e || !["opening", "open"].includes(e.state) || (this.#P(e.entryId, e.token, e.url), this.#c());
  }
  #p() {
    const e = this.#l;
    this.#l = null, e && (e.state = "ended", e.unsubscribe?.then((n) => n()).catch(() => {
    }), this.#x());
  }
  #x() {
    this.shadowRoot.querySelector("#frame").removeAttribute("src");
  }
  #c() {
    const e = this.shadowRoot, n = this.#n?.language;
    this.isConnected && this.setAttribute?.("lang", E(n));
    const t = this.#e;
    for (const b of e.querySelectorAll("[data-message]")) b.textContent = t[b.dataset.message];
    for (const [b, f] of [["menu", "menu"], ["overflow", "more"], ["settings", "integrationSettings"], ["github", "github"], ["device", "device"], ["add", "addPanel"]])
      e.querySelector(`#${b}`).setAttribute("aria-label", t[f]), e.querySelector(`#${b}`).setAttribute("title", t[f]);
    e.querySelector("#frame").setAttribute("title", t.frameTitle), e.querySelector("#version").textContent = F(this.#d?.config, n);
    const i = e.querySelector("#panels"), s = this.#v() ? this.#i ?? [] : [], l = JSON.stringify([s, E(n)]);
    if (l !== this.#u) {
      this.#u = l, i.replaceChildren();
      for (const b of s) {
        const f = document.createElement("option");
        f.value = b.entry_id, f.textContent = b.state === "reachable" ? b.title : b.state === "restarting" ? `${b.title} (${D(this.#e.restarting, { reason: C("sidebarReason", n)[b.reason] })})` : `${b.title} (${this.#e[b.state]})`, i.append(f);
      }
    }
    i.value = this.#o ?? "", e.querySelector("#picker").hidden = s.length === 0;
    const o = s.find((b) => b.entry_id === this.#o), p = e.querySelector("#device");
    p.hidden = !o?.device_id, o?.device_id && p.setAttribute("href", `/config/devices/device/${encodeURIComponent(o.device_id)}`);
    const u = this.#l, m = e.querySelector("#frame");
    let c = "", g = !1;
    this.#v() ? this.#r === "loading" ? g = !0 : this.#r !== "ready" ? c = this.#r : u?.state === "closed" && u.entryId === o?.entry_id ? c = "closed" : o?.state === "restarting" ? c = "restarting" : o?.state === "unreachable" ? c = "unreachableBody" : o?.state === "not_loaded" ? c = "notLoadedBody" : u?.state === "failed" ? c = u.code === "not_loaded" ? "notLoadedBody" : "failed" : u?.state !== "open" && !m.getAttribute("src") && (g = !0) : c = "admin";
    const w = e.querySelector("#status");
    w.textContent = c === "restarting" ? D(this.#e.restarting, { reason: C("sidebarReason", n)[o.reason] }) : c ? this.#e[c] : "";
    const v = ["unreachableBody", "notLoadedBody", "failed", "closed"].includes(c);
    e.querySelector("#failure").hidden = !v, e.querySelector("#failure-status").textContent = v ? w.textContent : "", e.querySelector("#next-step").textContent = v ? this.#e[{ unreachableBody: "unreachableNext", notLoadedBody: "notLoadedNext", failed: "failedNext", closed: "closedNext" }[c]] : "", w.hidden = !c || v;
    const A = e.querySelector("#loading");
    A.hidden = !g;
    const L = e.querySelector("#loading-text");
    L.hidden = !o, g && (L.textContent = o ? ca(o.title, n) : ""), m.hidden = !m.getAttribute("src");
  }
}
customElements.get("panel-assistant-sidebar") || customElements.define("panel-assistant-sidebar", ga);
const oe = "io.github.maxlyth.hapaneld", ha = "io.panelassistant.android", ma = "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity", ba = "io.panelassistant.android/io.panelassistant.android.MainActivity", fa = Object.freeze({
  [oe]: Object.freeze(["io.github.maxlyth.hapaneld/.MainActivity"]),
  [ha]: Object.freeze([ma, ba])
});
fa[oe][0];
const re = 64, se = 256 * 1024, va = 2147483647, ya = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, ka = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc[1-9][0-9]*$/, W = /^build-([1-9][0-9]{0,9})(-successor)?$/, R = (a, e) => typeof e == "string" && e.length <= re && a.exec(e)?.[0] === e, le = (a) => R(ya, a), de = (a) => R(ka, a), J = (a) => le(a) || de(a);
function wa(a) {
  if (!R(W, a)) return null;
  const e = Number(W.exec(a)[1]);
  return e <= va ? e : null;
}
const H = (a) => wa(a) !== null, Aa = (a) => Array.from(new Uint8Array(a), (e) => e.toString(16).padStart(2, "0")).join(""), za = (a = crypto) => Aa(a.getRandomValues(new Uint8Array(16))), S = (a, e) => typeof e == "string" && a.exec(e)?.[0] === e, P = (a, e) => a !== null && typeof a == "object" && !Array.isArray(a) && Object.keys(a).length === e.length && e.every((n) => Object.hasOwn(a, n)), O = "/api/panel_assistant/usb/release", Ca = "/api/panel_assistant/usb/handover", Sa = 35e3, Pa = /(?:[0-9]{1,3}\.){3}[0-9]{1,3}/, xa = /[a-z_]{1,48}/, X = 64 * 1024 * 1024, ja = 1800 * 1e3, Y = [
  "id",
  "tag",
  "checksum",
  "checksum_signature",
  "descriptor",
  "descriptor_signature",
  "apk_size",
  "apk_sha256"
], ee = ["id", "tag", "feed", "feed_signature", "apk_size", "apk_sha256"], q = 8192, ne = Math.ceil(se / 3) * 4 + q;
class I extends Error {
  constructor(e) {
    super(e), this.name = "HandoffError", this.code = e;
  }
}
function h(a, e = "invalid_response") {
  if (!a) throw new I(e);
}
function j(a, e, n = !1) {
  h(typeof a == "string" && a.length <= Math.ceil(e / 3) * 4 && S(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, a));
  const t = atob(a);
  return h(btoa(t) === a && t.length > 0 && (n ? t.length === e : t.length <= e)), Uint8Array.from(t, (i) => i.charCodeAt(0));
}
async function N(a, e, n, t = null) {
  h(a.status === 200 && !a.redirected && a.body);
  const i = a.headers.get("content-length");
  if (i !== null) {
    h(S(/0|[1-9][0-9]*/, i));
    const u = Number(i);
    h(Number.isSafeInteger(u) && u <= e && (t === null || u === t));
  }
  const s = a.body.getReader(), l = () => {
    s.cancel().catch(() => {
    });
  };
  n.addEventListener("abort", l, { once: !0 });
  const o = [];
  let p = 0;
  try {
    for (; ; ) {
      h(!n.aborted, "cancelled");
      const u = await s.read();
      if (h(!n.aborted, "cancelled"), u.done) break;
      p += u.value.byteLength, h(p <= e && (t === null || p <= t)), o.push(u.value);
    }
    return h(p > 0 && (i === null || p === Number(i)) && (t === null || p === t)), new Blob(o);
  } finally {
    n.removeEventListener("abort", l), l(), s.releaseLock();
  }
}
function Ha(a, e, {
  rcTag: n = null,
  language: t = a?.language,
  onState: i = () => {
  },
  windowObject: s = window,
  timeoutMs: l = 3e5
} = {}) {
  let o, p;
  const u = new Promise((r, z) => {
    o = r, p = z;
  }), m = new AbortController();
  let c = !1, g, w, v, A, L = !1, b = !1, f, $, Z, U, M = !1;
  const T = () => {
    clearInterval(Z), clearTimeout(U), f = void 0, s.removeEventListener("message", G);
  }, Q = (r) => {
    try {
      i(r);
    } catch {
    }
  }, x = (r = null) => {
    if (!c) {
      if (c = !0, m.abort(), clearTimeout(w), Q(r ?? "verified"), r) {
        T(), p(new I(r));
        return;
      }
      Z = setInterval(() => {
        g.closed && T();
      }, 2e3), U = setTimeout(T, ja), o();
    }
  };
  async function ce() {
    try {
      Q("preparing"), h(!c, "cancelled");
      const r = await a.fetchWithAuth(O, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(n === null ? {} : { release_candidate: n }),
        redirect: "error",
        signal: m.signal
      });
      h(!c, "cancelled"), h(r.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const z = await N(r, ne, m.signal);
      let d;
      try {
        d = JSON.parse(await z.text());
      } catch {
        throw new I("invalid_response");
      }
      const y = H(d?.tag);
      h(y || z.size <= q), h(!c, "cancelled"), h(P(d, y ? ee : Y) && S(/[0-9a-f]{32}/, d.id) && typeof d.tag == "string" && d.tag.length <= re && (n === null ? J(d.tag) || H(d.tag) : d.tag === n) && S(/[0-9a-f]{64}/, d.apk_sha256) && Number.isSafeInteger(d.apk_size) && d.apk_size > 0 && d.apk_size <= X);
      const k = y ? {
        tag: d.tag,
        feed: j(d.feed, se),
        feedSignature: j(d.feed_signature, 256, !0)
      } : {
        tag: d.tag,
        checksum: j(d.checksum, 512),
        checksumSignature: j(d.checksum_signature, 256, !0),
        descriptor: j(d.descriptor, 4096),
        descriptorSignature: j(d.descriptor_signature, 256, !0)
      };
      Q("downloading"), h(!c, "cancelled");
      const _ = await a.fetchWithAuth(`${O}/${d.id}/apk`, {
        method: "GET",
        redirect: "error",
        signal: m.signal
      });
      h(!c, "cancelled");
      const ge = await N(_, X, m.signal, d.apk_size);
      h(!c && !g.closed, "window_closed"), b = !0, $ = d.apk_sha256, f = { type: "ha-paneld/usb-bundle", nonce: A, bundle: k, apk: ge }, g.postMessage(f, v), Q("verifying");
    } catch (r) {
      x(r instanceof I ? r.code : "delivery_failed");
    }
  }
  async function pe(r) {
    if (!c || !f || g.closed || !P(r, ["type", "nonce", "requestId", "tag", "apkSha256"]) || !S(/[0-9a-f]{32}/, r.requestId)) return;
    let z = !1;
    try {
      h(r.tag === f.bundle.tag && r.apkSha256 === $);
      const d = AbortSignal.timeout(Math.min(l, 1e4)), y = await a.fetchWithAuth(O, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(n === null ? {} : { release_candidate: n }),
        redirect: "error",
        signal: d
      });
      h(y.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const k = JSON.parse(await (await N(
        y,
        H(r.tag) ? ne : q,
        d
      )).text());
      h(P(k, H(r.tag) ? ee : Y) && S(/[0-9a-f]{32}/, k.id) && k.tag === r.tag && k.apk_sha256 === r.apkSha256 && k.apk_size === f.apk.size), z = !0;
    } catch {
    }
    if (!(!f || g.closed))
      try {
        g.postMessage({
          type: "ha-paneld/usb-admission-result",
          nonce: A,
          requestId: r.requestId,
          tag: r.tag,
          apkSha256: r.apkSha256,
          admitted: z
        }, v);
      } catch {
      }
  }
  async function ue(r) {
    if (M || !c || !f || g.closed || !P(r, ["type", "nonce", "address"]) || !S(Pa, r.address)) return;
    M = !0;
    const z = (y, k = {}) => {
      try {
        g.closed || g.postMessage({ type: y, nonce: A, ...k }, v);
      } catch {
      }
    };
    z("ha-paneld/usb-handover-accepted");
    let d;
    try {
      const y = await a.fetchWithAuth(Ca, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address: r.address }),
        redirect: "error",
        signal: AbortSignal.timeout(Sa)
      }), k = await y.json().catch(() => null), _ = y.status === 200 ? k?.outcome : k?.error;
      d = S(xa, _) ? _ : `http_${y.status}`;
    } catch {
      d = "request_failed";
    } finally {
      M = !1;
    }
    z("ha-paneld/usb-handover-result", { outcome: d });
  }
  function G(r) {
    if (!(r.source !== g || r.origin !== v)) {
      if (r.data?.type === "ha-paneld/usb-admission" && r.data.nonce === A) {
        pe(r.data);
        return;
      }
      if (r.data?.type === "ha-paneld/usb-handover" && r.data.nonce === A) {
        ue(r.data);
        return;
      }
      if (!(!P(r.data, ["type", "nonce"]) || r.data.nonce !== A)) {
        if (r.data.type === "ha-paneld/usb-ready") {
          !L && !c ? (L = !0, ce()) : f && !g.closed && g.postMessage(f, v);
          return;
        }
        c || (r.data.type === "ha-paneld/usb-verified" && b ? x() : r.data.type === "ha-paneld/usb-error" && x("verification_failed"));
      }
    }
  }
  try {
    h(a && typeof a.fetchWithAuth == "function" && (n === null || J(n) || H(n)) && Number.isSafeInteger(l) && l > 0 && l <= 3e5, "invalid_request");
    const r = new URL(e);
    h(!r.username && !r.password && !r.hash && (r.protocol === "https:" || r.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(r.hostname)), "invalid_destination"), r.searchParams.set("lang", E(t)), v = r.origin, A = za(s.crypto), r.hash = new URLSearchParams({ ha_origin: s.location.origin, nonce: A, rc: n ?? "" }).toString(), s.addEventListener("message", G), g = s.open(r.href, "_blank"), h(g, "popup_blocked"), w = setTimeout(() => x("timeout"), l), Q("waiting");
  } catch (r) {
    x(r instanceof I ? r.code : "invalid_request");
  }
  return { completion: u, cancel: () => {
    x("cancelled"), T();
  } };
}
const ae = 30, te = 500, Ea = 128 * 1024;
function B(a) {
  if (!a) throw new Error("Invalid release catalogue");
}
const La = 128, Qa = (a) => H(a.tag) && typeof a.prerelease == "boolean" && typeof a.name == "string" && a.name.length > 0 && a.name.length <= La;
function Ia(a) {
  B(P(a, ["releases"]) && Array.isArray(a.releases) && a.releases.length <= ae + te);
  const e = /* @__PURE__ */ new Set();
  let n = 0, t = 0;
  return Object.freeze(a.releases.map((i) => P(i, ["tag", "prerelease", "name"]) ? (B(Qa(i) && !e.has(i.tag) && ++t <= te), e.add(i.tag), Object.freeze({ tag: i.tag, prerelease: i.prerelease, name: i.name })) : (B(P(i, ["tag", "prerelease"]) && typeof i.prerelease == "boolean" && (i.prerelease ? de(i.tag) : le(i.tag)) && !e.has(i.tag) && ++n <= ae), e.add(i.tag), Object.freeze({ tag: i.tag, prerelease: i.prerelease }))));
}
async function Ba(a, { signal: e, timeoutMs: n = 15e3 } = {}) {
  const t = new AbortController(), i = () => t.abort();
  e?.addEventListener("abort", i, { once: !0 }), e?.aborted && i();
  const s = setTimeout(i, n);
  let l;
  const o = new Promise((p, u) => {
    l = () => u(new Error("Release catalogue cancelled"));
  });
  t.signal.addEventListener("abort", l, { once: !0 });
  try {
    return B(!t.signal.aborted), await Promise.race([o, (async () => {
      const p = await a.fetchWithAuth("/api/panel_assistant/usb/releases", {
        method: "GET",
        redirect: "error",
        signal: t.signal
      });
      B(p.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const u = await N(p, Ea, t.signal);
      return Ia(JSON.parse(await u.text()));
    })()]);
  } finally {
    clearTimeout(s), e?.removeEventListener("abort", i), t.signal.removeEventListener("abort", l), t.abort();
  }
}
const Ta = `/* Generated from tools/tokens/tokens.json by tools/tokens/tokens.py; do not edit.
   Dark is the default. The light values apply when the viewing browser prefers light, unless
   ?theme=dark pinned dark (\`data-theme\` on the document, \`theme\` on a Panel Assistant host), and
   whenever ?theme=light pinned light. \`:host\` serves a shadow root, \`:root\` a page. */
:root,:host{color-scheme:dark;
  --bg:#111;--card:#181818;--card-head:#222;--input-bg:#1c1c1c;--input-bg2:#161616;--card-border:#242424;
  --divider:#2a2a2a;--config-divider:#3a3a3a;--border:#333;--border2:#383838;--border-strong:#444;--text:#eee;
  --text-bright:#fff;--text-soft:#ccc;--tab:#bbb;--dim:#888;--accent:#9af;--accent2:#9cf;--ok-dim:#8a8;
  --icon:#7c8a99;--icon-dim:#6a7a8a;--hover:#1a1a1a;--knob:#eee;--disabled-bg:#222;--disabled-fg:#666;
  --shadow:#000;--shimA:#141414;--shimB:#1f1f1f;--plot:#0d0d0d;--vtag-bg:#2a3340;--vtag-fg:#9cc;
  --vtagrec-bg:#1f4d2e;--vtagrec-fg:#8fe0a8;--bexp-bg:#0f2f3a;--bexp-fg:#5cc4e0;--bexp-bd:#1a4a5a;
  --setup-bg:#3a2f10;--setup-bd:#7a5a2a;--setup-fg:#f5c451;--setup-link:#ffd97a;--crit-bg:#3a1512;
  --crit-bd:#a5342a;--crit-fg:#ffb3a6;--crit-link:#ffc9bf;--info-bg:#12233a;--info-bd:#2a557a;
  --info-fg:#9cc4ee;--info-link:#bcd9f5;--api-desc:#667;--api-error:#d9534f;--api-ok:#5ad18a;
  --autobright-band:rgba(74,158,255,.15);--autobright-grid:#343a44;--autobright-label:#888;
  --backup-plain-warn:#c88;--cap-degraded:#d9a528;--cap-none:#d04a3b;--cap-ok:#48c774;--cfglink:#4a6a8a;
  --chart-bg:#0d0d0d;--chart-blocked:#f5a623;--chart-cpu:#4a9eff;--chart-expected:#f1bd52;--chart-gpu:#f5a623;
  --chart-grid:#383838;--chart-interaction:#d04a3b;--chart-label:#888;--chart-observed:#4a9eff;
  --chart-proposed:#b77cff;--chart-ram:#48c774;--chart-updates:#4a9eff;--edge-crit:#e05a48;
  --edge-info:#3d7fd0;--fact-warn:#f5c451;--fg-bad:#d04a3b;--fg-faint:#666;--fg-muted:#888;--fg-ok:#48c774;
  --fg-warn:#d9a528;--highlight:#4a9eff;--highlight-bg:rgba(74,158,255,.13);--install-rule:#2a2a2a;
  --link:#9cf;--listbox-shadow:rgba(0,0,0,.28);--log-error:#f07868;--log-info:#cfcfcf;--log-verbose:#777;
  --log-warn:#f5c451;--primary-text:#fff;--pkg-active:#3fb950;--pkg-dialog-bg:#1a1a1a;
  --pkg-dialog-border:#333;--pkg-dialog-fg:#eee;--pkg-divider:#222;--pkg-note:#9aa;--pkg-protected:#777;
  --pkg-tame:#7a2e2e;--primary:#2557a7;--primary-glow:rgba(37,87,167,.55);
  --primary-glow-off:rgba(37,87,167,0);--profile-danger-border:#8b342f;--profile-danger-fg:#ffb3a6;
  --profile-editor-active-line:rgba(127,153,204,.09);--profile-editor-selection:rgba(77,121,195,.35);
  --profile-info-bg:#12233a;--profile-info-border:#2a557a;--profile-info-fg:#9cc4ee;
  --profile-issue-error:#d05248;--profile-issue-info:#4284bb;--profile-issue-warning:#c6952d;
  --profile-modal-backdrop:rgba(0,0,0,.66);--profile-modal-shadow:rgba(0,0,0,.5);--profile-neutral-bg:#262b32;
  --profile-neutral-border:#626a75;--profile-neutral-fg:#d5d9df;--profile-ok-bg:#173521;
  --profile-ok-border:#326f43;--profile-ok-fg:#9ee5ad;--profile-primary-border:#376dc5;
  --profile-risk-bg:#3a1512;--profile-risk-border:#a5342a;--profile-warn-bg:#3a2f10;
  --profile-warn-border:#7a5a2a;--profile-warn-fg:#ffd97a;--pswitch-border:#2a3550;
  --pswitch-border-hover:#4a6ba0;--reboot-border:#7a3a2a;--reboot-fg:#f5a08a;--reload-border:#7a6330;
  --reload-fg:#f5cf82;--screenshot-backdrop:rgba(0,0,0,.72);--screenshot-error:#e07868;
  --screenshot-frame-bg:#000;--self-row-border:#2a2a2a;--shot-failed-fg:#a88;--sleep-allow:#9aafc2;
  --sleep-allow-fg:#172533;--sleep-blocked-a:#a33b3b;--sleep-blocked-b:#5b2626;--sleep-detected:#c89a18;
  --sleep-detected-fg:#17130a;--sleep-hold:#287dcc;--sleep-inhibited-stripe:#f1bd52;--sleep-interval-fg:#fff;
  --sleep-loading-overlay:rgba(18,22,28,.76);--sleep-off:#68727f;--sleep-off-fg:#f4f6f8;
  --sleep-policy-lane:rgba(74,158,255,.11);--status-bad:#d34b42;--status-ok:#45a65a;--status-warn:#c58a16;
  --tame-recommended:#2e6b3f;--uninstall-select-bg:#1c1c1c;--uninstall-select-border:#444;
  --uninstall-select-fg:#eee;--api-get-bg:#173d2a;--api-get-fg:#5ad18a;--api-post-bg:#1b3a5c;
  --api-post-fg:#6db3ff;--api-response-fg:#cdd;}
@media (prefers-color-scheme: light){:root:not([data-theme=dark]),:host(:not([theme=dark])){color-scheme:light;
  --bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--input-bg:#f6f7f9;--input-bg2:#fafbfc;--card-border:#d9dde3;
  --divider:#e4e7ec;--config-divider:#e4e7ec;--border:#c9cfd7;--border2:#c4cad2;--border-strong:#b6bec8;
  --text:#1b2430;--text-bright:#000;--text-soft:#2c3644;--tab:#4a5462;--dim:#6a7480;--accent:#1e56a8;
  --accent2:#0b6a8e;--ok-dim:#3f7d49;--icon:#5b6b7a;--icon-dim:#74818f;--hover:#e9ecef;--knob:#fff;
  --disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18);--shimA:#e8eaee;--shimB:#f6f7f9;
  --plot:#e8ecf0;--vtag-bg:#e2e8ef;--vtag-fg:#33586b;--vtagrec-bg:#d9efe0;--vtagrec-fg:#1f6b3a;
  --bexp-bg:#ddeef5;--bexp-fg:#0b607f;--bexp-bd:#b3d4e2;--setup-bg:#fcf4dc;--setup-bd:#e3cf94;
  --setup-fg:#7a5a10;--setup-link:#8a6400;--crit-bg:#fceceb;--crit-bd:#eab3ac;--crit-fg:#a02c20;
  --crit-link:#7c1f16;--info-bg:#e9f1fb;--info-bd:#b9d2ec;--info-fg:#26567e;--info-link:#1a4468;}}
:root[data-theme=light],:host([theme=light]){color-scheme:light;
  --bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--input-bg:#f6f7f9;--input-bg2:#fafbfc;--card-border:#d9dde3;
  --divider:#e4e7ec;--config-divider:#e4e7ec;--border:#c9cfd7;--border2:#c4cad2;--border-strong:#b6bec8;
  --text:#1b2430;--text-bright:#000;--text-soft:#2c3644;--tab:#4a5462;--dim:#6a7480;--accent:#1e56a8;
  --accent2:#0b6a8e;--ok-dim:#3f7d49;--icon:#5b6b7a;--icon-dim:#74818f;--hover:#e9ecef;--knob:#fff;
  --disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18);--shimA:#e8eaee;--shimB:#f6f7f9;
  --plot:#e8ecf0;--vtag-bg:#e2e8ef;--vtag-fg:#33586b;--vtagrec-bg:#d9efe0;--vtagrec-fg:#1f6b3a;
  --bexp-bg:#ddeef5;--bexp-fg:#0b607f;--bexp-bd:#b3d4e2;--setup-bg:#fcf4dc;--setup-bd:#e3cf94;
  --setup-fg:#7a5a10;--setup-link:#8a6400;--crit-bg:#fceceb;--crit-bd:#eab3ac;--crit-fg:#a02c20;
  --crit-link:#7c1f16;--info-bg:#e9f1fb;--info-bd:#b9d2ec;--info-fg:#26567e;--info-link:#1a4468;}
`, _a = `<div class="wiz-brand">${Xn.replace("<svg ", '<svg role="img" aria-label="Panel Assistant" ')}</div>`, Na = Object.freeze(Object.values(C("journey", "en")));
function Da(a) {
  return Na.map((e, n) => n < a ? `<li class="done">${e}</li>` : n === a ? `<li class="current" aria-current="step">${e}</li>` : `<li>${e}</li>`).join("");
}
function Ma(a, e, n = document, t = "en") {
  const i = Object.values(C("journey", t)).map((s, l) => {
    const o = n.createElement("li");
    return o.textContent = s, l < e && (o.className = "done"), l === e && (o.className = "current", o.setAttribute("aria-current", "step")), o;
  });
  a.replaceChildren(...i);
}
const Oa = `${Ta}
:root,:host{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
*,*::before,*::after{box-sizing:border-box}
.wiz{max-width:520px;margin:0 auto;padding:4px 0 24px;color:var(--text)}
.wiz-brand{display:flex;align-items:center;gap:.28em;font-size:1.3rem;font-weight:700;margin:0 0 14px}
.wiz-brand svg{display:block;height:2.1em;width:auto;max-width:100%}
.wiz-dots{display:flex;gap:14px;justify-content:center;list-style:none;margin:4px 0 14px;padding:0;flex-wrap:wrap}
.wiz-dots li{display:flex;align-items:center;gap:6px;font-size:.8rem;color:var(--dim)}
.wiz-dots li::before{content:"";flex:none;width:.55rem;height:.55rem;border-radius:50%;background:var(--dim);opacity:.35}
.wiz-dots li.current{color:var(--text);font-weight:600}
.wiz-dots li.current::before{background:var(--primary);opacity:1}
.wiz-dots li.done::before{background:var(--ok-dim);opacity:1}
.card{background:var(--card);border:1px solid var(--card-border);border-radius:12px;padding:14px 16px;margin:0 0 14px}
.card h2{margin:-14px -16px 12px;padding:9px 16px;background:var(--card-head);border-bottom:1px solid var(--divider);
  border-radius:12px 12px 0 0;font-size:1.15rem;line-height:1.35;color:var(--text)}
.card p{line-height:1.5;margin:2px 0 14px;color:var(--dim)}
.card p.lead{color:var(--text);font-size:1.05rem}
.card label{display:block;margin:14px 0 5px;font-weight:700;font-size:.92rem;color:var(--accent)}
.card select{display:block;width:100%;font:inherit;font-size:1rem;min-height:42px;padding:8px 10px;margin:0 0 6px;
  background:var(--input-bg2);border:1px solid var(--border2);color:var(--text);border-radius:6px}
button.primary,a.primary{display:block;width:100%;min-height:46px;margin-top:16px;padding:10px 16px;border:1px solid var(--primary);
  border-radius:8px;background:var(--primary);color:var(--primary-text);font:inherit;font-size:1rem;text-align:center;
  text-decoration:none;cursor:pointer}
button.secondary{display:block;width:100%;min-height:42px;margin-top:8px;padding:8px 16px;border:1px solid var(--border-strong);
  border-radius:8px;background:transparent;color:var(--accent);font:inherit;cursor:pointer}
button.primary:disabled,button.secondary:disabled{background:var(--disabled-bg);color:var(--disabled-fg);border-color:var(--divider);cursor:default}
.spinner{width:2.25rem;height:2.25rem;border-radius:50%;margin:.5rem 0 1.25rem;border:.25rem solid var(--divider);
  border-top-color:var(--primary);animation:wiz-spin .9s linear infinite}
@keyframes wiz-spin{to{transform:rotate(360deg)}}
.bar{height:.5rem;border-radius:1rem;background:var(--divider);overflow:hidden;margin:1rem 0 .9rem}
.bar>div{height:100%;width:0;background:var(--primary);transition:width .4s ease}
@media (prefers-reduced-motion:reduce){.spinner{animation-duration:3s}.bar>div{transition:none}}
.error h2{color:var(--crit-fg)}
[hidden]{display:none!important}
`;
class qa extends HTMLElement {
  #e;
  get #n() {
    return C("haInstall", this.#e?.language);
  }
  #d;
  #a;
  // A finished transfer keeps answering a reloaded installer window until this
  // page goes away or a new transfer starts.
  #t;
  #g = "ready";
  #i;
  #r = "loading";
  #s = [];
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${Oa}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      .card p.status{color:var(--text);margin:14px 0 0}
      #release{max-height:min(240px,40vh);overflow-y:auto}
    </style><main class="wiz">
      ${_a}
      <ol id="journey" class="wiz-dots">${Da(0)}</ol>
      <section class="card">
        <h2 data-message="title"></h2>
        <p class="lead" data-message="introduction"></p>
        <label for="release" data-message="release"></label>
        <select id="release" size="6" aria-describedby="catalog-status"></select>
        <p id="catalog-status" role="status" aria-live="polite"></p>
        <button id="retry" class="secondary" data-message="retry"></button>
        <button id="start" class="primary" data-message="start"></button>
        <p id="status" class="status" role="status" aria-live="polite"></p>
        <button id="cancel" class="secondary" data-message="cancel"></button>
      </section>
    </main>`;
    for (const e of this.shadowRoot.querySelectorAll("[data-message]"))
      e.textContent = this.#n[e.dataset.message];
    this.shadowRoot.querySelector("#start").addEventListener("click", () => this.#l()), this.shadowRoot.querySelector("#cancel").addEventListener("click", () => this.#a?.cancel()), this.shadowRoot.querySelector("#retry").addEventListener("click", () => this.#u()), this.shadowRoot.querySelector("#release").addEventListener("change", () => this.#o()), this.#o();
  }
  set hass(e) {
    const n = this.#e?.user?.id !== e?.user?.id || this.#e?.user?.is_admin !== e?.user?.is_admin || this.#e?.connection !== e?.connection || this.#e?.auth !== e?.auth, t = this.#e?.language !== e?.language;
    this.#e = e;
    const i = e?.themes?.darkMode;
    typeof i == "boolean" && this.setAttribute?.("theme", i ? "dark" : "light"), n && (this.#a?.cancel(), this.#u()), (n || t) && this.#o();
  }
  set panel(e) {
    const n = this.#d?.config?.installer_url !== e?.config?.installer_url;
    n && this.#a?.cancel(), this.#d = e, n && this.#u(), this.#o();
  }
  connectedCallback() {
    this.#u();
  }
  disconnectedCallback() {
    this.#a?.cancel(), this.#t?.cancel(), this.#t = void 0, this.#i?.abort(), this.#i = void 0;
  }
  async #u() {
    if (this.#i?.abort(), this.#i = void 0, this.#s = [], this.#r = "loading", this.shadowRoot.querySelector("#release").replaceChildren(), this.#o(), !this.isConnected || this.#e?.user?.is_admin !== !0 || !this.#d?.config?.installer_url) return;
    const e = new AbortController();
    this.#i = e;
    try {
      const n = await Ba(this.#e, { signal: e.signal });
      if (this.#i !== e) return;
      this.#s = n, this.#r = n.length ? "ready" : "empty";
      const t = this.shadowRoot.querySelector("#release"), i = document.createElement("option");
      i.value = "", i.textContent = this.#n.choose, i.disabled = !1, t.append(i);
      for (const s of n) {
        const l = document.createElement("option");
        l.value = s.tag;
        const o = s.name ? this.#n.devBuild : s.prerelease ? this.#n.testing : "";
        l.textContent = `${s.name ?? s.tag.replace(/^v/, "")}${o ? ` (${o})` : ""}`, t.append(l);
      }
      t.value = "";
    } catch {
      if (this.#i !== e) return;
      this.#r = "catalogError";
    } finally {
      this.#i === e && (this.#i = void 0, this.#o());
    }
  }
  #o() {
    const e = this.shadowRoot, n = this.#e?.language;
    this.isConnected && this.setAttribute?.("lang", E(n));
    for (const g of e.querySelectorAll("[data-message]")) g.textContent = this.#n[g.dataset.message];
    const t = e.querySelector("#journey");
    t.setAttribute("aria-label", C("installer", n).progress), Ma(t, 0, document, n);
    const i = e.querySelector("#release").children;
    if (i.length) {
      i[0].textContent = this.#n.choose;
      for (const [g, w] of this.#s.entries()) {
        const v = w.name ? this.#n.devBuild : w.prerelease ? this.#n.testing : "";
        i[g + 1].textContent = `${w.name ?? w.tag.replace(/^v/, "")}${v ? ` (${v})` : ""}`;
      }
    }
    const s = this.#e?.user?.is_admin === !0, l = typeof this.#d?.config?.installer_url == "string" && this.#d.config.installer_url.length > 0, o = this.shadowRoot.querySelector("#release").value, p = o === "" ? this.#s[0] : this.#s.find((g) => g.tag === o);
    this.shadowRoot.querySelector("#start").disabled = !s || !l || !!this.#a || !p, this.shadowRoot.querySelector("#cancel").disabled = !this.#a, this.shadowRoot.querySelector("#release").disabled = !!this.#a || this.#r !== "ready";
    const u = this.shadowRoot.querySelector("#catalog-status");
    u.textContent = s && l && this.#r !== "ready" ? this.#n[this.#r] : "", u.hidden = !u.textContent, this.shadowRoot.querySelector("#retry").hidden = !s || !l || !["catalogError", "empty"].includes(this.#r), this.shadowRoot.querySelector("#cancel").hidden = !this.#a;
    const m = s ? l ? this.#g : "unavailable" : "admin", c = this.shadowRoot.querySelector("#status");
    c.textContent = Object.hasOwn(this.#n, m) ? this.#n[m] : this.#n.failed, c.hidden = !c.textContent;
  }
  #l() {
    if (this.#a || this.#e?.user?.is_admin !== !0) return;
    const e = this.shadowRoot.querySelector("#release").value;
    if (!(e === "" ? this.#s[0] : this.#s.find((i) => i.tag === e)) || !this.isConnected) return;
    this.#t?.cancel(), this.#t = void 0;
    const t = Ha(this.#e, this.#d?.config?.installer_url, {
      rcTag: e || null,
      language: E(this.#e?.language),
      onState: (i) => {
        this.#g = i, this.#o();
      }
    });
    this.#a = t, this.#o(), t.completion.then(() => {
      this.#a === t && (this.#t = t);
    }, () => {
    }).finally(() => {
      this.#a === t && (this.#a = void 0), this.#o();
    });
  }
}
customElements.get("panel-assistant-usb-install") || customElements.define("panel-assistant-usb-install", qa);
