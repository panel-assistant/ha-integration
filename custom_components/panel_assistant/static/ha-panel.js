const we = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Open navigation", choosePanel: "Panel", more: "More options", addPanel: "Add panel", integrationSettings: "Integration settings", device: "This panel's Home Assistant device", showDevice: "Show device", github: "GitHub", unreachable: "unreachable", restarting: "Restarting ({reason})", not_loaded: "not loaded", opening: "Opening {panel}…", loadingHint: "Usually takes a few seconds", empty: "No panels are attached yet.", failed: "The panel could not be opened. It will be tried again shortly.", admin: "An administrator must open this page.", unreachableBody: "Home Assistant cannot reach this panel right now.", notLoadedBody: "This panel is not loaded in Home Assistant.", closed: "This panel was closed.", frameTitle: "Panel interface", picklesStory: "Pickles the panda has escaped and is causing havoc. He’s slow and stubborn, so getting him back may take a moment.", unreachableNext: "Check that the panel is powered on and connected to your network.", notLoadedNext: "Open Integration settings to check this panel’s connection.", failedNext: "Wait a moment while we try again, or choose another panel.", closedNext: "Choose another panel, or wait for this panel to reconnect." }, ke = { title: "Install ha-paneld on a panel", introduction: "Plug the panel into this computer with a USB cable. A new window will find it and install the app.", release: "Version", loading: "Loading versions…", catalogError: "The list of versions couldn’t be loaded.", empty: "No versions are available yet. Try again later.", choose: "Follow Panel Assistant’s channel (recommended)", testing: "test version", devBuild: "dev build", retry: "Try again", start: "Continue", cancel: "Cancel", ready: "", unavailable: "The installer isn’t available. Update Panel Assistant, then try again.", admin: "Ask a Home Assistant administrator to install panels.", waiting: "Continue in the new window.", preparing: "Getting the app ready…", downloading: "Getting the app ready…", verifying: "Getting the app ready…", verified: "Continue in the new window.", cancelled: "Cancelled.", popup_blocked: "Your browser blocked the new window. Allow pop-ups for this page, then press Continue.", invalid_request: "Choose a version first.", failed: "That didn’t work. Press Continue to try again." }, ze = { title: "Set up your panel", preparing: "Getting the app ready…", preparingSlow: "Still getting the app ready. Keep the Home Assistant tab open.", connectHeading: "Connect your panel", connectBody: "Plug the panel into this computer with a USB cable, then press Find my panel.", connect: "Find my panel", allowHeading: "Allow this computer", allowBody: "Look at the panel’s screen and tap Allow.", checkingPanel: "Checking your panel…", confirmHeading: "Ready to install", confirmBody: "This installs the app and makes it your panel’s home screen. Your other apps are not touched.", install: "Install", restartedDifferentVersion: "An earlier installation attempt used a different release. That attempt has been set aside.", alreadyInstalledHeading: "Already installed", alreadyInstalledBody: "This version is already on your panel. Continue to finish setting it up; nothing is copied or reinstalled.", continueSetup: "Continue", progressHeading: "Installing", stepCopying: "Copying the app to your panel…", stepFinishingCopy: "Finishing the copy…", stepInstalling: "Installing…", stepStarting: "Starting the app…", stepPermissions: "Giving the app what it needs to run…", stepOpening: "Opening your panel’s setup…", keepConnected: "Keep the cable plugged in until this finishes.", doneHeading: "Installed", doneOpening: "Taking you to your panel’s setup…", doneManual: "Finish setting up on the panel’s screen.", openSetup: "Open panel setup", errorHeading: "That didn’t work", tryAgain: "Try again", backToHa: "Back to Home Assistant", details: "Details for support", unsupported: "This browser can’t talk to USB devices. Open this page in Chrome or Edge on a computer.", handoffFailure: "The app couldn’t be fetched from Home Assistant. Go back to Home Assistant and start again.", noSelection: "No panel was chosen. Press Find my panel and pick it from the list, or try another cable: some cables power the panel but carry no data.", connectionTimeout: "The panel didn’t answer. Check the cable is firmly in, or swap it: a cable can power the panel and still carry no data.", disconnected: "The panel was disconnected. Plug it back in, wait for it to start, then press Try again.", cancelled: "Cancelled.", pageClosed: "The page was closed.", progress: "Progress", installProgress: "Installation progress", licenses: "Third-party licenses" }, Ie = { installErrorGeneric: "Something went wrong. Keep the panel plugged in and press Try again.", installErrorBusy: "The panel is busy with another install. Wait a minute, then try again.", installErrorStorage: "This browser couldn’t save its progress. Allow this site to store data, then try again.", installErrorTarget: "A different panel was connected. Plug in the same panel and try again.", installErrorArtifact: "The app download couldn’t be checked. Go back to Home Assistant and start again.", installErrorNotClean: "This panel already has the app. Update it from Home Assistant instead.", installErrorIncompatible: "This panel can’t run this version of the app.", installErrorConnection: "The connection to the panel dropped. Keep it plugged in and press Try again.", installErrorHealth: "The app is installed but hasn’t started yet. Wait a moment, then try again." }, je = { version: "Version", connect: "Connect", install: "Install", setup: "Set up" }, Ce = { update: "update", settings: "settings", recovery: "recovery", reboot: "reboot" }, Ee = { stopped: "Stopped", cause: "Cause", error: "Error", release: "Release", panel: "Panel", copied: "Copied", savedProgress: "Saved progress", earlierCopy: "Earlier copy", stagedCopy: "Staged copy", setAside: "Set aside", alreadyInstalled: "Already installed", permissions: "Permissions", setupAddress: "Setup address", setupHandover: "Setup handover", copiedDetail: "{bytes} bytes sent; waiting for the panel to confirm", none: "none", setupMissing: "not found; setup continues on the panel" }, xe = {
  sidebar: we,
  haInstall: ke,
  installer: ze,
  errors: Ie,
  journey: je,
  sidebarReason: Ce,
  support: Ee
}, Pe = { title: "Panel Assistant", versionLabel: "{version}, sestavení {build}", menu: "Otevřít navigaci", choosePanel: "Panel", more: "Další možnosti", addPanel: "Přidat panel", integrationSettings: "Nastavení integrace", device: "Zařízení tohoto panelu v Home Assistant", showDevice: "Zobrazit zařízení", github: "GitHub", unreachable: "nedostupný", restarting: "Restartování ({reason})", not_loaded: "nenačtený", opening: "Otevírání panelu {panel}…", loadingHint: "Obvykle to trvá několik sekund", empty: "Zatím nejsou připojené žádné panely.", failed: "Panel se nepodařilo otevřít. Brzy proběhne další pokus.", admin: "Tuto stránku musí otevřít správce.", unreachableBody: "Home Assistant se k tomuto panelu momentálně nedostane.", notLoadedBody: "Tento panel není v Home Assistant načtený.", closed: "Tento panel byl zavřen.", frameTitle: "Rozhraní panelu", picklesStory: "Panda Pickles utekl a tropí neplechu. Je pomalý a tvrdohlavý, takže dostat ho zpátky může chvíli trvat.", unreachableNext: "Zkontrolujte, že je panel zapnutý a připojený k vaší síti.", notLoadedNext: "Otevřete Nastavení integrace a zkontrolujte připojení tohoto panelu.", failedNext: "Chvíli počkejte, než to zkusíme znovu, nebo vyberte jiný panel.", closedNext: "Vyberte jiný panel nebo počkejte, až se tento panel znovu připojí." }, Me = { title: "Nainstalovat ha-paneld na panel", introduction: "Připojte panel k tomuto počítači kabelem USB. Nové okno ho najde a nainstaluje aplikaci.", release: "Verze", loading: "Načítání verzí…", catalogError: "Seznam verzí se nepodařilo načíst.", empty: "Zatím nejsou dostupné žádné verze. Zkuste to znovu později.", choose: "Podle kanálu Panel Assistant (doporučeno)", testing: "testovací verze", devBuild: "vývojové sestavení", retry: "Zkusit znovu", start: "Pokračovat", cancel: "Zrušit", ready: "", unavailable: "Instalátor není dostupný. Aktualizujte Panel Assistant a pak to zkuste znovu.", admin: "Požádejte správce Home Assistant o instalaci panelů.", waiting: "Pokračujte v novém okně.", preparing: "Příprava aplikace…", downloading: "Příprava aplikace…", verifying: "Příprava aplikace…", verified: "Pokračujte v novém okně.", cancelled: "Zrušeno.", popup_blocked: "Prohlížeč zablokoval nové okno. Povolte pro tuto stránku vyskakovací okna a pak stiskněte Pokračovat.", invalid_request: "Nejprve vyberte verzi.", failed: "To se nepodařilo. Stiskněte Pokračovat a zkuste to znovu." }, Se = { title: "Nastavte svůj panel", preparing: "Příprava aplikace…", preparingSlow: "Aplikace se stále připravuje. Nechte kartu Home Assistant otevřenou.", connectHeading: "Připojte svůj panel", connectBody: "Připojte panel k tomuto počítači kabelem USB a pak stiskněte Najít můj panel.", connect: "Najít můj panel", allowHeading: "Povolit tento počítač", allowBody: "Podívejte se na obrazovku panelu a klepněte na Povolit.", checkingPanel: "Kontrola panelu…", confirmHeading: "Připraveno k instalaci", confirmBody: "Tímto nainstalujete aplikaci a nastavíte ji jako domovskou obrazovku panelu. Vašich ostatních aplikací se to nedotkne.", install: "Nainstalovat", restartedDifferentVersion: "Předchozí pokus o instalaci používal jiné vydání. Ten pokus byl ponechán stranou.", alreadyInstalledHeading: "Už nainstalováno", alreadyInstalledBody: "Tato verze už na vašem panelu je. Pokračujte a dokončete nastavení; nic se nekopíruje ani znovu neinstaluje.", continueSetup: "Pokračovat", progressHeading: "Instalace", stepCopying: "Kopírování aplikace do panelu…", stepFinishingCopy: "Dokončování kopírování…", stepInstalling: "Instalování…", stepStarting: "Spouštění aplikace…", stepPermissions: "Poskytování toho, co aplikace potřebuje k běhu…", stepOpening: "Otevírání nastavení panelu…", keepConnected: "Nechte kabel připojený, dokud se proces nedokončí.", doneHeading: "Nainstalováno", doneOpening: "Přesměrování na nastavení panelu…", doneManual: "Dokončete nastavení na obrazovce panelu.", openSetup: "Otevřít nastavení panelu", errorHeading: "To se nepodařilo", tryAgain: "Zkusit znovu", backToHa: "Zpět do Home Assistant", details: "Podrobnosti pro podporu", unsupported: "Tento prohlížeč nedokáže komunikovat se zařízeními USB. Otevřete tuto stránku v prohlížeči Chrome nebo Edge na počítači.", handoffFailure: "Aplikaci se nepodařilo získat z Home Assistant. Vraťte se do Home Assistant a začněte znovu.", noSelection: "Nebyl vybrán žádný panel. Stiskněte Najít můj panel a vyberte ho ze seznamu nebo zkuste jiný kabel: některé kabely panel napájejí, ale nepřenášejí data.", connectionTimeout: "Panel neodpověděl. Zkontrolujte, že je kabel pevně připojený, nebo ho vyměňte: kabel může panel napájet a přitom nepřenášet data.", disconnected: "Panel byl odpojen. Znovu ho připojte, počkejte, až se spustí, a pak stiskněte Zkusit znovu.", cancelled: "Zrušeno.", pageClosed: "Stránka byla zavřena.", progress: "Průběh", installProgress: "Průběh instalace", licenses: "Licence třetích stran" }, De = { installErrorGeneric: "Něco se pokazilo. Nechte panel připojený a stiskněte Zkusit znovu.", installErrorBusy: "Panel je zaneprázdněný jinou instalací. Počkejte minutu a pak to zkuste znovu.", installErrorStorage: "Tento prohlížeč nedokázal uložit průběh. Povolte tomuto webu ukládání dat a pak to zkuste znovu.", installErrorTarget: "Byl připojen jiný panel. Připojte stejný panel a zkuste to znovu.", installErrorArtifact: "Staženou aplikaci se nepodařilo ověřit. Vraťte se do Home Assistant a začněte znovu.", installErrorNotClean: "Tento panel už aplikaci má. Aktualizujte ji z Home Assistant.", installErrorIncompatible: "Tento panel nedokáže spustit tuto verzi aplikace.", installErrorConnection: "Spojení s panelem se přerušilo. Nechte ho připojený a stiskněte Zkusit znovu.", installErrorHealth: "Aplikace je nainstalovaná, ale ještě se nespustila. Chvíli počkejte a pak to zkuste znovu." }, He = { version: "Verze", connect: "Připojit", install: "Nainstalovat", setup: "Nastavit" }, Ne = { update: "aktualizace", settings: "nastavení", recovery: "obnova", reboot: "restart" }, Be = { stopped: "Zastaveno", cause: "Příčina", error: "Chyba", release: "Vydání", panel: "Panel", copied: "Zkopírováno", savedProgress: "Uložený průběh", earlierCopy: "Dřívější kopie", stagedCopy: "Připravená kopie", setAside: "Odloženo", alreadyInstalled: "Už nainstalováno", permissions: "Oprávnění", setupAddress: "Adresa nastavení", setupHandover: "Předání nastavení", copiedDetail: "Odesláno {bytes} bajtů; čeká se na potvrzení panelu", none: "žádné", setupMissing: "nenalezeno; nastavení pokračuje na panelu" }, Le = {
  sidebar: Pe,
  haInstall: Me,
  installer: Se,
  errors: De,
  journey: He,
  sidebarReason: Ne,
  support: Be
}, Te = { title: "Panel Assistant", versionLabel: "{version} Build {build}", menu: "Navigation öffnen", choosePanel: "Panel", more: "Weitere Optionen", addPanel: "Panel hinzufügen", integrationSettings: "Integrationseinstellungen", device: "Home Assistant-Gerät dieses Panels", showDevice: "Gerät anzeigen", github: "GitHub", unreachable: "nicht erreichbar", restarting: "Neustart ({reason})", not_loaded: "nicht geladen", opening: "{panel} wird geöffnet…", loadingHint: "Dauert normalerweise nur wenige Sekunden", empty: "Es sind noch keine Panels verbunden.", failed: "Das Panel konnte nicht geöffnet werden. In Kürze wird es erneut versucht.", admin: "Diese Seite muss von einem Administrator geöffnet werden.", unreachableBody: "Home Assistant kann dieses Panel gerade nicht erreichen.", notLoadedBody: "Dieses Panel ist in Home Assistant nicht geladen.", closed: "Dieses Panel wurde geschlossen.", frameTitle: "Panel-Oberfläche", picklesStory: "Der Panda Pickles ist ausgebüxt und richtet Chaos an. Er ist langsam und stur, deshalb kann es einen Moment dauern, ihn zurückzuholen.", unreachableNext: "Prüfe, ob das Panel eingeschaltet und mit deinem Netzwerk verbunden ist.", notLoadedNext: "Öffne die Integrationseinstellungen, um die Verbindung dieses Panels zu prüfen.", failedNext: "Warte einen Moment, während wir es erneut versuchen, oder wähle ein anderes Panel.", closedNext: "Wähle ein anderes Panel oder warte, bis sich dieses Panel wieder verbindet." }, Oe = { title: "ha-paneld auf einem Panel installieren", introduction: "Verbinde das Panel über ein USB-Kabel mit diesem Computer. Ein neues Fenster findet es und installiert die App.", release: "Version", loading: "Versionen werden geladen…", catalogError: "Die Versionsliste konnte nicht geladen werden.", empty: "Es sind noch keine Versionen verfügbar. Versuche es später erneut.", choose: "Dem Kanal von Panel Assistant folgen (empfohlen)", testing: "Testversion", devBuild: "Entwicklungsbuild", retry: "Erneut versuchen", start: "Weiter", cancel: "Abbrechen", ready: "", unavailable: "Das Installationsprogramm ist nicht verfügbar. Aktualisiere Panel Assistant und versuche es erneut.", admin: "Bitte einen Home Assistant-Administrator, Panels zu installieren.", waiting: "Fahre im neuen Fenster fort.", preparing: "Die App wird vorbereitet…", downloading: "Die App wird vorbereitet…", verifying: "Die App wird vorbereitet…", verified: "Fahre im neuen Fenster fort.", cancelled: "Abgebrochen.", popup_blocked: "Dein Browser hat das neue Fenster blockiert. Erlaube Pop-ups für diese Seite und drücke dann „Weiter“.", invalid_request: "Wähle zuerst eine Version.", failed: "Das hat nicht geklappt. Drücke „Weiter“, um es erneut zu versuchen." }, Re = { title: "Dein Panel einrichten", preparing: "Die App wird vorbereitet…", preparingSlow: "Die App wird noch vorbereitet. Lass den Home Assistant-Tab geöffnet.", connectHeading: "Dein Panel verbinden", connectBody: "Verbinde das Panel über ein USB-Kabel mit diesem Computer und drücke dann „Mein Panel finden“.", connect: "Mein Panel finden", allowHeading: "Diesen Computer zulassen", allowBody: "Schau auf den Bildschirm des Panels und tippe auf „Zulassen“.", checkingPanel: "Dein Panel wird geprüft…", confirmHeading: "Bereit zur Installation", confirmBody: "Die App wird installiert und als Startbildschirm deines Panels eingerichtet. Deine anderen Apps bleiben unberührt.", install: "Installieren", restartedDifferentVersion: "Ein früherer Installationsversuch verwendete eine andere Version. Dieser Versuch wird nicht weiterverwendet.", alreadyInstalledHeading: "Bereits installiert", alreadyInstalledBody: "Diese Version ist bereits auf deinem Panel. Fahre fort, um die Einrichtung abzuschließen; es wird nichts kopiert oder neu installiert.", continueSetup: "Weiter", progressHeading: "Installation läuft", stepCopying: "Die App wird auf dein Panel kopiert…", stepFinishingCopy: "Der Kopiervorgang wird abgeschlossen…", stepInstalling: "Installation läuft…", stepStarting: "Die App wird gestartet…", stepPermissions: "Die App erhält alles, was sie zum Ausführen braucht…", stepOpening: "Die Einrichtung deines Panels wird geöffnet…", keepConnected: "Lass das Kabel angeschlossen, bis der Vorgang abgeschlossen ist.", doneHeading: "Installiert", doneOpening: "Du wirst zur Einrichtung deines Panels weitergeleitet…", doneManual: "Schließe die Einrichtung auf dem Bildschirm des Panels ab.", openSetup: "Panel-Einrichtung öffnen", errorHeading: "Das hat nicht geklappt", tryAgain: "Erneut versuchen", backToHa: "Zurück zu Home Assistant", details: "Details für den Support", unsupported: "Dieser Browser kann nicht mit USB-Geräten kommunizieren. Öffne diese Seite auf einem Computer in Chrome oder Edge.", handoffFailure: "Die App konnte nicht von Home Assistant abgerufen werden. Gehe zurück zu Home Assistant und beginne erneut.", noSelection: "Es wurde kein Panel ausgewählt. Drücke „Mein Panel finden“ und wähle es aus der Liste oder versuche ein anderes Kabel: Manche Kabel versorgen das Panel mit Strom, übertragen aber keine Daten.", connectionTimeout: "Das Panel hat nicht geantwortet. Prüfe, ob das Kabel fest sitzt, oder tausche es aus: Ein Kabel kann das Panel mit Strom versorgen, ohne Daten zu übertragen.", disconnected: "Die Verbindung zum Panel wurde getrennt. Schließe es wieder an, warte, bis es gestartet ist, und drücke dann „Erneut versuchen“.", cancelled: "Abgebrochen.", pageClosed: "Die Seite wurde geschlossen.", progress: "Fortschritt", installProgress: "Installationsfortschritt", licenses: "Lizenzen von Drittanbietern" }, qe = { installErrorGeneric: "Etwas ist schiefgegangen. Lass das Panel angeschlossen und drücke „Erneut versuchen“.", installErrorBusy: "Das Panel ist mit einer anderen Installation beschäftigt. Warte eine Minute und versuche es dann erneut.", installErrorStorage: "Dieser Browser konnte den Fortschritt nicht speichern. Erlaube dieser Website, Daten zu speichern, und versuche es erneut.", installErrorTarget: "Ein anderes Panel wurde angeschlossen. Schließe dasselbe Panel an und versuche es erneut.", installErrorArtifact: "Die heruntergeladene App konnte nicht geprüft werden. Gehe zurück zu Home Assistant und beginne erneut.", installErrorNotClean: "Die App ist bereits auf diesem Panel. Aktualisiere sie über Home Assistant.", installErrorIncompatible: "Dieses Panel kann diese Version der App nicht ausführen.", installErrorConnection: "Die Verbindung zum Panel wurde unterbrochen. Lass es angeschlossen und drücke „Erneut versuchen“.", installErrorHealth: "Die App ist installiert, aber noch nicht gestartet. Warte einen Moment und versuche es dann erneut." }, Qe = { version: "Version", connect: "Verbinden", install: "Installieren", setup: "Einrichten" }, Ge = { update: "Aktualisierung", settings: "Einstellungen", recovery: "Wiederherstellung", reboot: "Systemneustart" }, Ue = { stopped: "Gestoppt", cause: "Ursache", error: "Fehler", release: "Version", panel: "Panel", copied: "Kopiert", savedProgress: "Gespeicherter Fortschritt", earlierCopy: "Frühere Kopie", stagedCopy: "Bereitgestellte Kopie", setAside: "Zurückgestellt", alreadyInstalled: "Bereits installiert", permissions: "Berechtigungen", setupAddress: "Einrichtungsadresse", setupHandover: "Übergabe an die Einrichtung", copiedDetail: "{bytes} Bytes gesendet; Bestätigung des Panels ausstehend", none: "keine", setupMissing: "nicht gefunden; Einrichtung wird auf dem Panel fortgesetzt" }, Ve = {
  sidebar: Te,
  haInstall: Oe,
  installer: Re,
  errors: qe,
  journey: Qe,
  sidebarReason: Ge,
  support: Ue
}, Fe = { title: "Panel Assistant", versionLabel: "{version}, compilación {build}", menu: "Abrir navegación", choosePanel: "Panel", more: "Más opciones", addPanel: "Añadir panel", integrationSettings: "Configuración de la integración", device: "Dispositivo de Home Assistant de este panel", showDevice: "Mostrar dispositivo", github: "GitHub", unreachable: "inaccesible", restarting: "Reiniciando ({reason})", not_loaded: "sin cargar", opening: "Abriendo {panel}…", loadingHint: "Suele tardar unos segundos", empty: "Aún no hay paneles vinculados.", failed: "No se pudo abrir el panel. Se volverá a intentar en breve.", admin: "Un administrador debe abrir esta página.", unreachableBody: "Home Assistant no puede acceder a este panel en este momento.", notLoadedBody: "Este panel no está cargado en Home Assistant.", closed: "Este panel se cerró.", frameTitle: "Interfaz del panel", picklesStory: "Pickles el panda se ha escapado y está causando estragos. Es lento y testarudo, así que traerlo de vuelta puede llevar un momento.", unreachableNext: "Comprueba que el panel esté encendido y conectado a tu red.", notLoadedNext: "Abre Configuración de la integración para comprobar la conexión de este panel.", failedNext: "Espera un momento mientras volvemos a intentarlo, o elige otro panel.", closedNext: "Elige otro panel o espera a que este panel vuelva a conectarse." }, Ze = { title: "Instalar ha-paneld en un panel", introduction: "Conecta el panel a este ordenador con un cable USB. Una nueva ventana lo encontrará e instalará la aplicación.", release: "Versión", loading: "Cargando versiones…", catalogError: "No se pudo cargar la lista de versiones.", empty: "Aún no hay versiones disponibles. Vuelve a intentarlo más tarde.", choose: "Seguir el canal de Panel Assistant (recomendado)", testing: "versión de prueba", devBuild: "compilación de desarrollo", retry: "Volver a intentar", start: "Continuar", cancel: "Cancelar", ready: "", unavailable: "El instalador no está disponible. Actualiza Panel Assistant y vuelve a intentarlo.", admin: "Pide a un administrador de Home Assistant que instale los paneles.", waiting: "Continúa en la nueva ventana.", preparing: "Preparando la aplicación…", downloading: "Preparando la aplicación…", verifying: "Preparando la aplicación…", verified: "Continúa en la nueva ventana.", cancelled: "Cancelado.", popup_blocked: "Tu navegador bloqueó la nueva ventana. Permite las ventanas emergentes para esta página y pulsa Continuar.", invalid_request: "Elige primero una versión.", failed: "No ha funcionado. Pulsa Continuar para volver a intentarlo." }, _e = { title: "Configura tu panel", preparing: "Preparando la aplicación…", preparingSlow: "La aplicación aún se está preparando. Mantén abierta la pestaña de Home Assistant.", connectHeading: "Conecta tu panel", connectBody: "Conecta el panel a este ordenador con un cable USB y pulsa Buscar mi panel.", connect: "Buscar mi panel", allowHeading: "Autoriza este ordenador", allowBody: "Mira la pantalla del panel y toca Permitir.", checkingPanel: "Comprobando tu panel…", confirmHeading: "Listo para instalar", confirmBody: "Esto instala la aplicación y la convierte en la pantalla de inicio de tu panel. Las demás aplicaciones no se modifican.", install: "Instalar", restartedDifferentVersion: "Un intento anterior de instalación usó una versión diferente. Ese intento se ha dejado de lado.", alreadyInstalledHeading: "Ya instalada", alreadyInstalledBody: "Esta versión ya está en tu panel. Continúa para terminar la configuración; no se copia ni se reinstala nada.", continueSetup: "Continuar", progressHeading: "Instalando", stepCopying: "Copiando la aplicación a tu panel…", stepFinishingCopy: "Terminando la copia…", stepInstalling: "Instalando…", stepStarting: "Iniciando la aplicación…", stepPermissions: "Dando a la aplicación lo que necesita para funcionar…", stepOpening: "Abriendo la configuración de tu panel…", keepConnected: "Mantén el cable conectado hasta que termine.", doneHeading: "Instalada", doneOpening: "Te estamos llevando a la configuración de tu panel…", doneManual: "Termina la configuración en la pantalla del panel.", openSetup: "Abrir la configuración del panel", errorHeading: "No ha funcionado", tryAgain: "Volver a intentar", backToHa: "Volver a Home Assistant", details: "Detalles para soporte", unsupported: "Este navegador no puede comunicarse con dispositivos USB. Abre esta página en Chrome o Edge en un ordenador.", handoffFailure: "No se pudo obtener la aplicación de Home Assistant. Vuelve a Home Assistant y empieza de nuevo.", noSelection: "No se eligió ningún panel. Pulsa Buscar mi panel y selecciónalo en la lista, o prueba con otro cable: algunos cables alimentan el panel, pero no transmiten datos.", connectionTimeout: "El panel no respondió. Comprueba que el cable esté bien conectado o cámbialo: un cable puede alimentar el panel sin transmitir datos.", disconnected: "El panel se desconectó. Vuelve a conectarlo, espera a que inicie y pulsa Volver a intentar.", cancelled: "Cancelado.", pageClosed: "La página se cerró.", progress: "Progreso", installProgress: "Progreso de la instalación", licenses: "Licencias de terceros" }, We = { installErrorGeneric: "Algo salió mal. Mantén el panel conectado y pulsa Volver a intentar.", installErrorBusy: "El panel está ocupado con otra instalación. Espera un minuto y vuelve a intentarlo.", installErrorStorage: "Este navegador no pudo guardar el progreso. Permite que este sitio almacene datos y vuelve a intentarlo.", installErrorTarget: "Se conectó un panel diferente. Conecta el mismo panel y vuelve a intentarlo.", installErrorArtifact: "No se pudo comprobar la descarga de la aplicación. Vuelve a Home Assistant y empieza de nuevo.", installErrorNotClean: "Este panel ya tiene la aplicación. Actualízala desde Home Assistant.", installErrorIncompatible: "Este panel no puede ejecutar esta versión de la aplicación.", installErrorConnection: "Se perdió la conexión con el panel. Mantenlo conectado y pulsa Volver a intentar.", installErrorHealth: "La aplicación está instalada, pero aún no ha iniciado. Espera un momento y vuelve a intentarlo." }, Ye = { version: "Versión", connect: "Conectar", install: "Instalar", setup: "Configurar" }, Ke = { update: "actualización", settings: "configuración", recovery: "recuperación", reboot: "reinicio del sistema" }, Je = { stopped: "Detenido", cause: "Causa", error: "Error", release: "Versión", panel: "Panel", copied: "Copiado", savedProgress: "Progreso guardado", earlierCopy: "Copia anterior", stagedCopy: "Copia preparada", setAside: "Dejado de lado", alreadyInstalled: "Ya instalada", permissions: "Permisos", setupAddress: "Dirección de configuración", setupHandover: "Transferencia a la configuración", copiedDetail: "{bytes} bytes enviados; esperando la confirmación del panel", none: "ninguno", setupMissing: "no encontrada; la configuración continúa en el panel" }, Xe = {
  sidebar: Fe,
  haInstall: Ze,
  installer: _e,
  errors: We,
  journey: Ye,
  sidebarReason: Ke,
  support: Je
}, $e = { title: "Panel Assistant", versionLabel: "{version}, build {build}", menu: "Ouvrir la navigation", choosePanel: "Panneau", more: "Plus d’options", addPanel: "Ajouter un panneau", integrationSettings: "Paramètres de l’intégration", device: "Appareil Home Assistant de ce panneau", showDevice: "Afficher l’appareil", github: "GitHub", unreachable: "injoignable", restarting: "Redémarrage ({reason})", not_loaded: "non chargé", opening: "Ouverture de {panel}…", loadingHint: "Prend généralement quelques secondes", empty: "Aucun panneau n’est encore rattaché.", failed: "Impossible d’ouvrir le panneau. Une nouvelle tentative aura lieu sous peu.", admin: "Un administrateur doit ouvrir cette page.", unreachableBody: "Home Assistant ne peut pas joindre ce panneau pour le moment.", notLoadedBody: "Ce panneau n’est pas chargé dans Home Assistant.", closed: "Ce panneau a été fermé.", frameTitle: "Interface du panneau", picklesStory: "Pickles le panda s’est échappé et sème la pagaille. Il est lent et têtu, il faudra donc peut-être un moment pour le ramener.", unreachableNext: "Vérifiez que le panneau est allumé et connecté à votre réseau.", notLoadedNext: "Ouvrez les paramètres de l’intégration pour vérifier la connexion de ce panneau.", failedNext: "Patientez pendant la nouvelle tentative, ou choisissez un autre panneau.", closedNext: "Choisissez un autre panneau, ou attendez que celui-ci se reconnecte." }, en = { title: "Installer ha-paneld sur un panneau", introduction: "Branchez le panneau à cet ordinateur avec un câble USB. Une nouvelle fenêtre le trouvera et installera l’application.", release: "Version", loading: "Chargement des versions…", catalogError: "Impossible de charger la liste des versions.", empty: "Aucune version n’est encore disponible. Réessayez plus tard.", choose: "Suivre le canal de Panel Assistant (recommandé)", testing: "version de test", devBuild: "build de développement", retry: "Réessayer", start: "Continuer", cancel: "Annuler", ready: "", unavailable: "Le programme d’installation n’est pas disponible. Mettez à jour Panel Assistant, puis réessayez.", admin: "Demandez à un administrateur Home Assistant d’installer les panneaux.", waiting: "Continuez dans la nouvelle fenêtre.", preparing: "Préparation de l’application…", downloading: "Préparation de l’application…", verifying: "Préparation de l’application…", verified: "Continuez dans la nouvelle fenêtre.", cancelled: "Annulé.", popup_blocked: "Votre navigateur a bloqué la nouvelle fenêtre. Autorisez les fenêtres contextuelles pour cette page, puis appuyez sur « Continuer ».", invalid_request: "Choisissez d’abord une version.", failed: "Cela n’a pas fonctionné. Appuyez sur « Continuer » pour réessayer." }, nn = { title: "Configurer votre panneau", preparing: "Préparation de l’application…", preparingSlow: "La préparation de l’application se poursuit. Gardez l’onglet Home Assistant ouvert.", connectHeading: "Connecter votre panneau", connectBody: "Branchez le panneau à cet ordinateur avec un câble USB, puis appuyez sur « Trouver mon panneau ».", connect: "Trouver mon panneau", allowHeading: "Autoriser cet ordinateur", allowBody: "Regardez l’écran du panneau et appuyez sur « Autoriser ».", checkingPanel: "Vérification de votre panneau…", confirmHeading: "Prêt à installer", confirmBody: "L’application sera installée et deviendra l’écran d’accueil de votre panneau. Vos autres applications ne seront pas modifiées.", install: "Installer", restartedDifferentVersion: "Une tentative d’installation précédente utilisait une autre version. Cette tentative a été mise de côté.", alreadyInstalledHeading: "Déjà installée", alreadyInstalledBody: "Cette version est déjà sur votre panneau. Continuez pour terminer sa configuration ; rien ne sera copié ni réinstallé.", continueSetup: "Continuer", progressHeading: "Installation en cours", stepCopying: "Copie de l’application sur votre panneau…", stepFinishingCopy: "Fin de la copie…", stepInstalling: "Installation…", stepStarting: "Démarrage de l’application…", stepPermissions: "L’application reçoit ce qu’il lui faut pour fonctionner…", stepOpening: "Ouverture de la configuration de votre panneau…", keepConnected: "Gardez le câble branché jusqu’à la fin.", doneHeading: "Installée", doneOpening: "Ouverture de la configuration de votre panneau…", doneManual: "Terminez la configuration sur l’écran du panneau.", openSetup: "Ouvrir la configuration du panneau", errorHeading: "Cela n’a pas fonctionné", tryAgain: "Réessayer", backToHa: "Retour à Home Assistant", details: "Détails pour l’assistance", unsupported: "Ce navigateur ne peut pas communiquer avec les appareils USB. Ouvrez cette page dans Chrome ou Edge sur un ordinateur.", handoffFailure: "Impossible de récupérer l’application depuis Home Assistant. Retournez dans Home Assistant et recommencez.", noSelection: "Aucun panneau n’a été choisi. Appuyez sur « Trouver mon panneau » et sélectionnez-le dans la liste, ou essayez un autre câble : certains alimentent le panneau sans transmettre de données.", connectionTimeout: "Le panneau n’a pas répondu. Vérifiez que le câble est bien branché, ou changez-le : un câble peut alimenter le panneau sans transmettre de données.", disconnected: "Le panneau a été déconnecté. Rebranchez-le, attendez qu’il démarre, puis appuyez sur « Réessayer ».", cancelled: "Annulé.", pageClosed: "La page a été fermée.", progress: "Progression", installProgress: "Progression de l’installation", licenses: "Licences tierces" }, an = { installErrorGeneric: "Une erreur s’est produite. Gardez le panneau branché et appuyez sur « Réessayer ».", installErrorBusy: "Le panneau est occupé par une autre installation. Attendez une minute, puis réessayez.", installErrorStorage: "Ce navigateur n’a pas pu enregistrer sa progression. Autorisez ce site à stocker des données, puis réessayez.", installErrorTarget: "Un autre panneau a été connecté. Branchez le même panneau et réessayez.", installErrorArtifact: "Le téléchargement de l’application n’a pas pu être vérifié. Retournez dans Home Assistant et recommencez.", installErrorNotClean: "L’application est déjà sur ce panneau. Mettez-la plutôt à jour depuis Home Assistant.", installErrorIncompatible: "Ce panneau ne peut pas exécuter cette version de l’application.", installErrorConnection: "La connexion au panneau a été interrompue. Gardez-le branché et appuyez sur « Réessayer ».", installErrorHealth: "L’application est installée mais n’a pas encore démarré. Patientez un moment, puis réessayez." }, tn = { version: "Version", connect: "Connecter", install: "Installer", setup: "Configurer" }, on = { update: "mise à jour", settings: "paramètres", recovery: "récupération", reboot: "redémarrage du système" }, sn = { stopped: "Arrêté", cause: "Cause", error: "Erreur", release: "Version", panel: "Panneau", copied: "Copié", savedProgress: "Progression enregistrée", earlierCopy: "Copie précédente", stagedCopy: "Copie préparée", setAside: "Mis de côté", alreadyInstalled: "Déjà installée", permissions: "Autorisations", setupAddress: "Adresse de configuration", setupHandover: "Passage à la configuration", copiedDetail: "{bytes} octets envoyés ; en attente de confirmation du panneau", none: "aucun", setupMissing: "introuvable ; la configuration continue sur le panneau" }, rn = {
  sidebar: $e,
  haInstall: en,
  installer: nn,
  errors: an,
  journey: tn,
  sidebarReason: on,
  support: sn
}, ln = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Apri la navigazione", choosePanel: "Pannello", more: "Altre opzioni", addPanel: "Aggiungi pannello", integrationSettings: "Impostazioni dell’integrazione", device: "Dispositivo Home Assistant di questo pannello", showDevice: "Mostra dispositivo", github: "GitHub", unreachable: "non raggiungibile", restarting: "Riavvio in corso ({reason})", not_loaded: "non caricato", opening: "Apertura di {panel}…", loadingHint: "Di solito bastano pochi secondi", empty: "Non è ancora collegato alcun pannello.", failed: "Non è stato possibile aprire il pannello. Riproveremo tra poco.", admin: "Questa pagina deve essere aperta da un amministratore.", unreachableBody: "Home Assistant non riesce a raggiungere questo pannello al momento.", notLoadedBody: "Questo pannello non è caricato in Home Assistant.", closed: "Questo pannello è stato chiuso.", frameTitle: "Interfaccia del pannello", picklesStory: "Pickles il panda è scappato e sta creando scompiglio. È lento e testardo, quindi potrebbe volerci un momento per riportarlo indietro.", unreachableNext: "Controlla che il pannello sia acceso e connesso alla tua rete.", notLoadedNext: "Apri Impostazioni dell’integrazione per controllare la connessione di questo pannello.", failedNext: "Attendi un momento mentre riproviamo, oppure scegli un altro pannello.", closedNext: "Scegli un altro pannello, oppure attendi che questo pannello si riconnetta." }, dn = { title: "Installa ha-paneld su un pannello", introduction: "Collega il pannello a questo computer con un cavo USB. Una nuova finestra lo troverà e installerà l’app.", release: "Versione", loading: "Caricamento delle versioni…", catalogError: "Non è stato possibile caricare l’elenco delle versioni.", empty: "Non ci sono ancora versioni disponibili. Riprova più tardi.", choose: "Segui il canale di Panel Assistant (consigliato)", testing: "versione di prova", devBuild: "build di sviluppo", retry: "Riprova", start: "Continua", cancel: "Annulla", ready: "", unavailable: "Il programma di installazione non è disponibile. Aggiorna Panel Assistant, poi riprova.", admin: "Chiedi a un amministratore di Home Assistant di installare i pannelli.", waiting: "Continua nella nuova finestra.", preparing: "Preparazione dell’app…", downloading: "Preparazione dell’app…", verifying: "Preparazione dell’app…", verified: "Continua nella nuova finestra.", cancelled: "Annullato.", popup_blocked: "Il browser ha bloccato la nuova finestra. Consenti i popup per questa pagina, poi premi Continua.", invalid_request: "Scegli prima una versione.", failed: "L’operazione non è riuscita. Premi Continua per riprovare." }, cn = { title: "Configura il tuo pannello", preparing: "Preparazione dell’app…", preparingSlow: "La preparazione dell’app è ancora in corso. Tieni aperta la scheda di Home Assistant.", connectHeading: "Collega il tuo pannello", connectBody: "Collega il pannello a questo computer con un cavo USB, poi premi Trova il mio pannello.", connect: "Trova il mio pannello", allowHeading: "Autorizza questo computer", allowBody: "Guarda lo schermo del pannello e tocca Consenti.", checkingPanel: "Verifica del pannello…", confirmHeading: "Pronto per l’installazione", confirmBody: "Questa operazione installa l’app e la imposta come schermata iniziale del pannello. Le altre app non vengono modificate.", install: "Installa", restartedDifferentVersion: "Un precedente tentativo di installazione usava una versione diversa. Quel tentativo è stato accantonato.", alreadyInstalledHeading: "Già installata", alreadyInstalledBody: "Questa versione è già sul pannello. Continua per completare la configurazione; non viene copiato o reinstallato nulla.", continueSetup: "Continua", progressHeading: "Installazione in corso", stepCopying: "Copia dell’app sul pannello…", stepFinishingCopy: "Completamento della copia…", stepInstalling: "Installazione…", stepStarting: "Avvio dell’app…", stepPermissions: "Assegnazione all’app di ciò che le serve per funzionare…", stepOpening: "Apertura della configurazione del pannello…", keepConnected: "Lascia il cavo collegato fino al termine dell’operazione.", doneHeading: "Installata", doneOpening: "Apertura della configurazione del pannello…", doneManual: "Completa la configurazione sullo schermo del pannello.", openSetup: "Apri la configurazione del pannello", errorHeading: "L’operazione non è riuscita", tryAgain: "Riprova", backToHa: "Torna a Home Assistant", details: "Dettagli per l’assistenza", unsupported: "Questo browser non può comunicare con dispositivi USB. Apri questa pagina in Chrome o Edge su un computer.", handoffFailure: "Non è stato possibile recuperare l’app da Home Assistant. Torna a Home Assistant e ricomincia.", noSelection: "Non è stato scelto alcun pannello. Premi Trova il mio pannello e sceglilo dall’elenco, oppure prova un altro cavo: alcuni cavi alimentano il pannello ma non trasmettono dati.", connectionTimeout: "Il pannello non ha risposto. Controlla che il cavo sia ben inserito, oppure cambialo: un cavo può alimentare il pannello senza trasmettere dati.", disconnected: "Il pannello è stato scollegato. Ricollegalo, attendi che si avvii, poi premi Riprova.", cancelled: "Annullato.", pageClosed: "La pagina è stata chiusa.", progress: "Avanzamento", installProgress: "Avanzamento dell’installazione", licenses: "Licenze di terze parti" }, pn = { installErrorGeneric: "Si è verificato un problema. Lascia il pannello collegato e premi Riprova.", installErrorBusy: "Il pannello è impegnato in un’altra installazione. Attendi un minuto, poi riprova.", installErrorStorage: "Questo browser non è riuscito a salvare l’avanzamento. Consenti a questo sito di memorizzare dati, poi riprova.", installErrorTarget: "È stato collegato un pannello diverso. Collega lo stesso pannello e riprova.", installErrorArtifact: "Non è stato possibile verificare il download dell’app. Torna a Home Assistant e ricomincia.", installErrorNotClean: "L’app è già presente su questo pannello. Aggiornala da Home Assistant.", installErrorIncompatible: "Questo pannello non può eseguire questa versione dell’app.", installErrorConnection: "La connessione al pannello si è interrotta. Lascialo collegato e premi Riprova.", installErrorHealth: "L’app è installata ma non si è ancora avviata. Attendi un momento, poi riprova." }, un = { version: "Versione", connect: "Collega", install: "Installa", setup: "Configura" }, gn = { update: "aggiornamento", settings: "impostazioni", recovery: "ripristino", reboot: "riavvio di sistema" }, hn = { stopped: "Interrotto", cause: "Causa", error: "Errore", release: "Versione pubblicata", panel: "Pannello", copied: "Copiato", savedProgress: "Avanzamento salvato", earlierCopy: "Copia precedente", stagedCopy: "Copia preparata", setAside: "Accantonato", alreadyInstalled: "Già installata", permissions: "Autorizzazioni", setupAddress: "Indirizzo di configurazione", setupHandover: "Passaggio alla configurazione", copiedDetail: "{bytes} byte inviati; in attesa della conferma del pannello", none: "nessuno", setupMissing: "non trovato; la configurazione prosegue sul pannello" }, mn = {
  sidebar: ln,
  haInstall: dn,
  installer: cn,
  errors: pn,
  journey: un,
  sidebarReason: gn,
  support: hn
}, bn = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Navigatie openen", choosePanel: "Paneel", more: "Meer opties", addPanel: "Paneel toevoegen", integrationSettings: "Integratie-instellingen", device: "Het Home Assistant-apparaat van dit paneel", showDevice: "Apparaat tonen", github: "GitHub", unreachable: "onbereikbaar", restarting: "Opnieuw starten ({reason})", not_loaded: "niet geladen", opening: "{panel} openen…", loadingHint: "Duurt meestal enkele seconden", empty: "Er zijn nog geen panelen gekoppeld.", failed: "Het paneel kon niet worden geopend. Er wordt binnenkort opnieuw geprobeerd.", admin: "Een beheerder moet deze pagina openen.", unreachableBody: "Home Assistant kan dit paneel momenteel niet bereiken.", notLoadedBody: "Dit paneel is niet geladen in Home Assistant.", closed: "Dit paneel is gesloten.", frameTitle: "Paneelinterface", picklesStory: "Pickles de panda is ontsnapt en richt een ravage aan. Hij is traag en koppig, dus het kan even duren om hem terug te krijgen.", unreachableNext: "Controleer of het paneel aanstaat en is verbonden met je netwerk.", notLoadedNext: "Open Integratie-instellingen om de verbinding van dit paneel te controleren.", failedNext: "Wacht even terwijl we het opnieuw proberen, of kies een ander paneel.", closedNext: "Kies een ander paneel of wacht tot dit paneel opnieuw verbinding maakt." }, vn = { title: "ha-paneld op een paneel installeren", introduction: "Sluit het paneel met een USB-kabel aan op deze computer. Een nieuw venster zoekt het paneel en installeert de app.", release: "Versie", loading: "Versies laden…", catalogError: "De lijst met versies kon niet worden geladen.", empty: "Er zijn nog geen versies beschikbaar. Probeer het later opnieuw.", choose: "Kanaal van Panel Assistant volgen (aanbevolen)", testing: "testversie", devBuild: "ontwikkelbuild", retry: "Opnieuw proberen", start: "Doorgaan", cancel: "Annuleren", ready: "", unavailable: "Het installatieprogramma is niet beschikbaar. Werk Panel Assistant bij en probeer het opnieuw.", admin: "Vraag een Home Assistant-beheerder om panelen te installeren.", waiting: "Ga verder in het nieuwe venster.", preparing: "App voorbereiden…", downloading: "App voorbereiden…", verifying: "App voorbereiden…", verified: "Ga verder in het nieuwe venster.", cancelled: "Geannuleerd.", popup_blocked: "De browser heeft het nieuwe venster geblokkeerd. Sta pop-ups toe voor deze pagina en druk op Doorgaan.", invalid_request: "Kies eerst een versie.", failed: "Dat is niet gelukt. Druk op Doorgaan om het opnieuw te proberen." }, yn = { title: "Je paneel instellen", preparing: "App voorbereiden…", preparingSlow: "De app wordt nog voorbereid. Houd het Home Assistant-tabblad open.", connectHeading: "Je paneel verbinden", connectBody: "Sluit het paneel met een USB-kabel aan op deze computer en druk op Mijn paneel zoeken.", connect: "Mijn paneel zoeken", allowHeading: "Deze computer toestaan", allowBody: "Kijk op het scherm van het paneel en tik op Toestaan.", checkingPanel: "Je paneel controleren…", confirmHeading: "Klaar om te installeren", confirmBody: "Hiermee installeer je de app en maak je die het startscherm van je paneel. Je andere apps worden niet aangeraakt.", install: "Installeren", restartedDifferentVersion: "Een eerdere installatiepoging gebruikte een andere uitgave. Die poging is terzijde gelegd.", alreadyInstalledHeading: "Al geïnstalleerd", alreadyInstalledBody: "Deze versie staat al op je paneel. Ga door om het instellen af te ronden; er wordt niets gekopieerd of opnieuw geïnstalleerd.", continueSetup: "Doorgaan", progressHeading: "Installeren", stepCopying: "App naar je paneel kopiëren…", stepFinishingCopy: "Kopiëren afronden…", stepInstalling: "Installeren…", stepStarting: "App starten…", stepPermissions: "De app geven wat die nodig heeft om te werken…", stepOpening: "Paneelconfiguratie openen…", keepConnected: "Laat de kabel aangesloten totdat dit klaar is.", doneHeading: "Geïnstalleerd", doneOpening: "Je wordt naar de paneelconfiguratie geleid…", doneManual: "Rond het instellen af op het scherm van het paneel.", openSetup: "Paneelconfiguratie openen", errorHeading: "Dat is niet gelukt", tryAgain: "Opnieuw proberen", backToHa: "Terug naar Home Assistant", details: "Details voor ondersteuning", unsupported: "Deze browser kan niet communiceren met USB-apparaten. Open deze pagina in Chrome of Edge op een computer.", handoffFailure: "De app kon niet worden opgehaald uit Home Assistant. Ga terug naar Home Assistant en begin opnieuw.", noSelection: "Er is geen paneel gekozen. Druk op Mijn paneel zoeken en kies het in de lijst, of probeer een andere kabel: sommige kabels voeden het paneel zonder gegevens over te dragen.", connectionTimeout: "Het paneel antwoordde niet. Controleer of de kabel goed vastzit, of vervang die: een kabel kan het paneel voeden zonder gegevens over te dragen.", disconnected: "Het paneel is losgekoppeld. Sluit het weer aan, wacht tot het start en druk op Opnieuw proberen.", cancelled: "Geannuleerd.", pageClosed: "De pagina is gesloten.", progress: "Voortgang", installProgress: "Installatievoortgang", licenses: "Licenties van derden" }, fn = { installErrorGeneric: "Er ging iets mis. Laat het paneel aangesloten en druk op Opnieuw proberen.", installErrorBusy: "Het paneel is bezig met een andere installatie. Wacht een minuut en probeer het opnieuw.", installErrorStorage: "Deze browser kon de voortgang niet opslaan. Sta toe dat deze site gegevens opslaat en probeer het opnieuw.", installErrorTarget: "Er is een ander paneel aangesloten. Sluit hetzelfde paneel aan en probeer het opnieuw.", installErrorArtifact: "De app-download kon niet worden gecontroleerd. Ga terug naar Home Assistant en begin opnieuw.", installErrorNotClean: "De app staat al op dit paneel. Werk de app in plaats daarvan bij vanuit Home Assistant.", installErrorIncompatible: "Dit paneel kan deze versie van de app niet uitvoeren.", installErrorConnection: "De verbinding met het paneel is verbroken. Laat het aangesloten en druk op Opnieuw proberen.", installErrorHealth: "De app is geïnstalleerd, maar nog niet gestart. Wacht even en probeer het opnieuw." }, An = { version: "Versie", connect: "Verbinden", install: "Installeren", setup: "Instellen" }, wn = { update: "update", settings: "instellingen", recovery: "herstel", reboot: "herstart" }, kn = { stopped: "Gestopt", cause: "Oorzaak", error: "Fout", release: "Uitgave", panel: "Paneel", copied: "Gekopieerd", savedProgress: "Opgeslagen voortgang", earlierCopy: "Eerdere kopie", stagedCopy: "Klaargezette kopie", setAside: "Terzijde gelegd", alreadyInstalled: "Al geïnstalleerd", permissions: "Machtigingen", setupAddress: "Configuratieadres", setupHandover: "Configuratieoverdracht", copiedDetail: "{bytes} bytes verstuurd; wachten op bevestiging van het paneel", none: "geen", setupMissing: "niet gevonden; instellen gaat verder op het paneel" }, zn = {
  sidebar: bn,
  haInstall: vn,
  installer: yn,
  errors: fn,
  journey: An,
  sidebarReason: wn,
  support: kn
}, In = { title: "Panel Assistant", versionLabel: "{version} kompilacja {build}", menu: "Otwórz nawigację", choosePanel: "Panel", more: "Więcej opcji", addPanel: "Dodaj panel", integrationSettings: "Ustawienia integracji", device: "Urządzenie tego panelu w Home Assistant", showDevice: "Pokaż urządzenie", github: "GitHub", unreachable: "nieosiągalny", restarting: "Ponowne uruchamianie ({reason})", not_loaded: "niewczytany", opening: "Otwieranie {panel}…", loadingHint: "Zwykle zajmuje to kilka sekund", empty: "Nie podłączono jeszcze żadnych paneli.", failed: "Nie udało się otworzyć panelu. Wkrótce nastąpi kolejna próba.", admin: "Tę stronę musi otworzyć administrator.", unreachableBody: "Home Assistant nie może teraz połączyć się z tym panelem.", notLoadedBody: "Ten panel nie jest wczytany w Home Assistant.", closed: "Ten panel został zamknięty.", frameTitle: "Interfejs panelu", picklesStory: "Panda Pickles uciekł i rozrabia. Jest powolny i uparty, więc sprowadzenie go z powrotem może chwilę potrwać.", unreachableNext: "Sprawdź, czy panel jest włączony i połączony z Twoją siecią.", notLoadedNext: "Otwórz Ustawienia integracji, aby sprawdzić połączenie tego panelu.", failedNext: "Poczekaj chwilę, aż spróbujemy ponownie, lub wybierz inny panel.", closedNext: "Wybierz inny panel lub poczekaj, aż ten panel połączy się ponownie." }, jn = { title: "Zainstaluj ha-paneld na panelu", introduction: "Podłącz panel do tego komputera kablem USB. Nowe okno znajdzie panel i zainstaluje aplikację.", release: "Wersja", loading: "Wczytywanie wersji…", catalogError: "Nie udało się wczytać listy wersji.", empty: "Nie ma jeszcze dostępnych wersji. Spróbuj ponownie później.", choose: "Używaj kanału Panel Assistant (zalecane)", testing: "wersja testowa", devBuild: "kompilacja deweloperska", retry: "Spróbuj ponownie", start: "Kontynuuj", cancel: "Anuluj", ready: "", unavailable: "Instalator jest niedostępny. Zaktualizuj Panel Assistant, a potem spróbuj ponownie.", admin: "Poproś administratora Home Assistant o zainstalowanie paneli.", waiting: "Kontynuuj w nowym oknie.", preparing: "Przygotowywanie aplikacji…", downloading: "Przygotowywanie aplikacji…", verifying: "Przygotowywanie aplikacji…", verified: "Kontynuuj w nowym oknie.", cancelled: "Anulowano.", popup_blocked: "Przeglądarka zablokowała nowe okno. Zezwól na wyskakujące okna dla tej strony, a potem naciśnij Kontynuuj.", invalid_request: "Najpierw wybierz wersję.", failed: "Nie udało się. Naciśnij Kontynuuj, aby spróbować ponownie." }, Cn = { title: "Skonfiguruj swój panel", preparing: "Przygotowywanie aplikacji…", preparingSlow: "Aplikacja nadal jest przygotowywana. Zostaw otwartą kartę Home Assistant.", connectHeading: "Podłącz swój panel", connectBody: "Podłącz panel do tego komputera kablem USB, a potem naciśnij Znajdź mój panel.", connect: "Znajdź mój panel", allowHeading: "Zezwól temu komputerowi", allowBody: "Spójrz na ekran panelu i dotknij Zezwól.", checkingPanel: "Sprawdzanie panelu…", confirmHeading: "Gotowe do instalacji", confirmBody: "To zainstaluje aplikację i ustawi ją jako ekran główny panelu. Pozostałe aplikacje pozostaną nietknięte.", install: "Zainstaluj", restartedDifferentVersion: "Wcześniejsza próba instalacji używała innego wydania. Ta próba została odłożona na bok.", alreadyInstalledHeading: "Już zainstalowano", alreadyInstalledBody: "Ta wersja jest już na Twoim panelu. Kontynuuj, aby dokończyć konfigurację; nic nie zostanie skopiowane ani zainstalowane ponownie.", continueSetup: "Kontynuuj", progressHeading: "Instalowanie", stepCopying: "Kopiowanie aplikacji na panel…", stepFinishingCopy: "Kończenie kopiowania…", stepInstalling: "Instalowanie…", stepStarting: "Uruchamianie aplikacji…", stepPermissions: "Zapewnianie aplikacji warunków do działania…", stepOpening: "Otwieranie konfiguracji panelu…", keepConnected: "Zostaw kabel podłączony do zakończenia.", doneHeading: "Zainstalowano", doneOpening: "Przechodzenie do konfiguracji panelu…", doneManual: "Dokończ konfigurację na ekranie panelu.", openSetup: "Otwórz konfigurację panelu", errorHeading: "Nie udało się", tryAgain: "Spróbuj ponownie", backToHa: "Wróć do Home Assistant", details: "Szczegóły dla pomocy technicznej", unsupported: "Ta przeglądarka nie może komunikować się z urządzeniami USB. Otwórz tę stronę w Chrome lub Edge na komputerze.", handoffFailure: "Nie udało się pobrać aplikacji z Home Assistant. Wróć do Home Assistant i zacznij od nowa.", noSelection: "Nie wybrano panelu. Naciśnij Znajdź mój panel i wybierz go z listy lub spróbuj innego kabla: niektóre kable zasilają panel, ale nie przesyłają danych.", connectionTimeout: "Panel nie odpowiedział. Sprawdź, czy kabel jest dobrze wpięty, lub wymień go: kabel może zasilać panel, a mimo to nie przesyłać danych.", disconnected: "Panel został odłączony. Podłącz go ponownie, poczekaj na jego uruchomienie, a potem naciśnij Spróbuj ponownie.", cancelled: "Anulowano.", pageClosed: "Strona została zamknięta.", progress: "Postęp", installProgress: "Postęp instalacji", licenses: "Licencje innych podmiotów" }, En = { installErrorGeneric: "Coś poszło nie tak. Zostaw panel podłączony i naciśnij Spróbuj ponownie.", installErrorBusy: "Panel jest zajęty inną instalacją. Poczekaj minutę, a potem spróbuj ponownie.", installErrorStorage: "Przeglądarka nie mogła zapisać postępu. Zezwól tej stronie na zapisywanie danych, a potem spróbuj ponownie.", installErrorTarget: "Podłączono inny panel. Podłącz ten sam panel co wcześniej i spróbuj ponownie.", installErrorArtifact: "Nie udało się sprawdzić pobranej aplikacji. Wróć do Home Assistant i zacznij od nowa.", installErrorNotClean: "Ten panel ma już aplikację. Zamiast instalować, zaktualizuj ją z Home Assistant.", installErrorIncompatible: "Ten panel nie może uruchomić tej wersji aplikacji.", installErrorConnection: "Połączenie z panelem zostało przerwane. Zostaw go podłączonego i naciśnij Spróbuj ponownie.", installErrorHealth: "Aplikacja jest zainstalowana, ale jeszcze się nie uruchomiła. Poczekaj chwilę, a potem spróbuj ponownie." }, xn = { version: "Wersja", connect: "Połącz", install: "Zainstaluj", setup: "Skonfiguruj" }, Pn = { update: "aktualizacja", settings: "ustawienia", recovery: "odzyskiwanie", reboot: "restart systemu" }, Mn = { stopped: "Zatrzymano", cause: "Przyczyna", error: "Błąd", release: "Wydanie", panel: "Panel", copied: "Skopiowano", savedProgress: "Zapisany postęp", earlierCopy: "Wcześniejsza kopia", stagedCopy: "Przygotowana kopia", setAside: "Odłożono", alreadyInstalled: "Już zainstalowano", permissions: "Uprawnienia", setupAddress: "Adres konfiguracji", setupHandover: "Przekazanie konfiguracji", copiedDetail: "Wysłano {bytes} bajtów; oczekiwanie na potwierdzenie panelu", none: "brak", setupMissing: "nie znaleziono; konfiguracja trwa dalej na panelu" }, Sn = {
  sidebar: In,
  haInstall: jn,
  installer: Cn,
  errors: En,
  journey: xn,
  sidebarReason: Pn,
  support: Mn
}, Dn = { title: "Panel Assistant", versionLabel: "{version} compilação {build}", menu: "Abrir navegação", choosePanel: "Painel", more: "Mais opções", addPanel: "Adicionar painel", integrationSettings: "Configurações da integração", device: "Dispositivo deste painel no Home Assistant", showDevice: "Mostrar dispositivo", github: "GitHub", unreachable: "inacessível", restarting: "Reiniciando ({reason})", not_loaded: "não carregado", opening: "Abrindo {panel}…", loadingHint: "Geralmente leva alguns segundos", empty: "Ainda não há painéis vinculados.", failed: "Não foi possível abrir o painel. Uma nova tentativa será feita em breve.", admin: "Um administrador precisa abrir esta página.", unreachableBody: "O Home Assistant não consegue acessar este painel no momento.", notLoadedBody: "Este painel não está carregado no Home Assistant.", closed: "Este painel foi fechado.", frameTitle: "Interface do painel", picklesStory: "Pickles, o panda, escapou e está fazendo uma bagunça. Ele é lento e teimoso, então trazê-lo de volta pode levar um tempinho.", unreachableNext: "Verifique se o painel está ligado e conectado à sua rede.", notLoadedNext: "Abra Configurações da integração para verificar a conexão deste painel.", failedNext: "Aguarde um momento enquanto tentamos novamente ou escolha outro painel.", closedNext: "Escolha outro painel ou aguarde a reconexão deste painel." }, Hn = { title: "Instalar ha-paneld em um painel", introduction: "Conecte o painel a este computador com um cabo USB. Uma nova janela encontrará o painel e instalará o aplicativo.", release: "Versão", loading: "Carregando versões…", catalogError: "Não foi possível carregar a lista de versões.", empty: "Ainda não há versões disponíveis. Tente novamente mais tarde.", choose: "Seguir o canal do Panel Assistant (recomendado)", testing: "versão de teste", devBuild: "compilação de desenvolvimento", retry: "Tentar novamente", start: "Continuar", cancel: "Cancelar", ready: "", unavailable: "O instalador não está disponível. Atualize o Panel Assistant e tente novamente.", admin: "Peça a um administrador do Home Assistant para instalar os painéis.", waiting: "Continue na nova janela.", preparing: "Preparando o aplicativo…", downloading: "Preparando o aplicativo…", verifying: "Preparando o aplicativo…", verified: "Continue na nova janela.", cancelled: "Cancelado.", popup_blocked: "Seu navegador bloqueou a nova janela. Permita pop-ups nesta página e pressione Continuar.", invalid_request: "Escolha uma versão primeiro.", failed: "Não deu certo. Pressione Continuar para tentar novamente." }, Nn = { title: "Configure seu painel", preparing: "Preparando o aplicativo…", preparingSlow: "Ainda estamos preparando o aplicativo. Mantenha a aba do Home Assistant aberta.", connectHeading: "Conecte seu painel", connectBody: "Conecte o painel a este computador com um cabo USB e pressione Encontrar meu painel.", connect: "Encontrar meu painel", allowHeading: "Permita este computador", allowBody: "Olhe a tela do painel e toque em Permitir.", checkingPanel: "Verificando seu painel…", confirmHeading: "Pronto para instalar", confirmBody: "Isso instala o aplicativo e o torna a tela inicial do seu painel. Seus outros aplicativos não são alterados.", install: "Instalar", restartedDifferentVersion: "Uma tentativa anterior de instalação usou uma versão diferente. Essa tentativa foi deixada de lado.", alreadyInstalledHeading: "Já instalado", alreadyInstalledBody: "Esta versão já está no seu painel. Continue para concluir a configuração; nada é copiado ou reinstalado.", continueSetup: "Continuar", progressHeading: "Instalando", stepCopying: "Copiando o aplicativo para seu painel…", stepFinishingCopy: "Concluindo a cópia…", stepInstalling: "Instalando…", stepStarting: "Iniciando o aplicativo…", stepPermissions: "Concedendo ao aplicativo o que ele precisa para funcionar…", stepOpening: "Abrindo a configuração do seu painel…", keepConnected: "Mantenha o cabo conectado até terminar.", doneHeading: "Instalado", doneOpening: "Levando você à configuração do seu painel…", doneManual: "Conclua a configuração na tela do painel.", openSetup: "Abrir configuração do painel", errorHeading: "Não deu certo", tryAgain: "Tentar novamente", backToHa: "Voltar ao Home Assistant", details: "Detalhes para suporte", unsupported: "Este navegador não consegue se comunicar com dispositivos USB. Abra esta página no Chrome ou no Edge em um computador.", handoffFailure: "Não foi possível obter o aplicativo do Home Assistant. Volte ao Home Assistant e recomece.", noSelection: "Nenhum painel foi escolhido. Pressione Encontrar meu painel e selecione-o na lista ou tente outro cabo: alguns cabos alimentam o painel, mas não transmitem dados.", connectionTimeout: "O painel não respondeu. Verifique se o cabo está bem conectado ou troque-o: um cabo pode alimentar o painel e ainda assim não transmitir dados.", disconnected: "O painel foi desconectado. Conecte-o novamente, aguarde a inicialização e pressione Tentar novamente.", cancelled: "Cancelado.", pageClosed: "A página foi fechada.", progress: "Progresso", installProgress: "Progresso da instalação", licenses: "Licenças de terceiros" }, Bn = { installErrorGeneric: "Algo deu errado. Mantenha o painel conectado e pressione Tentar novamente.", installErrorBusy: "O painel está ocupado com outra instalação. Aguarde um minuto e tente novamente.", installErrorStorage: "Este navegador não conseguiu salvar o progresso. Permita que este site armazene dados e tente novamente.", installErrorTarget: "Outro painel foi conectado. Conecte o mesmo painel e tente novamente.", installErrorArtifact: "Não foi possível verificar o download do aplicativo. Volte ao Home Assistant e recomece.", installErrorNotClean: "Este painel já tem o aplicativo. Atualize-o pelo Home Assistant.", installErrorIncompatible: "Este painel não consegue executar esta versão do aplicativo.", installErrorConnection: "A conexão com o painel caiu. Mantenha-o conectado e pressione Tentar novamente.", installErrorHealth: "O aplicativo está instalado, mas ainda não iniciou. Aguarde um momento e tente novamente." }, Ln = { version: "Versão", connect: "Conectar", install: "Instalar", setup: "Configurar" }, Tn = { update: "atualização", settings: "configurações", recovery: "recuperação", reboot: "reinicialização" }, On = { stopped: "Interrompido", cause: "Causa", error: "Erro", release: "Versão", panel: "Painel", copied: "Copiado", savedProgress: "Progresso salvo", earlierCopy: "Cópia anterior", stagedCopy: "Cópia preparada", setAside: "Deixado de lado", alreadyInstalled: "Já instalado", permissions: "Permissões", setupAddress: "Endereço de configuração", setupHandover: "Transferência da configuração", copiedDetail: "{bytes} bytes enviados; aguardando a confirmação do painel", none: "nenhum", setupMissing: "não encontrado; a configuração continua no painel" }, Rn = {
  sidebar: Dn,
  haInstall: Hn,
  installer: Nn,
  errors: Bn,
  journey: Ln,
  sidebarReason: Tn,
  support: On
}, qn = { title: "Panel Assistant", versionLabel: "{version}, збірка {build}", menu: "Відкрити навігацію", choosePanel: "Панель", more: "Інші параметри", addPanel: "Додати панель", integrationSettings: "Налаштування інтеграції", device: "Пристрій цієї панелі в Home Assistant", showDevice: "Показати пристрій", github: "GitHub", unreachable: "Недоступна", restarting: "Перезапускається ({reason})", not_loaded: "Не завантажено", opening: "Відкриття {panel}…", loadingHint: "Зазвичай це займає кілька секунд", empty: "Панелей ще не приєднано.", failed: "Не вдалося відкрити панель. Незабаром спробу буде повторено.", admin: "Цю сторінку має відкрити адміністратор.", unreachableBody: "Home Assistant зараз не може зв’язатися з цією панеллю.", notLoadedBody: "Цю панель не завантажено в Home Assistant.", closed: "Цю панель було закрито.", frameTitle: "Інтерфейс панелі", picklesStory: "Панда Pickles утік і спричиняє безлад. Він повільний та впертий, тому його повернення може зайняти трохи часу.", unreachableNext: "Перевірте, чи панель увімкнена та під’єднана до вашої мережі.", notLoadedNext: "Відкрийте «Налаштування інтеграції», щоб перевірити з’єднання з цією панеллю.", failedNext: "Зачекайте трохи, поки ми повторимо спробу, або виберіть іншу панель.", closedNext: "Виберіть іншу панель або зачекайте, поки ця панель під’єднається знову." }, Qn = { title: "Встановити ha-paneld на панель", introduction: "Під’єднайте панель до цього комп’ютера кабелем USB. Нове вікно знайде її та встановить програму.", release: "Версія", loading: "Завантаження версій…", catalogError: "Не вдалося завантажити список версій.", empty: "Поки що немає доступних версій. Спробуйте ще раз пізніше.", choose: "Дотримуватися каналу Panel Assistant (рекомендовано)", testing: "Тестова версія", devBuild: "Збірка для розробки", retry: "Спробувати ще раз", start: "Продовжити", cancel: "Скасувати", ready: "", unavailable: "Засіб встановлення недоступний. Оновіть Panel Assistant і повторіть спробу.", admin: "Попросіть адміністратора Home Assistant встановити панелі.", waiting: "Продовжуйте в новому вікні.", preparing: "Підготовка програми…", downloading: "Підготовка програми…", verifying: "Підготовка програми…", verified: "Продовжуйте в новому вікні.", cancelled: "Скасовано.", popup_blocked: "Браузер заблокував нове вікно. Дозвольте спливні вікна для цієї сторінки та натисніть «Продовжити».", invalid_request: "Спочатку виберіть версію.", failed: "Не вдалося. Натисніть «Продовжити», щоб спробувати ще раз." }, Gn = { title: "Налаштуйте панель", preparing: "Підготовка програми…", preparingSlow: "Підготовка програми ще триває. Залиште вкладку Home Assistant відкритою.", connectHeading: "Під’єднайте панель", connectBody: "Під’єднайте панель до цього комп’ютера кабелем USB, а потім натисніть «Знайти мою панель».", connect: "Знайти мою панель", allowHeading: "Дозволити цей комп’ютер", allowBody: "Подивіться на екран панелі та торкніться «Дозволити».", checkingPanel: "Перевірка панелі…", confirmHeading: "Готово до встановлення", confirmBody: "Це встановить програму й зробить її головним екраном панелі. Інші ваші програми не зачіпатимуться.", install: "Встановити", restartedDifferentVersion: "Попередня спроба встановлення використовувала інший випуск. Цю спробу відкладено вбік.", alreadyInstalledHeading: "Уже встановлено", alreadyInstalledBody: "Ця версія вже є на панелі. Продовжуйте, щоб завершити її налаштування; нічого не копіюється й не перевстановлюється.", continueSetup: "Продовжити", progressHeading: "Встановлення", stepCopying: "Копіювання програми на панель…", stepFinishingCopy: "Завершення копіювання…", stepInstalling: "Встановлення…", stepStarting: "Запуск програми…", stepPermissions: "Надання програмі всього необхідного для роботи…", stepOpening: "Відкриття налаштування панелі…", keepConnected: "Залиште кабель під’єднаним до завершення.", doneHeading: "Встановлено", doneOpening: "Перехід до налаштування панелі…", doneManual: "Завершіть налаштування на екрані панелі.", openSetup: "Відкрити налаштування панелі", errorHeading: "Не вдалося", tryAgain: "Спробувати ще раз", backToHa: "Назад до Home Assistant", details: "Подробиці для служби підтримки", unsupported: "Цей браузер не може взаємодіяти з пристроями USB. Відкрийте цю сторінку в Chrome або Edge на комп’ютері.", handoffFailure: "Не вдалося отримати програму з Home Assistant. Поверніться до Home Assistant і почніть знову.", noSelection: "Панель не вибрано. Натисніть «Знайти мою панель» і виберіть її зі списку або спробуйте інший кабель: деякі кабелі живлять панель, але не передають даних.", connectionTimeout: "Панель не відповіла. Перевірте, чи кабель щільно під’єднано, або замініть його: кабель може живити панель, але не передавати даних.", disconnected: "Панель було від’єднано. Під’єднайте її знову, дочекайтеся запуску й натисніть «Спробувати ще раз».", cancelled: "Скасовано.", pageClosed: "Сторінку було закрито.", progress: "Перебіг", installProgress: "Перебіг встановлення", licenses: "Ліцензії сторонніх компонентів" }, Un = { installErrorGeneric: "Щось пішло не так. Залиште панель під’єднаною та натисніть «Спробувати ще раз».", installErrorBusy: "На панелі вже триває інше встановлення. Зачекайте хвилину й повторіть спробу.", installErrorStorage: "Браузеру не вдалося зберегти перебіг роботи. Дозвольте цьому сайту зберігати дані й повторіть спробу.", installErrorTarget: "Було під’єднано іншу панель. Під’єднайте ту саму панель і повторіть спробу.", installErrorArtifact: "Не вдалося перевірити завантажену програму. Поверніться до Home Assistant і почніть знову.", installErrorNotClean: "На цій панелі вже є програма. Натомість оновіть її з Home Assistant.", installErrorIncompatible: "Ця панель не може запускати цю версію програми.", installErrorConnection: "З’єднання з панеллю перервалося. Залиште її під’єднаною та натисніть «Спробувати ще раз».", installErrorHealth: "Програму встановлено, але вона ще не запустилася. Зачекайте трохи й повторіть спробу." }, Vn = { version: "Версія", connect: "Під’єднатися", install: "Встановити", setup: "Налаштувати" }, Fn = { update: "Оновлення", settings: "Налаштування", recovery: "Відновлення", reboot: "Перезавантаження" }, Zn = { stopped: "Зупинено", cause: "Причина", error: "Помилка", release: "Випуск", panel: "Панель", copied: "Скопійовано", savedProgress: "Збережений прогрес", earlierCopy: "Попередня копія", stagedCopy: "Підготовлена копія", setAside: "Відкладено вбік", alreadyInstalled: "Уже встановлено", permissions: "Дозволи", setupAddress: "Адреса налаштування", setupHandover: "Передача до налаштування", copiedDetail: "Надіслано {bytes} байт; очікується підтвердження від панелі", none: "Немає", setupMissing: "Не знайдено; налаштування триває на панелі" }, _n = {
  sidebar: qn,
  haInstall: Qn,
  installer: Gn,
  errors: Un,
  journey: Vn,
  sidebarReason: Fn,
  support: Zn
}, Wn = { title: "Panel Assistant", versionLabel: "{version} 构建 {build}", menu: "打开导航", choosePanel: "面板", more: "更多选项", addPanel: "添加面板", integrationSettings: "集成设置", device: "此面板的 Home Assistant 设备", showDevice: "显示设备", github: "GitHub", unreachable: "无法连接", restarting: "正在重启（{reason}）", not_loaded: "未加载", opening: "正在打开 {panel}…", loadingHint: "通常需要几秒钟", empty: "尚未接入任何面板。", failed: "无法打开面板。稍后会再次尝试。", admin: "此页面必须由管理员打开。", unreachableBody: "Home Assistant 目前无法连接到此面板。", notLoadedBody: "此面板尚未在 Home Assistant 中加载。", closed: "此面板已关闭。", frameTitle: "面板界面", picklesStory: "熊猫 Pickles 逃跑了，正在到处捣乱。他动作慢又固执，要把他带回来可能需要一点时间。", unreachableNext: "请检查面板是否已开机并连接到您的网络。", notLoadedNext: "打开集成设置，检查此面板的连接。", failedNext: "请稍候，我们正在重试；您也可以选择其他面板。", closedNext: "请选择其他面板，或等待此面板重新连接。" }, Yn = { title: "在面板上安装 ha-paneld", introduction: "用 USB 数据线将面板连接到此电脑。新窗口会查找面板并安装应用。", release: "版本", loading: "正在加载版本…", catalogError: "无法加载版本列表。", empty: "目前没有可用版本。请稍后重试。", choose: "跟随 Panel Assistant 的发布渠道（推荐）", testing: "测试版本", devBuild: "开发构建", retry: "重试", start: "继续", cancel: "取消", ready: "", unavailable: "安装程序不可用。请更新 Panel Assistant 后重试。", admin: "请让 Home Assistant 管理员安装面板。", waiting: "请在新窗口中继续。", preparing: "正在准备应用…", downloading: "正在准备应用…", verifying: "正在准备应用…", verified: "请在新窗口中继续。", cancelled: "已取消。", popup_blocked: "浏览器阻止了新窗口。请允许此页面弹出窗口，然后点击“继续”。", invalid_request: "请先选择版本。", failed: "未能完成。请点击“继续”重试。" }, Kn = { title: "设置您的面板", preparing: "正在准备应用…", preparingSlow: "仍在准备应用。请保持 Home Assistant 标签页打开。", connectHeading: "连接您的面板", connectBody: "用 USB 数据线将面板连接到此电脑，然后点击“查找我的面板”。", connect: "查找我的面板", allowHeading: "允许此电脑连接", allowBody: "请查看面板屏幕并点击“允许”。", checkingPanel: "正在检查您的面板…", confirmHeading: "可以开始安装", confirmBody: "这会安装应用，并将其设为面板的主屏幕。您的其他应用不会受到影响。", install: "安装", restartedDifferentVersion: "先前的一次安装尝试使用了另一个发行版本。那次尝试已搁置。", alreadyInstalledHeading: "已安装", alreadyInstalledBody: "您的面板上已有此版本。请继续完成设置；不会复制或重新安装任何内容。", continueSetup: "继续", progressHeading: "正在安装", stepCopying: "正在将应用复制到您的面板…", stepFinishingCopy: "正在完成复制…", stepInstalling: "正在安装…", stepStarting: "正在启动应用…", stepPermissions: "正在为应用提供运行所需的条件…", stepOpening: "正在打开面板设置…", keepConnected: "请保持数据线连接，直到完成。", doneHeading: "已安装", doneOpening: "正在带您进入面板设置…", doneManual: "请在面板屏幕上完成设置。", openSetup: "打开面板设置", errorHeading: "未能完成", tryAgain: "重试", backToHa: "返回 Home Assistant", details: "技术支持详细信息", unsupported: "此浏览器无法与 USB 设备通信。请在电脑上使用 Chrome 或 Edge 打开此页面。", handoffFailure: "无法从 Home Assistant 获取应用。请返回 Home Assistant 重新开始。", noSelection: "未选择面板。请点击“查找我的面板”并从列表中选择，或尝试另一根数据线：有些线缆只能给面板供电，无法传输数据。", connectionTimeout: "面板未响应。请检查线缆是否插紧，或更换线缆：有些线缆能给面板供电，却无法传输数据。", disconnected: "面板已断开连接。请重新连接，等待面板启动后点击“重试”。", cancelled: "已取消。", pageClosed: "页面已关闭。", progress: "进度", installProgress: "安装进度", licenses: "第三方许可" }, Jn = { installErrorGeneric: "发生了错误。请保持面板连接，然后点击“重试”。", installErrorBusy: "面板正在进行另一项安装。请等待一分钟后重试。", installErrorStorage: "此浏览器无法保存进度。请允许此网站存储数据，然后重试。", installErrorTarget: "连接了另一个面板。请连接原来的面板后重试。", installErrorArtifact: "无法校验下载的应用。请返回 Home Assistant 重新开始。", installErrorNotClean: "此面板上已有该应用。请通过 Home Assistant 更新。", installErrorIncompatible: "此面板无法运行此版本的应用。", installErrorConnection: "与面板的连接已中断。请保持面板连接，然后点击“重试”。", installErrorHealth: "应用已安装，但尚未启动。请稍候再重试。" }, Xn = { version: "版本", connect: "连接", install: "安装", setup: "设置" }, $n = { update: "更新", settings: "设置", recovery: "恢复", reboot: "系统重启" }, ea = { stopped: "已停止", cause: "原因", error: "错误", release: "发行版本", panel: "面板", copied: "已复制", savedProgress: "已保存的进度", earlierCopy: "先前的副本", stagedCopy: "暂存的副本", setAside: "已搁置", alreadyInstalled: "已安装", permissions: "权限", setupAddress: "设置地址", setupHandover: "设置交接", copiedDetail: "已发送 {bytes} 字节；正在等待面板确认", none: "无", setupMissing: "未找到；设置将在面板上继续" }, na = {
  sidebar: Wn,
  haInstall: Yn,
  installer: Kn,
  errors: Jn,
  journey: Xn,
  sidebarReason: $n,
  support: ea
}, ce = Object.freeze({ en: xe, cs: Le, de: Ve, es: Xe, fr: rn, it: mn, nl: zn, pl: Sn, "pt-BR": Rn, uk: _n, "zh-Hans": na }), V = ce.en, aa = ["en", "cs", "de", "es", "fr", "it", "nl", "pl", "uk", "zh-Hans"];
function M(a) {
  if (typeof a != "string") return "en";
  const e = a.trim().replaceAll("_", "-").toLowerCase();
  if (e === "pt-br" || e.startsWith("pt-br-")) return "pt-BR";
  if (e === "zh" || e === "zh-cn" || e.startsWith("zh-cn-") || e === "zh-sg" || e.startsWith("zh-sg-") || e === "zh-hans" || e.startsWith("zh-hans-")) return "zh-Hans";
  const n = e.split("-")[0];
  return aa.includes(n) ? n : "en";
}
function j(a, e, n = ce) {
  const t = V[a], i = n[M(e)]?.[a], r = (l) => [...l.matchAll(/\{([A-Za-z_]+)\}/g)].map((o) => o[1]).sort().join(",");
  return Object.fromEntries(Object.entries(t).map(([l, o]) => {
    const h = i?.[l];
    return [l, typeof h == "string" && (h || !o) && r(h) === r(o) ? h : o];
  }));
}
function T(a, e) {
  return a.replace(/\{([A-Za-z_]+)\}/g, (n, t) => Object.hasOwn(e, t) ? String(e[t]) : n);
}
Object.freeze(V.sidebar);
const ta = "data:image/png;base64,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", ia = "https://github.com/panel-assistant/ha-integration", oa = "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12", pe = "panel_assistant.sidebar.entry", sa = "/config/integrations/dashboard/add?domain=panel_assistant", ra = "/config/integrations/integration/panel_assistant", la = /* @__PURE__ */ new Set(["reachable", "unreachable", "not_loaded", "restarting"]), da = /* @__PURE__ */ new Set(["update", "settings", "recovery", "reboot"]), ca = 5e3, pa = /* @__PURE__ */ new Set(["current", "added", "updated", "removed"]);
function ua(a) {
  if (!a || !Array.isArray(a.panels) || a.panels.length > 200) throw Error("invalid panels");
  const e = /* @__PURE__ */ new Set();
  return a.panels.map((n) => {
    if (!n || typeof n.entry_id != "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(n.entry_id) || e.has(n.entry_id) || typeof n.title != "string" || n.title.length > 256 || !la.has(n.state) || n.state === "restarting" && !da.has(n.reason) || n.device_id !== null && n.device_id !== void 0 && typeof n.device_id != "string") throw Error("invalid panel");
    return e.add(n.entry_id), {
      entry_id: n.entry_id,
      title: n.title,
      state: n.state,
      device_id: n.device_id ?? null,
      ...n.state === "restarting" ? { reason: n.reason } : {}
    };
  });
}
function ga(a) {
  return typeof a == "string" ? a.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}
function J(a, e = "en") {
  const n = a?.version, t = a?.build;
  return typeof n != "string" || !/^[0-9A-Za-z.+-]{1,32}$/.test(n) || !Number.isSafeInteger(t) || t < 0 ? "" : T(j("sidebar", e).versionLabel, { version: n, build: t });
}
function ha(a, e = "en") {
  return T(j("sidebar", e).opening, { panel: typeof a == "string" ? a : "" });
}
function X(a) {
  history.pushState(null, "", a), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
}
function ma() {
  try {
    return localStorage.getItem(pe);
  } catch {
    return null;
  }
}
function ba(a) {
  try {
    localStorage.setItem(pe, a);
  } catch {
  }
}
class va extends HTMLElement {
  get #e() {
    return j("sidebar", this.#n?.language);
  }
  #n;
  #d;
  #a;
  #t = !1;
  #g;
  #i = null;
  #s = "loading";
  #r = 0;
  #u = "";
  #o = null;
  #l = null;
  #m = null;
  #b = null;
  #w = () => this.#M();
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
      <img id="icon" src="${ta}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <button id="overflow" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="more"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z"/></svg></button>
      <div id="backdrop"></div>
      <div id="more">
      <a id="device"><svg class="wide-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg><svg class="menu-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19,18H5V6H19M21,4H3C1.89,4 1,4.89 1,6V18A2,2 0 0,0 3,20H21A2,2 0 0,0 23,18V6C23,4.89 22.1,4 21,4Z"/></svg><span class="item-label" data-message="showDevice"></span></a>
      <div id="spacer"></div>
      <a id="github" href="${ia}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${oa}"/></svg><span class="item-label" data-message="github"></span></a>
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
    t.setAttribute("aria-label", this.#e.more), t.setAttribute("title", this.#e.more), t.hidden = !0, e.querySelector("#backdrop").hidden = !0, e.querySelector("#dot").hidden = !0, t.addEventListener("click", () => this.#h(!this.#k())), e.querySelector("#backdrop").addEventListener("click", () => this.#h(!1)), e.querySelector("#root").addEventListener("keydown", (o) => {
      o.key !== "Escape" || !this.#k() || (this.#h(!1), t.focus?.());
    }), e.querySelector("#frame").setAttribute("title", this.#e.frameTitle);
    const i = e.querySelector("#settings");
    i.setAttribute("aria-label", this.#e.integrationSettings), i.setAttribute("title", this.#e.integrationSettings);
    const r = e.querySelector("#github");
    r.setAttribute("aria-label", this.#e.github), r.setAttribute("title", this.#e.github), r.addEventListener("click", () => this.#h(!1));
    const l = e.querySelector("#device");
    l.setAttribute("aria-label", this.#e.device), l.setAttribute("title", this.#e.device), l.addEventListener("click", (o) => {
      const h = l.getAttribute("href");
      this.#h(!1), !(!h || o.defaultPrevented || o.button !== 0 || o.metaKey || o.ctrlKey || o.shiftKey || o.altKey) && (o.preventDefault(), X(h));
    });
    for (const [o, h] of [["add", sa], ["settings", ra]]) {
      const u = e.querySelector(`#${o}`);
      u.setAttribute("href", h), u.addEventListener("click", (g) => {
        this.#h(!1), !(g.defaultPrevented || g.button !== 0 || g.metaKey || g.ctrlKey || g.shiftKey || g.altKey) && (g.preventDefault(), X(h));
      });
    }
    e.querySelector("#panels").addEventListener("change", (o) => this.#j(o.target.value)), this.#c();
  }
  get hass() {
    return this.#n;
  }
  set hass(e) {
    const n = this.#n;
    if (this.#n = e, !!this.isConnected) {
      if (this.#v(), n?.connection !== e?.connection || n?.user?.id !== e?.user?.id || n?.user?.is_admin !== e?.user?.is_admin) {
        this.#I();
        return;
      }
      (n?.language !== e?.language || !!n?.themes?.darkMode != !!e?.themes?.darkMode) && (this.#p(), this.#A());
    }
  }
  get panel() {
    return this.#d;
  }
  set panel(e) {
    this.#d = e, this.shadowRoot.querySelector("#version").textContent = J(e?.config, this.#n?.language);
  }
  get route() {
    return this.#a;
  }
  set route(e) {
    const n = this.#a?.path;
    this.#a = e, n !== e?.path && this.#j(e?.path?.slice(1));
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
    for (const r of ["device", "github", "add", "settings"]) {
      const l = n.querySelector(`#${r}`);
      this.#t ? l.setAttribute("role", "menuitem") : l.removeAttribute("role");
    }
    this.#t || this.#h(!1), this.#v();
  }
  #k() {
    return this.shadowRoot.querySelector("#more").getAttribute("data-open") !== null;
  }
  #h(e) {
    const n = this.shadowRoot, t = n.querySelector("#more"), i = e && this.#t;
    i ? t.setAttribute("data-open", "") : t.removeAttribute("data-open"), n.querySelector("#backdrop").hidden = !i, n.querySelector("#overflow").setAttribute("aria-expanded", String(i));
  }
  // Home Assistant's menu button shows a dot while persistent notifications exist; on a phone this
  // header replaces it, so it keeps the dot from the same subscription.
  #v() {
    const e = this.isConnected && this.#t ? this.#n?.connection : void 0, n = this.#b;
    if (n?.connection === e || (n && (this.#b = null, n.unsubscribe?.then((i) => i()).catch(() => {
    }), this.shadowRoot.querySelector("#dot").hidden = !0), !e?.subscribeMessage)) return;
    const t = { connection: e, notifications: {}, unsubscribe: null };
    this.#b = t, t.unsubscribe = Promise.resolve().then(() => e.subscribeMessage((i) => this.#x(t, i), { type: "persistent_notification/subscribe" })), t.unsubscribe.catch(() => {
    });
  }
  #x(e, n) {
    if (!(this.#b !== e || !pa.has(n?.type) || !n.notifications || typeof n.notifications != "object")) {
      if (n.type === "current") e.notifications = { ...n.notifications };
      else if (n.type === "removed") for (const t of Object.keys(n.notifications)) delete e.notifications[t];
      else e.notifications = { ...e.notifications, ...n.notifications };
      this.shadowRoot.querySelector("#dot").hidden = Object.keys(e.notifications).length === 0;
    }
  }
  connectedCallback() {
    clearInterval(this.#m), this.#I(), this.#v(), this.#m = setInterval(() => this.#f(), ca);
  }
  disconnectedCallback() {
    clearInterval(this.#m), this.#m = null, this.#r++, this.#z(), this.#p(), this.#v();
  }
  #y() {
    return this.#n?.user?.is_admin === !0;
  }
  #z() {
    this.#g?.removeEventListener?.("ready", this.#w), this.#g = void 0;
  }
  #I() {
    this.#z(), this.#p(), this.#i = null, this.#u = "", this.#s = "loading", this.#y() && this.#n.connection && (this.#g = this.#n.connection, this.#g.addEventListener("ready", this.#w)), this.#c(), this.#f();
  }
  async #f() {
    const e = ++this.#r;
    if (!this.#y()) {
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
      n = ua(t);
    } catch {
      if (e !== this.#r) return;
      this.#i = null, this.#s = "failed", this.#p(), this.#c();
      return;
    }
    if (e === this.#r) {
      if (this.#i = n, this.#s = n.length ? "ready" : "empty", !n.some((t) => t.entry_id === this.#o)) {
        const t = this.#a?.path?.slice(1), i = n.some((r) => r.entry_id === t) ? t : ma();
        this.#o = n.some((r) => r.entry_id === i) ? i : n[0]?.entry_id ?? null;
      }
      this.#A();
    }
  }
  #j(e) {
    !this.#i?.some((n) => n.entry_id === e) || e === this.#o || (this.#o = e, ba(e), this.#p(), this.#A());
  }
  // Opens a session when the selected panel is reachable and none is live for it.
  #A() {
    const e = this.#i?.find((t) => t.entry_id === this.#o), n = this.#l;
    !e || e.state !== "reachable" ? n && !(n.state === "closed" && n.entryId === e?.entry_id) && this.#p() : (!n || n.entryId !== e.entry_id || !["opening", "open"].includes(n.state)) && (this.#p(), this.#C(e.entry_id, null, null)), this.#c();
  }
  #C(e, n, t) {
    const i = this.#n, r = { entryId: e, token: n, url: t, state: "opening", code: null, unsubscribe: null };
    this.#l = r;
    const l = {
      type: "panel_assistant/embed_session",
      entry_id: e,
      language: i.language,
      theme: i.themes?.darkMode ? "dark" : "light",
      ...n ? { resume: n } : {}
    };
    r.unsubscribe = Promise.resolve().then(() => i.connection.subscribeMessage((o) => this.#P(r, o), l, { resubscribe: !1 })), r.unsubscribe.catch((o) => {
      this.#l === r && (r.state = "failed", r.code = o?.code ?? null, this.#E(), this.#c());
    });
  }
  #P(e, n) {
    if (!(this.#l !== e || !n))
      if (n.kind === "opened") {
        const t = ga(n.url);
        if (!t) {
          this.#p(), this.#l = { entryId: e.entryId, state: "failed", code: null, unsubscribe: null }, this.#c();
          return;
        }
        e.token = t, e.url = n.url, e.state = "open";
        const i = this.shadowRoot.querySelector("#frame");
        i.getAttribute("src") !== n.url && i.setAttribute("src", n.url), this.#c();
      } else n.kind === "closed" && (this.#p(), this.#l = { entryId: e.entryId, state: "closed", code: null, unsubscribe: null }, this.#c(), this.#f());
  }
  // The connection came back; subscriptions made with resubscribe:false are gone, and
  // their unsubscribe functions must not be called: command ids restart per socket.
  #M() {
    const e = this.#l;
    !this.isConnected || !e || !["opening", "open"].includes(e.state) || (this.#C(e.entryId, e.token, e.url), this.#c());
  }
  #p() {
    const e = this.#l;
    this.#l = null, e && (e.state = "ended", e.unsubscribe?.then((n) => n()).catch(() => {
    }), this.#E());
  }
  #E() {
    this.shadowRoot.querySelector("#frame").removeAttribute("src");
  }
  #c() {
    const e = this.shadowRoot, n = this.#n?.language;
    this.isConnected && this.setAttribute?.("lang", M(n));
    const t = this.#e;
    for (const b of e.querySelectorAll("[data-message]")) b.textContent = t[b.dataset.message];
    for (const [b, v] of [["menu", "menu"], ["overflow", "more"], ["settings", "integrationSettings"], ["github", "github"], ["device", "device"], ["add", "addPanel"]])
      e.querySelector(`#${b}`).setAttribute("aria-label", t[v]), e.querySelector(`#${b}`).setAttribute("title", t[v]);
    e.querySelector("#frame").setAttribute("title", t.frameTitle), e.querySelector("#version").textContent = J(this.#d?.config, n);
    const i = e.querySelector("#panels"), r = this.#y() ? this.#i ?? [] : [], l = JSON.stringify([r, M(n)]);
    if (l !== this.#u) {
      this.#u = l, i.replaceChildren();
      for (const b of r) {
        const v = document.createElement("option");
        v.value = b.entry_id, v.textContent = b.state === "reachable" ? b.title : b.state === "restarting" ? `${b.title} (${T(this.#e.restarting, { reason: j("sidebarReason", n)[b.reason] })})` : `${b.title} (${this.#e[b.state]})`, i.append(v);
      }
    }
    i.value = this.#o ?? "", e.querySelector("#picker").hidden = r.length === 0;
    const o = r.find((b) => b.entry_id === this.#o), h = e.querySelector("#device");
    h.hidden = !o?.device_id, o?.device_id && h.setAttribute("href", `/config/devices/device/${encodeURIComponent(o.device_id)}`);
    const u = this.#l, g = e.querySelector("#frame");
    let d = "", p = !1;
    this.#y() ? this.#s === "loading" ? p = !0 : this.#s !== "ready" ? d = this.#s : u?.state === "closed" && u.entryId === o?.entry_id ? d = "closed" : o?.state === "restarting" ? d = "restarting" : o?.state === "unreachable" ? d = "unreachableBody" : o?.state === "not_loaded" ? d = "notLoadedBody" : u?.state === "failed" ? d = u.code === "not_loaded" ? "notLoadedBody" : "failed" : u?.state !== "open" && !g.getAttribute("src") && (p = !0) : d = "admin";
    const y = e.querySelector("#status");
    y.textContent = d === "restarting" ? T(this.#e.restarting, { reason: j("sidebarReason", n)[o.reason] }) : d ? this.#e[d] : "";
    const f = ["unreachableBody", "notLoadedBody", "failed", "closed"].includes(d);
    e.querySelector("#failure").hidden = !f, e.querySelector("#failure-status").textContent = f ? y.textContent : "", e.querySelector("#next-step").textContent = f ? this.#e[{ unreachableBody: "unreachableNext", notLoadedBody: "notLoadedNext", failed: "failedNext", closed: "closedNext" }[d]] : "", y.hidden = !d || f;
    const z = e.querySelector("#loading");
    z.hidden = !p;
    const S = e.querySelector("#loading-text");
    S.hidden = !o, p && (S.textContent = o ? ha(o.title, n) : ""), g.hidden = !g.getAttribute("src");
  }
}
customElements.get("panel-assistant-sidebar") || customElements.define("panel-assistant-sidebar", va);
const F = "io.github.maxlyth.hapaneld", Z = "io.panelassistant.android", ya = "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity", fa = "io.panelassistant.android/io.panelassistant.android.MainActivity", Aa = Object.freeze({
  [F]: Object.freeze(["io.github.maxlyth.hapaneld/.MainActivity"]),
  [Z]: Object.freeze([ya, fa])
});
Aa[F][0];
const ue = 64, ge = 256 * 1024, wa = 2147483647, ka = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, za = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc[1-9][0-9]*$/, $ = /^build-([1-9][0-9]{0,9})(-successor)?$/, Ia = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/, O = (a, e) => typeof e == "string" && e.length <= ue && a.exec(e)?.[0] === e, he = (a) => O(ka, a), me = (a) => O(za, a), ee = (a) => he(a) || me(a);
function be(a) {
  if (!O($, a)) return null;
  const e = Number($.exec(a)[1]);
  return e <= wa ? e : null;
}
const P = (a) => be(a) !== null, ja = (a) => P(a) ? a.endsWith("-successor") ? Z : F : null, Ca = (a) => O(Ia, a), Ea = (a, e) => `${a} build ${e}`, q = "/api/panel_assistant/usb/release", xa = "/api/panel_assistant/usb/handover", Pa = 35e3, Ma = /(?:[0-9]{1,3}\.){3}[0-9]{1,3}/, Sa = /[a-z_]{1,48}/, ne = 64 * 1024 * 1024, Da = 1800 * 1e3, ae = [
  "id",
  "tag",
  "checksum",
  "checksum_signature",
  "descriptor",
  "descriptor_signature",
  "apk_size",
  "apk_sha256"
], te = ["id", "tag", "feed", "feed_signature", "apk_size", "apk_sha256"], U = 8192, ie = Math.ceil(ge / 3) * 4 + U, C = (a, e) => typeof e == "string" && a.exec(e)?.[0] === e, H = (a, e) => a !== null && typeof a == "object" && !Array.isArray(a) && Object.keys(a).length === e.length && e.every((n) => Object.hasOwn(a, n));
class N extends Error {
  constructor(e) {
    super(e), this.name = "HandoffError", this.code = e;
  }
}
function m(a, e = "invalid_response") {
  if (!a) throw new N(e);
}
function x(a, e, n = !1) {
  m(typeof a == "string" && a.length <= Math.ceil(e / 3) * 4 && C(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, a));
  const t = atob(a);
  return m(btoa(t) === a && t.length > 0 && (n ? t.length === e : t.length <= e)), Uint8Array.from(t, (i) => i.charCodeAt(0));
}
async function Q(a, e, n, t = null) {
  m(a.status === 200 && !a.redirected && a.body);
  const i = a.headers.get("content-length");
  if (i !== null) {
    m(C(/0|[1-9][0-9]*/, i));
    const u = Number(i);
    m(Number.isSafeInteger(u) && u <= e && (t === null || u === t));
  }
  const r = a.body.getReader(), l = () => {
    r.cancel().catch(() => {
    });
  };
  n.addEventListener("abort", l, { once: !0 });
  const o = [];
  let h = 0;
  try {
    for (; ; ) {
      m(!n.aborted, "cancelled");
      const u = await r.read();
      if (m(!n.aborted, "cancelled"), u.done) break;
      h += u.value.byteLength, m(h <= e && (t === null || h <= t)), o.push(u.value);
    }
    return m(h > 0 && (i === null || h === Number(i)) && (t === null || h === t)), new Blob(o);
  } finally {
    n.removeEventListener("abort", l), l(), r.releaseLock();
  }
}
function Ha(a, e, {
  rcTag: n = null,
  language: t = a?.language,
  onState: i = () => {
  },
  windowObject: r = window,
  timeoutMs: l = 3e5
} = {}) {
  let o, h;
  const u = new Promise((s, A) => {
    o = s, h = A;
  }), g = new AbortController();
  let d = !1, p, y, f, z, S = !1, b = !1, v, _, W, Y, R = !1;
  const B = () => {
    clearInterval(W), clearTimeout(Y), v = void 0, r.removeEventListener("message", K);
  }, D = (s) => {
    try {
      i(s);
    } catch {
    }
  }, E = (s = null) => {
    if (!d) {
      if (d = !0, g.abort(), clearTimeout(y), D(s ?? "verified"), s) {
        B(), h(new N(s));
        return;
      }
      W = setInterval(() => {
        p.closed && B();
      }, 2e3), Y = setTimeout(B, Da), o();
    }
  };
  async function ve() {
    try {
      D("preparing"), m(!d, "cancelled");
      const s = await a.fetchWithAuth(q, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(n === null ? {} : { release_candidate: n }),
        redirect: "error",
        signal: g.signal
      });
      m(!d, "cancelled"), m(s.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const A = await Q(s, ie, g.signal);
      let c;
      try {
        c = JSON.parse(await A.text());
      } catch {
        throw new N("invalid_response");
      }
      const w = P(c?.tag);
      m(w || A.size <= U), m(!d, "cancelled"), m(H(c, w ? te : ae) && C(/[0-9a-f]{32}/, c.id) && typeof c.tag == "string" && c.tag.length <= ue && (n === null ? ee(c.tag) || P(c.tag) : c.tag === n) && C(/[0-9a-f]{64}/, c.apk_sha256) && Number.isSafeInteger(c.apk_size) && c.apk_size > 0 && c.apk_size <= ne);
      const k = w ? {
        tag: c.tag,
        feed: x(c.feed, ge),
        feedSignature: x(c.feed_signature, 256, !0)
      } : {
        tag: c.tag,
        checksum: x(c.checksum, 512),
        checksumSignature: x(c.checksum_signature, 256, !0),
        descriptor: x(c.descriptor, 4096),
        descriptorSignature: x(c.descriptor_signature, 256, !0)
      };
      D("downloading"), m(!d, "cancelled");
      const L = await a.fetchWithAuth(`${q}/${c.id}/apk`, {
        method: "GET",
        redirect: "error",
        signal: g.signal
      });
      m(!d, "cancelled");
      const Ae = await Q(L, ne, g.signal, c.apk_size);
      m(!d && !p.closed, "window_closed"), b = !0, _ = c.apk_sha256, v = { type: "ha-paneld/usb-bundle", nonce: z, bundle: k, apk: Ae }, p.postMessage(v, f), D("verifying");
    } catch (s) {
      E(s instanceof N ? s.code : "delivery_failed");
    }
  }
  async function ye(s) {
    if (!d || !v || p.closed || !H(s, ["type", "nonce", "requestId", "tag", "apkSha256"]) || !C(/[0-9a-f]{32}/, s.requestId)) return;
    let A = !1;
    try {
      m(s.tag === v.bundle.tag && s.apkSha256 === _);
      const c = AbortSignal.timeout(Math.min(l, 1e4)), w = await a.fetchWithAuth(q, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(n === null ? {} : { release_candidate: n }),
        redirect: "error",
        signal: c
      });
      m(w.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const k = JSON.parse(await (await Q(
        w,
        P(s.tag) ? ie : U,
        c
      )).text());
      m(H(k, P(s.tag) ? te : ae) && C(/[0-9a-f]{32}/, k.id) && k.tag === s.tag && k.apk_sha256 === s.apkSha256 && k.apk_size === v.apk.size), A = !0;
    } catch {
    }
    if (!(!v || p.closed))
      try {
        p.postMessage({
          type: "ha-paneld/usb-admission-result",
          nonce: z,
          requestId: s.requestId,
          tag: s.tag,
          apkSha256: s.apkSha256,
          admitted: A
        }, f);
      } catch {
      }
  }
  async function fe(s) {
    if (R || !d || !v || p.closed || !H(s, ["type", "nonce", "address"]) || !C(Ma, s.address)) return;
    R = !0;
    const A = (w, k = {}) => {
      try {
        p.closed || p.postMessage({ type: w, nonce: z, ...k }, f);
      } catch {
      }
    };
    A("ha-paneld/usb-handover-accepted");
    let c;
    try {
      const w = await a.fetchWithAuth(xa, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address: s.address }),
        redirect: "error",
        signal: AbortSignal.timeout(Pa)
      }), k = await w.json().catch(() => null), L = w.status === 200 ? k?.outcome : k?.error;
      c = C(Sa, L) ? L : `http_${w.status}`;
    } catch {
      c = "request_failed";
    } finally {
      R = !1;
    }
    A("ha-paneld/usb-handover-result", { outcome: c });
  }
  function K(s) {
    if (!(s.source !== p || s.origin !== f)) {
      if (s.data?.type === "ha-paneld/usb-admission" && s.data.nonce === z) {
        ye(s.data);
        return;
      }
      if (s.data?.type === "ha-paneld/usb-handover" && s.data.nonce === z) {
        fe(s.data);
        return;
      }
      if (!(!H(s.data, ["type", "nonce"]) || s.data.nonce !== z)) {
        if (s.data.type === "ha-paneld/usb-ready") {
          !S && !d ? (S = !0, ve()) : v && !p.closed && p.postMessage(v, f);
          return;
        }
        d || (s.data.type === "ha-paneld/usb-verified" && b ? E() : s.data.type === "ha-paneld/usb-error" && E("verification_failed"));
      }
    }
  }
  try {
    m(a && typeof a.fetchWithAuth == "function" && (n === null || ee(n) || P(n)) && Number.isSafeInteger(l) && l > 0 && l <= 3e5, "invalid_request");
    const s = new URL(e);
    m(!s.username && !s.password && !s.hash && (s.protocol === "https:" || s.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(s.hostname)), "invalid_destination"), s.searchParams.set("lang", M(t)), f = s.origin;
    const A = new Uint8Array(16);
    r.crypto.getRandomValues(A), z = Array.from(A, (c) => c.toString(16).padStart(2, "0")).join(""), s.hash = new URLSearchParams({ ha_origin: r.location.origin, nonce: z, rc: n ?? "" }).toString(), r.addEventListener("message", K), p = r.open(s.href, "_blank"), m(p, "popup_blocked"), y = setTimeout(() => E("timeout"), l), D("waiting");
  } catch (s) {
    E(s instanceof N ? s.code : "invalid_request");
  }
  return { completion: u, cancel: () => {
    E("cancelled"), B();
  } };
}
const oe = 30, se = 500, re = 128 * 1024, Na = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?$/, G = (a, e) => a !== null && typeof a == "object" && !Array.isArray(a) && Object.keys(a).length === e.length && e.every((n) => Object.hasOwn(a, n));
function I(a) {
  if (!a) throw new Error("Invalid release catalogue");
}
function Ba(a) {
  const e = be(a.tag), n = typeof a.name == "string" ? a.name.split(" ")[0] : null, t = typeof n == "string" ? Na.exec(n) : null, i = t?.[4], r = ja(a.tag) === Z ? " (Panel Assistant)" : "";
  return e !== null && t !== null && t[0] === n && typeof a.prerelease == "boolean" && a.prerelease === !!i && (!i || i.split(".").every((l) => l && !/^0[0-9]+$/.test(l))) && Ca(n) && a.name === `${Ea(n, e)}${r}`;
}
function La(a) {
  I(G(a, ["releases"]) && Array.isArray(a.releases) && a.releases.length <= oe + se);
  const e = /* @__PURE__ */ new Set();
  let n = 0, t = 0;
  return Object.freeze(a.releases.map((i) => G(i, ["tag", "prerelease", "name"]) ? (I(Ba(i) && !e.has(i.tag) && ++t <= se), e.add(i.tag), Object.freeze({ tag: i.tag, prerelease: i.prerelease, name: i.name })) : (I(G(i, ["tag", "prerelease"]) && typeof i.prerelease == "boolean" && (i.prerelease ? me(i.tag) : he(i.tag)) && !e.has(i.tag) && ++n <= oe), e.add(i.tag), Object.freeze({ tag: i.tag, prerelease: i.prerelease }))));
}
async function Ta(a, { signal: e, timeoutMs: n = 15e3 } = {}) {
  const t = new AbortController(), i = () => t.abort();
  e?.addEventListener("abort", i, { once: !0 }), e?.aborted && i();
  const r = setTimeout(i, n);
  let l, o;
  const h = new Promise((u, g) => {
    o = () => g(new Error("Release catalogue cancelled"));
  });
  t.signal.addEventListener("abort", o, { once: !0 });
  try {
    return I(!t.signal.aborted), await Promise.race([h, (async () => {
      const u = await a.fetchWithAuth("/api/panel_assistant/usb/releases", {
        method: "GET",
        redirect: "error",
        signal: t.signal
      });
      I(!t.signal.aborted && u.status === 200 && !u.redirected && u.body && u.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const g = u.headers.get("content-length");
      I(g === null || /^(0|[1-9][0-9]*)$/.exec(g)?.[0] === g && Number(g) <= re), l = u.body.getReader();
      const d = [];
      let p = 0;
      for (; ; ) {
        const y = await l.read();
        if (I(!t.signal.aborted), y.done) break;
        p += y.value.byteLength, I(p <= re), d.push(y.value);
      }
      return I(p > 0 && (g === null || p === Number(g))), La(JSON.parse(await new Blob(d).text()));
    })()]);
  } finally {
    clearTimeout(r), e?.removeEventListener("abort", i), t.signal.removeEventListener("abort", o), t.abort(), l && l.cancel().catch(() => {
    });
  }
}
const Oa = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDgiIGhlaWdodD0iMTA4IiB2aWV3Qm94PSIwIDAgMTA4IDEwOCI+CjxwYXRoIGQ9Ik0yOCwzMiBoNTIgYTQsNCAwIDAgMSA0LDQgdjM3IGE0LDQgMCAwIDEgLTQsNCBoLTUyIGE0LDQgMCAwIDEgLTQsLTQgdi0zNyBhNCw0IDAgMCAxIDQsLTQgeiIgZmlsbD0iIzM3NDc0RiIvPgo8cGF0aCBkPSJNMjksMzUgaDUwIGEyLDIgMCAwIDEgMiwyIHYzNSBhMiwyIDAgMCAxIC0yLDIgaC01MCBhMiwyIDAgMCAxIC0yLC0yIHYtMzUgYTIsMiAwIDAgMSAyLC0yIHoiIGZpbGw9IiMwRTE2MjAiLz4KPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoNDIuMDAsNDIuNTApIHNjYWxlKDAuMTAwMCkiPgo8cGF0aCBmaWxsPSIjRjJGNEY5IiBkPSJNMjQwIDIyNC44MTNDMjQwIDIzMy4wNjMgMjMzLjI1IDIzOS44MTMgMjI1IDIzOS44MTNIMTVDNi43NSAyMzkuODEzIDAgMjMzLjA2MyAwIDIyNC44MTNWMTM0LjgxM0MwIDEyNi41NjMgNC43NyAxMTUuMDQzIDEwLjYxIDEwOS4yMDNMMTA5LjM5IDEwLjQyM0MxMTUuMjIgNC41OTMwNCAxMjQuNzcgNC41OTMwNCAxMzAuNiAxMC40MjNMMjI5LjM5IDEwOS4yMTNDMjM1LjIyIDExNS4wNDMgMjQwIDEyNi41NzMgMjQwIDEzNC44MjNWMjI0LjgyM1YyMjQuODEzWiIvPgo8cGF0aCBmaWxsPSIjMThCQ0YyIiBkPSJNMjI5LjM5IDEwOS4yMDNMMTMwLjYxIDEwLjQyM0MxMjQuNzggNC41OTMwNCAxMTUuMjMgNC41OTMwNCAxMDkuNCAxMC40MjNMMTAuNjEgMTA5LjIwM0M0Ljc4IDExNS4wMzMgMCAxMjYuNTYzIDAgMTM0LjgxM1YyMjQuODEzQzAgMjMzLjA2MyA2Ljc1IDIzOS44MTMgMTUgMjM5LjgxM0gxMDcuMjdMNjYuNjQgMTk5LjE4M0M2NC41NSAxOTkuOTAzIDYyLjMyIDIwMC4zMTMgNjAgMjAwLjMxM0M0OC43IDIwMC4zMTMgMzkuNSAxOTEuMTEzIDM5LjUgMTc5LjgxM0MzOS41IDE2OC41MTMgNDguNyAxNTkuMzEzIDYwIDE1OS4zMTNDNzEuMyAxNTkuMzEzIDgwLjUgMTY4LjUxMyA4MC41IDE3OS44MTNDODAuNSAxODIuMTQzIDgwLjA5IDE4NC4zNzMgNzkuMzcgMTg2LjQ2M0wxMTEgMjE4LjA5M1YxMDIuMjEzQzEwNC4yIDk4Ljg3MyA5OS41IDkxLjg5MyA5OS41IDgzLjgyM0M5OS41IDcyLjUyMyAxMDguNyA2My4zMjMgMTIwIDYzLjMyM0MxMzEuMyA2My4zMjMgMTQwLjUgNzIuNTIzIDE0MC41IDgzLjgyM0MxNDAuNSA5MS44OTMgMTM1LjggOTguODczIDEyOSAxMDIuMjEzVjE4My40ODNMMTYwLjQ2IDE1Mi4wMjNDMTU5Ljg0IDE1MC4wNjMgMTU5LjUgMTQ3Ljk4MyAxNTkuNSAxNDUuODIzQzE1OS41IDEzNC41MjMgMTY4LjcgMTI1LjMyMyAxODAgMTI1LjMyM0MxOTEuMyAxMjUuMzIzIDIwMC41IDEzNC41MjMgMjAwLjUgMTQ1LjgyM0MyMDAuNSAxNTcuMTIzIDE5MS4zIDE2Ni4zMjMgMTgwIDE2Ni4zMjNDMTc3LjUgMTY2LjMyMyAxNzUuMTIgMTY1Ljg1MyAxNzIuOTEgMTY1LjAzM0wxMjkgMjA4Ljk0M1YyMzkuODIzSDIyNUMyMzMuMjUgMjM5LjgyMyAyNDAgMjMzLjA3MyAyNDAgMjI0LjgyM1YxMzQuODIzQzI0MCAxMjYuNTczIDIzNS4yMyAxMTUuMDUzIDIyOS4zOSAxMDkuMjEzVjEwOS4yMDNaIi8+CjwvZz4KPC9zdmc+Cg==", Ra = Object.freeze(Object.values(j("journey", "en")));
function qa(a) {
  return Ra.map((e, n) => n < a ? `<li class="done">${e}</li>` : n === a ? `<li class="current" aria-current="step">${e}</li>` : `<li>${e}</li>`).join("");
}
function Qa(a, e, n = document, t = "en") {
  const i = Object.values(j("journey", t)).map((r, l) => {
    const o = n.createElement("li");
    return o.textContent = r, l < e && (o.className = "done"), l === e && (o.className = "current", o.setAttribute("aria-current", "step")), o;
  });
  a.replaceChildren(...i);
}
const le = "--bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--card-border:#d9dde3;--divider:#e4e7ec;--input-bg:#fafbfc;--border:#c4cad2;--border-strong:#b6bec8;--text:#1b2430;--dim:#6a7480;--accent:#1e56a8;--ok:#3f7d49;--bad:#a02c20;--disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18)", de = "--bg:#111;--card:#181818;--card-head:#222;--card-border:#242424;--divider:#2a2a2a;--input-bg:#161616;--border:#383838;--border-strong:#444;--text:#eee;--dim:#888;--accent:#9af;--ok:#8a8;--bad:#ffb3a6;--disabled-bg:#222;--disabled-fg:#666;--shadow:#000", Ga = `
:root,:host{color-scheme:light dark;${le};--primary:#2557a7;--primary-text:#fff;
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root,:host{${de}}}
:host([theme=light]){color-scheme:light;${le}}
:host([theme=dark]){color-scheme:dark;${de}}
*,*::before,*::after{box-sizing:border-box}
.wiz{max-width:520px;margin:0 auto;padding:4px 0 24px;color:var(--text)}
.wiz-brand{display:flex;align-items:center;gap:.5em;font-size:1.3rem;font-weight:700;margin:0 0 14px}
.wiz-brand img{width:2.1em;height:2.1em;border-radius:6px;flex:none}
.wiz-dots{display:flex;gap:14px;justify-content:center;list-style:none;margin:4px 0 14px;padding:0;flex-wrap:wrap}
.wiz-dots li{display:flex;align-items:center;gap:6px;font-size:.8rem;color:var(--dim)}
.wiz-dots li::before{content:"";flex:none;width:.55rem;height:.55rem;border-radius:50%;background:var(--dim);opacity:.35}
.wiz-dots li.current{color:var(--text);font-weight:600}
.wiz-dots li.current::before{background:var(--primary);opacity:1}
.wiz-dots li.done::before{background:var(--ok);opacity:1}
.card{background:var(--card);border:1px solid var(--card-border);border-radius:12px;padding:14px 16px;margin:0 0 14px}
.card h2{margin:-14px -16px 12px;padding:9px 16px;background:var(--card-head);border-bottom:1px solid var(--divider);
  border-radius:12px 12px 0 0;font-size:1.15rem;line-height:1.35;color:var(--text)}
.card p{line-height:1.5;margin:2px 0 14px;color:var(--dim)}
.card p.lead{color:var(--text);font-size:1.05rem}
.card label{display:block;margin:14px 0 5px;font-weight:700;font-size:.92rem;color:var(--accent)}
.card select{display:block;width:100%;font:inherit;font-size:1rem;min-height:42px;padding:8px 10px;margin:0 0 6px;
  background:var(--input-bg);border:1px solid var(--border);color:var(--text);border-radius:6px}
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
.error h2{color:var(--bad)}
[hidden]{display:none!important}
`;
Object.freeze(V.haInstall);
class Ua extends HTMLElement {
  #e;
  get #n() {
    return j("haInstall", this.#e?.language);
  }
  #d;
  #a;
  // A finished transfer keeps answering a reloaded installer window until this
  // page goes away or a new transfer starts.
  #t;
  #g = "ready";
  #i;
  #s = "loading";
  #r = [];
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${Ga}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      .card p.status{color:var(--text);margin:14px 0 0}
    </style><main class="wiz">
      <div class="wiz-brand"><img src="${Oa}" alt=""><span>ha-paneld</span></div>
      <ol id="journey" class="wiz-dots">${qa(0)}</ol>
      <section class="card">
        <h2 data-message="title"></h2>
        <p class="lead" data-message="introduction"></p>
        <label for="release" data-message="release"></label>
        <select id="release" aria-describedby="catalog-status"></select>
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
    const n = this.#e?.user?.id !== e?.user?.id || this.#e?.user?.is_admin !== e?.user?.is_admin || this.#e?.connection !== e?.connection || this.#e?.auth !== e?.auth;
    this.#e = e;
    const t = e?.themes?.darkMode;
    typeof t == "boolean" && this.setAttribute?.("theme", t ? "dark" : "light"), n && (this.#a?.cancel(), this.#u()), this.#o();
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
    if (this.#i?.abort(), this.#i = void 0, this.#r = [], this.#s = "loading", this.shadowRoot.querySelector("#release").replaceChildren(), this.#o(), !this.isConnected || this.#e?.user?.is_admin !== !0 || !this.#d?.config?.installer_url) return;
    const e = new AbortController();
    this.#i = e;
    try {
      const n = await Ta(this.#e, { signal: e.signal });
      if (this.#i !== e) return;
      this.#r = n, this.#s = n.length ? "ready" : "empty";
      const t = this.shadowRoot.querySelector("#release"), i = document.createElement("option");
      i.value = "", i.textContent = this.#n.choose, i.disabled = !1, t.append(i);
      for (const r of n) {
        const l = document.createElement("option");
        l.value = r.tag;
        const o = r.name ? this.#n.devBuild : r.prerelease ? this.#n.testing : "";
        l.textContent = `${r.name ?? r.tag.replace(/^v/, "")}${o ? ` (${o})` : ""}`, t.append(l);
      }
      t.value = "";
    } catch {
      if (this.#i !== e) return;
      this.#s = "catalogError";
    } finally {
      this.#i === e && (this.#i = void 0, this.#o());
    }
  }
  #o() {
    const e = this.shadowRoot, n = this.#e?.language;
    this.isConnected && this.setAttribute?.("lang", M(n));
    for (const p of e.querySelectorAll("[data-message]")) p.textContent = this.#n[p.dataset.message];
    const t = e.querySelector("#journey");
    t.setAttribute("aria-label", j("installer", n).progress), Qa(t, 0, document, n);
    const i = e.querySelector("#release").children;
    if (i.length) {
      i[0].textContent = this.#n.choose;
      for (const [p, y] of this.#r.entries()) {
        const f = y.name ? this.#n.devBuild : y.prerelease ? this.#n.testing : "";
        i[p + 1].textContent = `${y.name ?? y.tag.replace(/^v/, "")}${f ? ` (${f})` : ""}`;
      }
    }
    const r = this.#e?.user?.is_admin === !0, l = typeof this.#d?.config?.installer_url == "string" && this.#d.config.installer_url.length > 0, o = this.shadowRoot.querySelector("#release").value, h = o === "" ? this.#r[0] : this.#r.find((p) => p.tag === o);
    this.shadowRoot.querySelector("#start").disabled = !r || !l || !!this.#a || !h, this.shadowRoot.querySelector("#cancel").disabled = !this.#a, this.shadowRoot.querySelector("#release").disabled = !!this.#a || this.#s !== "ready";
    const u = this.shadowRoot.querySelector("#catalog-status");
    u.textContent = r && l && this.#s !== "ready" ? this.#n[this.#s] : "", u.hidden = !u.textContent, this.shadowRoot.querySelector("#retry").hidden = !r || !l || !["catalogError", "empty"].includes(this.#s), this.shadowRoot.querySelector("#cancel").hidden = !this.#a;
    const g = r ? l ? this.#g : "unavailable" : "admin", d = this.shadowRoot.querySelector("#status");
    d.textContent = Object.hasOwn(this.#n, g) ? this.#n[g] : this.#n.failed, d.hidden = !d.textContent;
  }
  #l() {
    if (this.#a || this.#e?.user?.is_admin !== !0) return;
    const e = this.shadowRoot.querySelector("#release").value;
    if (!(e === "" ? this.#r[0] : this.#r.find((i) => i.tag === e)) || !this.isConnected) return;
    this.#t?.cancel(), this.#t = void 0;
    const t = Ha(this.#e, this.#d?.config?.installer_url, {
      rcTag: e || null,
      language: M(this.#e?.language),
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
customElements.get("panel-assistant-usb-install") || customElements.define("panel-assistant-usb-install", Ua);
