//#region src/stale-module.mjs
var e = ["panel-assistant-sidebar", "panel-assistant-usb-install"], t = "panel-assistant-module-reloaded";
if (e.some((e) => customElements.get(e))) {
	let e = null;
	try {
		e = sessionStorage.getItem(t), sessionStorage.setItem(t, import.meta.url);
	} catch {
		e = import.meta.url;
	}
	e !== import.meta.url && location.reload();
}
//#endregion
//#region src/translations/index.mjs
var n = Object.freeze({
	en: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version} build {build}",
			menu: "Open navigation",
			choosePanel: "Panel",
			more: "More options",
			addPanel: "Add panel",
			integrationSettings: "Integration settings",
			device: "This panel's Home Assistant device",
			showDevice: "Show device",
			github: "GitHub",
			unreachable: "unreachable",
			restarting: "Restarting ({reason})",
			restartingNext: "This panel will come back automatically. There is no need to refresh.",
			not_loaded: "not loaded",
			opening: "Opening {panel}…",
			loadingHint: "Just a moment",
			empty: "No panels are attached yet.",
			failed: "The panel could not be opened. It will be tried again shortly.",
			admin: "An administrator must open this page.",
			unreachableBody: "Home Assistant cannot reach this panel right now.",
			notLoadedBody: "This panel is not loaded in Home Assistant.",
			closed: "This panel was closed.",
			frameTitle: "Panel interface",
			picklesStory: "Pickles the panda has escaped and is causing havoc. He’s slow and stubborn, so getting him back may take a moment.",
			unreachableNext: "Check that the panel is powered on and connected to your network.",
			notLoadedNext: "Open Integration settings to check this panel’s connection.",
			failedNext: "Wait a moment while we try again, or choose another panel.",
			closedNext: "Choose another panel, or wait for this panel to reconnect.",
			pillOpening: "Opening",
			pillUnreachable: "Unreachable",
			pillNotLoaded: "Not loaded",
			pillRetrying: "Retrying",
			pillClosed: "Closed"
		},
		haInstall: {
			title: "Install Panel Assistant on a panel",
			introduction: "Plug the panel into this computer with a USB cable. A new window will find it and install the app.",
			release: "Version",
			loading: "Loading versions…",
			catalogError: "The list of versions couldn’t be loaded.",
			empty: "No versions are available yet. Try again later.",
			choose: "Recommended version",
			otherVersions: "Choose another version",
			testing: "test version",
			devBuild: "dev build",
			retry: "Try again",
			start: "Continue",
			cancel: "Cancel",
			ready: "",
			unavailable: "The installer isn’t available. Update Panel Assistant, then try again.",
			admin: "Ask a Home Assistant administrator to install panels.",
			waiting: "Continue in the new window.",
			preparing: "Getting the app ready…",
			downloading: "Getting the app ready…",
			verifying: "Getting the app ready…",
			verified: "Continue in the new window.",
			installed: "Panel Assistant is installed. Finish the panel's setup in the installer window, then close this tab.",
			cancelled: "Cancelled.",
			popup_blocked: "Your browser blocked the new window. Allow pop-ups for this page, then press Continue.",
			invalid_request: "Choose a version first.",
			failed: "That didn’t work. Press Continue to try again."
		},
		installer: {
			title: "Set up your panel",
			preparing: "Getting the app ready…",
			preparingSlow: "Still getting the app ready. Keep the Home Assistant tab open.",
			connectHeading: "Connect your panel",
			connectBody: "Plug the panel into this computer with a USB cable, then press Find my panel.",
			usbDebugging: "USB debugging must be on in the panel’s developer options.",
			usbDebuggingHelp: "How to turn it on",
			connect: "Find my panel",
			allowHeading: "Allow this computer",
			allowBody: "On the panel, tick “Always allow from this computer” and tap Allow, so Panel Assistant can update it later without the cable.",
			checkingPanel: "Checking your panel…",
			confirmHeading: "Ready to install",
			confirmBody: "This installs the app and makes it your panel’s home screen. Your other apps are not touched.",
			install: "Install",
			restartedDifferentVersion: "An earlier installation attempt used a different release. That attempt has been set aside.",
			alreadyInstalledHeading: "Already installed",
			alreadyInstalledBody: "This version is already on your panel. Continue to finish setting it up; nothing is copied or reinstalled.",
			continueSetup: "Continue",
			progressHeading: "Installing",
			stepCopying: "Copying the app to your panel…",
			stepFinishingCopy: "Finishing the copy…",
			stepInstalling: "Installing…",
			stepStarting: "Starting the app…",
			stepPermissions: "Giving the app what it needs to run…",
			stepOpening: "Opening your panel’s setup…",
			keepConnected: "Keep the cable plugged in until this finishes.",
			doneHeading: "Installed",
			doneOpening: "Taking you to your panel’s setup…",
			doneManual: "Finish setting up on the panel’s screen.",
			openSetup: "Open panel setup",
			errorHeading: "That didn’t work",
			tryAgain: "Try again",
			backToHa: "Back to Home Assistant",
			details: "Details for support",
			unsupported: "This browser can’t talk to USB devices. Open this page in Chrome or Edge on a computer.",
			handoffFailure: "The app couldn’t be fetched from Home Assistant. Go back to Home Assistant and start again.",
			noSelection: "No panel was chosen. Press Find my panel and pick it from the list, or try another cable: some cables power the panel but carry no data.",
			connectionTimeout: "The panel didn’t answer. Check the cable is firmly in, or swap it: a cable can power the panel and still carry no data.",
			disconnected: "The panel was disconnected. Plug it back in, wait for it to start, then press Try again.",
			cancelled: "Cancelled.",
			pageClosed: "The page was closed.",
			progress: "Progress",
			installProgress: "Installation progress",
			licenses: "Third-party licenses",
			doneAnotherHomeAssistant: "This panel is signed in to another Home Assistant. Change Home Assistant and sign in on the panel to connect it here.",
			reconnectedHeading: "Panel Assistant can reach this panel again",
			reconnectedBody: "This panel already has the app, so nothing was installed. Home Assistant can now update it over your network."
		},
		errors: {
			installErrorGeneric: "Something went wrong. Keep the panel plugged in and press Try again.",
			installErrorBusy: "The panel is busy with another install. Wait a minute, then try again.",
			installErrorStorage: "This browser couldn’t save its progress. Allow this site to store data, then try again.",
			installErrorTarget: "A different panel was connected. Plug in the same panel and try again.",
			installErrorArtifact: "The app download couldn’t be checked. Go back to Home Assistant and start again.",
			installErrorNotClean: "This panel already has the app. Update it from Home Assistant instead.",
			installErrorIncompatible: "This panel can’t run this version of the app.",
			installErrorConnection: "The connection to the panel dropped. Keep it plugged in and press Try again.",
			installErrorHealth: "The app is installed but hasn’t started yet. Wait a moment, then try again.",
			installErrorPermissions: "The app is installed, but its permissions couldn’t be confirmed. Trying again is safe: keep the panel plugged in and press Try again."
		},
		journey: {
			version: "Version",
			connect: "Connect",
			install: "Install",
			setup: "Set up"
		},
		sidebarReason: {
			update: "update",
			settings: "settings",
			recovery: "recovery",
			reboot: "reboot"
		},
		support: {
			stopped: "Stopped",
			cause: "Cause",
			failedCheck: "Failed check",
			error: "Error",
			release: "Release",
			panel: "Panel",
			copied: "Copied",
			savedProgress: "Saved progress",
			earlierCopy: "Earlier copy",
			stagedCopy: "Staged copy",
			setAside: "Set aside",
			alreadyInstalled: "Already installed",
			permissions: "Permissions",
			setupAddress: "Setup address",
			setupHandover: "Setup handover",
			displaySizing: "Display and text size",
			networkAdb: "Network ADB",
			copiedDetail: "{bytes} bytes sent; waiting for the panel to confirm",
			none: "none",
			setupMissing: "not found; setup continues on the panel"
		}
	},
	cs: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version}, sestavení {build}",
			menu: "Otevřít navigaci",
			choosePanel: "Panel",
			more: "Další možnosti",
			addPanel: "Přidat panel",
			integrationSettings: "Nastavení integrace",
			device: "Zařízení tohoto panelu v Home Assistant",
			showDevice: "Zobrazit zařízení",
			github: "GitHub",
			unreachable: "nedostupný",
			restarting: "Restartování ({reason})",
			restartingNext: "Tento panel bude automaticky znovu dostupný. Stránku není třeba obnovovat.",
			not_loaded: "nenačtený",
			opening: "Otevírání panelu {panel}…",
			loadingHint: "Chvilku strpení",
			empty: "Zatím nejsou připojené žádné panely.",
			failed: "Panel se nepodařilo otevřít. Brzy proběhne další pokus.",
			admin: "Tuto stránku musí otevřít správce.",
			unreachableBody: "Home Assistant se k tomuto panelu momentálně nedostane.",
			notLoadedBody: "Tento panel není v Home Assistant načtený.",
			closed: "Tento panel byl zavřen.",
			frameTitle: "Rozhraní panelu",
			picklesStory: "Panda Pickles utekl a tropí neplechu. Je pomalý a tvrdohlavý, takže dostat ho zpátky může chvíli trvat.",
			unreachableNext: "Zkontrolujte, že je panel zapnutý a připojený k vaší síti.",
			notLoadedNext: "Otevřete Nastavení integrace a zkontrolujte připojení tohoto panelu.",
			failedNext: "Chvíli počkejte, než to zkusíme znovu, nebo vyberte jiný panel.",
			closedNext: "Vyberte jiný panel nebo počkejte, až se tento panel znovu připojí.",
			pillOpening: "Otevírání",
			pillUnreachable: "Nedostupný",
			pillNotLoaded: "Nenačtený",
			pillRetrying: "Opakování",
			pillClosed: "Zavřeno"
		},
		haInstall: {
			title: "Nainstalovat Panel Assistant na panel",
			introduction: "Připojte panel k tomuto počítači kabelem USB. Nové okno ho najde a nainstaluje aplikaci.",
			release: "Verze",
			loading: "Načítání verzí…",
			catalogError: "Seznam verzí se nepodařilo načíst.",
			empty: "Zatím nejsou dostupné žádné verze. Zkuste to znovu později.",
			choose: "Doporučená verze",
			otherVersions: "Vybrat jinou verzi",
			testing: "testovací verze",
			devBuild: "vývojové sestavení",
			retry: "Zkusit znovu",
			start: "Pokračovat",
			cancel: "Zrušit",
			ready: "",
			unavailable: "Instalátor není dostupný. Aktualizujte Panel Assistant a pak to zkuste znovu.",
			admin: "Požádejte správce Home Assistant o instalaci panelů.",
			waiting: "Pokračujte v novém okně.",
			preparing: "Příprava aplikace…",
			downloading: "Příprava aplikace…",
			verifying: "Příprava aplikace…",
			verified: "Pokračujte v novém okně.",
			installed: "Panel Assistant je nainstalován. Dokončete nastavení panelu v okně instalátoru a pak tuto kartu zavřete.",
			cancelled: "Zrušeno.",
			popup_blocked: "Prohlížeč zablokoval nové okno. Povolte pro tuto stránku vyskakovací okna a pak stiskněte Pokračovat.",
			invalid_request: "Nejprve vyberte verzi.",
			failed: "To se nepodařilo. Stiskněte Pokračovat a zkuste to znovu."
		},
		installer: {
			title: "Nastavte svůj panel",
			preparing: "Příprava aplikace…",
			preparingSlow: "Aplikace se stále připravuje. Nechte kartu Home Assistant otevřenou.",
			connectHeading: "Připojte svůj panel",
			connectBody: "Připojte panel k tomuto počítači kabelem USB a pak stiskněte Najít můj panel.",
			usbDebugging: "Ladění přes USB musí být zapnuté v nastavení pro vývojáře na panelu.",
			usbDebuggingHelp: "Jak to zapnout",
			connect: "Najít můj panel",
			allowHeading: "Povolit tento počítač",
			allowBody: "Na panelu zaškrtněte „Vždy povolit z tohoto počítače“ a klepněte na Povolit, aby ho Panel Assistant mohl později aktualizovat bez kabelu.",
			checkingPanel: "Kontrola panelu…",
			confirmHeading: "Připraveno k instalaci",
			confirmBody: "Tímto nainstalujete aplikaci a nastavíte ji jako domovskou obrazovku panelu. Vašich ostatních aplikací se to nedotkne.",
			install: "Nainstalovat",
			restartedDifferentVersion: "Předchozí pokus o instalaci používal jiné vydání. Ten pokus byl ponechán stranou.",
			alreadyInstalledHeading: "Už nainstalováno",
			alreadyInstalledBody: "Tato verze už na vašem panelu je. Pokračujte a dokončete nastavení; nic se nekopíruje ani znovu neinstaluje.",
			continueSetup: "Pokračovat",
			progressHeading: "Instalace",
			stepCopying: "Kopírování aplikace do panelu…",
			stepFinishingCopy: "Dokončování kopírování…",
			stepInstalling: "Instalování…",
			stepStarting: "Spouštění aplikace…",
			stepPermissions: "Poskytování toho, co aplikace potřebuje k běhu…",
			stepOpening: "Otevírání nastavení panelu…",
			keepConnected: "Nechte kabel připojený, dokud se proces nedokončí.",
			doneHeading: "Nainstalováno",
			doneOpening: "Přesměrování na nastavení panelu…",
			doneManual: "Dokončete nastavení na obrazovce panelu.",
			openSetup: "Otevřít nastavení panelu",
			errorHeading: "To se nepodařilo",
			tryAgain: "Zkusit znovu",
			backToHa: "Zpět do Home Assistant",
			details: "Podrobnosti pro podporu",
			unsupported: "Tento prohlížeč nedokáže komunikovat se zařízeními USB. Otevřete tuto stránku v prohlížeči Chrome nebo Edge na počítači.",
			handoffFailure: "Aplikaci se nepodařilo získat z Home Assistant. Vraťte se do Home Assistant a začněte znovu.",
			noSelection: "Nebyl vybrán žádný panel. Stiskněte Najít můj panel a vyberte ho ze seznamu nebo zkuste jiný kabel: některé kabely panel napájejí, ale nepřenášejí data.",
			connectionTimeout: "Panel neodpověděl. Zkontrolujte, že je kabel pevně připojený, nebo ho vyměňte: kabel může panel napájet a přitom nepřenášet data.",
			disconnected: "Panel byl odpojen. Znovu ho připojte, počkejte, až se spustí, a pak stiskněte Zkusit znovu.",
			cancelled: "Zrušeno.",
			pageClosed: "Stránka byla zavřena.",
			progress: "Průběh",
			installProgress: "Průběh instalace",
			licenses: "Licence třetích stran",
			doneAnotherHomeAssistant: "Tento panel je přihlášen k jiné instanci Home Assistant. Chcete-li jej připojit sem, změňte na panelu instanci Home Assistant a přihlaste se.",
			reconnectedHeading: "Panel Assistant se k panelu znovu dostane",
			reconnectedBody: "Aplikace už na panelu je, takže se nic neinstalovalo. Home Assistant ho teď může aktualizovat přes vaši síť."
		},
		errors: {
			installErrorGeneric: "Něco se pokazilo. Nechte panel připojený a stiskněte Zkusit znovu.",
			installErrorBusy: "Panel je zaneprázdněný jinou instalací. Počkejte minutu a pak to zkuste znovu.",
			installErrorStorage: "Tento prohlížeč nedokázal uložit průběh. Povolte tomuto webu ukládání dat a pak to zkuste znovu.",
			installErrorTarget: "Byl připojen jiný panel. Připojte stejný panel a zkuste to znovu.",
			installErrorArtifact: "Staženou aplikaci se nepodařilo ověřit. Vraťte se do Home Assistant a začněte znovu.",
			installErrorNotClean: "Tento panel už aplikaci má. Aktualizujte ji z Home Assistant.",
			installErrorIncompatible: "Tento panel nedokáže spustit tuto verzi aplikace.",
			installErrorConnection: "Spojení s panelem se přerušilo. Nechte ho připojený a stiskněte Zkusit znovu.",
			installErrorHealth: "Aplikace je nainstalovaná, ale ještě se nespustila. Chvíli počkejte a pak to zkuste znovu.",
			installErrorPermissions: "Aplikace je nainstalovaná, ale její oprávnění se nepodařilo ověřit. Zkusit to znovu je bezpečné: nechte panel připojený a stiskněte Zkusit znovu."
		},
		journey: {
			version: "Verze",
			connect: "Připojit",
			install: "Nainstalovat",
			setup: "Nastavit"
		},
		sidebarReason: {
			update: "aktualizace",
			settings: "nastavení",
			recovery: "obnova",
			reboot: "restart"
		},
		support: {
			stopped: "Zastaveno",
			cause: "Příčina",
			failedCheck: "Neúspěšná kontrola",
			error: "Chyba",
			release: "Vydání",
			panel: "Panel",
			copied: "Zkopírováno",
			savedProgress: "Uložený průběh",
			earlierCopy: "Dřívější kopie",
			stagedCopy: "Připravená kopie",
			setAside: "Odloženo",
			alreadyInstalled: "Už nainstalováno",
			permissions: "Oprávnění",
			setupAddress: "Adresa nastavení",
			setupHandover: "Předání nastavení",
			displaySizing: "Velikost zobrazení a textu",
			networkAdb: "ADB přes síť",
			copiedDetail: "Odesláno {bytes} bajtů; čeká se na potvrzení panelu",
			none: "žádné",
			setupMissing: "nenalezeno; nastavení pokračuje na panelu"
		}
	},
	de: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version} Build {build}",
			menu: "Navigation öffnen",
			choosePanel: "Panel",
			more: "Weitere Optionen",
			addPanel: "Panel hinzufügen",
			integrationSettings: "Integrationseinstellungen",
			device: "Home Assistant-Gerät dieses Panels",
			showDevice: "Gerät anzeigen",
			github: "GitHub",
			unreachable: "nicht erreichbar",
			restarting: "Neustart ({reason})",
			restartingNext: "Dieses Panel wird automatisch wieder angezeigt. Du musst die Seite nicht aktualisieren.",
			not_loaded: "nicht geladen",
			opening: "{panel} wird geöffnet…",
			loadingHint: "Einen Moment bitte",
			empty: "Es sind noch keine Panels verbunden.",
			failed: "Das Panel konnte nicht geöffnet werden. In Kürze wird es erneut versucht.",
			admin: "Diese Seite muss von einem Administrator geöffnet werden.",
			unreachableBody: "Home Assistant kann dieses Panel gerade nicht erreichen.",
			notLoadedBody: "Dieses Panel ist in Home Assistant nicht geladen.",
			closed: "Dieses Panel wurde geschlossen.",
			frameTitle: "Panel-Oberfläche",
			picklesStory: "Der Panda Pickles ist ausgebüxt und richtet Chaos an. Er ist langsam und stur, deshalb kann es einen Moment dauern, ihn zurückzuholen.",
			unreachableNext: "Prüfe, ob das Panel eingeschaltet und mit deinem Netzwerk verbunden ist.",
			notLoadedNext: "Öffne die Integrationseinstellungen, um die Verbindung dieses Panels zu prüfen.",
			failedNext: "Warte einen Moment, während wir es erneut versuchen, oder wähle ein anderes Panel.",
			closedNext: "Wähle ein anderes Panel oder warte, bis sich dieses Panel wieder verbindet.",
			pillOpening: "Wird geöffnet",
			pillUnreachable: "Nicht erreichbar",
			pillNotLoaded: "Nicht geladen",
			pillRetrying: "Neuer Versuch",
			pillClosed: "Geschlossen"
		},
		haInstall: {
			title: "Panel Assistant auf einem Panel installieren",
			introduction: "Verbinde das Panel über ein USB-Kabel mit diesem Computer. Ein neues Fenster findet es und installiert die App.",
			release: "Version",
			loading: "Versionen werden geladen…",
			catalogError: "Die Versionsliste konnte nicht geladen werden.",
			empty: "Es sind noch keine Versionen verfügbar. Versuche es später erneut.",
			choose: "Empfohlene Version",
			otherVersions: "Andere Version wählen",
			testing: "Testversion",
			devBuild: "Entwicklungsbuild",
			retry: "Erneut versuchen",
			start: "Weiter",
			cancel: "Abbrechen",
			ready: "",
			unavailable: "Das Installationsprogramm ist nicht verfügbar. Aktualisiere Panel Assistant und versuche es erneut.",
			admin: "Bitte einen Home Assistant-Administrator, Panels zu installieren.",
			waiting: "Fahre im neuen Fenster fort.",
			preparing: "Die App wird vorbereitet…",
			downloading: "Die App wird vorbereitet…",
			verifying: "Die App wird vorbereitet…",
			verified: "Fahre im neuen Fenster fort.",
			installed: "Panel Assistant ist installiert. Schließe die Einrichtung des Panels im Installationsfenster ab und schließe dann diesen Tab.",
			cancelled: "Abgebrochen.",
			popup_blocked: "Dein Browser hat das neue Fenster blockiert. Erlaube Pop-ups für diese Seite und drücke dann „Weiter“.",
			invalid_request: "Wähle zuerst eine Version.",
			failed: "Das hat nicht geklappt. Drücke „Weiter“, um es erneut zu versuchen."
		},
		installer: {
			title: "Dein Panel einrichten",
			preparing: "Die App wird vorbereitet…",
			preparingSlow: "Die App wird noch vorbereitet. Lass den Home Assistant-Tab geöffnet.",
			connectHeading: "Dein Panel verbinden",
			connectBody: "Verbinde das Panel über ein USB-Kabel mit diesem Computer und drücke dann „Mein Panel finden“.",
			usbDebugging: "USB-Debugging muss in den Entwicklereinstellungen des Panels aktiviert sein.",
			usbDebuggingHelp: "So schaltest du es ein",
			connect: "Mein Panel finden",
			allowHeading: "Diesen Computer zulassen",
			allowBody: "Setze auf dem Panel das Häkchen bei „Von diesem Computer immer zulassen“ und tippe auf „Zulassen“, damit Panel Assistant es später ohne Kabel aktualisieren kann.",
			checkingPanel: "Dein Panel wird geprüft…",
			confirmHeading: "Bereit zur Installation",
			confirmBody: "Die App wird installiert und als Startbildschirm deines Panels eingerichtet. Deine anderen Apps bleiben unberührt.",
			install: "Installieren",
			restartedDifferentVersion: "Ein früherer Installationsversuch verwendete eine andere Version. Dieser Versuch wird nicht weiterverwendet.",
			alreadyInstalledHeading: "Bereits installiert",
			alreadyInstalledBody: "Diese Version ist bereits auf deinem Panel. Fahre fort, um die Einrichtung abzuschließen; es wird nichts kopiert oder neu installiert.",
			continueSetup: "Weiter",
			progressHeading: "Installation läuft",
			stepCopying: "Die App wird auf dein Panel kopiert…",
			stepFinishingCopy: "Der Kopiervorgang wird abgeschlossen…",
			stepInstalling: "Installation läuft…",
			stepStarting: "Die App wird gestartet…",
			stepPermissions: "Die App erhält alles, was sie zum Ausführen braucht…",
			stepOpening: "Die Einrichtung deines Panels wird geöffnet…",
			keepConnected: "Lass das Kabel angeschlossen, bis der Vorgang abgeschlossen ist.",
			doneHeading: "Installiert",
			doneOpening: "Du wirst zur Einrichtung deines Panels weitergeleitet…",
			doneManual: "Schließe die Einrichtung auf dem Bildschirm des Panels ab.",
			openSetup: "Panel-Einrichtung öffnen",
			errorHeading: "Das hat nicht geklappt",
			tryAgain: "Erneut versuchen",
			backToHa: "Zurück zu Home Assistant",
			details: "Details für den Support",
			unsupported: "Dieser Browser kann nicht mit USB-Geräten kommunizieren. Öffne diese Seite auf einem Computer in Chrome oder Edge.",
			handoffFailure: "Die App konnte nicht von Home Assistant abgerufen werden. Gehe zurück zu Home Assistant und beginne erneut.",
			noSelection: "Es wurde kein Panel ausgewählt. Drücke „Mein Panel finden“ und wähle es aus der Liste oder versuche ein anderes Kabel: Manche Kabel versorgen das Panel mit Strom, übertragen aber keine Daten.",
			connectionTimeout: "Das Panel hat nicht geantwortet. Prüfe, ob das Kabel fest sitzt, oder tausche es aus: Ein Kabel kann das Panel mit Strom versorgen, ohne Daten zu übertragen.",
			disconnected: "Die Verbindung zum Panel wurde getrennt. Schließe es wieder an, warte, bis es gestartet ist, und drücke dann „Erneut versuchen“.",
			cancelled: "Abgebrochen.",
			pageClosed: "Die Seite wurde geschlossen.",
			progress: "Fortschritt",
			installProgress: "Installationsfortschritt",
			licenses: "Lizenzen von Drittanbietern",
			doneAnotherHomeAssistant: "Dieses Panel ist bei einem anderen Home Assistant angemeldet. Ändere Home Assistant und melde dich auf dem Panel an, um es hier zu verbinden.",
			reconnectedHeading: "Panel Assistant erreicht dieses Panel wieder",
			reconnectedBody: "Die App ist bereits auf diesem Panel, daher wurde nichts installiert. Home Assistant kann es jetzt über dein Netzwerk aktualisieren."
		},
		errors: {
			installErrorGeneric: "Etwas ist schiefgegangen. Lass das Panel angeschlossen und drücke „Erneut versuchen“.",
			installErrorBusy: "Das Panel ist mit einer anderen Installation beschäftigt. Warte eine Minute und versuche es dann erneut.",
			installErrorStorage: "Dieser Browser konnte den Fortschritt nicht speichern. Erlaube dieser Website, Daten zu speichern, und versuche es erneut.",
			installErrorTarget: "Ein anderes Panel wurde angeschlossen. Schließe dasselbe Panel an und versuche es erneut.",
			installErrorArtifact: "Die heruntergeladene App konnte nicht geprüft werden. Gehe zurück zu Home Assistant und beginne erneut.",
			installErrorNotClean: "Die App ist bereits auf diesem Panel. Aktualisiere sie über Home Assistant.",
			installErrorIncompatible: "Dieses Panel kann diese Version der App nicht ausführen.",
			installErrorConnection: "Die Verbindung zum Panel wurde unterbrochen. Lass es angeschlossen und drücke „Erneut versuchen“.",
			installErrorHealth: "Die App ist installiert, aber noch nicht gestartet. Warte einen Moment und versuche es dann erneut.",
			installErrorPermissions: "Die App ist installiert, aber ihre Berechtigungen konnten nicht bestätigt werden. Ein erneuter Versuch ist sicher: Lass das Panel angeschlossen und drücke „Erneut versuchen“."
		},
		journey: {
			version: "Version",
			connect: "Verbinden",
			install: "Installieren",
			setup: "Einrichten"
		},
		sidebarReason: {
			update: "Aktualisierung",
			settings: "Einstellungen",
			recovery: "Wiederherstellung",
			reboot: "Systemneustart"
		},
		support: {
			stopped: "Gestoppt",
			cause: "Ursache",
			failedCheck: "Fehlgeschlagene Prüfung",
			error: "Fehler",
			release: "Version",
			panel: "Panel",
			copied: "Kopiert",
			savedProgress: "Gespeicherter Fortschritt",
			earlierCopy: "Frühere Kopie",
			stagedCopy: "Bereitgestellte Kopie",
			setAside: "Zurückgestellt",
			alreadyInstalled: "Bereits installiert",
			permissions: "Berechtigungen",
			setupAddress: "Einrichtungsadresse",
			setupHandover: "Übergabe an die Einrichtung",
			displaySizing: "Anzeige- und Textgröße",
			networkAdb: "ADB über das Netzwerk",
			copiedDetail: "{bytes} Bytes gesendet; Bestätigung des Panels ausstehend",
			none: "keine",
			setupMissing: "nicht gefunden; Einrichtung wird auf dem Panel fortgesetzt"
		}
	},
	es: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version}, compilación {build}",
			menu: "Abrir navegación",
			choosePanel: "Panel",
			more: "Más opciones",
			addPanel: "Añadir panel",
			integrationSettings: "Configuración de la integración",
			device: "Dispositivo de Home Assistant de este panel",
			showDevice: "Mostrar dispositivo",
			github: "GitHub",
			unreachable: "inaccesible",
			restarting: "Reiniciando ({reason})",
			restartingNext: "Este panel volverá a estar disponible automáticamente. No es necesario actualizar la página.",
			not_loaded: "sin cargar",
			opening: "Abriendo {panel}…",
			loadingHint: "Un momento",
			empty: "Aún no hay paneles vinculados.",
			failed: "No se pudo abrir el panel. Se volverá a intentar en breve.",
			admin: "Un administrador debe abrir esta página.",
			unreachableBody: "Home Assistant no puede acceder a este panel en este momento.",
			notLoadedBody: "Este panel no está cargado en Home Assistant.",
			closed: "Este panel se cerró.",
			frameTitle: "Interfaz del panel",
			picklesStory: "Pickles el panda se ha escapado y está causando estragos. Es lento y testarudo, así que traerlo de vuelta puede llevar un momento.",
			unreachableNext: "Comprueba que el panel esté encendido y conectado a tu red.",
			notLoadedNext: "Abre Configuración de la integración para comprobar la conexión de este panel.",
			failedNext: "Espera un momento mientras volvemos a intentarlo, o elige otro panel.",
			closedNext: "Elige otro panel o espera a que este panel vuelva a conectarse.",
			pillOpening: "Abriendo",
			pillUnreachable: "Inaccesible",
			pillNotLoaded: "Sin cargar",
			pillRetrying: "Reintentando",
			pillClosed: "Cerrado"
		},
		haInstall: {
			title: "Instalar Panel Assistant en un panel",
			introduction: "Conecta el panel a este ordenador con un cable USB. Una nueva ventana lo encontrará e instalará la aplicación.",
			release: "Versión",
			loading: "Cargando versiones…",
			catalogError: "No se pudo cargar la lista de versiones.",
			empty: "Aún no hay versiones disponibles. Vuelve a intentarlo más tarde.",
			choose: "Versión recomendada",
			otherVersions: "Elegir otra versión",
			testing: "versión de prueba",
			devBuild: "compilación de desarrollo",
			retry: "Volver a intentar",
			start: "Continuar",
			cancel: "Cancelar",
			ready: "",
			unavailable: "El instalador no está disponible. Actualiza Panel Assistant y vuelve a intentarlo.",
			admin: "Pide a un administrador de Home Assistant que instale los paneles.",
			waiting: "Continúa en la nueva ventana.",
			preparing: "Preparando la aplicación…",
			downloading: "Preparando la aplicación…",
			verifying: "Preparando la aplicación…",
			verified: "Continúa en la nueva ventana.",
			installed: "Panel Assistant está instalado. Termina la configuración del panel en la ventana del instalador y después cierra esta pestaña.",
			cancelled: "Cancelado.",
			popup_blocked: "Tu navegador bloqueó la nueva ventana. Permite las ventanas emergentes para esta página y pulsa Continuar.",
			invalid_request: "Elige primero una versión.",
			failed: "No ha funcionado. Pulsa Continuar para volver a intentarlo."
		},
		installer: {
			title: "Configura tu panel",
			preparing: "Preparando la aplicación…",
			preparingSlow: "La aplicación aún se está preparando. Mantén abierta la pestaña de Home Assistant.",
			connectHeading: "Conecta tu panel",
			connectBody: "Conecta el panel a este ordenador con un cable USB y pulsa Buscar mi panel.",
			usbDebugging: "La depuración por USB debe estar activada en los ajustes de desarrollador del panel.",
			usbDebuggingHelp: "Cómo activarla",
			connect: "Buscar mi panel",
			allowHeading: "Autoriza este ordenador",
			allowBody: "En el panel, marca «Permitir siempre desde este ordenador» y toca Permitir, para que Panel Assistant pueda actualizarlo más adelante sin el cable.",
			checkingPanel: "Comprobando tu panel…",
			confirmHeading: "Listo para instalar",
			confirmBody: "Esto instala la aplicación y la convierte en la pantalla de inicio de tu panel. Las demás aplicaciones no se modifican.",
			install: "Instalar",
			restartedDifferentVersion: "Un intento anterior de instalación usó una versión diferente. Ese intento se ha dejado de lado.",
			alreadyInstalledHeading: "Ya instalada",
			alreadyInstalledBody: "Esta versión ya está en tu panel. Continúa para terminar la configuración; no se copia ni se reinstala nada.",
			continueSetup: "Continuar",
			progressHeading: "Instalando",
			stepCopying: "Copiando la aplicación a tu panel…",
			stepFinishingCopy: "Terminando la copia…",
			stepInstalling: "Instalando…",
			stepStarting: "Iniciando la aplicación…",
			stepPermissions: "Dando a la aplicación lo que necesita para funcionar…",
			stepOpening: "Abriendo la configuración de tu panel…",
			keepConnected: "Mantén el cable conectado hasta que termine.",
			doneHeading: "Instalada",
			doneOpening: "Te estamos llevando a la configuración de tu panel…",
			doneManual: "Termina la configuración en la pantalla del panel.",
			openSetup: "Abrir la configuración del panel",
			errorHeading: "No ha funcionado",
			tryAgain: "Volver a intentar",
			backToHa: "Volver a Home Assistant",
			details: "Detalles para soporte",
			unsupported: "Este navegador no puede comunicarse con dispositivos USB. Abre esta página en Chrome o Edge en un ordenador.",
			handoffFailure: "No se pudo obtener la aplicación de Home Assistant. Vuelve a Home Assistant y empieza de nuevo.",
			noSelection: "No se eligió ningún panel. Pulsa Buscar mi panel y selecciónalo en la lista, o prueba con otro cable: algunos cables alimentan el panel, pero no transmiten datos.",
			connectionTimeout: "El panel no respondió. Comprueba que el cable esté bien conectado o cámbialo: un cable puede alimentar el panel sin transmitir datos.",
			disconnected: "El panel se desconectó. Vuelve a conectarlo, espera a que inicie y pulsa Volver a intentar.",
			cancelled: "Cancelado.",
			pageClosed: "La página se cerró.",
			progress: "Progreso",
			installProgress: "Progreso de la instalación",
			licenses: "Licencias de terceros",
			doneAnotherHomeAssistant: "Este panel tiene la sesión iniciada en otro Home Assistant. Cambia de Home Assistant e inicia sesión en el panel para conectarlo aquí.",
			reconnectedHeading: "Panel Assistant vuelve a llegar a este panel",
			reconnectedBody: "Este panel ya tiene la app, así que no se ha instalado nada. Home Assistant ya puede actualizarlo a través de tu red."
		},
		errors: {
			installErrorGeneric: "Algo salió mal. Mantén el panel conectado y pulsa Volver a intentar.",
			installErrorBusy: "El panel está ocupado con otra instalación. Espera un minuto y vuelve a intentarlo.",
			installErrorStorage: "Este navegador no pudo guardar el progreso. Permite que este sitio almacene datos y vuelve a intentarlo.",
			installErrorTarget: "Se conectó un panel diferente. Conecta el mismo panel y vuelve a intentarlo.",
			installErrorArtifact: "No se pudo comprobar la descarga de la aplicación. Vuelve a Home Assistant y empieza de nuevo.",
			installErrorNotClean: "Este panel ya tiene la aplicación. Actualízala desde Home Assistant.",
			installErrorIncompatible: "Este panel no puede ejecutar esta versión de la aplicación.",
			installErrorConnection: "Se perdió la conexión con el panel. Mantenlo conectado y pulsa Volver a intentar.",
			installErrorHealth: "La aplicación está instalada, pero aún no ha iniciado. Espera un momento y vuelve a intentarlo.",
			installErrorPermissions: "La aplicación está instalada, pero no se pudieron confirmar sus permisos. Volver a intentarlo es seguro: mantén el panel conectado y pulsa Volver a intentar."
		},
		journey: {
			version: "Versión",
			connect: "Conectar",
			install: "Instalar",
			setup: "Configurar"
		},
		sidebarReason: {
			update: "actualización",
			settings: "configuración",
			recovery: "recuperación",
			reboot: "reinicio del sistema"
		},
		support: {
			stopped: "Detenido",
			cause: "Causa",
			failedCheck: "Comprobación fallida",
			error: "Error",
			release: "Versión",
			panel: "Panel",
			copied: "Copiado",
			savedProgress: "Progreso guardado",
			earlierCopy: "Copia anterior",
			stagedCopy: "Copia preparada",
			setAside: "Dejado de lado",
			alreadyInstalled: "Ya instalada",
			permissions: "Permisos",
			setupAddress: "Dirección de configuración",
			setupHandover: "Transferencia a la configuración",
			displaySizing: "Tamaño de pantalla y texto",
			networkAdb: "ADB por red",
			copiedDetail: "{bytes} bytes enviados; esperando la confirmación del panel",
			none: "ninguno",
			setupMissing: "no encontrada; la configuración continúa en el panel"
		}
	},
	fr: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version}, build {build}",
			menu: "Ouvrir la navigation",
			choosePanel: "Panneau",
			more: "Plus d’options",
			addPanel: "Ajouter un panneau",
			integrationSettings: "Paramètres de l’intégration",
			device: "Appareil Home Assistant de ce panneau",
			showDevice: "Afficher l’appareil",
			github: "GitHub",
			unreachable: "injoignable",
			restarting: "Redémarrage ({reason})",
			restartingNext: "Ce panneau reviendra automatiquement. Il n’est pas nécessaire d’actualiser la page.",
			not_loaded: "non chargé",
			opening: "Ouverture de {panel}…",
			loadingHint: "Un instant",
			empty: "Aucun panneau n’est encore rattaché.",
			failed: "Impossible d’ouvrir le panneau. Une nouvelle tentative aura lieu sous peu.",
			admin: "Un administrateur doit ouvrir cette page.",
			unreachableBody: "Home Assistant ne peut pas joindre ce panneau pour le moment.",
			notLoadedBody: "Ce panneau n’est pas chargé dans Home Assistant.",
			closed: "Ce panneau a été fermé.",
			frameTitle: "Interface du panneau",
			picklesStory: "Pickles le panda s’est échappé et sème la pagaille. Il est lent et têtu, il faudra donc peut-être un moment pour le ramener.",
			unreachableNext: "Vérifiez que le panneau est allumé et connecté à votre réseau.",
			notLoadedNext: "Ouvrez les paramètres de l’intégration pour vérifier la connexion de ce panneau.",
			failedNext: "Patientez pendant la nouvelle tentative, ou choisissez un autre panneau.",
			closedNext: "Choisissez un autre panneau, ou attendez que celui-ci se reconnecte.",
			pillOpening: "Ouverture",
			pillUnreachable: "Injoignable",
			pillNotLoaded: "Non chargé",
			pillRetrying: "Nouvel essai",
			pillClosed: "Fermé"
		},
		haInstall: {
			title: "Installer Panel Assistant sur un panneau",
			introduction: "Branchez le panneau à cet ordinateur avec un câble USB. Une nouvelle fenêtre le trouvera et installera l’application.",
			release: "Version",
			loading: "Chargement des versions…",
			catalogError: "Impossible de charger la liste des versions.",
			empty: "Aucune version n’est encore disponible. Réessayez plus tard.",
			choose: "Version recommandée",
			otherVersions: "Choisir une autre version",
			testing: "version de test",
			devBuild: "build de développement",
			retry: "Réessayer",
			start: "Continuer",
			cancel: "Annuler",
			ready: "",
			unavailable: "Le programme d’installation n’est pas disponible. Mettez à jour Panel Assistant, puis réessayez.",
			admin: "Demandez à un administrateur Home Assistant d’installer les panneaux.",
			waiting: "Continuez dans la nouvelle fenêtre.",
			preparing: "Préparation de l’application…",
			downloading: "Préparation de l’application…",
			verifying: "Préparation de l’application…",
			verified: "Continuez dans la nouvelle fenêtre.",
			installed: "Panel Assistant est installé. Terminez la configuration du panneau dans la fenêtre de l'installateur, puis fermez cet onglet.",
			cancelled: "Annulé.",
			popup_blocked: "Votre navigateur a bloqué la nouvelle fenêtre. Autorisez les fenêtres contextuelles pour cette page, puis appuyez sur « Continuer ».",
			invalid_request: "Choisissez d’abord une version.",
			failed: "Cela n’a pas fonctionné. Appuyez sur « Continuer » pour réessayer."
		},
		installer: {
			title: "Configurer votre panneau",
			preparing: "Préparation de l’application…",
			preparingSlow: "La préparation de l’application se poursuit. Gardez l’onglet Home Assistant ouvert.",
			connectHeading: "Connecter votre panneau",
			connectBody: "Branchez le panneau à cet ordinateur avec un câble USB, puis appuyez sur « Trouver mon panneau ».",
			usbDebugging: "Le débogage USB doit être activé dans les paramètres pour les développeurs du panneau.",
			usbDebuggingHelp: "Comment l’activer",
			connect: "Trouver mon panneau",
			allowHeading: "Autoriser cet ordinateur",
			allowBody: "Sur le panneau, cochez « Toujours autoriser cet ordinateur » et appuyez sur « Autoriser », pour que Panel Assistant puisse le mettre à jour plus tard sans le câble.",
			checkingPanel: "Vérification de votre panneau…",
			confirmHeading: "Prêt à installer",
			confirmBody: "L’application sera installée et deviendra l’écran d’accueil de votre panneau. Vos autres applications ne seront pas modifiées.",
			install: "Installer",
			restartedDifferentVersion: "Une tentative d’installation précédente utilisait une autre version. Cette tentative a été mise de côté.",
			alreadyInstalledHeading: "Déjà installée",
			alreadyInstalledBody: "Cette version est déjà sur votre panneau. Continuez pour terminer sa configuration ; rien ne sera copié ni réinstallé.",
			continueSetup: "Continuer",
			progressHeading: "Installation en cours",
			stepCopying: "Copie de l’application sur votre panneau…",
			stepFinishingCopy: "Fin de la copie…",
			stepInstalling: "Installation…",
			stepStarting: "Démarrage de l’application…",
			stepPermissions: "L’application reçoit ce qu’il lui faut pour fonctionner…",
			stepOpening: "Ouverture de la configuration de votre panneau…",
			keepConnected: "Gardez le câble branché jusqu’à la fin.",
			doneHeading: "Installée",
			doneOpening: "Ouverture de la configuration de votre panneau…",
			doneManual: "Terminez la configuration sur l’écran du panneau.",
			openSetup: "Ouvrir la configuration du panneau",
			errorHeading: "Cela n’a pas fonctionné",
			tryAgain: "Réessayer",
			backToHa: "Retour à Home Assistant",
			details: "Détails pour l’assistance",
			unsupported: "Ce navigateur ne peut pas communiquer avec les appareils USB. Ouvrez cette page dans Chrome ou Edge sur un ordinateur.",
			handoffFailure: "Impossible de récupérer l’application depuis Home Assistant. Retournez dans Home Assistant et recommencez.",
			noSelection: "Aucun panneau n’a été choisi. Appuyez sur « Trouver mon panneau » et sélectionnez-le dans la liste, ou essayez un autre câble : certains alimentent le panneau sans transmettre de données.",
			connectionTimeout: "Le panneau n’a pas répondu. Vérifiez que le câble est bien branché, ou changez-le : un câble peut alimenter le panneau sans transmettre de données.",
			disconnected: "Le panneau a été déconnecté. Rebranchez-le, attendez qu’il démarre, puis appuyez sur « Réessayer ».",
			cancelled: "Annulé.",
			pageClosed: "La page a été fermée.",
			progress: "Progression",
			installProgress: "Progression de l’installation",
			licenses: "Licences tierces",
			doneAnotherHomeAssistant: "Ce panneau a une session ouverte sur un autre Home Assistant. Changez de Home Assistant et connectez-vous sur le panneau pour le connecter ici.",
			reconnectedHeading: "Panel Assistant peut de nouveau joindre ce panneau",
			reconnectedBody: "Ce panneau a déjà l'application, rien n'a donc été installé. Home Assistant peut maintenant le mettre à jour via votre réseau."
		},
		errors: {
			installErrorGeneric: "Une erreur s’est produite. Gardez le panneau branché et appuyez sur « Réessayer ».",
			installErrorBusy: "Le panneau est occupé par une autre installation. Attendez une minute, puis réessayez.",
			installErrorStorage: "Ce navigateur n’a pas pu enregistrer sa progression. Autorisez ce site à stocker des données, puis réessayez.",
			installErrorTarget: "Un autre panneau a été connecté. Branchez le même panneau et réessayez.",
			installErrorArtifact: "Le téléchargement de l’application n’a pas pu être vérifié. Retournez dans Home Assistant et recommencez.",
			installErrorNotClean: "L’application est déjà sur ce panneau. Mettez-la plutôt à jour depuis Home Assistant.",
			installErrorIncompatible: "Ce panneau ne peut pas exécuter cette version de l’application.",
			installErrorConnection: "La connexion au panneau a été interrompue. Gardez-le branché et appuyez sur « Réessayer ».",
			installErrorHealth: "L’application est installée mais n’a pas encore démarré. Patientez un moment, puis réessayez.",
			installErrorPermissions: "L’application est installée, mais ses autorisations n’ont pas pu être confirmées. Vous pouvez réessayer sans risque : gardez le panneau branché et appuyez sur « Réessayer »."
		},
		journey: {
			version: "Version",
			connect: "Connecter",
			install: "Installer",
			setup: "Configurer"
		},
		sidebarReason: {
			update: "mise à jour",
			settings: "paramètres",
			recovery: "récupération",
			reboot: "redémarrage du système"
		},
		support: {
			stopped: "Arrêté",
			cause: "Cause",
			failedCheck: "Vérification échouée",
			error: "Erreur",
			release: "Version",
			panel: "Panneau",
			copied: "Copié",
			savedProgress: "Progression enregistrée",
			earlierCopy: "Copie précédente",
			stagedCopy: "Copie préparée",
			setAside: "Mis de côté",
			alreadyInstalled: "Déjà installée",
			permissions: "Autorisations",
			setupAddress: "Adresse de configuration",
			setupHandover: "Passage à la configuration",
			displaySizing: "Taille d’affichage et du texte",
			networkAdb: "ADB réseau",
			copiedDetail: "{bytes} octets envoyés ; en attente de confirmation du panneau",
			none: "aucun",
			setupMissing: "introuvable ; la configuration continue sur le panneau"
		}
	},
	it: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version} build {build}",
			menu: "Apri la navigazione",
			choosePanel: "Pannello",
			more: "Altre opzioni",
			addPanel: "Aggiungi pannello",
			integrationSettings: "Impostazioni dell’integrazione",
			device: "Dispositivo Home Assistant di questo pannello",
			showDevice: "Mostra dispositivo",
			github: "GitHub",
			unreachable: "non raggiungibile",
			restarting: "Riavvio in corso ({reason})",
			restartingNext: "Questo pannello tornerà disponibile automaticamente. Non è necessario aggiornare la pagina.",
			not_loaded: "non caricato",
			opening: "Apertura di {panel}…",
			loadingHint: "Un momento",
			empty: "Non è ancora collegato alcun pannello.",
			failed: "Non è stato possibile aprire il pannello. Riproveremo tra poco.",
			admin: "Questa pagina deve essere aperta da un amministratore.",
			unreachableBody: "Home Assistant non riesce a raggiungere questo pannello al momento.",
			notLoadedBody: "Questo pannello non è caricato in Home Assistant.",
			closed: "Questo pannello è stato chiuso.",
			frameTitle: "Interfaccia del pannello",
			picklesStory: "Pickles il panda è scappato e sta creando scompiglio. È lento e testardo, quindi potrebbe volerci un momento per riportarlo indietro.",
			unreachableNext: "Controlla che il pannello sia acceso e connesso alla tua rete.",
			notLoadedNext: "Apri Impostazioni dell’integrazione per controllare la connessione di questo pannello.",
			failedNext: "Attendi un momento mentre riproviamo, oppure scegli un altro pannello.",
			closedNext: "Scegli un altro pannello, oppure attendi che questo pannello si riconnetta.",
			pillOpening: "Apertura",
			pillUnreachable: "Non raggiungibile",
			pillNotLoaded: "Non caricato",
			pillRetrying: "Nuovo tentativo",
			pillClosed: "Chiuso"
		},
		haInstall: {
			title: "Installa Panel Assistant su un pannello",
			introduction: "Collega il pannello a questo computer con un cavo USB. Una nuova finestra lo troverà e installerà l’app.",
			release: "Versione",
			loading: "Caricamento delle versioni…",
			catalogError: "Non è stato possibile caricare l’elenco delle versioni.",
			empty: "Non ci sono ancora versioni disponibili. Riprova più tardi.",
			choose: "Versione consigliata",
			otherVersions: "Scegli un’altra versione",
			testing: "versione di prova",
			devBuild: "build di sviluppo",
			retry: "Riprova",
			start: "Continua",
			cancel: "Annulla",
			ready: "",
			unavailable: "Il programma di installazione non è disponibile. Aggiorna Panel Assistant, poi riprova.",
			admin: "Chiedi a un amministratore di Home Assistant di installare i pannelli.",
			waiting: "Continua nella nuova finestra.",
			preparing: "Preparazione dell’app…",
			downloading: "Preparazione dell’app…",
			verifying: "Preparazione dell’app…",
			verified: "Continua nella nuova finestra.",
			installed: "Panel Assistant è installato. Completa la configurazione del pannello nella finestra dell'installer, poi chiudi questa scheda.",
			cancelled: "Annullato.",
			popup_blocked: "Il browser ha bloccato la nuova finestra. Consenti i popup per questa pagina, poi premi Continua.",
			invalid_request: "Scegli prima una versione.",
			failed: "L’operazione non è riuscita. Premi Continua per riprovare."
		},
		installer: {
			title: "Configura il tuo pannello",
			preparing: "Preparazione dell’app…",
			preparingSlow: "La preparazione dell’app è ancora in corso. Tieni aperta la scheda di Home Assistant.",
			connectHeading: "Collega il tuo pannello",
			connectBody: "Collega il pannello a questo computer con un cavo USB, poi premi Trova il mio pannello.",
			usbDebugging: "Il debug USB deve essere attivo nelle impostazioni sviluppatore del pannello.",
			usbDebuggingHelp: "Come attivarlo",
			connect: "Trova il mio pannello",
			allowHeading: "Autorizza questo computer",
			allowBody: "Sul pannello, seleziona «Consenti sempre da questo computer» e tocca Consenti, così Panel Assistant potrà aggiornarlo in seguito senza il cavo.",
			checkingPanel: "Verifica del pannello…",
			confirmHeading: "Pronto per l’installazione",
			confirmBody: "Questa operazione installa l’app e la imposta come schermata iniziale del pannello. Le altre app non vengono modificate.",
			install: "Installa",
			restartedDifferentVersion: "Un precedente tentativo di installazione usava una versione diversa. Quel tentativo è stato accantonato.",
			alreadyInstalledHeading: "Già installata",
			alreadyInstalledBody: "Questa versione è già sul pannello. Continua per completare la configurazione; non viene copiato o reinstallato nulla.",
			continueSetup: "Continua",
			progressHeading: "Installazione in corso",
			stepCopying: "Copia dell’app sul pannello…",
			stepFinishingCopy: "Completamento della copia…",
			stepInstalling: "Installazione…",
			stepStarting: "Avvio dell’app…",
			stepPermissions: "Assegnazione all’app di ciò che le serve per funzionare…",
			stepOpening: "Apertura della configurazione del pannello…",
			keepConnected: "Lascia il cavo collegato fino al termine dell’operazione.",
			doneHeading: "Installata",
			doneOpening: "Apertura della configurazione del pannello…",
			doneManual: "Completa la configurazione sullo schermo del pannello.",
			openSetup: "Apri la configurazione del pannello",
			errorHeading: "L’operazione non è riuscita",
			tryAgain: "Riprova",
			backToHa: "Torna a Home Assistant",
			details: "Dettagli per l’assistenza",
			unsupported: "Questo browser non può comunicare con dispositivi USB. Apri questa pagina in Chrome o Edge su un computer.",
			handoffFailure: "Non è stato possibile recuperare l’app da Home Assistant. Torna a Home Assistant e ricomincia.",
			noSelection: "Non è stato scelto alcun pannello. Premi Trova il mio pannello e sceglilo dall’elenco, oppure prova un altro cavo: alcuni cavi alimentano il pannello ma non trasmettono dati.",
			connectionTimeout: "Il pannello non ha risposto. Controlla che il cavo sia ben inserito, oppure cambialo: un cavo può alimentare il pannello senza trasmettere dati.",
			disconnected: "Il pannello è stato scollegato. Ricollegalo, attendi che si avvii, poi premi Riprova.",
			cancelled: "Annullato.",
			pageClosed: "La pagina è stata chiusa.",
			progress: "Avanzamento",
			installProgress: "Avanzamento dell’installazione",
			licenses: "Licenze di terze parti",
			doneAnotherHomeAssistant: "Su questo pannello è attivo l'accesso a un altro Home Assistant. Cambia Home Assistant ed esegui l'accesso sul pannello per collegarlo qui.",
			reconnectedHeading: "Panel Assistant raggiunge di nuovo questo pannello",
			reconnectedBody: "Questo pannello ha già l'app, quindi non è stato installato nulla. Home Assistant ora può aggiornarlo tramite la tua rete."
		},
		errors: {
			installErrorGeneric: "Si è verificato un problema. Lascia il pannello collegato e premi Riprova.",
			installErrorBusy: "Il pannello è impegnato in un’altra installazione. Attendi un minuto, poi riprova.",
			installErrorStorage: "Questo browser non è riuscito a salvare l’avanzamento. Consenti a questo sito di memorizzare dati, poi riprova.",
			installErrorTarget: "È stato collegato un pannello diverso. Collega lo stesso pannello e riprova.",
			installErrorArtifact: "Non è stato possibile verificare il download dell’app. Torna a Home Assistant e ricomincia.",
			installErrorNotClean: "L’app è già presente su questo pannello. Aggiornala da Home Assistant.",
			installErrorIncompatible: "Questo pannello non può eseguire questa versione dell’app.",
			installErrorConnection: "La connessione al pannello si è interrotta. Lascialo collegato e premi Riprova.",
			installErrorHealth: "L’app è installata ma non si è ancora avviata. Attendi un momento, poi riprova.",
			installErrorPermissions: "L’app è installata, ma non è stato possibile confermarne le autorizzazioni. Riprovare è sicuro: lascia il pannello collegato e premi Riprova."
		},
		journey: {
			version: "Versione",
			connect: "Collega",
			install: "Installa",
			setup: "Configura"
		},
		sidebarReason: {
			update: "aggiornamento",
			settings: "impostazioni",
			recovery: "ripristino",
			reboot: "riavvio di sistema"
		},
		support: {
			stopped: "Interrotto",
			cause: "Causa",
			failedCheck: "Controllo non riuscito",
			error: "Errore",
			release: "Versione pubblicata",
			panel: "Pannello",
			copied: "Copiato",
			savedProgress: "Avanzamento salvato",
			earlierCopy: "Copia precedente",
			stagedCopy: "Copia preparata",
			setAside: "Accantonato",
			alreadyInstalled: "Già installata",
			permissions: "Autorizzazioni",
			setupAddress: "Indirizzo di configurazione",
			setupHandover: "Passaggio alla configurazione",
			displaySizing: "Dimensioni di visualizzazione e del testo",
			networkAdb: "ADB di rete",
			copiedDetail: "{bytes} byte inviati; in attesa della conferma del pannello",
			none: "nessuno",
			setupMissing: "non trovato; la configurazione prosegue sul pannello"
		}
	},
	nl: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version} build {build}",
			menu: "Navigatie openen",
			choosePanel: "Paneel",
			more: "Meer opties",
			addPanel: "Paneel toevoegen",
			integrationSettings: "Integratie-instellingen",
			device: "Het Home Assistant-apparaat van dit paneel",
			showDevice: "Apparaat tonen",
			github: "GitHub",
			unreachable: "onbereikbaar",
			restarting: "Opnieuw starten ({reason})",
			restartingNext: "Dit paneel komt automatisch terug. Je hoeft de pagina niet te vernieuwen.",
			not_loaded: "niet geladen",
			opening: "{panel} openen…",
			loadingHint: "Een ogenblik",
			empty: "Er zijn nog geen panelen gekoppeld.",
			failed: "Het paneel kon niet worden geopend. Er wordt binnenkort opnieuw geprobeerd.",
			admin: "Een beheerder moet deze pagina openen.",
			unreachableBody: "Home Assistant kan dit paneel momenteel niet bereiken.",
			notLoadedBody: "Dit paneel is niet geladen in Home Assistant.",
			closed: "Dit paneel is gesloten.",
			frameTitle: "Paneelinterface",
			picklesStory: "Pickles de panda is ontsnapt en richt een ravage aan. Hij is traag en koppig, dus het kan even duren om hem terug te krijgen.",
			unreachableNext: "Controleer of het paneel aanstaat en is verbonden met je netwerk.",
			notLoadedNext: "Open Integratie-instellingen om de verbinding van dit paneel te controleren.",
			failedNext: "Wacht even terwijl we het opnieuw proberen, of kies een ander paneel.",
			closedNext: "Kies een ander paneel of wacht tot dit paneel opnieuw verbinding maakt.",
			pillOpening: "Openen",
			pillUnreachable: "Onbereikbaar",
			pillNotLoaded: "Niet geladen",
			pillRetrying: "Opnieuw proberen",
			pillClosed: "Gesloten"
		},
		haInstall: {
			title: "Panel Assistant op een paneel installeren",
			introduction: "Sluit het paneel met een USB-kabel aan op deze computer. Een nieuw venster zoekt het paneel en installeert de app.",
			release: "Versie",
			loading: "Versies laden…",
			catalogError: "De lijst met versies kon niet worden geladen.",
			empty: "Er zijn nog geen versies beschikbaar. Probeer het later opnieuw.",
			choose: "Aanbevolen versie",
			otherVersions: "Een andere versie kiezen",
			testing: "testversie",
			devBuild: "ontwikkelbuild",
			retry: "Opnieuw proberen",
			start: "Doorgaan",
			cancel: "Annuleren",
			ready: "",
			unavailable: "Het installatieprogramma is niet beschikbaar. Werk Panel Assistant bij en probeer het opnieuw.",
			admin: "Vraag een Home Assistant-beheerder om panelen te installeren.",
			waiting: "Ga verder in het nieuwe venster.",
			preparing: "App voorbereiden…",
			downloading: "App voorbereiden…",
			verifying: "App voorbereiden…",
			verified: "Ga verder in het nieuwe venster.",
			installed: "Panel Assistant is geïnstalleerd. Rond de installatie van het paneel af in het installatievenster en sluit dan dit tabblad.",
			cancelled: "Geannuleerd.",
			popup_blocked: "De browser heeft het nieuwe venster geblokkeerd. Sta pop-ups toe voor deze pagina en druk op Doorgaan.",
			invalid_request: "Kies eerst een versie.",
			failed: "Dat is niet gelukt. Druk op Doorgaan om het opnieuw te proberen."
		},
		installer: {
			title: "Je paneel instellen",
			preparing: "App voorbereiden…",
			preparingSlow: "De app wordt nog voorbereid. Houd het Home Assistant-tabblad open.",
			connectHeading: "Je paneel verbinden",
			connectBody: "Sluit het paneel met een USB-kabel aan op deze computer en druk op Mijn paneel zoeken.",
			usbDebugging: "USB-foutopsporing moet aan staan in de ontwikkelaarsopties van het paneel.",
			usbDebuggingHelp: "Zo zet je het aan",
			connect: "Mijn paneel zoeken",
			allowHeading: "Deze computer toestaan",
			allowBody: "Vink op het paneel ‘Altijd toestaan vanaf deze computer’ aan en tik op Toestaan, zodat Panel Assistant het later zonder kabel kan bijwerken.",
			checkingPanel: "Je paneel controleren…",
			confirmHeading: "Klaar om te installeren",
			confirmBody: "Hiermee installeer je de app en maak je die het startscherm van je paneel. Je andere apps worden niet aangeraakt.",
			install: "Installeren",
			restartedDifferentVersion: "Een eerdere installatiepoging gebruikte een andere uitgave. Die poging is terzijde gelegd.",
			alreadyInstalledHeading: "Al geïnstalleerd",
			alreadyInstalledBody: "Deze versie staat al op je paneel. Ga door om het instellen af te ronden; er wordt niets gekopieerd of opnieuw geïnstalleerd.",
			continueSetup: "Doorgaan",
			progressHeading: "Installeren",
			stepCopying: "App naar je paneel kopiëren…",
			stepFinishingCopy: "Kopiëren afronden…",
			stepInstalling: "Installeren…",
			stepStarting: "App starten…",
			stepPermissions: "De app geven wat die nodig heeft om te werken…",
			stepOpening: "Paneelconfiguratie openen…",
			keepConnected: "Laat de kabel aangesloten totdat dit klaar is.",
			doneHeading: "Geïnstalleerd",
			doneOpening: "Je wordt naar de paneelconfiguratie geleid…",
			doneManual: "Rond het instellen af op het scherm van het paneel.",
			openSetup: "Paneelconfiguratie openen",
			errorHeading: "Dat is niet gelukt",
			tryAgain: "Opnieuw proberen",
			backToHa: "Terug naar Home Assistant",
			details: "Details voor ondersteuning",
			unsupported: "Deze browser kan niet communiceren met USB-apparaten. Open deze pagina in Chrome of Edge op een computer.",
			handoffFailure: "De app kon niet worden opgehaald uit Home Assistant. Ga terug naar Home Assistant en begin opnieuw.",
			noSelection: "Er is geen paneel gekozen. Druk op Mijn paneel zoeken en kies het in de lijst, of probeer een andere kabel: sommige kabels voeden het paneel zonder gegevens over te dragen.",
			connectionTimeout: "Het paneel antwoordde niet. Controleer of de kabel goed vastzit, of vervang die: een kabel kan het paneel voeden zonder gegevens over te dragen.",
			disconnected: "Het paneel is losgekoppeld. Sluit het weer aan, wacht tot het start en druk op Opnieuw proberen.",
			cancelled: "Geannuleerd.",
			pageClosed: "De pagina is gesloten.",
			progress: "Voortgang",
			installProgress: "Installatievoortgang",
			licenses: "Licenties van derden",
			doneAnotherHomeAssistant: "Dit paneel is aangemeld bij een andere Home Assistant. Wijzig op het paneel Home Assistant en meld je aan om het hier te verbinden.",
			reconnectedHeading: "Panel Assistant kan dit paneel weer bereiken",
			reconnectedBody: "Dit paneel heeft de app al, dus er is niets geïnstalleerd. Home Assistant kan het nu via je netwerk bijwerken."
		},
		errors: {
			installErrorGeneric: "Er ging iets mis. Laat het paneel aangesloten en druk op Opnieuw proberen.",
			installErrorBusy: "Het paneel is bezig met een andere installatie. Wacht een minuut en probeer het opnieuw.",
			installErrorStorage: "Deze browser kon de voortgang niet opslaan. Sta toe dat deze site gegevens opslaat en probeer het opnieuw.",
			installErrorTarget: "Er is een ander paneel aangesloten. Sluit hetzelfde paneel aan en probeer het opnieuw.",
			installErrorArtifact: "De app-download kon niet worden gecontroleerd. Ga terug naar Home Assistant en begin opnieuw.",
			installErrorNotClean: "De app staat al op dit paneel. Werk de app in plaats daarvan bij vanuit Home Assistant.",
			installErrorIncompatible: "Dit paneel kan deze versie van de app niet uitvoeren.",
			installErrorConnection: "De verbinding met het paneel is verbroken. Laat het aangesloten en druk op Opnieuw proberen.",
			installErrorHealth: "De app is geïnstalleerd, maar nog niet gestart. Wacht even en probeer het opnieuw.",
			installErrorPermissions: "De app is geïnstalleerd, maar de machtigingen konden niet worden bevestigd. Opnieuw proberen is veilig: laat het paneel aangesloten en druk op Opnieuw proberen."
		},
		journey: {
			version: "Versie",
			connect: "Verbinden",
			install: "Installeren",
			setup: "Instellen"
		},
		sidebarReason: {
			update: "update",
			settings: "instellingen",
			recovery: "herstel",
			reboot: "herstart"
		},
		support: {
			stopped: "Gestopt",
			cause: "Oorzaak",
			failedCheck: "Mislukte controle",
			error: "Fout",
			release: "Uitgave",
			panel: "Paneel",
			copied: "Gekopieerd",
			savedProgress: "Opgeslagen voortgang",
			earlierCopy: "Eerdere kopie",
			stagedCopy: "Klaargezette kopie",
			setAside: "Terzijde gelegd",
			alreadyInstalled: "Al geïnstalleerd",
			permissions: "Machtigingen",
			setupAddress: "Configuratieadres",
			setupHandover: "Configuratieoverdracht",
			displaySizing: "Weergave- en tekstgrootte",
			networkAdb: "ADB via netwerk",
			copiedDetail: "{bytes} bytes verstuurd; wachten op bevestiging van het paneel",
			none: "geen",
			setupMissing: "niet gevonden; instellen gaat verder op het paneel"
		}
	},
	pl: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version} kompilacja {build}",
			menu: "Otwórz nawigację",
			choosePanel: "Panel",
			more: "Więcej opcji",
			addPanel: "Dodaj panel",
			integrationSettings: "Ustawienia integracji",
			device: "Urządzenie tego panelu w Home Assistant",
			showDevice: "Pokaż urządzenie",
			github: "GitHub",
			unreachable: "nieosiągalny",
			restarting: "Ponowne uruchamianie ({reason})",
			restartingNext: "Ten panel automatycznie stanie się ponownie dostępny. Nie trzeba odświeżać strony.",
			not_loaded: "niewczytany",
			opening: "Otwieranie panelu {panel}…",
			loadingHint: "Chwileczkę",
			empty: "Nie podłączono jeszcze żadnych paneli.",
			failed: "Nie udało się otworzyć panelu. Wkrótce nastąpi kolejna próba.",
			admin: "Tę stronę musi otworzyć administrator.",
			unreachableBody: "Home Assistant nie może teraz połączyć się z tym panelem.",
			notLoadedBody: "Ten panel nie jest wczytany w Home Assistant.",
			closed: "Ten panel został zamknięty.",
			frameTitle: "Interfejs panelu",
			picklesStory: "Panda Pickles uciekł i rozrabia. Jest powolny i uparty, więc sprowadzenie go z powrotem może chwilę potrwać.",
			unreachableNext: "Sprawdź, czy panel jest włączony i połączony z twoją siecią.",
			notLoadedNext: "Otwórz Ustawienia integracji, aby sprawdzić połączenie tego panelu.",
			failedNext: "Poczekaj chwilę, aż spróbujemy ponownie, lub wybierz inny panel.",
			closedNext: "Wybierz inny panel lub poczekaj, aż ten panel połączy się ponownie.",
			pillOpening: "Otwieranie",
			pillUnreachable: "Nieosiągalny",
			pillNotLoaded: "Niewczytany",
			pillRetrying: "Ponawianie",
			pillClosed: "Zamknięty"
		},
		haInstall: {
			title: "Zainstaluj Panel Assistant na panelu",
			introduction: "Podłącz panel do tego komputera kablem USB. Nowe okno znajdzie panel i zainstaluje aplikację.",
			release: "Wersja",
			loading: "Wczytywanie wersji…",
			catalogError: "Nie udało się wczytać listy wersji.",
			empty: "Nie ma jeszcze dostępnych wersji. Spróbuj ponownie później.",
			choose: "Zalecana wersja",
			otherVersions: "Wybierz inną wersję",
			testing: "wersja testowa",
			devBuild: "kompilacja deweloperska",
			retry: "Spróbuj ponownie",
			start: "Kontynuuj",
			cancel: "Anuluj",
			ready: "",
			unavailable: "Instalator jest niedostępny. Zaktualizuj Panel Assistant, a potem spróbuj ponownie.",
			admin: "Poproś administratora Home Assistant o zainstalowanie paneli.",
			waiting: "Kontynuuj w nowym oknie.",
			preparing: "Przygotowywanie aplikacji…",
			downloading: "Przygotowywanie aplikacji…",
			verifying: "Przygotowywanie aplikacji…",
			verified: "Kontynuuj w nowym oknie.",
			installed: "Panel Assistant jest zainstalowany. Dokończ konfigurację panelu w oknie instalatora, a następnie zamknij tę kartę.",
			cancelled: "Anulowano.",
			popup_blocked: "Przeglądarka zablokowała nowe okno. Zezwól na wyskakujące okna dla tej strony, a potem naciśnij Kontynuuj.",
			invalid_request: "Najpierw wybierz wersję.",
			failed: "Nie udało się. Naciśnij Kontynuuj, aby spróbować ponownie."
		},
		installer: {
			title: "Skonfiguruj swój panel",
			preparing: "Przygotowywanie aplikacji…",
			preparingSlow: "Aplikacja nadal jest przygotowywana. Zostaw otwartą kartę Home Assistant.",
			connectHeading: "Podłącz swój panel",
			connectBody: "Podłącz panel do tego komputera kablem USB, a potem naciśnij Znajdź mój panel.",
			usbDebugging: "Debugowanie USB musi być włączone w opcjach programisty panelu.",
			usbDebuggingHelp: "Jak je włączyć",
			connect: "Znajdź mój panel",
			allowHeading: "Zezwól temu komputerowi",
			allowBody: "Na panelu zaznacz „Zawsze zezwalaj z tego komputera” i dotknij Zezwól, aby Panel Assistant mógł go później aktualizować bez kabla.",
			checkingPanel: "Sprawdzanie panelu…",
			confirmHeading: "Gotowe do instalacji",
			confirmBody: "To zainstaluje aplikację i ustawi ją jako ekran główny panelu. Pozostałe aplikacje pozostaną nietknięte.",
			install: "Zainstaluj",
			restartedDifferentVersion: "Wcześniejsza próba instalacji używała innego wydania. Ta próba została odłożona na bok.",
			alreadyInstalledHeading: "Już zainstalowano",
			alreadyInstalledBody: "Ta wersja jest już na twoim panelu. Kontynuuj, aby dokończyć konfigurację; nic nie zostanie skopiowane ani zainstalowane ponownie.",
			continueSetup: "Kontynuuj",
			progressHeading: "Instalowanie",
			stepCopying: "Kopiowanie aplikacji na panel…",
			stepFinishingCopy: "Kończenie kopiowania…",
			stepInstalling: "Instalowanie…",
			stepStarting: "Uruchamianie aplikacji…",
			stepPermissions: "Zapewnianie aplikacji warunków do działania…",
			stepOpening: "Otwieranie konfiguracji panelu…",
			keepConnected: "Zostaw kabel podłączony do zakończenia.",
			doneHeading: "Zainstalowano",
			doneOpening: "Przechodzenie do konfiguracji panelu…",
			doneManual: "Dokończ konfigurację na ekranie panelu.",
			openSetup: "Otwórz konfigurację panelu",
			errorHeading: "Nie udało się",
			tryAgain: "Spróbuj ponownie",
			backToHa: "Wróć do Home Assistant",
			details: "Szczegóły dla pomocy technicznej",
			unsupported: "Ta przeglądarka nie może komunikować się z urządzeniami USB. Otwórz tę stronę w Chrome lub Edge na komputerze.",
			handoffFailure: "Nie udało się pobrać aplikacji z Home Assistant. Wróć do Home Assistant i zacznij od nowa.",
			noSelection: "Nie wybrano panelu. Naciśnij Znajdź mój panel i wybierz go z listy lub spróbuj innego kabla: niektóre kable zasilają panel, ale nie przesyłają danych.",
			connectionTimeout: "Panel nie odpowiedział. Sprawdź, czy kabel jest dobrze wpięty, lub wymień go: kabel może zasilać panel, a mimo to nie przesyłać danych.",
			disconnected: "Panel został odłączony. Podłącz go ponownie, poczekaj na jego uruchomienie, a potem naciśnij Spróbuj ponownie.",
			cancelled: "Anulowano.",
			pageClosed: "Strona została zamknięta.",
			progress: "Postęp",
			installProgress: "Postęp instalacji",
			licenses: "Licencje innych podmiotów",
			doneAnotherHomeAssistant: "Ten panel jest zalogowany do innej instancji Home Assistant. Zmień na panelu instancję Home Assistant i zaloguj się, aby połączyć go tutaj.",
			reconnectedHeading: "Panel Assistant znów ma dostęp do tego panelu",
			reconnectedBody: "Ten panel ma już aplikację, więc nic nie zostało zainstalowane. Home Assistant może go teraz aktualizować przez twoją sieć."
		},
		errors: {
			installErrorGeneric: "Coś poszło nie tak. Zostaw panel podłączony i naciśnij Spróbuj ponownie.",
			installErrorBusy: "Panel jest zajęty inną instalacją. Poczekaj minutę, a potem spróbuj ponownie.",
			installErrorStorage: "Przeglądarka nie mogła zapisać postępu. Zezwól tej stronie na zapisywanie danych, a potem spróbuj ponownie.",
			installErrorTarget: "Podłączono inny panel. Podłącz ten sam panel co wcześniej i spróbuj ponownie.",
			installErrorArtifact: "Nie udało się sprawdzić pobranej aplikacji. Wróć do Home Assistant i zacznij od nowa.",
			installErrorNotClean: "Ten panel ma już aplikację. Zamiast instalować, zaktualizuj ją z Home Assistant.",
			installErrorIncompatible: "Ten panel nie może uruchomić tej wersji aplikacji.",
			installErrorConnection: "Połączenie z panelem zostało przerwane. Zostaw go podłączonego i naciśnij Spróbuj ponownie.",
			installErrorHealth: "Aplikacja jest zainstalowana, ale jeszcze się nie uruchomiła. Poczekaj chwilę, a potem spróbuj ponownie.",
			installErrorPermissions: "Aplikacja jest zainstalowana, ale nie udało się potwierdzić jej uprawnień. Ponowna próba jest bezpieczna: zostaw panel podłączony i naciśnij Spróbuj ponownie."
		},
		journey: {
			version: "Wersja",
			connect: "Połącz",
			install: "Zainstaluj",
			setup: "Skonfiguruj"
		},
		sidebarReason: {
			update: "aktualizacja",
			settings: "ustawienia",
			recovery: "odzyskiwanie",
			reboot: "restart systemu"
		},
		support: {
			stopped: "Zatrzymano",
			cause: "Przyczyna",
			failedCheck: "Nieudane sprawdzenie",
			error: "Błąd",
			release: "Wydanie",
			panel: "Panel",
			copied: "Skopiowano",
			savedProgress: "Zapisany postęp",
			earlierCopy: "Wcześniejsza kopia",
			stagedCopy: "Przygotowana kopia",
			setAside: "Odłożono",
			alreadyInstalled: "Już zainstalowano",
			permissions: "Uprawnienia",
			setupAddress: "Adres konfiguracji",
			setupHandover: "Przekazanie konfiguracji",
			displaySizing: "Rozmiar wyświetlania i tekstu",
			networkAdb: "ADB przez sieć",
			copiedDetail: "Wysłano {bytes} bajtów; oczekiwanie na potwierdzenie panelu",
			none: "brak",
			setupMissing: "nie znaleziono; konfiguracja trwa dalej na panelu"
		}
	},
	"pt-BR": {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version} compilação {build}",
			menu: "Abrir navegação",
			choosePanel: "Painel",
			more: "Mais opções",
			addPanel: "Adicionar painel",
			integrationSettings: "Configurações da integração",
			device: "Dispositivo deste painel no Home Assistant",
			showDevice: "Mostrar dispositivo",
			github: "GitHub",
			unreachable: "inacessível",
			restarting: "Reiniciando ({reason})",
			restartingNext: "Este painel voltará automaticamente. Não é necessário atualizar a página.",
			not_loaded: "não carregado",
			opening: "Abrindo {panel}…",
			loadingHint: "Só um momento",
			empty: "Ainda não há painéis vinculados.",
			failed: "Não foi possível abrir o painel. Uma nova tentativa será feita em breve.",
			admin: "Um administrador precisa abrir esta página.",
			unreachableBody: "O Home Assistant não consegue acessar este painel no momento.",
			notLoadedBody: "Este painel não está carregado no Home Assistant.",
			closed: "Este painel foi fechado.",
			frameTitle: "Interface do painel",
			picklesStory: "Pickles, o panda, escapou e está fazendo uma bagunça. Ele é lento e teimoso, então trazê-lo de volta pode levar um tempinho.",
			unreachableNext: "Verifique se o painel está ligado e conectado à sua rede.",
			notLoadedNext: "Abra Configurações da integração para verificar a conexão deste painel.",
			failedNext: "Aguarde um momento enquanto tentamos novamente ou escolha outro painel.",
			closedNext: "Escolha outro painel ou aguarde a reconexão deste painel.",
			pillOpening: "Abrindo",
			pillUnreachable: "Inacessível",
			pillNotLoaded: "Não carregado",
			pillRetrying: "Tentando novamente",
			pillClosed: "Fechado"
		},
		haInstall: {
			title: "Instalar Panel Assistant em um painel",
			introduction: "Conecte o painel a este computador com um cabo USB. Uma nova janela encontrará o painel e instalará o aplicativo.",
			release: "Versão",
			loading: "Carregando versões…",
			catalogError: "Não foi possível carregar a lista de versões.",
			empty: "Ainda não há versões disponíveis. Tente novamente mais tarde.",
			choose: "Versão recomendada",
			otherVersions: "Escolher outra versão",
			testing: "versão de teste",
			devBuild: "compilação de desenvolvimento",
			retry: "Tentar novamente",
			start: "Continuar",
			cancel: "Cancelar",
			ready: "",
			unavailable: "O instalador não está disponível. Atualize o Panel Assistant e tente novamente.",
			admin: "Peça a um administrador do Home Assistant para instalar os painéis.",
			waiting: "Continue na nova janela.",
			preparing: "Preparando o aplicativo…",
			downloading: "Preparando o aplicativo…",
			verifying: "Preparando o aplicativo…",
			verified: "Continue na nova janela.",
			installed: "O Panel Assistant está instalado. Conclua a configuração do painel na janela do instalador e depois feche esta guia.",
			cancelled: "Cancelado.",
			popup_blocked: "Seu navegador bloqueou a nova janela. Permita pop-ups nesta página e pressione Continuar.",
			invalid_request: "Escolha uma versão primeiro.",
			failed: "Não deu certo. Pressione Continuar para tentar novamente."
		},
		installer: {
			title: "Configure seu painel",
			preparing: "Preparando o aplicativo…",
			preparingSlow: "Ainda estamos preparando o aplicativo. Mantenha a aba do Home Assistant aberta.",
			connectHeading: "Conecte seu painel",
			connectBody: "Conecte o painel a este computador com um cabo USB e pressione Encontrar meu painel.",
			usbDebugging: "A depuração USB deve estar ativada nas configurações de desenvolvedor do painel.",
			usbDebuggingHelp: "Como ativar",
			connect: "Encontrar meu painel",
			allowHeading: "Permita este computador",
			allowBody: "No painel, marque “Sempre permitir a partir deste computador” e toque em Permitir, para que o Panel Assistant possa atualizá-lo depois sem o cabo.",
			checkingPanel: "Verificando seu painel…",
			confirmHeading: "Pronto para instalar",
			confirmBody: "Isso instala o aplicativo e o torna a tela inicial do seu painel. Seus outros aplicativos não são alterados.",
			install: "Instalar",
			restartedDifferentVersion: "Uma tentativa anterior de instalação usou uma versão diferente. Essa tentativa foi deixada de lado.",
			alreadyInstalledHeading: "Já instalado",
			alreadyInstalledBody: "Esta versão já está no seu painel. Continue para concluir a configuração; nada é copiado ou reinstalado.",
			continueSetup: "Continuar",
			progressHeading: "Instalando",
			stepCopying: "Copiando o aplicativo para seu painel…",
			stepFinishingCopy: "Concluindo a cópia…",
			stepInstalling: "Instalando…",
			stepStarting: "Iniciando o aplicativo…",
			stepPermissions: "Concedendo ao aplicativo o que ele precisa para funcionar…",
			stepOpening: "Abrindo a configuração do seu painel…",
			keepConnected: "Mantenha o cabo conectado até terminar.",
			doneHeading: "Instalado",
			doneOpening: "Levando você à configuração do seu painel…",
			doneManual: "Conclua a configuração na tela do painel.",
			openSetup: "Abrir configuração do painel",
			errorHeading: "Não deu certo",
			tryAgain: "Tentar novamente",
			backToHa: "Voltar ao Home Assistant",
			details: "Detalhes para suporte",
			unsupported: "Este navegador não consegue se comunicar com dispositivos USB. Abra esta página no Chrome ou no Edge em um computador.",
			handoffFailure: "Não foi possível obter o aplicativo do Home Assistant. Volte ao Home Assistant e recomece.",
			noSelection: "Nenhum painel foi escolhido. Pressione Encontrar meu painel e selecione-o na lista ou tente outro cabo: alguns cabos alimentam o painel, mas não transmitem dados.",
			connectionTimeout: "O painel não respondeu. Verifique se o cabo está bem conectado ou troque-o: um cabo pode alimentar o painel e ainda assim não transmitir dados.",
			disconnected: "O painel foi desconectado. Conecte-o novamente, aguarde a inicialização e pressione Tentar novamente.",
			cancelled: "Cancelado.",
			pageClosed: "A página foi fechada.",
			progress: "Progresso",
			installProgress: "Progresso da instalação",
			licenses: "Licenças de terceiros",
			doneAnotherHomeAssistant: "Este painel está atualmente com sessão iniciada em outro Home Assistant. Troque o Home Assistant e faça login no painel para conectá-lo aqui.",
			reconnectedHeading: "O Panel Assistant alcança este painel de novo",
			reconnectedBody: "Este painel já tem o app, então nada foi instalado. O Home Assistant agora pode atualizá-lo pela sua rede."
		},
		errors: {
			installErrorGeneric: "Algo deu errado. Mantenha o painel conectado e pressione Tentar novamente.",
			installErrorBusy: "O painel está ocupado com outra instalação. Aguarde um minuto e tente novamente.",
			installErrorStorage: "Este navegador não conseguiu salvar o progresso. Permita que este site armazene dados e tente novamente.",
			installErrorTarget: "Outro painel foi conectado. Conecte o mesmo painel e tente novamente.",
			installErrorArtifact: "Não foi possível verificar o download do aplicativo. Volte ao Home Assistant e recomece.",
			installErrorNotClean: "Este painel já tem o aplicativo. Atualize-o pelo Home Assistant.",
			installErrorIncompatible: "Este painel não consegue executar esta versão do aplicativo.",
			installErrorConnection: "A conexão com o painel caiu. Mantenha-o conectado e pressione Tentar novamente.",
			installErrorHealth: "O aplicativo está instalado, mas ainda não iniciou. Aguarde um momento e tente novamente.",
			installErrorPermissions: "O aplicativo está instalado, mas não foi possível confirmar as permissões dele. Tentar de novo é seguro: mantenha o painel conectado e pressione Tentar novamente."
		},
		journey: {
			version: "Versão",
			connect: "Conectar",
			install: "Instalar",
			setup: "Configurar"
		},
		sidebarReason: {
			update: "atualização",
			settings: "configurações",
			recovery: "recuperação",
			reboot: "reinicialização"
		},
		support: {
			stopped: "Interrompido",
			cause: "Causa",
			failedCheck: "Verificação com falha",
			error: "Erro",
			release: "Versão",
			panel: "Painel",
			copied: "Copiado",
			savedProgress: "Progresso salvo",
			earlierCopy: "Cópia anterior",
			stagedCopy: "Cópia preparada",
			setAside: "Deixado de lado",
			alreadyInstalled: "Já instalado",
			permissions: "Permissões",
			setupAddress: "Endereço de configuração",
			setupHandover: "Transferência da configuração",
			displaySizing: "Escala de exibição e tamanho do texto",
			networkAdb: "ADB pela rede",
			copiedDetail: "{bytes} bytes enviados; aguardando a confirmação do painel",
			none: "nenhum",
			setupMissing: "não encontrado; a configuração continua no painel"
		}
	},
	uk: {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version}, збірка {build}",
			menu: "Відкрити навігацію",
			choosePanel: "Панель",
			more: "Інші параметри",
			addPanel: "Додати панель",
			integrationSettings: "Налаштування інтеграції",
			device: "Пристрій цієї панелі в Home Assistant",
			showDevice: "Показати пристрій",
			github: "GitHub",
			unreachable: "недоступна",
			restarting: "Перезапускається ({reason})",
			restartingNext: "Ця панель повернеться автоматично. Оновлювати сторінку не потрібно.",
			not_loaded: "не завантажено",
			opening: "Відкриття {panel}…",
			loadingHint: "Зачекайте мить",
			empty: "Панелей ще не приєднано.",
			failed: "Не вдалося відкрити панель. Незабаром спробу буде повторено.",
			admin: "Цю сторінку має відкрити адміністратор.",
			unreachableBody: "Home Assistant зараз не може зв’язатися з цією панеллю.",
			notLoadedBody: "Цю панель не завантажено в Home Assistant.",
			closed: "Цю панель було закрито.",
			frameTitle: "Інтерфейс панелі",
			picklesStory: "Панда Pickles утік і спричиняє безлад. Він повільний та впертий, тому його повернення може зайняти трохи часу.",
			unreachableNext: "Перевірте, чи панель увімкнена та під’єднана до вашої мережі.",
			notLoadedNext: "Відкрийте «Налаштування інтеграції», щоб перевірити з’єднання з цією панеллю.",
			failedNext: "Зачекайте трохи, поки ми повторимо спробу, або виберіть іншу панель.",
			closedNext: "Виберіть іншу панель або зачекайте, поки ця панель під’єднається знову.",
			pillOpening: "Відкриття",
			pillUnreachable: "Недоступна",
			pillNotLoaded: "Не завантажено",
			pillRetrying: "Повторна спроба",
			pillClosed: "Закрито"
		},
		haInstall: {
			title: "Встановити Panel Assistant на панель",
			introduction: "Під’єднайте панель до цього комп’ютера кабелем USB. Нове вікно знайде її та встановить програму.",
			release: "Версія",
			loading: "Завантаження версій…",
			catalogError: "Не вдалося завантажити список версій.",
			empty: "Поки що немає доступних версій. Спробуйте ще раз пізніше.",
			choose: "Рекомендована версія",
			otherVersions: "Обрати іншу версію",
			testing: "Тестова версія",
			devBuild: "Збірка для розробки",
			retry: "Спробувати ще раз",
			start: "Продовжити",
			cancel: "Скасувати",
			ready: "",
			unavailable: "Засіб встановлення недоступний. Оновіть Panel Assistant і повторіть спробу.",
			admin: "Попросіть адміністратора Home Assistant встановити панелі.",
			waiting: "Продовжуйте в новому вікні.",
			preparing: "Підготовка програми…",
			downloading: "Підготовка програми…",
			verifying: "Підготовка програми…",
			verified: "Продовжуйте в новому вікні.",
			installed: "Panel Assistant установлено. Завершіть налаштування панелі у вікні інсталятора, а потім закрийте цю вкладку.",
			cancelled: "Скасовано.",
			popup_blocked: "Браузер заблокував нове вікно. Дозвольте спливні вікна для цієї сторінки та натисніть «Продовжити».",
			invalid_request: "Спочатку виберіть версію.",
			failed: "Не вдалося. Натисніть «Продовжити», щоб спробувати ще раз."
		},
		installer: {
			title: "Налаштуйте панель",
			preparing: "Підготовка програми…",
			preparingSlow: "Підготовка програми ще триває. Залиште вкладку Home Assistant відкритою.",
			connectHeading: "Під’єднайте панель",
			connectBody: "Під’єднайте панель до цього комп’ютера кабелем USB, а потім натисніть «Знайти мою панель».",
			usbDebugging: "Налагодження USB має бути ввімкнено в налаштуваннях розробника на панелі.",
			usbDebuggingHelp: "Як це ввімкнути",
			connect: "Знайти мою панель",
			allowHeading: "Дозволити цьому комп’ютеру",
			allowBody: "На панелі позначте «Завжди дозволяти з цього комп’ютера» й торкніться «Дозволити», щоб Panel Assistant міг пізніше оновлювати її без кабелю.",
			checkingPanel: "Перевірка панелі…",
			confirmHeading: "Готово до встановлення",
			confirmBody: "Це встановить програму й зробить її головним екраном панелі. Інші ваші програми не зачіпатимуться.",
			install: "Встановити",
			restartedDifferentVersion: "Попередня спроба встановлення використовувала інший випуск. Цю спробу відкладено вбік.",
			alreadyInstalledHeading: "Уже встановлено",
			alreadyInstalledBody: "Ця версія вже є на панелі. Продовжуйте, щоб завершити її налаштування; нічого не копіюється й не перевстановлюється.",
			continueSetup: "Продовжити",
			progressHeading: "Встановлення",
			stepCopying: "Копіювання програми на панель…",
			stepFinishingCopy: "Завершення копіювання…",
			stepInstalling: "Встановлення…",
			stepStarting: "Запуск програми…",
			stepPermissions: "Надання програмі всього необхідного для роботи…",
			stepOpening: "Відкриття налаштування панелі…",
			keepConnected: "Залиште кабель під’єднаним до завершення.",
			doneHeading: "Встановлено",
			doneOpening: "Перехід до налаштування панелі…",
			doneManual: "Завершіть налаштування на екрані панелі.",
			openSetup: "Відкрити налаштування панелі",
			errorHeading: "Не вдалося",
			tryAgain: "Спробувати ще раз",
			backToHa: "Назад до Home Assistant",
			details: "Подробиці для служби підтримки",
			unsupported: "Цей браузер не може взаємодіяти з пристроями USB. Відкрийте цю сторінку в Chrome або Edge на комп’ютері.",
			handoffFailure: "Не вдалося отримати програму з Home Assistant. Поверніться до Home Assistant і почніть знову.",
			noSelection: "Панель не вибрано. Натисніть «Знайти мою панель» і виберіть її зі списку або спробуйте інший кабель: деякі кабелі живлять панель, але не передають даних.",
			connectionTimeout: "Панель не відповіла. Перевірте, чи кабель щільно під’єднано, або замініть його: кабель може живити панель, але не передавати даних.",
			disconnected: "Панель було від’єднано. Під’єднайте її знову, дочекайтеся запуску й натисніть «Спробувати ще раз».",
			cancelled: "Скасовано.",
			pageClosed: "Сторінку було закрито.",
			progress: "Перебіг",
			installProgress: "Перебіг встановлення",
			licenses: "Ліцензії сторонніх компонентів",
			doneAnotherHomeAssistant: "На цій панелі виконано вхід в інший екземпляр Home Assistant. Змініть на панелі екземпляр Home Assistant і увійдіть, щоб підключити її тут.",
			reconnectedHeading: "Panel Assistant знову має доступ до цієї панелі",
			reconnectedBody: "Застосунок уже є на цій панелі, тож нічого не встановлено. Тепер Home Assistant може оновлювати її через вашу мережу."
		},
		errors: {
			installErrorGeneric: "Щось пішло не так. Залиште панель під’єднаною та натисніть «Спробувати ще раз».",
			installErrorBusy: "На панелі вже триває інше встановлення. Зачекайте хвилину й повторіть спробу.",
			installErrorStorage: "Браузеру не вдалося зберегти перебіг роботи. Дозвольте цьому сайту зберігати дані й повторіть спробу.",
			installErrorTarget: "Було під’єднано іншу панель. Під’єднайте ту саму панель і повторіть спробу.",
			installErrorArtifact: "Не вдалося перевірити завантажену програму. Поверніться до Home Assistant і почніть знову.",
			installErrorNotClean: "На цій панелі вже є програма. Натомість оновіть її з Home Assistant.",
			installErrorIncompatible: "Ця панель не може запускати цю версію програми.",
			installErrorConnection: "З’єднання з панеллю перервалося. Залиште її під’єднаною та натисніть «Спробувати ще раз».",
			installErrorHealth: "Програму встановлено, але вона ще не запустилася. Зачекайте трохи й повторіть спробу.",
			installErrorPermissions: "Програму встановлено, але її дозволи не вдалося підтвердити. Повторна спроба безпечна: залиште панель під’єднаною та натисніть «Спробувати ще раз»."
		},
		journey: {
			version: "Версія",
			connect: "Під’єднатися",
			install: "Встановити",
			setup: "Налаштувати"
		},
		sidebarReason: {
			update: "оновлення",
			settings: "налаштування",
			recovery: "відновлення",
			reboot: "перезавантаження"
		},
		support: {
			stopped: "Зупинено",
			cause: "Причина",
			failedCheck: "Невдала перевірка",
			error: "Помилка",
			release: "Випуск",
			panel: "Панель",
			copied: "Скопійовано",
			savedProgress: "Збережений прогрес",
			earlierCopy: "Попередня копія",
			stagedCopy: "Підготовлена копія",
			setAside: "Відкладено вбік",
			alreadyInstalled: "Уже встановлено",
			permissions: "Дозволи",
			setupAddress: "Адреса налаштування",
			setupHandover: "Передача до налаштування",
			displaySizing: "Розмір відображення й тексту",
			networkAdb: "ADB через мережу",
			copiedDetail: "Надіслано {bytes} байт; очікується підтвердження від панелі",
			none: "Немає",
			setupMissing: "Не знайдено; налаштування триває на панелі"
		}
	},
	"zh-Hans": {
		sidebar: {
			title: "Panel Assistant",
			versionLabel: "{version} 构建 {build}",
			menu: "打开导航",
			choosePanel: "面板",
			more: "更多选项",
			addPanel: "添加面板",
			integrationSettings: "集成设置",
			device: "此面板的 Home Assistant 设备",
			showDevice: "显示设备",
			github: "GitHub",
			unreachable: "无法连接",
			restarting: "正在重启（{reason}）",
			restartingNext: "此面板会自动重新显示，无需刷新。",
			not_loaded: "未加载",
			opening: "正在打开 {panel}…",
			loadingHint: "请稍候",
			empty: "尚未接入任何面板。",
			failed: "无法打开面板。稍后会再次尝试。",
			admin: "此页面必须由管理员打开。",
			unreachableBody: "Home Assistant 目前无法连接到此面板。",
			notLoadedBody: "此面板尚未在 Home Assistant 中加载。",
			closed: "此面板已关闭。",
			frameTitle: "面板界面",
			picklesStory: "熊猫 Pickles 逃跑了，正在到处捣乱。他动作慢又固执，要把他带回来可能需要一点时间。",
			unreachableNext: "请检查面板是否已开机并连接到您的网络。",
			notLoadedNext: "打开集成设置，检查此面板的连接。",
			failedNext: "请稍候，我们正在重试；您也可以选择其他面板。",
			closedNext: "请选择其他面板，或等待此面板重新连接。",
			pillOpening: "正在打开",
			pillUnreachable: "无法连接",
			pillNotLoaded: "未加载",
			pillRetrying: "正在重试",
			pillClosed: "已关闭"
		},
		haInstall: {
			title: "在面板上安装 Panel Assistant",
			introduction: "用 USB 数据线将面板连接到此电脑。新窗口会查找面板并安装应用。",
			release: "版本",
			loading: "正在加载版本…",
			catalogError: "无法加载版本列表。",
			empty: "目前没有可用版本。请稍后重试。",
			choose: "推荐版本",
			otherVersions: "选择其他版本",
			testing: "测试版本",
			devBuild: "开发构建",
			retry: "重试",
			start: "继续",
			cancel: "取消",
			ready: "",
			unavailable: "安装程序不可用。请更新 Panel Assistant 后重试。",
			admin: "请让 Home Assistant 管理员安装面板。",
			waiting: "请在新窗口中继续。",
			preparing: "正在准备应用…",
			downloading: "正在准备应用…",
			verifying: "正在准备应用…",
			verified: "请在新窗口中继续。",
			installed: "Panel Assistant 已安装。请在安装程序窗口中完成面板的设置，然后关闭此标签页。",
			cancelled: "已取消。",
			popup_blocked: "浏览器阻止了新窗口。请允许此页面弹出窗口，然后点击“继续”。",
			invalid_request: "请先选择版本。",
			failed: "未能完成。请点击“继续”重试。"
		},
		installer: {
			title: "设置您的面板",
			preparing: "正在准备应用…",
			preparingSlow: "仍在准备应用。请保持 Home Assistant 标签页打开。",
			connectHeading: "连接您的面板",
			connectBody: "用 USB 数据线将面板连接到此电脑，然后点击“查找我的面板”。",
			usbDebugging: "必须在面板的开发者选项中开启 USB 调试。",
			usbDebuggingHelp: "如何开启",
			connect: "查找我的面板",
			allowHeading: "允许此电脑连接",
			allowBody: "请在面板上勾选“始终允许使用这台计算机进行调试”并点击“允许”，这样 Panel Assistant 以后无需数据线也能更新面板。",
			checkingPanel: "正在检查您的面板…",
			confirmHeading: "可以开始安装",
			confirmBody: "这会安装应用，并将其设为面板的主屏幕。您的其他应用不会受到影响。",
			install: "安装",
			restartedDifferentVersion: "先前的一次安装尝试使用了另一个发行版本。那次尝试已搁置。",
			alreadyInstalledHeading: "已安装",
			alreadyInstalledBody: "您的面板上已有此版本。请继续完成设置；不会复制或重新安装任何内容。",
			continueSetup: "继续",
			progressHeading: "正在安装",
			stepCopying: "正在将应用复制到您的面板…",
			stepFinishingCopy: "正在完成复制…",
			stepInstalling: "正在安装…",
			stepStarting: "正在启动应用…",
			stepPermissions: "正在为应用提供运行所需的条件…",
			stepOpening: "正在打开面板设置…",
			keepConnected: "请保持数据线连接，直到完成。",
			doneHeading: "已安装",
			doneOpening: "正在带您进入面板设置…",
			doneManual: "请在面板屏幕上完成设置。",
			openSetup: "打开面板设置",
			errorHeading: "未能完成",
			tryAgain: "重试",
			backToHa: "返回 Home Assistant",
			details: "技术支持详细信息",
			unsupported: "此浏览器无法与 USB 设备通信。请在电脑上使用 Chrome 或 Edge 打开此页面。",
			handoffFailure: "无法从 Home Assistant 获取应用。请返回 Home Assistant 重新开始。",
			noSelection: "未选择面板。请点击“查找我的面板”并从列表中选择，或尝试另一根数据线：有些线缆只能给面板供电，无法传输数据。",
			connectionTimeout: "面板未响应。请检查线缆是否插紧，或更换线缆：有些线缆能给面板供电，却无法传输数据。",
			disconnected: "面板已断开连接。请重新连接，等待面板启动后点击“重试”。",
			cancelled: "已取消。",
			pageClosed: "页面已关闭。",
			progress: "进度",
			installProgress: "安装进度",
			licenses: "第三方许可",
			doneAnotherHomeAssistant: "此面板当前已登录另一个 Home Assistant。请在面板上更改 Home Assistant 并登录，以将其连接到此处。",
			reconnectedHeading: "Panel Assistant 已能再次连接此面板",
			reconnectedBody: "此面板已安装应用，因此没有安装任何内容。Home Assistant 现在可以通过您的网络更新它。"
		},
		errors: {
			installErrorGeneric: "发生了错误。请保持面板连接，然后点击“重试”。",
			installErrorBusy: "面板正在进行另一项安装。请等待一分钟后重试。",
			installErrorStorage: "此浏览器无法保存进度。请允许此网站存储数据，然后重试。",
			installErrorTarget: "连接了另一个面板。请连接原来的面板后重试。",
			installErrorArtifact: "无法校验下载的应用。请返回 Home Assistant 重新开始。",
			installErrorNotClean: "此面板上已有该应用。请通过 Home Assistant 更新。",
			installErrorIncompatible: "此面板无法运行此版本的应用。",
			installErrorConnection: "与面板的连接已中断。请保持面板连接，然后点击“重试”。",
			installErrorHealth: "应用已安装，但尚未启动。请稍候再重试。",
			installErrorPermissions: "应用已安装，但无法确认其权限。可以放心重试：请保持面板连接，然后点击“重试”。"
		},
		journey: {
			version: "版本",
			connect: "连接",
			install: "安装",
			setup: "设置"
		},
		sidebarReason: {
			update: "更新",
			settings: "设置",
			recovery: "恢复",
			reboot: "系统重启"
		},
		support: {
			stopped: "已停止",
			cause: "原因",
			failedCheck: "检查未通过",
			error: "错误",
			release: "发行版本",
			panel: "面板",
			copied: "已复制",
			savedProgress: "已保存的进度",
			earlierCopy: "先前的副本",
			stagedCopy: "暂存的副本",
			setAside: "已搁置",
			alreadyInstalled: "已安装",
			permissions: "权限",
			setupAddress: "设置地址",
			setupHandover: "设置交接",
			displaySizing: "显示和文字大小",
			networkAdb: "网络 ADB",
			copiedDetail: "已发送 {bytes} 字节；正在等待面板确认",
			none: "无",
			setupMissing: "未找到；设置将在面板上继续"
		}
	}
}), r = n.en, i = Object.keys(n);
function a(e) {
	if (typeof e != "string") return "en";
	let t = e.trim().replaceAll("_", "-").toLowerCase();
	if (t === "pt-br" || t.startsWith("pt-br-")) return "pt-BR";
	if (t === "zh" || t === "zh-cn" || t.startsWith("zh-cn-") || t === "zh-sg" || t.startsWith("zh-sg-") || t === "zh-hans" || t.startsWith("zh-hans-")) return "zh-Hans";
	let n = t.split("-")[0];
	return i.includes(n) ? n : "en";
}
function o(e, t, i = n) {
	let o = r[e], s = i[a(t)]?.[e], c = (e) => [...e.matchAll(/\{([A-Za-z_]+)\}/g)].map((e) => e[1]).sort().join(",");
	return Object.fromEntries(Object.entries(o).map(([e, t]) => {
		let n = s?.[e];
		return [e, typeof n == "string" && (n || !t) && c(n) === c(t) ? n : t];
	}));
}
function s(e, t) {
	return e.replace(/\{([A-Za-z_]+)\}/g, (e, n) => Object.hasOwn(t, n) ? String(t[n]) : e);
}
Object.freeze({
	repository: "panel-assistant/android",
	commit: "2a51f70906efbabf25f184a4a79a8fbcd9e9bd9e",
	sha256: {
		TOKENS_CSS: "cbee8463fdcdfb4d409182a1a02a7d5586d01be06f90a6bca1d8abbf271d4209",
		COMPONENTS_CSS: "9e0bd741a29e9dd0143b70a1e0ed000e84507a336505fc1e4c3cb63a51e0c207",
		ICON_SVG: "7c67447903573a39586503128cf5cab60989f86ba3fdaa45070a1211990278d7",
		NAME_SVG: "62d6cba58c86fd2fe160822da9e8eeb09e1a2157738e63ffb2305c5a87208568"
	}
});
var c = "/* Generated from tools/tokens/tokens.json by tools/tokens/tokens.py; do not edit.\n   Dark is the default. The light values apply when the viewing browser prefers light, unless\n   ?theme=dark pinned dark (`data-theme` on the document, `theme` on a Panel Assistant host), and\n   whenever ?theme=light pinned light. `:host` serves a shadow root, `:root` a page. */\n:root,:host{color-scheme:dark;\n  --bg:#111;--card:#181818;--card-head:#222;--input-bg:#1c1c1c;--input-bg2:#161616;--card-border:#242424;\n  --divider:#2a2a2a;--config-divider:#3a3a3a;--border:#333;--border2:#383838;--border-strong:#444;--text:#eee;\n  --text-bright:#fff;--text-soft:#ccc;--tab:#bbb;--dim:#888;--accent:#9af;--accent2:#9cf;--ok-dim:#8a8;\n  --icon:#7c8a99;--icon-dim:#6a7a8a;--hover:#1a1a1a;--knob:#eee;--disabled-bg:#222;--disabled-fg:#666;\n  --shadow:#000;--shimA:#141414;--shimB:#1f1f1f;--plot:#0d0d0d;--vtagrec-bg:#1f4d2e;--vtagrec-fg:#8fe0a8;\n  --setup-bg:#3a2f10;--setup-bd:#7a5a2a;--setup-fg:#f5c451;--setup-link:#ffd97a;--crit-bg:#3a1512;\n  --crit-fg:#ffb3a6;--crit-link:#ffc9bf;--info-bg:#12233a;--info-bd:#2a557a;--info-fg:#9cc4ee;\n  --info-link:#bcd9f5;--api-desc:#667;--api-error:#d9534f;--api-ok:#5ad18a;\n  --autobright-band:rgba(74,158,255,.15);--autobright-grid:#343a44;--autobright-label:#888;\n  --backup-plain-warn:#c88;--cap-degraded:#d9a528;--cap-none:#d04a3b;--cap-ok:#48c774;--cfglink:#4a6a8a;\n  --chart-bg:#0d0d0d;--chart-blocked:#f5a623;--chart-cpu:#4a9eff;--chart-expected:#f1bd52;--chart-gpu:#f5a623;\n  --chart-grid:#383838;--chart-interaction:#d04a3b;--chart-label:#888;--chart-observed:#4a9eff;\n  --chart-proposed:#b77cff;--chart-ram:#48c774;--chart-updates:#4a9eff;--edge-crit:#e05a48;\n  --edge-info:#3d7fd0;--fact-warn:#f5c451;--fg-bad:#d04a3b;--fg-faint:#666;--fg-muted:#888;--fg-ok:#48c774;\n  --fg-warn:#d9a528;--highlight:#4a9eff;--install-rule:#2a2a2a;--link:#9cf;--listbox-shadow:rgba(0,0,0,.28);\n  --log-error:#f07868;--log-info:#cfcfcf;--log-verbose:#777;--log-warn:#f5c451;--primary-text:#fff;\n  --pkg-active:#3fb950;--pkg-dialog-bg:#1a1a1a;--pkg-dialog-border:#333;--pkg-dialog-fg:#eee;\n  --pkg-divider:#222;--pkg-note:#9aa;--pkg-protected:#777;--primary:#2557a7;\n  --primary-glow:rgba(37,87,167,.55);--primary-glow-off:rgba(37,87,167,0);--profile-danger-border:#8b342f;\n  --profile-danger-fg:#ffb3a6;--profile-editor-active-line:rgba(127,153,204,.09);\n  --profile-editor-selection:rgba(77,121,195,.35);--profile-issue-error:#d05248;--profile-issue-info:#4284bb;\n  --profile-issue-warning:#c6952d;--profile-modal-backdrop:rgba(0,0,0,.66);\n  --profile-modal-shadow:rgba(0,0,0,.5);--profile-ok-fg:#9ee5ad;--pswitch-border:#2a3550;\n  --pswitch-border-hover:#4a6ba0;--screenshot-backdrop:rgba(0,0,0,.72);--screenshot-error:#e07868;\n  --screenshot-frame-bg:#000;--self-row-border:#2a2a2a;--shot-failed-fg:#a88;--sleep-allow:#9aafc2;\n  --sleep-allow-fg:#172533;--sleep-blocked-a:#a33b3b;--sleep-blocked-b:#5b2626;--sleep-detected:#c89a18;\n  --sleep-detected-fg:#17130a;--sleep-hold:#287dcc;--sleep-inhibited-stripe:#f1bd52;--sleep-interval-fg:#fff;\n  --sleep-loading-overlay:rgba(18,22,28,.76);--sleep-off:#68727f;--sleep-off-fg:#f4f6f8;\n  --sleep-policy-lane:rgba(74,158,255,.11);--status-bad:#d34b42;--status-ok:#45a65a;--status-warn:#c58a16;\n  --uninstall-select-bg:#1c1c1c;--uninstall-select-border:#444;--uninstall-select-fg:#eee;\n  --api-response-fg:#cdd;}\n@media (prefers-color-scheme: light){:root:not([data-theme=dark]),:host(:not([theme=dark])){color-scheme:light;\n  --bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--input-bg:#f6f7f9;--input-bg2:#fafbfc;--card-border:#d9dde3;\n  --divider:#e4e7ec;--config-divider:#e4e7ec;--border:#c9cfd7;--border2:#c4cad2;--border-strong:#b6bec8;\n  --text:#1b2430;--text-bright:#000;--text-soft:#2c3644;--tab:#4a5462;--dim:#6a7480;--accent:#1e56a8;\n  --accent2:#0b6a8e;--ok-dim:#3f7d49;--icon:#5b6b7a;--icon-dim:#74818f;--hover:#e9ecef;--knob:#fff;\n  --disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18);--shimA:#e8eaee;--shimB:#f6f7f9;\n  --plot:#e8ecf0;--vtagrec-bg:#d9efe0;--vtagrec-fg:#1f6b3a;--setup-bg:#fcf4dc;--setup-bd:#e3cf94;\n  --setup-fg:#7a5a10;--setup-link:#8a6400;--crit-bg:#fceceb;--crit-fg:#a02c20;--crit-link:#7c1f16;\n  --info-bg:#e9f1fb;--info-bd:#b9d2ec;--info-fg:#26567e;--info-link:#1a4468;}}\n:root[data-theme=light],:host([theme=light]){color-scheme:light;\n  --bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--input-bg:#f6f7f9;--input-bg2:#fafbfc;--card-border:#d9dde3;\n  --divider:#e4e7ec;--config-divider:#e4e7ec;--border:#c9cfd7;--border2:#c4cad2;--border-strong:#b6bec8;\n  --text:#1b2430;--text-bright:#000;--text-soft:#2c3644;--tab:#4a5462;--dim:#6a7480;--accent:#1e56a8;\n  --accent2:#0b6a8e;--ok-dim:#3f7d49;--icon:#5b6b7a;--icon-dim:#74818f;--hover:#e9ecef;--knob:#fff;\n  --disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18);--shimA:#e8eaee;--shimB:#f6f7f9;\n  --plot:#e8ecf0;--vtagrec-bg:#d9efe0;--vtagrec-fg:#1f6b3a;--setup-bg:#fcf4dc;--setup-bd:#e3cf94;\n  --setup-fg:#7a5a10;--setup-link:#8a6400;--crit-bg:#fceceb;--crit-fg:#a02c20;--crit-link:#7c1f16;\n  --info-bg:#e9f1fb;--info-bd:#b9d2ec;--info-fg:#26567e;--info-link:#1a4468;}\n", l = "/* Page header lock-up from the brand asset set (website src/brand): beside a mark H high the name is drawn\n   0.536 H tall (Sora 40 beside a 56 mark; name.svg is its 30-unit ink box), 0.18 H away, with its baseline\n   on the bottom edge of the house, 0.233 H above the mark's foot. The version caption fills that 0.233 H\n   under the name, so the header keeps the old logo's height (H = 2.1 x 1.3rem) and its first-paint size. */\n.logo{height:2.73rem;width:3.64rem;margin-right:.49rem;flex:none}\n.brand{display:flex;flex-direction:column;align-self:flex-end;flex:none;white-space:nowrap;color:var(--text)}\n.brand-name{display:block;height:1.4625rem;width:14.084rem;fill:currentColor}\n.brand-version{display:block;height:.636rem;font-size:11px;line-height:.636rem;color:var(--dim);font-weight:400}\n.card{background:var(--card);border:1px solid var(--card-border);border-radius:12px;padding:14px 16px;margin:0 0 18px;break-inside:avoid;-webkit-column-break-inside:avoid;box-sizing:border-box;--single-column:0}\n/* Card title-bar: a subtly demarcated full-width header (slight tint + divider) separating the title\n   from content. Also the intended drag handle for card reordering + home for a collapse triangle. */\n.card h2{margin:-14px -16px 12px;padding:9px 16px;background:var(--card-head);border-bottom:1px solid var(--divider);border-radius:12px 12px 0 0}\n/* Native radio choices keep full release labels and compatibility reasons readable. */\n.choice-list{min-width:0;max-width:100%;max-height:240px;overflow-y:auto}\n.choice-list label{display:block;position:relative;margin:6px 0;color:var(--text)}\n.choice-list input[type=radio]{position:absolute;left:12px;top:14px;width:auto;min-width:0;margin:0;accent-color:var(--primary)}\n.choice-list span{display:block;padding:10px 12px 10px 38px;border:1px solid var(--border-strong);border-radius:8px;background:var(--input-bg);white-space:normal;word-wrap:break-word;overflow-wrap:break-word}\n.choice-list input:checked+span{border-color:var(--primary)}\n.choice-list input:focus+span{outline:2px solid var(--primary);outline-offset:-2px}\n.choice-list input:disabled+span{color:var(--disabled-fg)}\n/* Self-contained so it looks identical on <button> AND <a> (the \"Open in HA\" link) — anchors don't\n   inherit the button{} border/radius/colour, so spell them out + kill the link underline. */\n.pbtn{font-size:.8rem;padding:6px 12px;background:var(--input-bg);border:1px solid var(--border-strong);border-radius:8px;color:var(--text);cursor:pointer;text-decoration:none;display:inline-block;line-height:1.3;font-family:inherit}\n/* Button: bare <button> and .pbtn.primary are the primary action, .pbtn the quiet one; .on is a pressed quiet button. */\n.pbtn.primary,.pbtn.on{background:var(--primary);border-color:var(--primary);color:var(--primary-text)}\n.pbtn.danger{border-color:var(--profile-danger-border);color:var(--profile-danger-fg)}   /* a quiet button whose action cannot be taken back */\n.pbtn:disabled{opacity:.4;cursor:not-allowed}   /* capability missing (no a11y / no root) — shown but inert */\n/* Notice row: every advisory on these pages, one line the height of the old strip. Severity is the pill\n   word (Notice.kt / HaNotice in i18n.js), never a fill: the five that ask for attention carry one accent\n   dot, Unavailable and Resolved an outlined pill with none. Narrow widths wrap the action under the text. */\n.notice{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin:10px 0!important;color:var(--text);font-size:.92rem;overflow-wrap:anywhere}\n/* Pill: one word in an outlined capsule, the one badge on every page. A pill that asks for attention\n   carries the accent dot (.pill.dot; a notice row's pill unless it is Unavailable or Resolved); state is\n   the word, never a fill colour. */\n.pill,.notice-pill{display:inline-flex;align-items:center;gap:6px;flex:0 0 auto;white-space:nowrap;padding:0 10px;line-height:1.3;border-radius:999px;border:1px solid var(--border-strong);font-size:.82rem;font-weight:600;vertical-align:middle}\n.pill.dot::before,.notice-pill::before{content:\"\";width:7px;height:7px;border-radius:50%;background:var(--accent)}\n.card h2 .pill{float:right;color:var(--text)}   /* the title bar is accent-coloured; elsewhere a pill takes its context's colour */\n.notice[data-severity=unavailable] .notice-pill::before,.notice[data-severity=resolved] .notice-pill::before{display:none}\n.notice[data-severity=unavailable],.notice[data-severity=resolved]{color:var(--dim)}\n.notice-text{flex:1 1 18rem;min-width:0}\n.notice-title{font-weight:600}\n.notice-detail{color:var(--dim)}\n.notice a{color:var(--accent);font-weight:600}\n.notice-action{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-left:auto}\n/* Guided setup (/setup) — one linear column, deliberately NOT the .cards masonry: a journey has an\n   order, and multi-column layout destroys it. Reuses the theme tokens and the .notice row so\n   the wizard inherits dark/light for free. */\n.wiz{max-width:520px;margin:0 auto;padding:4px 0 24px}\n.wiz-step .card{margin:0 0 14px}\n.wiz-step h2{text-transform:none;letter-spacing:0;font-size:1.15rem;color:var(--text)}\n/* The step heading is focused programmatically (tabindex=-1) so screen readers announce each new\n   step, but it is not keyboard-reachable, so its default focus ring is pure visual noise — it made the\n   title bar look like a selected text field on load. Suppress the ring; the announcement remains. */\n.wiz-step h2:focus{outline:none}\n.wiz-lead{color:var(--dim);margin:2px 0 14px}\n/* Wizard selects match the form's field metrics — browser-default select text read as tiny against the\n   1rem inputs on hardware review; proportionate, not dominant, not minuscule. */\n.wiz select{width:100%;font:inherit;font-size:1rem;min-height:42px;padding:8px 10px;margin:6px 0;background:var(--input-bg2);border:1px solid var(--border2);color:var(--text);border-radius:6px}\n/* The Custom dashboard-path input sits directly beneath the wizard's select and has to read as the\n   same control family; the generic input metrics are visibly smaller than .wiz select on hardware. */\n.wiz .hd-custom-input{font-size:1rem;min-height:42px;padding:8px 10px;margin:6px 0}\n.wiz .hd-custom-note{display:block;color:var(--dim);line-height:1.45}\n.wiz-fld{display:block;margin:14px 0}\n.wiz-fld>b{display:block;margin-bottom:5px;font-size:.92rem}\n.wiz-fld>small{display:block;color:var(--dim);margin-top:5px;line-height:1.45}\n.wiz-consequence{color:var(--dim);font-size:.88rem;line-height:1.5;margin:10px 0 0;border-left:3px solid var(--divider);padding-left:10px}\n/* Live entity-id preview and wizard banners can contain maximum-length entity IDs or opaque backend\n   values with no break opportunities. Keep those values inside the Setup card without changing the\n   wrapping policy of banners on the other management pages. */\n.wiz-preview,.wiz .notice{overflow-wrap:anywhere;word-break:break-word}\n/* Live entity-id preview: the consequence of the panel-id choice, shown rather than explained. */\n.wiz-preview{margin:8px 0 0;padding:8px 10px;background:var(--input-bg);border:1px solid var(--divider);border-radius:8px;font:.8rem/1.6 ui-monospace,Menlo,Consolas,monospace;color:var(--dim)}\n.wiz-preview b{color:var(--text);font-weight:600}\n/* The one forward affordance on every step: full width, labelled with its consequence. */\n.wiz button.wiz-primary{display:block;width:100%;min-height:46px;font-size:1rem;margin-top:16px}\n/* A pressed save must LOOK pressed: an unstyled disabled state read as \"nothing happened\" on hardware\n   review, and users re-clicked. */\n.wiz .wiz-secondary{display:block;box-sizing:border-box;width:100%;min-height:42px;margin-top:8px;font-size:.95rem}\n.wiz-dots{display:flex;gap:14px;justify-content:center;list-style:none;margin:4px 0 14px;padding:0;flex-wrap:wrap}\n.wiz-dots li{display:flex;align-items:center;gap:6px;font-size:.8rem;color:var(--dim)}\n.wiz-dots li::before{content:\"\";flex:none;width:.55rem;height:.55rem;border-radius:50%;background:var(--dim);opacity:.35}\n.wiz-dots li.done::before{background:var(--ok-dim);opacity:1}\n.wiz-dots li.current{color:var(--text);font-weight:600}\n.wiz-dots li.current::before{background:var(--primary);opacity:1}\n/* Verification is a STATE of a dot, not its own step — users don't experience waiting as a stage. */\n.wiz-dots li.busy::before{background:var(--primary);opacity:1;animation:wiz-breathe 1.6s ease-in-out infinite}\n/* Async status line. The breathing dot is never the only signal — the TEXT changes as time passes, so\n   reduced-motion and screen-reader users get strictly more information than the dot conveys. */\n.wiz-status{display:flex;align-items:baseline;gap:8px;margin:14px 0 0;min-height:1.4em}\n.wiz-status::before{content:\"\";flex:none;width:.55rem;height:.55rem;border-radius:50%;background:var(--primary);animation:wiz-breathe 1.6s ease-in-out infinite;align-self:center}\n.wiz-status.ok::before{background:var(--ok-dim);animation:none}\n@keyframes wiz-breathe{0%,100%{opacity:.35}50%{opacity:1}}\n@media (prefers-reduced-motion:reduce){.wiz-dots li.busy::before,.wiz-status::before{animation:none;opacity:.85}}\n/* The area confirmation that shares the wizard's dashboard page. */\n/* Deterministic bring-up milestones (proof card): real signals, no spinner. */\n.wiz-milestones{margin:12px 0 0;border-left:3px solid var(--divider);padding-left:12px}\n.wiz-milestone{margin:4px 0;color:var(--dim);font-size:.88rem;line-height:1.5}\n.hd-area-label{display:block;margin:16px 0 5px;font-size:.92rem}\n.hd-area-note{display:block;color:var(--dim);margin-top:5px;line-height:1.45}\n/* Sign-in routes: the recommended one first and visually primary, both honest about the account. */\n.wiz-route{border:1px solid var(--divider);border-radius:10px;padding:12px 14px;margin:10px 0}\n.wiz-route.recommended{border-color:var(--primary)}\n.wiz-route .pill{margin-left:6px}\n.wiz-route p{margin:6px 0;color:var(--dim);font-size:.9rem;line-height:1.5}\n.wiz-escape{text-align:center;margin:6px 0 0}\n.wiz-escape a{color:var(--dim)}\n.wiz details{margin:10px 0 0}\n.wiz details summary{cursor:pointer;color:var(--accent)}\n.wiz details p{color:var(--dim);font-size:.9rem;line-height:1.55}\n@media (max-width:600px){.wiz{padding-left:2px;padding-right:2px}.wiz-dots{gap:10px}}\n", u = "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"24 32 60 45\">\n<path d=\"M28,32 h52 a4,4 0 0 1 4,4 v37 a4,4 0 0 1 -4,4 h-52 a4,4 0 0 1 -4,-4 v-37 a4,4 0 0 1 4,-4 z\" fill=\"#607D8B\"/>\n<path d=\"M29,35 h50 a2,2 0 0 1 2,2 v35 a2,2 0 0 1 -2,2 h-50 a2,2 0 0 1 -2,-2 v-35 a2,2 0 0 1 2,-2 z\" fill=\"#10284A\" stroke=\"#CFD8DC\" stroke-width=\"1\"/>\n<g transform=\"translate(42.00,42.50) scale(0.1000)\">\n<path fill=\"#F2F4F9\" d=\"M240 224.813C240 233.063 233.25 239.813 225 239.813H15C6.75 239.813 0 233.063 0 224.813V134.813C0 126.563 4.77 115.043 10.61 109.203L109.39 10.423C115.22 4.59304 124.77 4.59304 130.6 10.423L229.39 109.213C235.22 115.043 240 126.573 240 134.823V224.823V224.813Z\"/>\n<path fill=\"#18BCF2\" d=\"M229.39 109.203L130.61 10.423C124.78 4.59304 115.23 4.59304 109.4 10.423L10.61 109.203C4.78 115.033 0 126.563 0 134.813V224.813C0 233.063 6.75 239.813 15 239.813H107.27L66.64 199.183C64.55 199.903 62.32 200.313 60 200.313C48.7 200.313 39.5 191.113 39.5 179.813C39.5 168.513 48.7 159.313 60 159.313C71.3 159.313 80.5 168.513 80.5 179.813C80.5 182.143 80.09 184.373 79.37 186.463L111 218.093V102.213C104.2 98.873 99.5 91.893 99.5 83.823C99.5 72.523 108.7 63.323 120 63.323C131.3 63.323 140.5 72.523 140.5 83.823C140.5 91.893 135.8 98.873 129 102.213V183.483L160.46 152.023C159.84 150.063 159.5 147.983 159.5 145.823C159.5 134.523 168.7 125.323 180 125.323C191.3 125.323 200.5 134.523 200.5 145.823C200.5 157.123 191.3 166.323 180 166.323C177.5 166.323 175.12 165.853 172.91 165.033L129 208.943V239.823H225C233.25 239.823 240 233.073 240 224.823V134.823C240 126.573 235.23 115.053 229.39 109.213V109.203Z\"/>\n</g>\n</svg>\n", d = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"288.90\" height=\"30\" viewBox=\"0 -30 288.90 30\">\n<path fill=\"currentColor\" d=\"M7.92 -9.56L7.92 -13.76L14 -13.76Q15.92 -13.76 17.18 -14.5Q18.44 -15.24 19.1 -16.54Q19.76 -17.84 19.76 -19.48Q19.76 -21.12 19.1 -22.42Q18.44 -23.72 17.18 -24.46Q15.92 -25.2 14 -25.2L7.92 -25.2L7.92 -29.4L13.44 -29.4Q17.08 -29.4 19.58 -28.22Q22.08 -27.04 23.38 -24.88Q24.68 -22.72 24.68 -19.8L24.68 -19.16Q24.68 -16.24 23.38 -14.08Q22.08 -11.92 19.58 -10.74Q17.08 -9.56 13.44 -9.56ZM3.92 0L3.92 -29.4L8.72 -29.4L8.72 0ZM40.64 0L40.64 -6.44L39.88 -6.44L39.88 -13.68Q39.88 -15.72 38.84 -16.74Q37.8 -17.76 35.76 -17.76Q34.64 -17.76 33.28 -17.72Q31.92 -17.68 30.6 -17.64Q29.28 -17.6 28.28 -17.52L28.28 -21.56Q29.2 -21.64 30.26 -21.7Q31.32 -21.76 32.46 -21.8Q33.6 -21.84 34.64 -21.84Q37.92 -21.84 40.08 -21Q42.24 -20.16 43.3 -18.34Q44.36 -16.52 44.36 -13.48L44.36 0ZM33.64 0.56Q31.28 0.56 29.52 -0.26Q27.76 -1.08 26.8 -2.66Q25.84 -4.24 25.84 -6.44Q25.84 -8.76 26.94 -10.28Q28.04 -11.8 30.08 -12.56Q32.12 -13.32 34.96 -13.32L40.36 -13.32L40.36 -10.24L34.84 -10.24Q32.72 -10.24 31.6 -9.22Q30.48 -8.2 30.48 -6.56Q30.48 -4.92 31.6 -3.94Q32.72 -2.96 34.84 -2.96Q36.08 -2.96 37.2 -3.42Q38.32 -3.88 39.06 -4.96Q39.8 -6.04 39.88 -8L41.08 -6.44Q40.88 -4.16 39.98 -2.62Q39.08 -1.08 37.5 -0.26Q35.92 0.56 33.64 0.56ZM49.35 0L49.35 -21.56L53.11 -21.56L53.11 -12.32L52.67 -12.32Q52.67 -15.52 53.53 -17.72Q54.39 -19.92 56.15 -21.06Q57.91 -22.2 60.51 -22.2L60.71 -22.2Q64.71 -22.2 66.67 -19.68Q68.63 -17.16 68.63 -12.32L68.63 0L63.91 0L63.91 -12.96Q63.91 -15.2 62.65 -16.56Q61.39 -17.92 59.15 -17.92Q56.87 -17.92 55.47 -16.52Q54.07 -15.12 54.07 -12.76L54.07 0ZM83.05 0.76Q80.25 0.76 78.17 -0.2Q76.09 -1.16 74.75 -2.76Q73.41 -4.36 72.75 -6.34Q72.09 -8.32 72.09 -10.4L72.09 -11.16Q72.09 -13.28 72.75 -15.26Q73.41 -17.24 74.75 -18.82Q76.09 -20.4 78.11 -21.34Q80.13 -22.28 82.81 -22.28Q86.29 -22.28 88.61 -20.76Q90.93 -19.24 92.09 -16.82Q93.25 -14.4 93.25 -11.6L93.25 -9.72L74.13 -9.72L74.13 -12.88L90.09 -12.88L88.77 -11.24Q88.77 -13.44 88.11 -15Q87.45 -16.56 86.13 -17.42Q84.81 -18.28 82.81 -18.28Q80.73 -18.28 79.33 -17.34Q77.93 -16.4 77.25 -14.72Q76.57 -13.04 76.57 -10.76Q76.57 -8.6 77.25 -6.9Q77.93 -5.2 79.37 -4.22Q80.81 -3.24 83.05 -3.24Q85.29 -3.24 86.73 -4.22Q88.17 -5.2 88.49 -6.52L92.85 -6.52Q92.41 -4.32 91.07 -2.68Q89.73 -1.04 87.69 -0.14Q85.65 0.76 83.05 0.76ZM97.03 0L97.03 -29.2L101.75 -29.2L101.75 0ZM94.19 -25.6L94.19 -29.2L101.75 -29.2L101.75 -25.6ZM111.97 0L122.21 -29.2L130.17 -29.2L140.73 0L135.77 0L126.53 -25.96L128.49 -25.04L123.65 -25.04L125.73 -25.96L116.77 0ZM118.61 -8.24L120.13 -12.4L132.37 -12.4L133.93 -8.24ZM150.93 0.68Q146.57 0.68 144.15 -1.18Q141.73 -3.04 141.61 -6.28L145.93 -6.28Q146.01 -5.08 147.17 -4.08Q148.33 -3.08 150.97 -3.08Q153.21 -3.08 154.49 -3.96Q155.77 -4.84 155.77 -6.16Q155.77 -7.36 154.81 -8.06Q153.85 -8.76 151.65 -8.96L149.53 -9.2Q146.21 -9.56 144.27 -11.14Q142.33 -12.72 142.33 -15.48Q142.33 -17.64 143.41 -19.12Q144.49 -20.6 146.39 -21.4Q148.29 -22.2 150.77 -22.2Q154.61 -22.2 156.97 -20.5Q159.33 -18.8 159.41 -15.52L155.09 -15.52Q155.05 -16.72 153.95 -17.58Q152.85 -18.44 150.77 -18.44Q148.77 -18.44 147.71 -17.64Q146.65 -16.84 146.65 -15.6Q146.65 -14.48 147.45 -13.84Q148.25 -13.2 150.05 -13L152.17 -12.8Q155.81 -12.4 157.95 -10.78Q160.09 -9.16 160.09 -6.28Q160.09 -4.2 158.93 -2.64Q157.77 -1.08 155.71 -0.2Q153.65 0.68 150.93 0.68ZM171.13 0.68Q166.77 0.68 164.35 -1.18Q161.93 -3.04 161.81 -6.28L166.13 -6.28Q166.21 -5.08 167.37 -4.08Q168.53 -3.08 171.17 -3.08Q173.41 -3.08 174.69 -3.96Q175.97 -4.84 175.97 -6.16Q175.97 -7.36 175.01 -8.06Q174.05 -8.76 171.85 -8.96L169.73 -9.2Q166.41 -9.56 164.47 -11.14Q162.53 -12.72 162.53 -15.48Q162.53 -17.64 163.61 -19.12Q164.69 -20.6 166.59 -21.4Q168.49 -22.2 170.97 -22.2Q174.81 -22.2 177.17 -20.5Q179.53 -18.8 179.61 -15.52L175.29 -15.52Q175.25 -16.72 174.15 -17.58Q173.05 -18.44 170.97 -18.44Q168.97 -18.44 167.91 -17.64Q166.85 -16.84 166.85 -15.6Q166.85 -14.48 167.65 -13.84Q168.45 -13.2 170.25 -13L172.37 -12.8Q176.01 -12.4 178.15 -10.78Q180.29 -9.16 180.29 -6.28Q180.29 -4.2 179.13 -2.64Q177.97 -1.08 175.91 -0.2Q173.85 0.68 171.13 0.68ZM184.93 0L184.93 -21.56L189.61 -21.56L189.61 0ZM181.77 -17.96L181.77 -21.56L189.61 -21.56L189.61 -17.96ZM186.45 -24.56Q185.01 -24.56 184.31 -25.32Q183.61 -26.08 183.61 -27.28Q183.61 -28.44 184.31 -29.22Q185.01 -30 186.45 -30Q187.93 -30 188.63 -29.22Q189.33 -28.44 189.33 -27.28Q189.33 -26.08 188.63 -25.32Q187.93 -24.56 186.45 -24.56ZM202.49 0.68Q198.13 0.68 195.71 -1.18Q193.29 -3.04 193.17 -6.28L197.49 -6.28Q197.57 -5.08 198.73 -4.08Q199.89 -3.08 202.53 -3.08Q204.77 -3.08 206.05 -3.96Q207.33 -4.84 207.33 -6.16Q207.33 -7.36 206.37 -8.06Q205.41 -8.76 203.21 -8.96L201.09 -9.2Q197.77 -9.56 195.83 -11.14Q193.89 -12.72 193.89 -15.48Q193.89 -17.64 194.97 -19.12Q196.05 -20.6 197.95 -21.4Q199.85 -22.2 202.33 -22.2Q206.17 -22.2 208.53 -20.5Q210.89 -18.8 210.97 -15.52L206.65 -15.52Q206.61 -16.72 205.51 -17.58Q204.41 -18.44 202.33 -18.44Q200.33 -18.44 199.27 -17.64Q198.21 -16.84 198.21 -15.6Q198.21 -14.48 199.01 -13.84Q199.81 -13.2 201.61 -13L203.73 -12.8Q207.37 -12.4 209.51 -10.78Q211.65 -9.16 211.65 -6.28Q211.65 -4.2 210.49 -2.64Q209.33 -1.08 207.27 -0.2Q205.21 0.68 202.49 0.68ZM224.09 0.28Q221.41 0.28 219.57 -0.46Q217.73 -1.2 216.77 -2.96Q215.81 -4.72 215.81 -7.72L215.85 -27.84L220.29 -27.84L220.29 -7.32Q220.29 -5.64 221.19 -4.74Q222.09 -3.84 223.73 -3.84L227.33 -3.84L227.33 0.28ZM212.25 -18.04L212.25 -21.56L227.33 -21.56L227.33 -18.04ZM243.81 0L243.81 -6.44L243.05 -6.44L243.05 -13.68Q243.05 -15.72 242.01 -16.74Q240.97 -17.76 238.93 -17.76Q237.81 -17.76 236.45 -17.72Q235.09 -17.68 233.77 -17.64Q232.45 -17.6 231.45 -17.52L231.45 -21.56Q232.37 -21.64 233.43 -21.7Q234.49 -21.76 235.63 -21.8Q236.77 -21.84 237.81 -21.84Q241.09 -21.84 243.25 -21Q245.41 -20.16 246.47 -18.34Q247.53 -16.52 247.53 -13.48L247.53 0ZM236.81 0.56Q234.45 0.56 232.69 -0.26Q230.93 -1.08 229.97 -2.66Q229.01 -4.24 229.01 -6.44Q229.01 -8.76 230.11 -10.28Q231.21 -11.8 233.25 -12.56Q235.29 -13.32 238.13 -13.32L243.53 -13.32L243.53 -10.24L238.01 -10.24Q235.89 -10.24 234.77 -9.22Q233.65 -8.2 233.65 -6.56Q233.65 -4.92 234.77 -3.94Q235.89 -2.96 238.01 -2.96Q239.25 -2.96 240.37 -3.42Q241.49 -3.88 242.23 -4.96Q242.97 -6.04 243.05 -8L244.25 -6.44Q244.05 -4.16 243.15 -2.62Q242.25 -1.08 240.67 -0.26Q239.09 0.56 236.81 0.56ZM252.52 0L252.52 -21.56L256.28 -21.56L256.28 -12.32L255.84 -12.32Q255.84 -15.52 256.7 -17.72Q257.56 -19.92 259.32 -21.06Q261.08 -22.2 263.68 -22.2L263.88 -22.2Q267.88 -22.2 269.84 -19.68Q271.8 -17.16 271.8 -12.32L271.8 0L267.08 0L267.08 -12.96Q267.08 -15.2 265.82 -16.56Q264.56 -17.92 262.32 -17.92Q260.04 -17.92 258.64 -16.52Q257.24 -15.12 257.24 -12.76L257.24 0ZM285.66 0.28Q282.98 0.28 281.14 -0.46Q279.3 -1.2 278.34 -2.96Q277.38 -4.72 277.38 -7.72L277.42 -27.84L281.86 -27.84L281.86 -7.32Q281.86 -5.64 282.76 -4.74Q283.66 -3.84 285.3 -3.84L288.9 -3.84L288.9 0.28ZM273.82 -18.04L273.82 -21.56L288.9 -21.56L288.9 -18.04Z\"/>\n</svg>\n", f = `data:image/svg+xml,${encodeURIComponent(u)}`;
function p(e, t = "en") {
	let n = e?.version, r = e?.build;
	return typeof n != "string" || !/^[0-9A-Za-z.+-]{1,32}$/.test(n) || !Number.isSafeInteger(r) || r < 0 ? "" : s(o("sidebar", t).versionLabel, {
		version: n,
		build: r
	});
}
var m = `<div class="wiz-brand"><img class="logo" src="${f}" alt=""><span class="brand">${d.replace(/<!--[^]*?-->\s*/g, "").trim().replace("<svg ", "<svg class=\"brand-name\" role=\"img\" aria-label=\"Panel Assistant\" ")}<small class="brand-version" id="brand-version"></small></span></div>`, h = Object.freeze(Object.values(o("journey", "en")));
function g(e) {
	return h.map((t, n) => n < e ? `<li class="done">${t}</li>` : n === e ? `<li class="current" aria-current="step">${t}</li>` : `<li>${t}</li>`).join("");
}
function _(e, t, n = document, r = "en") {
	let i = Object.values(o("journey", r)).map((e, r) => {
		let i = n.createElement("li");
		return i.textContent = e, r < t && (i.className = "done"), r === t && (i.className = "current", i.setAttribute("aria-current", "step")), i;
	});
	e.replaceChildren(...i);
}
var v = `${c}${l}
:root,:host{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text)}
*,*::before,*::after{box-sizing:border-box}
.wiz-brand{display:flex;margin:0 0 14px}
.wiz-brand .brand{flex:0 1 auto;min-width:0}
.wiz-brand .brand-name{max-width:100%;height:auto}
/* The shared caption box (.636rem) is shorter than its 11px text, so the version touched the name's
   foot. Drop the text clear of it without growing the lock-up: the name stays on the house's edge. */
.wiz-brand .brand-version{margin:3px 0 -3px}
.wiz a.wiz-primary{display:flex;align-items:center;justify-content:center;width:100%;min-height:46px;margin-top:16px;font-size:1rem}
.bar{height:.5rem;border-radius:1rem;background:var(--divider);overflow:hidden;margin:1rem 0 .9rem}
.bar>div{height:100%;width:0;background:var(--primary);transition:width .4s ease}
@media (prefers-reduced-motion:reduce){.bar>div{transition:none}}
[hidden]{display:none!important}
`, y = "https://github.com/panel-assistant/ha-integration", b = "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12", x = "panel_assistant.sidebar.entry", S = "panel-assistant-ui", C = "panel-assistant-wait", w = "/config/integrations/dashboard/add?domain=panel_assistant", T = "/config/integrations/integration/panel_assistant", E = /* @__PURE__ */ new Set([
	"reachable",
	"unreachable",
	"not_loaded",
	"restarting"
]), D = /* @__PURE__ */ new Set([
	"update",
	"settings",
	"recovery",
	"reboot"
]), O = 5e3, ee = /* @__PURE__ */ new Set([
	"current",
	"added",
	"updated",
	"removed"
]);
function k(e) {
	if (!e || !Array.isArray(e.panels) || e.panels.length > 200) throw Error("invalid panels");
	let t = /* @__PURE__ */ new Set();
	return e.panels.map((e) => {
		if (!e || typeof e.entry_id != "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(e.entry_id) || t.has(e.entry_id) || typeof e.title != "string" || e.title.length > 256 || !E.has(e.state) || e.state === "restarting" && !D.has(e.reason) || e.device_id !== null && e.device_id !== void 0 && typeof e.device_id != "string") throw Error("invalid panel");
		return t.add(e.entry_id), {
			entry_id: e.entry_id,
			title: e.title,
			state: e.state,
			device_id: e.device_id ?? null,
			...e.state === "restarting" ? { reason: e.reason } : {}
		};
	});
}
function A(e) {
	return typeof e == "string" ? e.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}
var j = /* @__PURE__ */ new Set([
	"configure",
	"setup",
	"profiles",
	"install",
	"logs",
	"entities"
]);
function M(e) {
	let [t = "", n, r] = typeof e == "string" ? e.slice(1).split("/") : [];
	return j.has(n) ? {
		entryId: t,
		suffix: /^[A-Za-z0-9_-]{1,64}$/.test(r ?? "") ? `${n}#${r}` : n
	} : {
		entryId: t,
		suffix: ""
	};
}
function te(e, t = "en") {
	return s(o("sidebar", t).opening, { panel: typeof e == "string" ? e : "" });
}
function ne(e) {
	history.pushState(null, "", e), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
}
function re() {
	try {
		return localStorage.getItem(x);
	} catch {
		return null;
	}
}
function ie(e) {
	try {
		localStorage.setItem(x, e);
	} catch {}
}
var ae = class extends HTMLElement {
	get #e() {
		return o("sidebar", this.#t?.language);
	}
	#t;
	#n;
	#r;
	#i = !1;
	#a;
	#o = null;
	#s = "loading";
	#c = 0;
	#l = "";
	#u = null;
	#d = null;
	#f = null;
	#p = null;
	#m = null;
	#h = null;
	#g = () => this.#P();
	#_ = /* @__PURE__ */ new Map();
	#v = 0;
	#y = !1;
	#b = (e) => {
		let t = this.shadowRoot.querySelector("#frame");
		e.data?.type === C && e.source && e.source === t.contentWindow ? (this.#x = !0, t.dataset.ready = "false", this.#B()) : this.#z(e);
	};
	#x = !1;
	constructor() {
		super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${c}${l}
      :host{display:block;height:100vh;height:100dvh;overflow:hidden;background:var(--primary-background-color,#fafafa);color:var(--primary-text-color,#212121)}
      [hidden]{display:none!important}
      .root{display:flex;flex-direction:column;height:100%;overflow:hidden}
      header{display:flex;flex-wrap:wrap;flex-shrink:0;align-items:center;gap:8px;min-height:56px;padding:4px 12px 4px 20px;font-size:1rem;background:var(--app-header-background-color,var(--primary-color,#03a9f4));color:var(--app-header-text-color,#fff);border-bottom:1px solid var(--divider-color,#e0e0e0);box-sizing:border-box}
      h1{font-size:1.25rem;font-weight:400;margin:0}
      #icon{width:48px;height:36px;flex-shrink:0}
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
      /* Loading and failure fill the pane as one shared card: a pill word, one line, a quiet next step. */
      .state{background:var(--bg);box-sizing:border-box;flex:1;min-height:0;overflow:auto;display:flex;align-items:center;justify-content:safe center;padding:20px}
      .state .card{width:100%;max-width:520px;margin:0;display:flex;flex-direction:column;align-items:flex-start;gap:12px;color:var(--text)}
      .state .card p{margin:0;line-height:1.5}
      .state .title{font-size:1.5rem;font-weight:300;line-height:1.25;overflow-wrap:anywhere}
      .state .dim{color:var(--dim)}
      #pickles{width:180px;max-width:100%;height:180px;object-fit:contain;flex-shrink:0;align-self:center}
      #status{margin:0;padding:16px;color:var(--secondary-text-color,#727272)}
      iframe{flex:1;min-height:0;border:0;width:100%;display:block;background:var(--bg);color-scheme:light}
      :host([theme="dark"]) iframe{color-scheme:dark}
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
      <img id="icon" src="${f}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <button id="overflow" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="more"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z"/></svg></button>
      <div id="backdrop"></div>
      <div id="more">
      <a id="device"><svg class="wide-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg><svg class="menu-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19,18H5V6H19M21,4H3C1.89,4 1,4.89 1,6V18A2,2 0 0,0 3,20H21A2,2 0 0,0 23,18V6C23,4.89 22.1,4 21,4Z"/></svg><span class="item-label" data-message="showDevice"></span></a>
      <div id="spacer"></div>
      <a id="github" href="${y}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${b}"/></svg><span class="item-label" data-message="github"></span></a>
      <a id="add"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg><span id="add-label" class="item-label" data-message="addPanel"></span></a>
      <a id="settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z"/></svg><span class="item-label" data-message="integrationSettings"></span></a>
      </div>
      <div id="slot"></div>
    </header><div id="failure" class="state" hidden><section class="card"><span id="failure-pill" class="pill dot"></span><img id="pickles" src="/panel_assistant/usb/pickles.svg" alt=""><p id="failure-status" class="title" role="status" aria-live="polite"></p><p id="pickles-story" data-message="picklesStory"></p><p id="next-step" class="dim"></p></section></div><p id="status" role="status" aria-live="polite"></p><div id="loading" class="state" hidden><section class="card"><span id="loading-pill" class="pill dot" data-message="pillOpening"></span><p id="loading-text" class="title" role="status" aria-live="polite"></p><p id="loading-hint" class="wiz-status dim" data-message="loadingHint"></p></section></div><iframe id="frame"></iframe></div>`;
		let e = this.shadowRoot;
		for (let t of e.querySelectorAll("[data-message]")) t.textContent = this.#e[t.dataset.message];
		let t = e.querySelector("#menu");
		t.setAttribute("aria-label", this.#e.menu), t.hidden = !0, t.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", {
			bubbles: !0,
			composed: !0
		})));
		let n = e.querySelector("#overflow");
		n.setAttribute("aria-label", this.#e.more), n.setAttribute("title", this.#e.more), n.hidden = !0, e.querySelector("#backdrop").hidden = !0, e.querySelector("#dot").hidden = !0, n.addEventListener("click", () => this.#C(!this.#S())), e.querySelector("#backdrop").addEventListener("click", () => this.#C(!1)), e.querySelector("#root").addEventListener("keydown", (e) => {
			e.key === "Escape" && this.#S() && (this.#C(!1), n.focus?.());
		}), e.querySelector("#frame").setAttribute("title", this.#e.frameTitle), e.querySelector("#frame").addEventListener("load", () => {
			let t = e.querySelector("#frame");
			t.dataset.ready = String(!!t.getAttribute("src") && !this.#x), this.#x = !1, this.#B();
		});
		let r = e.querySelector("#settings");
		r.setAttribute("aria-label", this.#e.integrationSettings), r.setAttribute("title", this.#e.integrationSettings);
		let i = e.querySelector("#github");
		i.setAttribute("aria-label", this.#e.github), i.setAttribute("title", this.#e.github), i.addEventListener("click", () => this.#C(!1));
		let a = e.querySelector("#device");
		a.setAttribute("aria-label", this.#e.device), a.setAttribute("title", this.#e.device), a.addEventListener("click", (e) => {
			let t = a.getAttribute("href");
			this.#C(!1), !(!t || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) && (e.preventDefault(), ne(t));
		});
		for (let [t, n] of [["add", w], ["settings", T]]) {
			let r = e.querySelector(`#${t}`);
			r.setAttribute("href", n), r.addEventListener("click", (e) => {
				this.#C(!1), !(e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) && (e.preventDefault(), ne(n));
			});
		}
		e.querySelector("#panels").addEventListener("change", (e) => {
			let t = e.target.value, n = this.#p?.page;
			this.#d = n ? {
				entryId: t,
				suffix: n
			} : null, this.#A(t);
		}), this.#B();
	}
	get hass() {
		return this.#t;
	}
	set hass(e) {
		let t = this.#t;
		this.#t = e;
		let n = e?.themes?.darkMode;
		if (typeof n == "boolean" && this.setAttribute?.("theme", n ? "dark" : "light"), this.isConnected) {
			if (this.#w(), t?.connection !== e?.connection || t?.user?.id !== e?.user?.id || t?.user?.is_admin !== e?.user?.is_admin) {
				this.#O();
				return;
			}
			(t?.language !== e?.language || !!t?.themes?.darkMode != !!e?.themes?.darkMode) && (this.#F(), this.#j());
		}
	}
	get panel() {
		return this.#n;
	}
	set panel(e) {
		this.#n = e, this.shadowRoot.querySelector("#version").textContent = p(e?.config, this.#t?.language);
	}
	get route() {
		return this.#r;
	}
	set route(e) {
		let t = this.#r?.path;
		if (this.#r = e, t === e?.path) return;
		let n = M(e?.path);
		this.#d = n.suffix ? n : null, this.#A(n.entryId);
	}
	get narrow() {
		return this.#i;
	}
	set narrow(e) {
		this.#i = e === !0;
		let t = this.shadowRoot, n = t.querySelector("#root");
		this.#i ? n.setAttribute("data-narrow", "") : n.removeAttribute("data-narrow"), t.querySelector("#menu").hidden = !this.#i, t.querySelector("#overflow").hidden = !this.#i, t.querySelector("#title").hidden = this.#i, t.querySelector("#version").hidden = this.#i;
		let r = t.querySelector("#more");
		this.#i ? r.setAttribute("role", "menu") : r.removeAttribute("role");
		for (let e of [
			"device",
			"github",
			"add",
			"settings"
		]) {
			let n = t.querySelector(`#${e}`);
			this.#i ? n.setAttribute("role", "menuitem") : n.removeAttribute("role");
		}
		this.#i || this.#C(!1), this.#w();
	}
	#S() {
		return this.shadowRoot.querySelector("#more").getAttribute("data-open") !== null;
	}
	#C(e) {
		let t = this.shadowRoot, n = t.querySelector("#more"), r = e && this.#i;
		r ? n.setAttribute("data-open", "") : n.removeAttribute("data-open"), t.querySelector("#backdrop").hidden = !r, t.querySelector("#overflow").setAttribute("aria-expanded", String(r));
	}
	#w() {
		let e = this.isConnected && this.#i ? this.#t?.connection : void 0, t = this.#h;
		if (t?.connection === e || (t && (this.#h = null, t.unsubscribe?.then((e) => e()).catch(() => {}), this.shadowRoot.querySelector("#dot").hidden = !0), !e?.subscribeMessage)) return;
		let n = {
			connection: e,
			notifications: {},
			unsubscribe: null
		};
		this.#h = n, n.unsubscribe = Promise.resolve().then(() => e.subscribeMessage((e) => this.#T(n, e), { type: "persistent_notification/subscribe" })), n.unsubscribe.catch(() => {});
	}
	#T(e, t) {
		if (this.#h === e && ee.has(t?.type) && t.notifications && typeof t.notifications == "object") {
			if (t.type === "current") e.notifications = { ...t.notifications };
			else if (t.type === "removed") for (let n of Object.keys(t.notifications)) delete e.notifications[n];
			else e.notifications = {
				...e.notifications,
				...t.notifications
			};
			this.shadowRoot.querySelector("#dot").hidden = Object.keys(e.notifications).length === 0;
		}
	}
	connectedCallback() {
		window.addEventListener("message", this.#b), clearInterval(this.#m), this.#O(), this.#w(), this.#m = setInterval(() => this.#k(), O);
	}
	disconnectedCallback() {
		window.removeEventListener("message", this.#b), clearInterval(this.#m), this.#m = null, this.#c++, this.#D(), this.#F(), this.#w();
	}
	#E() {
		return this.#t?.user?.is_admin === !0;
	}
	#D() {
		this.#a?.removeEventListener?.("ready", this.#g), this.#a = void 0;
	}
	#O() {
		this.#D(), this.#F(), this.#o = null, this.#l = "", this.#s = "loading", this.#E() && this.#t.connection && (this.#a = this.#t.connection, this.#a.addEventListener("ready", this.#g)), this.#B(), this.#k();
	}
	async #k() {
		let e = ++this.#c;
		if (!this.#E()) {
			this.#B();
			return;
		}
		let t;
		try {
			let e;
			try {
				e = await this.#t.callWS({ type: "panel_assistant/embed_panels" });
			} catch (e) {
				if (this.#o) return;
				throw e;
			}
			t = k(e);
		} catch {
			if (e !== this.#c) return;
			this.#o = null, this.#s = "failed", this.#F(), this.#B();
			return;
		}
		if (e !== this.#c) return;
		if (this.#o = t, this.#s = t.length ? "ready" : "empty", !t.some((e) => e.entry_id === this.#u)) {
			let e = M(this.#r?.path).entryId, n = t.some((t) => t.entry_id === e) ? e : re();
			this.#u = t.some((e) => e.entry_id === n) ? n : t[0]?.entry_id ?? null;
		}
		this.#j();
		let n = this.#p;
		this.#y && n?.state === "open" && t.some((e) => e.entry_id === n.entryId && e.state === "reachable") && (this.#y = !1, this.#L({
			type: S,
			open: !0
		}));
	}
	#A(e) {
		this.#d?.entryId !== e && (this.#d = null), this.#o?.some((t) => t.entry_id === e) && (e !== this.#u || this.#d) && (this.#u = e, ie(e), this.#F(), this.#j());
	}
	#j() {
		let e = this.#o?.find((e) => e.entry_id === this.#u), t = this.#p;
		!e || e.state !== "reachable" ? t && (t.state !== "closed" || t.entryId !== e?.entry_id) && this.#F() : (!t || t.entryId !== e.entry_id || !["opening", "open"].includes(t.state)) && (this.#F(), this.#M(e.entry_id, null, null)), this.#B();
	}
	#M(e, t, n) {
		let r = this.#t, i = {
			entryId: e,
			token: t,
			url: n,
			state: "opening",
			code: null,
			unsubscribe: null,
			reopen: !!t,
			page: t ? this.#p?.page : void 0
		};
		this.#p = i;
		let a = {
			type: "panel_assistant/embed_session",
			entry_id: e,
			language: r.language,
			theme: r.themes?.darkMode ? "dark" : "light",
			...t ? { resume: t } : {}
		};
		i.unsubscribe = Promise.resolve().then(() => r.connection.subscribeMessage((e) => this.#N(i, e), a, { resubscribe: !1 })), i.unsubscribe.catch((e) => {
			this.#p === i && (i.state = "failed", i.code = e?.code ?? null, this.#I(), this.#B());
		});
	}
	#N(e, t) {
		if (this.#p === e && t) {
			if (t.kind === "opened") {
				let n = A(t.url);
				if (!n) {
					this.#F(), this.#p = {
						entryId: e.entryId,
						state: "failed",
						code: null,
						unsubscribe: null
					}, this.#B();
					return;
				}
				e.token = n, e.url = t.url, e.state = "open";
				let r = this.shadowRoot.querySelector("#frame");
				if (this.#f !== t.url) {
					delete r.dataset.ready, this.#x = !1, this.#f = t.url;
					let n = this.#d?.entryId === e.entryId ? this.#d.suffix : "";
					n && (this.#d = null), r.setAttribute("src", t.url + n);
				} else e.reopen && this.#L({
					type: S,
					open: !0
				});
				e.reopen = !1, this.#B();
			} else t.kind === "page" ? (t.page === "" || j.has(t.page)) && (e.page = t.page) : t.kind === "closed" && (this.#L({
				type: S,
				closed: !0
			}), this.#F(), this.#p = {
				entryId: e.entryId,
				state: "closed",
				code: null,
				unsubscribe: null
			}, this.#B(), this.#k());
		}
	}
	#P() {
		let e = this.#p;
		this.isConnected && e && ["opening", "open"].includes(e.state) && (this.#R(!1), this.#L({
			type: S,
			closed: !0
		}), this.#M(e.entryId, e.token, e.url), this.#B());
	}
	#F() {
		this.#R(!0);
		let e = this.#p;
		this.#p = null, e && (e.state = "ended", e.unsubscribe?.then((e) => e()).catch(() => {}), this.#I());
	}
	#I() {
		let e = this.shadowRoot.querySelector("#frame");
		delete e.dataset.ready, this.#x = !1, this.#f = null, e.removeAttribute("src");
	}
	#L(e) {
		this.shadowRoot.querySelector("#frame").contentWindow?.postMessage(e, "*");
	}
	#R(e) {
		this.#v++, this.#y = !1;
		let t = [...this.#_.values()];
		if (this.#_.clear(), e) for (let e of t) e.then((e) => e()).catch(() => {});
	}
	#z(e) {
		let t = this.shadowRoot.querySelector("#frame").contentWindow, n = e.data?.frame, r = this.#p;
		if (!t || e.data?.type !== "panel-assistant-ui" || !n || typeof n != "object") return;
		let i = e.source === null && e.origin === "null" && n.op === "unsubscribe";
		if (e.source !== t && !i) return;
		let a = this.#v, o = (e) => {
			!i && a === this.#v && this.#L({
				type: S,
				frame: e
			});
		}, s = () => o({
			command_id: n.command_id,
			outcome: "failed",
			code: "failed"
		});
		if (r?.state !== "open") {
			s();
			return;
		}
		let c = {
			type: "panel_assistant/ui",
			entry_id: r.entryId,
			request: n
		}, l = this.#t.connection;
		if (n.op === "unsubscribe") {
			let e = this.#_.get(n.args?.subscription);
			this.#_.delete(n.args?.subscription), e?.then((e) => e()).catch(() => {}), o({
				command_id: n.command_id,
				outcome: "applied"
			});
		} else if (n.op === "subscribe") {
			let e = Promise.resolve().then(() => l.subscribeMessage((e) => {
				if (e?.closed === !0) {
					if (this.#_.delete(n.command_id), a !== this.#v) return;
					this.#y = !0, this.#L({
						type: S,
						closed: !0
					});
				} else o(e);
			}, c, { resubscribe: !1 }));
			this.#_.set(n.command_id, e), e.catch(() => {
				this.#_.get(n.command_id) === e && this.#_.delete(n.command_id), s();
			});
		} else {
			let e = null;
			if (n.op === "ha.oauth.start") try {
				e = window.open("", "_blank"), e && (e.opener = null);
			} catch {
				e?.close(), e = null;
			}
			Promise.resolve().then(() => this.#t.callWS(c)).then((t) => {
				if (e && !e.closed) {
					let n;
					try {
						n = new URL(t?.result?.authorization_url);
					} catch {}
					a === this.#v && t?.outcome === "applied" && ["http:", "https:"].includes(n?.protocol) ? e.location.replace(n.href) : e.close();
				}
				o(t);
			}, () => {
				e?.close(), s();
			});
		}
	}
	#B() {
		let e = this.shadowRoot, t = this.#t?.language;
		this.isConnected && this.setAttribute?.("lang", a(t));
		let n = this.#e;
		for (let t of e.querySelectorAll("[data-message]")) t.textContent = n[t.dataset.message];
		for (let [t, r] of [
			["menu", "menu"],
			["overflow", "more"],
			["settings", "integrationSettings"],
			["github", "github"],
			["device", "device"],
			["add", "addPanel"]
		]) e.querySelector(`#${t}`).setAttribute("aria-label", n[r]), e.querySelector(`#${t}`).setAttribute("title", n[r]);
		e.querySelector("#frame").setAttribute("title", n.frameTitle), e.querySelector("#version").textContent = p(this.#n?.config, t);
		let r = e.querySelector("#panels"), i = this.#E() ? this.#o ?? [] : [], c = JSON.stringify([i, a(t)]);
		if (c !== this.#l) {
			this.#l = c, r.replaceChildren();
			for (let e of i) {
				let n = document.createElement("option");
				n.value = e.entry_id, n.textContent = e.state === "reachable" ? e.title : e.state === "restarting" ? `${e.title} · ${s(this.#e.restarting, { reason: o("sidebarReason", t)[e.reason] })}` : `${e.title} (${this.#e[e.state]})`, r.append(n);
			}
		}
		r.value = this.#u ?? "", e.querySelector("#picker").hidden = i.length === 0;
		let l = i.find((e) => e.entry_id === this.#u), u = e.querySelector("#device");
		u.hidden = !l?.device_id, l?.device_id && u.setAttribute("href", `/config/devices/device/${encodeURIComponent(l.device_id)}`);
		let d = this.#p, f = e.querySelector("#frame"), m = "", h = !1;
		this.#E() ? this.#s === "loading" ? h = !0 : this.#s === "ready" ? d?.state === "closed" && d.entryId === l?.entry_id ? m = "closed" : l?.state === "restarting" ? m = "restarting" : l?.state === "unreachable" ? m = "unreachableBody" : l?.state === "not_loaded" ? m = "notLoadedBody" : d?.state === "failed" ? m = d.code === "not_loaded" ? "notLoadedBody" : "failed" : f.dataset.ready !== "true" && (h = !0) : m = this.#s : m = "admin";
		let g = e.querySelector("#status");
		g.textContent = m === "restarting" ? s(this.#e.restarting, { reason: o("sidebarReason", t)[l.reason] }) : m ? this.#e[m] : "";
		let _ = [
			"unreachableBody",
			"notLoadedBody",
			"failed",
			"closed"
		].includes(m);
		e.querySelector("#failure").hidden = !_, e.querySelector("#failure-status").textContent = _ ? g.textContent : "", e.querySelector("#next-step").textContent = _ ? this.#e[{
			unreachableBody: "unreachableNext",
			notLoadedBody: "notLoadedNext",
			failed: "failedNext",
			closed: "closedNext"
		}[m]] : "", e.querySelector("#failure-pill").textContent = _ ? this.#e[{
			unreachableBody: "pillUnreachable",
			notLoadedBody: "pillNotLoaded",
			failed: "pillRetrying",
			closed: "pillClosed"
		}[m]] : "", g.hidden = !m || _ || m === "restarting";
		let v = e.querySelector("#loading"), y = m === "restarting";
		v.hidden = !h && !y;
		let b = e.querySelector("#loading-text");
		b.hidden = !l, (h || y) && (b.textContent = y ? g.textContent : l ? te(l.title, t) : ""), e.querySelector("#loading-hint").textContent = l && (h || y) ? n.restartingNext : n.loadingHint, e.querySelector("#loading-pill").textContent = y ? o("sidebarReason", t)[l.reason] : n.pillOpening, f.hidden = !!m || h || f.dataset.ready !== "true";
	}
};
customElements.get("panel-assistant-sidebar") || customElements.define("panel-assistant-sidebar", ae);
//#endregion
//#region src/display-provisioning.mjs
function oe(e) {
	if (typeof e != "object" || !e) return null;
	let { density: t, font_scale: n } = e;
	return !(t === null || Number.isInteger(t) && t >= 80 && t <= 640) || !(n === null || typeof n == "number" && n >= .5 && n <= 1.5) || t === null && n === null ? null : Object.freeze({
		density: t,
		fontScale: n
	});
}
//#endregion
//#region src/app-identity.mjs
var se = "io.panelassistant.android";
Object.freeze(["io.panelassistant.android.install.v1", "io.github.maxlyth.hapaneld.install.v1"]), Object.freeze(["io.panelassistant.android.buildfeed.v1", "io.github.maxlyth.hapaneld.buildfeed.v1"]);
var N = "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity", P = "io.panelassistant.android/io.panelassistant.android.MainActivity";
Object.freeze({ [se]: Object.freeze([N, P]) }), Object.freeze({
	[N]: "io.panelassistant.android/io.github.maxlyth.hapaneld.DashboardActivity",
	[P]: "io.panelassistant.android/io.panelassistant.android.DashboardActivity"
}), Object.freeze({
	[N]: Object.freeze(["io.panelassistant.android/io.github.maxlyth.hapaneld.DashboardActivity"]),
	[P]: Object.freeze(["io.panelassistant.android/.DashboardActivity", "io.panelassistant.android/io.panelassistant.android.DashboardActivity"])
});
var ce = Object.freeze({
	[N]: "io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService",
	[P]: "io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService"
});
Object.freeze({
	[N]: Object.freeze(["io.panelassistant.android/io.github.maxlyth.hapaneld.input.PanelAccessibilityService"]),
	[P]: Object.freeze(["io.panelassistant.android/.input.PanelAccessibilityService", "io.panelassistant.android/io.panelassistant.android.input.PanelAccessibilityService"])
}), Object.freeze({ [P]: Object.freeze([ce[N]]) });
var F = 262144, le = 2147483647, ue = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, de = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-(?:rc|preview)[1-9][0-9]*$/, I = /^build-([1-9][0-9]{0,9})(-successor)?$/, L = (e, t) => typeof t == "string" && t.length <= 64 && e.exec(t)?.[0] === t, R = (e) => L(ue, e), z = (e) => L(de, e), B = (e) => R(e) || z(e);
function fe(e) {
	if (!L(I, e)) return null;
	let t = Number(I.exec(e)[1]);
	return t <= le ? t : null;
}
var V = (e) => fe(e) !== null, pe = (e) => Array.from(new Uint8Array(e), (e) => e.toString(16).padStart(2, "0")).join(""), me = (e = crypto) => pe(e.getRandomValues(/* @__PURE__ */ new Uint8Array(16))), H = (e, t) => typeof t == "string" && e.exec(t)?.[0] === t, U = (e, t) => typeof e == "object" && !!e && !Array.isArray(e) && Object.keys(e).length === t.length && t.every((t) => Object.hasOwn(e, t)), W = "/api/panel_assistant/usb/release", he = "/api/panel_assistant/usb/handover", ge = "/api/panel_assistant/usb/adb_key", _e = 35e3, ve = /(?:[0-9]{1,3}\.){3}[0-9]{1,3}/, ye = /[a-z_]{1,48}/, G = 67108864, be = 18e5, K = [
	"id",
	"tag",
	"checksum",
	"checksum_signature",
	"descriptor",
	"descriptor_signature",
	"apk_size",
	"apk_sha256"
], xe = [
	"id",
	"tag",
	"feed",
	"feed_signature",
	"apk_size",
	"apk_sha256"
], q = 8192, J = Math.ceil(F / 3) * 4 + q, Y = class extends Error {
	constructor(e) {
		super(e), this.name = "HandoffError", this.code = e;
	}
};
function X(e, t = "invalid_response") {
	if (!e) throw new Y(t);
}
function Z(e, t, n = !1) {
	X(typeof e == "string" && e.length <= Math.ceil(t / 3) * 4 && H(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, e));
	let r = atob(e);
	return X(btoa(r) === e && r.length > 0 && (n ? r.length === t : r.length <= t)), Uint8Array.from(r, (e) => e.charCodeAt(0));
}
async function Q(e, t, n, r = null) {
	X(e.status === 200 && !e.redirected && e.body);
	let i = e.headers.get("content-length");
	if (i !== null) {
		X(H(/0|[1-9][0-9]*/, i));
		let e = Number(i);
		X(Number.isSafeInteger(e) && e <= t && (r === null || e === r));
	}
	let a = e.body.getReader(), o = () => {
		a.cancel().catch(() => {});
	};
	n.addEventListener("abort", o, { once: !0 });
	let s = [], c = 0;
	try {
		for (;;) {
			X(!n.aborted, "cancelled");
			let e = await a.read();
			if (X(!n.aborted, "cancelled"), e.done) break;
			c += e.value.byteLength, X(c <= t && (r === null || c <= r)), s.push(e.value);
		}
		return X(c > 0 && (i === null || c === Number(i)) && (r === null || c === r)), new Blob(s);
	} finally {
		n.removeEventListener("abort", o), o(), a.releaseLock();
	}
}
function Se(e, t, { rcTag: n = null, language: r = e?.language, onState: i = () => {}, windowObject: o = window, timeoutMs: s = 3e5 } = {}) {
	let c, l, u = new Promise((e, t) => {
		c = e, l = t;
	}), d = new AbortController(), f = !1, p, m, h, g, _ = !1, v = !1, y, b, x, S, C = !1, w = () => {
		clearInterval(x), clearTimeout(S), y = void 0, o.removeEventListener("message", A);
	}, T = (e) => {
		try {
			i(e);
		} catch {}
	}, E = (e = null) => {
		if (!f) {
			if (f = !0, d.abort(), clearTimeout(m), T(e ?? "verified"), e) {
				w(), l(new Y(e));
				return;
			}
			x = setInterval(() => {
				p.closed && w();
			}, 2e3), S = setTimeout(w, be), c();
		}
	};
	async function D() {
		try {
			T("preparing"), X(!f, "cancelled");
			let t = await e.fetchWithAuth(W, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(n === null ? {} : { release_candidate: n }),
				redirect: "error",
				signal: d.signal
			});
			X(!f, "cancelled"), X(t.headers.get("content-type")?.split(";")[0].trim() === "application/json");
			let r = await Q(t, J, d.signal), i;
			try {
				i = JSON.parse(await r.text());
			} catch {
				throw new Y("invalid_response");
			}
			let a = V(i?.tag);
			X(a || r.size <= q), X(!f, "cancelled"), X(U(i, a ? xe : K) && H(/[0-9a-f]{32}/, i.id) && typeof i.tag == "string" && i.tag.length <= 64 && (n === null ? B(i.tag) || V(i.tag) : i.tag === n) && H(/[0-9a-f]{64}/, i.apk_sha256) && Number.isSafeInteger(i.apk_size) && i.apk_size > 0 && i.apk_size <= G);
			let o = a ? {
				tag: i.tag,
				feed: Z(i.feed, F),
				feedSignature: Z(i.feed_signature, 256, !0)
			} : {
				tag: i.tag,
				checksum: Z(i.checksum, 512),
				checksumSignature: Z(i.checksum_signature, 256, !0),
				descriptor: Z(i.descriptor, 4096),
				descriptorSignature: Z(i.descriptor_signature, 256, !0)
			};
			T("downloading"), X(!f, "cancelled");
			let s = await e.fetchWithAuth(`${W}/${i.id}/apk`, {
				method: "GET",
				redirect: "error",
				signal: d.signal
			});
			X(!f, "cancelled");
			let c = await Q(s, G, d.signal, i.apk_size);
			X(!f && !p.closed, "window_closed"), v = !0, b = i.apk_sha256, y = {
				type: "ha-paneld/usb-bundle",
				nonce: g,
				bundle: o,
				apk: c
			}, p.postMessage(y, h), T("verifying");
		} catch (e) {
			E(e instanceof Y ? e.code : "delivery_failed");
		}
	}
	async function O(t) {
		if (!f || !y || p.closed || !U(t, [
			"type",
			"nonce",
			"requestId",
			"tag",
			"apkSha256"
		]) || !H(/[0-9a-f]{32}/, t.requestId)) return;
		let r = !1;
		try {
			X(t.tag === y.bundle.tag && t.apkSha256 === b);
			let i = AbortSignal.timeout(Math.min(s, 1e4)), a = await e.fetchWithAuth(W, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(n === null ? {} : { release_candidate: n }),
				redirect: "error",
				signal: i
			});
			X(a.headers.get("content-type")?.split(";")[0].trim() === "application/json");
			let o = JSON.parse(await (await Q(a, V(t.tag) ? J : q, i)).text());
			X(U(o, V(t.tag) ? xe : K) && H(/[0-9a-f]{32}/, o.id) && o.tag === t.tag && o.apk_sha256 === t.apkSha256 && o.apk_size === y.apk.size), r = !0;
		} catch {}
		if (y && !p.closed) try {
			p.postMessage({
				type: "ha-paneld/usb-admission-result",
				nonce: g,
				requestId: t.requestId,
				tag: t.tag,
				apkSha256: t.apkSha256,
				admitted: r
			}, h);
		} catch {}
	}
	async function ee(t) {
		if (C || !f || !y || p.closed || !U(t, [
			"type",
			"nonce",
			"address"
		]) || !H(ve, t.address)) return;
		C = !0;
		let n = (e, t = {}) => {
			try {
				p.closed || p.postMessage({
					type: e,
					nonce: g,
					...t
				}, h);
			} catch {}
		};
		n("ha-paneld/usb-handover-accepted");
		let r, i = null, a;
		try {
			let n = await e.fetchWithAuth(he, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ address: t.address }),
				redirect: "error",
				signal: AbortSignal.timeout(_e)
			}), o = await n.json().catch(() => null), s = n.status === 200 ? o?.outcome : o?.error;
			r = H(ye, s) ? s : `http_${n.status}`, n.status === 200 && (i = oe(o?.display)), n.status === 200 && o?.registration === "added_to_another_home_assistant" && (a = o.registration);
		} catch {
			r = "request_failed";
		} finally {
			C = !1;
		}
		n("ha-paneld/usb-handover-result", {
			outcome: r,
			...i ? { display: {
				density: i.density,
				font_scale: i.fontScale
			} } : {},
			...a ? { registration: a } : {}
		}), T("installed");
	}
	async function k() {
		let t = null;
		try {
			let n = await e.fetchWithAuth(ge, {
				method: "GET",
				redirect: "error",
				signal: AbortSignal.timeout(Math.min(s, 1e4))
			}), r = n.status === 200 ? await n.json() : null;
			typeof r?.public_key == "string" && r.public_key.length <= 1024 && (t = r.public_key);
		} catch {}
		if (y && !p.closed) try {
			p.postMessage({
				type: "ha-paneld/usb-adb-key-result",
				nonce: g,
				key: t
			}, h);
		} catch {}
	}
	function A(e) {
		if (e.source === p && e.origin === h) {
			if (e.data?.type === "ha-paneld/usb-adb-key" && e.data.nonce === g && U(e.data, ["type", "nonce"])) {
				f && k();
				return;
			}
			if (e.data?.type === "ha-paneld/usb-admission" && e.data.nonce === g) {
				O(e.data);
				return;
			}
			if (e.data?.type === "ha-paneld/usb-handover" && e.data.nonce === g) {
				ee(e.data);
				return;
			}
			if (U(e.data, ["type", "nonce"]) && e.data.nonce === g) {
				if (e.data.type === "ha-paneld/usb-ready") {
					!_ && !f ? (_ = !0, D()) : y && !p.closed && p.postMessage(y, h);
					return;
				}
				f || (e.data.type === "ha-paneld/usb-verified" && v ? E() : e.data.type === "ha-paneld/usb-error" && E("verification_failed"));
			}
		}
	}
	try {
		X(e && typeof e.fetchWithAuth == "function" && (n === null || B(n) || V(n)) && Number.isSafeInteger(s) && s > 0 && s <= 3e5, "invalid_request");
		let i = new URL(t);
		X(!i.username && !i.password && !i.hash && (i.protocol === "https:" || i.protocol === "http:" && [
			"localhost",
			"127.0.0.1",
			"[::1]"
		].includes(i.hostname)), "invalid_destination"), i.searchParams.set("lang", a(r)), h = i.origin, g = me(o.crypto), i.hash = new URLSearchParams({
			ha_origin: o.location.origin,
			nonce: g,
			rc: n ?? ""
		}).toString(), o.addEventListener("message", A), p = o.open(i.href, "_blank"), X(p, "popup_blocked"), m = setTimeout(() => E("timeout"), s), T("waiting");
	} catch (e) {
		E(e instanceof Y ? e.code : "invalid_request");
	}
	return {
		completion: u,
		cancel: () => {
			E("cancelled"), w();
		}
	};
}
//#endregion
//#region src/release-catalog.mjs
var Ce = 30, we = 500, Te = 131072;
function $(e) {
	if (!e) throw Error("Invalid release catalogue");
}
var Ee = 128, De = (e) => V(e.tag) && typeof e.prerelease == "boolean" && typeof e.name == "string" && e.name.length > 0 && e.name.length <= Ee;
function Oe(e) {
	$(U(e, ["releases"]) && Array.isArray(e.releases) && e.releases.length <= 530);
	let t = /* @__PURE__ */ new Set(), n = 0, r = 0;
	return Object.freeze(e.releases.map((e) => U(e, [
		"tag",
		"prerelease",
		"name"
	]) ? ($(De(e) && !t.has(e.tag) && ++r <= we), t.add(e.tag), Object.freeze({
		tag: e.tag,
		prerelease: e.prerelease,
		name: e.name
	})) : ($(U(e, ["tag", "prerelease"]) && typeof e.prerelease == "boolean" && (e.prerelease ? z(e.tag) : R(e.tag)) && !t.has(e.tag) && ++n <= Ce), t.add(e.tag), Object.freeze({
		tag: e.tag,
		prerelease: e.prerelease
	}))));
}
async function ke(e, { signal: t, timeoutMs: n = 725e3 } = {}) {
	let r = new AbortController(), i = () => r.abort();
	t?.addEventListener("abort", i, { once: !0 }), t?.aborted && i();
	let a = setTimeout(i, n), o, s = new Promise((e, t) => {
		o = () => t(/* @__PURE__ */ Error("Release catalogue cancelled"));
	});
	r.signal.addEventListener("abort", o, { once: !0 });
	try {
		return $(!r.signal.aborted), await Promise.race([s, (async () => {
			let t = await e.fetchWithAuth("/api/panel_assistant/usb/releases", {
				method: "GET",
				redirect: "error",
				signal: r.signal
			});
			$(t.headers.get("content-type")?.split(";")[0].trim() === "application/json");
			let n = await Q(t, Te, r.signal);
			return Oe(JSON.parse(await n.text()));
		})()]);
	} finally {
		clearTimeout(a), t?.removeEventListener("abort", i), r.signal.removeEventListener("abort", o), r.abort();
	}
}
//#endregion
//#region src/ha-install-panel.mjs
var Ae = class extends HTMLElement {
	#e;
	get #t() {
		return o("haInstall", this.#e?.language);
	}
	#n;
	#r;
	#i;
	#a = "ready";
	#o;
	#s = "loading";
	#c = [];
	constructor() {
		super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${v}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      #release{border:0;padding:0;margin:14px 0;min-width:0}
      #release legend{font-size:.92rem;font-weight:600;margin-bottom:5px}
      #release-choices{max-height:min(240px,40vh)}
    </style><main class="wiz">
      ${m}
      <ol id="journey" class="wiz-dots">${g(0)}</ol>
      <section class="wiz-step"><div class="card">
        <h2 data-message="title"></h2>
        <p class="wiz-lead" data-message="introduction"></p>
        <details id="versions"><summary data-message="otherVersions"></summary>
        <fieldset id="release" aria-describedby="catalog-status"><legend data-message="release"></legend><div id="release-choices" class="choice-list"></div></fieldset></details>
        <p id="catalog-status" role="status" aria-live="polite"></p>
        <button id="retry" class="pbtn wiz-secondary" data-message="retry"></button>
        <button id="start" class="pbtn primary wiz-primary" data-message="start"></button>
        <p id="status" role="status" aria-live="polite"></p>
        <button id="cancel" class="pbtn wiz-secondary" data-message="cancel"></button>
      </div></section>
    </main>`;
		for (let e of this.shadowRoot.querySelectorAll("[data-message]")) e.textContent = this.#t[e.dataset.message];
		this.shadowRoot.querySelector("#start").addEventListener("click", () => this.#d()), this.shadowRoot.querySelector("#cancel").addEventListener("click", () => this.#r?.cancel()), this.shadowRoot.querySelector("#retry").addEventListener("click", () => this.#l()), this.shadowRoot.querySelector("#release").addEventListener("change", () => this.#u()), this.#u();
	}
	set hass(e) {
		let t = this.#e?.user?.id !== e?.user?.id || this.#e?.user?.is_admin !== e?.user?.is_admin || this.#e?.connection !== e?.connection || this.#e?.auth !== e?.auth, n = this.#e?.language !== e?.language;
		this.#e = e;
		let r = e?.themes?.darkMode;
		typeof r == "boolean" && this.setAttribute?.("theme", r ? "dark" : "light"), t && (this.#r?.cancel(), this.#l()), (t || n) && this.#u();
	}
	set panel(e) {
		let t = this.#n?.config?.installer_url !== e?.config?.installer_url;
		t && this.#r?.cancel(), this.#n = e, t && this.#l(), this.#u();
	}
	connectedCallback() {
		this.#l();
	}
	disconnectedCallback() {
		this.#r?.cancel(), this.#i?.cancel(), this.#i = void 0, this.#o?.abort(), this.#o = void 0;
	}
	async #l() {
		if (this.#o?.abort(), this.#o = void 0, this.#c = [], this.#s = "loading", this.shadowRoot.querySelector("#release-choices").replaceChildren(), this.#u(), !this.isConnected || this.#e?.user?.is_admin !== !0 || !this.#n?.config?.installer_url) return;
		let e = new AbortController();
		this.#o = e;
		try {
			let t = await ke(this.#e, { signal: e.signal });
			if (this.#o !== e) return;
			this.#c = t, this.#s = t.length ? "ready" : "empty";
			let n = this.shadowRoot.querySelector("#release-choices");
			for (let [e, r] of [null, ...t].entries()) {
				let t = document.createElement("label"), i = document.createElement("input");
				i.type = "radio", i.name = "release", i.value = r?.tag ?? "", i.checked = e === 0;
				let a = document.createElement("span");
				t.append(i, a), n.append(t);
			}
		} catch {
			if (this.#o !== e) return;
			this.#s = "catalogError";
		} finally {
			this.#o === e && (this.#o = void 0, this.#u());
		}
	}
	#u() {
		let e = this.shadowRoot, t = this.#e?.language;
		this.isConnected && this.setAttribute?.("lang", a(t));
		for (let t of e.querySelectorAll("[data-message]")) t.textContent = this.#t[t.dataset.message];
		e.querySelector("#brand-version").textContent = p(this.#n?.config, t);
		let n = e.querySelector("#journey");
		n.setAttribute("aria-label", o("installer", t).progress), _(n, 0, document, t);
		let r = e.querySelector("#release-choices").children;
		if (r.length) {
			r[0].querySelector("span").textContent = this.#t.choose;
			for (let [e, t] of this.#c.entries()) {
				let n = t.name ? this.#t.devBuild : t.prerelease ? this.#t.testing : "";
				r[e + 1].querySelector("span").textContent = `${t.name ?? t.tag.replace(/^v/, "")}${n ? ` (${n})` : ""}`;
			}
		}
		let i = this.#e?.user?.is_admin === !0, s = typeof this.#n?.config?.installer_url == "string" && this.#n.config.installer_url.length > 0, c = this.shadowRoot.querySelector("#release input:checked")?.value, l = c === "" ? this.#c[0] : this.#c.find((e) => e.tag === c), u = this.#a === "installed";
		this.shadowRoot.querySelector("#start").hidden = u, this.shadowRoot.querySelector("#versions").hidden = u, this.shadowRoot.querySelector("#start").disabled = !i || !s || !!this.#r || !l, this.shadowRoot.querySelector("#cancel").disabled = !this.#r, this.shadowRoot.querySelector("#release").disabled = !!this.#r || this.#s !== "ready";
		let d = this.shadowRoot.querySelector("#catalog-status");
		d.textContent = i && s && this.#s !== "ready" ? this.#t[this.#s] : "", d.hidden = !d.textContent, this.shadowRoot.querySelector("#retry").hidden = !i || !s || !["catalogError", "empty"].includes(this.#s), this.shadowRoot.querySelector("#cancel").hidden = !this.#r;
		let f = i ? s ? this.#a : "unavailable" : "admin", m = this.shadowRoot.querySelector("#status");
		m.textContent = Object.hasOwn(this.#t, f) ? this.#t[f] : this.#t.failed, m.hidden = !m.textContent;
	}
	#d() {
		if (this.#r || this.#e?.user?.is_admin !== !0) return;
		let e = this.shadowRoot.querySelector("#release input:checked")?.value;
		if (!(e === "" ? this.#c[0] : this.#c.find((t) => t.tag === e)) || !this.isConnected) return;
		this.#i?.cancel(), this.#i = void 0;
		let t;
		t = Se(this.#e, this.#n?.config?.installer_url, {
			rcTag: e || null,
			language: a(this.#e?.language),
			onState: (e) => {
				t && t !== this.#r && t !== this.#i || (this.#a = e, this.#u());
			}
		}), this.#r = t, this.#u(), t.completion.then(() => {
			this.#r === t && (this.#i = t);
		}, () => {}).finally(() => {
			this.#r === t && (this.#r = void 0), this.#u();
		});
	}
};
customElements.get("panel-assistant-usb-install") || customElements.define("panel-assistant-usb-install", Ae);
//#endregion
