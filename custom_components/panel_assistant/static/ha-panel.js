const Ie = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Open navigation", choosePanel: "Panel", more: "More options", addPanel: "Add panel", integrationSettings: "Integration settings", device: "This panel's Home Assistant device", showDevice: "Show device", github: "GitHub", unreachable: "unreachable", restarting: "Restarting ({reason})", not_loaded: "not loaded", opening: "Opening {panel}…", loadingHint: "Usually takes a few seconds", empty: "No panels are attached yet.", failed: "The panel could not be opened. It will be tried again shortly.", admin: "An administrator must open this page.", unreachableBody: "Home Assistant cannot reach this panel right now.", notLoadedBody: "This panel is not loaded in Home Assistant.", closed: "This panel was closed.", frameTitle: "Panel interface", picklesStory: "Pickles the panda has escaped and is causing havoc. He’s slow and stubborn, so getting him back may take a moment.", unreachableNext: "Check that the panel is powered on and connected to your network.", notLoadedNext: "Open Integration settings to check this panel’s connection.", failedNext: "Wait a moment while we try again, or choose another panel.", closedNext: "Choose another panel, or wait for this panel to reconnect." }, we = { title: "Install ha-paneld on a panel", introduction: "Plug the panel into this computer with a USB cable. A new window will find it and install the app.", release: "Version", loading: "Loading versions…", catalogError: "The list of versions couldn’t be loaded.", empty: "No versions are available yet. Try again later.", choose: "Follow Panel Assistant’s channel (recommended)", testing: "test version", devBuild: "dev build", retry: "Try again", start: "Continue", cancel: "Cancel", ready: "", unavailable: "The installer isn’t available. Update Panel Assistant, then try again.", admin: "Ask a Home Assistant administrator to install panels.", waiting: "Continue in the new window.", preparing: "Getting the app ready…", downloading: "Getting the app ready…", verifying: "Getting the app ready…", verified: "Continue in the new window.", cancelled: "Cancelled.", popup_blocked: "Your browser blocked the new window. Allow pop-ups for this page, then press Continue.", invalid_request: "Choose a version first.", failed: "That didn’t work. Press Continue to try again." }, Ce = { title: "Set up your panel", preparing: "Getting the app ready…", preparingSlow: "Still getting the app ready. Keep the Home Assistant tab open.", connectHeading: "Connect your panel", connectBody: "Plug the panel into this computer with a USB cable, then press Find my panel.", connect: "Find my panel", allowHeading: "Allow this computer", allowBody: "Look at the panel’s screen and tap Allow.", checkingPanel: "Checking your panel…", confirmHeading: "Ready to install", confirmBody: "This installs the app and makes it your panel’s home screen. Your other apps are not touched.", install: "Install", restartedDifferentVersion: "An earlier installation attempt used a different release. That attempt has been set aside.", alreadyInstalledHeading: "Already installed", alreadyInstalledBody: "This version is already on your panel. Continue to finish setting it up; nothing is copied or reinstalled.", continueSetup: "Continue", progressHeading: "Installing", stepCopying: "Copying the app to your panel…", stepFinishingCopy: "Finishing the copy…", stepInstalling: "Installing…", stepStarting: "Starting the app…", stepPermissions: "Giving the app what it needs to run…", stepOpening: "Opening your panel’s setup…", keepConnected: "Keep the cable plugged in until this finishes.", doneHeading: "Installed", doneOpening: "Taking you to your panel’s setup…", doneManual: "Finish setting up on the panel’s screen.", openSetup: "Open panel setup", errorHeading: "That didn’t work", tryAgain: "Try again", backToHa: "Back to Home Assistant", details: "Details for support", unsupported: "This browser can’t talk to USB devices. Open this page in Chrome or Edge on a computer.", handoffFailure: "The app couldn’t be fetched from Home Assistant. Go back to Home Assistant and start again.", noSelection: "No panel was chosen. Press Find my panel and pick it from the list, or try another cable: some cables power the panel but carry no data.", connectionTimeout: "The panel didn’t answer. Check the cable is firmly in, or swap it: a cable can power the panel and still carry no data.", disconnected: "The panel was disconnected. Plug it back in, wait for it to start, then press Try again.", cancelled: "Cancelled.", pageClosed: "The page was closed.", progress: "Progress", installProgress: "Installation progress", licenses: "Third-party licenses" }, xe = { installErrorGeneric: "Something went wrong. Keep the panel plugged in and press Try again.", installErrorBusy: "The panel is busy with another install. Wait a minute, then try again.", installErrorStorage: "This browser couldn’t save its progress. Allow this site to store data, then try again.", installErrorTarget: "A different panel was connected. Plug in the same panel and try again.", installErrorArtifact: "The app download couldn’t be checked. Go back to Home Assistant and start again.", installErrorNotClean: "This panel already has the app. Update it from Home Assistant instead.", installErrorIncompatible: "This panel can’t run this version of the app.", installErrorConnection: "The connection to the panel dropped. Keep it plugged in and press Try again.", installErrorHealth: "The app is installed but hasn’t started yet. Wait a moment, then try again." }, Me = { version: "Version", connect: "Connect", install: "Install", setup: "Set up" }, Ee = { update: "update", settings: "settings", recovery: "recovery", reboot: "reboot" }, ze = { stopped: "Stopped", cause: "Cause", error: "Error", release: "Release", panel: "Panel", copied: "Copied", savedProgress: "Saved progress", earlierCopy: "Earlier copy", stagedCopy: "Staged copy", setAside: "Set aside", alreadyInstalled: "Already installed", permissions: "Permissions", setupAddress: "Setup address", setupHandover: "Setup handover", copiedDetail: "{bytes} bytes sent; waiting for the panel to confirm", none: "none", setupMissing: "not found; setup continues on the panel" }, Se = {
  sidebar: Ie,
  haInstall: we,
  installer: Ce,
  errors: xe,
  journey: Me,
  sidebarReason: Ee,
  support: ze
}, De = { title: "Panel Assistant", versionLabel: "{version} Build {build}", menu: "Navigation öffnen", choosePanel: "Panel", more: "Weitere Optionen", addPanel: "Panel hinzufügen", integrationSettings: "Integrationseinstellungen", device: "Home Assistant-Gerät dieses Panels", showDevice: "Gerät anzeigen", github: "GitHub", unreachable: "nicht erreichbar", restarting: "Neustart ({reason})", not_loaded: "nicht geladen", opening: "{panel} wird geöffnet…", loadingHint: "Dauert normalerweise nur wenige Sekunden", empty: "Es sind noch keine Panels verbunden.", failed: "Das Panel konnte nicht geöffnet werden. In Kürze wird es erneut versucht.", admin: "Diese Seite muss von einem Administrator geöffnet werden.", unreachableBody: "Home Assistant kann dieses Panel gerade nicht erreichen.", notLoadedBody: "Dieses Panel ist in Home Assistant nicht geladen.", closed: "Dieses Panel wurde geschlossen.", frameTitle: "Panel-Oberfläche", picklesStory: "Der Panda Pickles ist ausgebüxt und richtet Chaos an. Er ist langsam und stur, deshalb kann es einen Moment dauern, ihn zurückzuholen.", unreachableNext: "Prüfe, ob das Panel eingeschaltet und mit deinem Netzwerk verbunden ist.", notLoadedNext: "Öffne die Integrationseinstellungen, um die Verbindung dieses Panels zu prüfen.", failedNext: "Warte einen Moment, während wir es erneut versuchen, oder wähle ein anderes Panel.", closedNext: "Wähle ein anderes Panel oder warte, bis sich dieses Panel wieder verbindet." }, ke = { title: "ha-paneld auf einem Panel installieren", introduction: "Verbinde das Panel über ein USB-Kabel mit diesem Computer. Ein neues Fenster findet es und installiert die App.", release: "Version", loading: "Versionen werden geladen…", catalogError: "Die Versionsliste konnte nicht geladen werden.", empty: "Es sind noch keine Versionen verfügbar. Versuche es später erneut.", choose: "Dem Kanal von Panel Assistant folgen (empfohlen)", testing: "Testversion", devBuild: "Entwicklungsbuild", retry: "Erneut versuchen", start: "Weiter", cancel: "Abbrechen", ready: "", unavailable: "Das Installationsprogramm ist nicht verfügbar. Aktualisiere Panel Assistant und versuche es erneut.", admin: "Bitte einen Home Assistant-Administrator, Panels zu installieren.", waiting: "Fahre im neuen Fenster fort.", preparing: "Die App wird vorbereitet…", downloading: "Die App wird vorbereitet…", verifying: "Die App wird vorbereitet…", verified: "Fahre im neuen Fenster fort.", cancelled: "Abgebrochen.", popup_blocked: "Dein Browser hat das neue Fenster blockiert. Erlaube Pop-ups für diese Seite und drücke dann „Weiter“.", invalid_request: "Wähle zuerst eine Version.", failed: "Das hat nicht geklappt. Drücke „Weiter“, um es erneut zu versuchen." }, Pe = { title: "Dein Panel einrichten", preparing: "Die App wird vorbereitet…", preparingSlow: "Die App wird noch vorbereitet. Lass den Home Assistant-Tab geöffnet.", connectHeading: "Dein Panel verbinden", connectBody: "Verbinde das Panel über ein USB-Kabel mit diesem Computer und drücke dann „Mein Panel finden“.", connect: "Mein Panel finden", allowHeading: "Diesen Computer zulassen", allowBody: "Schau auf den Bildschirm des Panels und tippe auf „Zulassen“.", checkingPanel: "Dein Panel wird geprüft…", confirmHeading: "Bereit zur Installation", confirmBody: "Die App wird installiert und als Startbildschirm deines Panels eingerichtet. Deine anderen Apps bleiben unberührt.", install: "Installieren", restartedDifferentVersion: "Ein früherer Installationsversuch verwendete eine andere Version. Dieser Versuch wird nicht weiterverwendet.", alreadyInstalledHeading: "Bereits installiert", alreadyInstalledBody: "Diese Version ist bereits auf deinem Panel. Fahre fort, um die Einrichtung abzuschließen; es wird nichts kopiert oder neu installiert.", continueSetup: "Weiter", progressHeading: "Installation läuft", stepCopying: "Die App wird auf dein Panel kopiert…", stepFinishingCopy: "Der Kopiervorgang wird abgeschlossen…", stepInstalling: "Installation läuft…", stepStarting: "Die App wird gestartet…", stepPermissions: "Die App erhält alles, was sie zum Ausführen braucht…", stepOpening: "Die Einrichtung deines Panels wird geöffnet…", keepConnected: "Lass das Kabel angeschlossen, bis der Vorgang abgeschlossen ist.", doneHeading: "Installiert", doneOpening: "Du wirst zur Einrichtung deines Panels weitergeleitet…", doneManual: "Schließe die Einrichtung auf dem Bildschirm des Panels ab.", openSetup: "Panel-Einrichtung öffnen", errorHeading: "Das hat nicht geklappt", tryAgain: "Erneut versuchen", backToHa: "Zurück zu Home Assistant", details: "Details für den Support", unsupported: "Dieser Browser kann nicht mit USB-Geräten kommunizieren. Öffne diese Seite auf einem Computer in Chrome oder Edge.", handoffFailure: "Die App konnte nicht von Home Assistant abgerufen werden. Gehe zurück zu Home Assistant und beginne erneut.", noSelection: "Es wurde kein Panel ausgewählt. Drücke „Mein Panel finden“ und wähle es aus der Liste oder versuche ein anderes Kabel: Manche Kabel versorgen das Panel mit Strom, übertragen aber keine Daten.", connectionTimeout: "Das Panel hat nicht geantwortet. Prüfe, ob das Kabel fest sitzt, oder tausche es aus: Ein Kabel kann das Panel mit Strom versorgen, ohne Daten zu übertragen.", disconnected: "Die Verbindung zum Panel wurde getrennt. Schließe es wieder an, warte, bis es gestartet ist, und drücke dann „Erneut versuchen“.", cancelled: "Abgebrochen.", pageClosed: "Die Seite wurde geschlossen.", progress: "Fortschritt", installProgress: "Installationsfortschritt", licenses: "Lizenzen von Drittanbietern" }, Le = { installErrorGeneric: "Etwas ist schiefgegangen. Lass das Panel angeschlossen und drücke „Erneut versuchen“.", installErrorBusy: "Das Panel ist mit einer anderen Installation beschäftigt. Warte eine Minute und versuche es dann erneut.", installErrorStorage: "Dieser Browser konnte den Fortschritt nicht speichern. Erlaube dieser Website, Daten zu speichern, und versuche es erneut.", installErrorTarget: "Ein anderes Panel wurde angeschlossen. Schließe dasselbe Panel an und versuche es erneut.", installErrorArtifact: "Die heruntergeladene App konnte nicht geprüft werden. Gehe zurück zu Home Assistant und beginne erneut.", installErrorNotClean: "Die App ist bereits auf diesem Panel. Aktualisiere sie über Home Assistant.", installErrorIncompatible: "Dieses Panel kann diese Version der App nicht ausführen.", installErrorConnection: "Die Verbindung zum Panel wurde unterbrochen. Lass es angeschlossen und drücke „Erneut versuchen“.", installErrorHealth: "Die App ist installiert, aber noch nicht gestartet. Warte einen Moment und versuche es dann erneut." }, je = { version: "Version", connect: "Verbinden", install: "Installieren", setup: "Einrichten" }, He = { update: "Aktualisierung", settings: "Einstellungen", recovery: "Wiederherstellung", reboot: "Systemneustart" }, Ne = { stopped: "Gestoppt", cause: "Ursache", error: "Fehler", release: "Version", panel: "Panel", copied: "Kopiert", savedProgress: "Gespeicherter Fortschritt", earlierCopy: "Frühere Kopie", stagedCopy: "Bereitgestellte Kopie", setAside: "Zurückgestellt", alreadyInstalled: "Bereits installiert", permissions: "Berechtigungen", setupAddress: "Einrichtungsadresse", setupHandover: "Übergabe an die Einrichtung", copiedDetail: "{bytes} Bytes gesendet; Bestätigung des Panels ausstehend", none: "keine", setupMissing: "nicht gefunden; Einrichtung wird auf dem Panel fortgesetzt" }, Be = {
  sidebar: De,
  haInstall: ke,
  installer: Pe,
  errors: Le,
  journey: je,
  sidebarReason: He,
  support: Ne
}, Te = { title: "Panel Assistant", versionLabel: "{version}, compilación {build}", menu: "Abrir navegación", choosePanel: "Panel", more: "Más opciones", addPanel: "Añadir panel", integrationSettings: "Configuración de la integración", device: "Dispositivo de Home Assistant de este panel", showDevice: "Mostrar dispositivo", github: "GitHub", unreachable: "inaccesible", restarting: "Reiniciando ({reason})", not_loaded: "sin cargar", opening: "Abriendo {panel}…", loadingHint: "Suele tardar unos segundos", empty: "Aún no hay paneles vinculados.", failed: "No se pudo abrir el panel. Se volverá a intentar en breve.", admin: "Un administrador debe abrir esta página.", unreachableBody: "Home Assistant no puede acceder a este panel en este momento.", notLoadedBody: "Este panel no está cargado en Home Assistant.", closed: "Este panel se cerró.", frameTitle: "Interfaz del panel", picklesStory: "Pickles el panda se ha escapado y está causando estragos. Es lento y testarudo, así que traerlo de vuelta puede llevar un momento.", unreachableNext: "Comprueba que el panel esté encendido y conectado a tu red.", notLoadedNext: "Abre Configuración de la integración para comprobar la conexión de este panel.", failedNext: "Espera un momento mientras volvemos a intentarlo, o elige otro panel.", closedNext: "Elige otro panel o espera a que este panel vuelva a conectarse." }, Oe = { title: "Instalar ha-paneld en un panel", introduction: "Conecta el panel a este ordenador con un cable USB. Una nueva ventana lo encontrará e instalará la aplicación.", release: "Versión", loading: "Cargando versiones…", catalogError: "No se pudo cargar la lista de versiones.", empty: "Aún no hay versiones disponibles. Vuelve a intentarlo más tarde.", choose: "Seguir el canal de Panel Assistant (recomendado)", testing: "versión de prueba", devBuild: "compilación de desarrollo", retry: "Volver a intentar", start: "Continuar", cancel: "Cancelar", ready: "", unavailable: "El instalador no está disponible. Actualiza Panel Assistant y vuelve a intentarlo.", admin: "Pide a un administrador de Home Assistant que instale los paneles.", waiting: "Continúa en la nueva ventana.", preparing: "Preparando la aplicación…", downloading: "Preparando la aplicación…", verifying: "Preparando la aplicación…", verified: "Continúa en la nueva ventana.", cancelled: "Cancelado.", popup_blocked: "Tu navegador bloqueó la nueva ventana. Permite las ventanas emergentes para esta página y pulsa Continuar.", invalid_request: "Elige primero una versión.", failed: "No ha funcionado. Pulsa Continuar para volver a intentarlo." }, Re = { title: "Configura tu panel", preparing: "Preparando la aplicación…", preparingSlow: "La aplicación aún se está preparando. Mantén abierta la pestaña de Home Assistant.", connectHeading: "Conecta tu panel", connectBody: "Conecta el panel a este ordenador con un cable USB y pulsa Buscar mi panel.", connect: "Buscar mi panel", allowHeading: "Autoriza este ordenador", allowBody: "Mira la pantalla del panel y toca Permitir.", checkingPanel: "Comprobando tu panel…", confirmHeading: "Listo para instalar", confirmBody: "Esto instala la aplicación y la convierte en la pantalla de inicio de tu panel. Las demás aplicaciones no se modifican.", install: "Instalar", restartedDifferentVersion: "Un intento anterior de instalación usó una versión diferente. Ese intento se ha dejado de lado.", alreadyInstalledHeading: "Ya instalada", alreadyInstalledBody: "Esta versión ya está en tu panel. Continúa para terminar la configuración; no se copia ni se reinstala nada.", continueSetup: "Continuar", progressHeading: "Instalando", stepCopying: "Copiando la aplicación a tu panel…", stepFinishingCopy: "Terminando la copia…", stepInstalling: "Instalando…", stepStarting: "Iniciando la aplicación…", stepPermissions: "Dando a la aplicación lo que necesita para funcionar…", stepOpening: "Abriendo la configuración de tu panel…", keepConnected: "Mantén el cable conectado hasta que termine.", doneHeading: "Instalada", doneOpening: "Te estamos llevando a la configuración de tu panel…", doneManual: "Termina la configuración en la pantalla del panel.", openSetup: "Abrir la configuración del panel", errorHeading: "No ha funcionado", tryAgain: "Volver a intentar", backToHa: "Volver a Home Assistant", details: "Detalles para soporte", unsupported: "Este navegador no puede comunicarse con dispositivos USB. Abre esta página en Chrome o Edge en un ordenador.", handoffFailure: "No se pudo obtener la aplicación de Home Assistant. Vuelve a Home Assistant y empieza de nuevo.", noSelection: "No se eligió ningún panel. Pulsa Buscar mi panel y selecciónalo en la lista, o prueba con otro cable: algunos cables alimentan el panel, pero no transmiten datos.", connectionTimeout: "El panel no respondió. Comprueba que el cable esté bien conectado o cámbialo: un cable puede alimentar el panel sin transmitir datos.", disconnected: "El panel se desconectó. Vuelve a conectarlo, espera a que inicie y pulsa Volver a intentar.", cancelled: "Cancelado.", pageClosed: "La página se cerró.", progress: "Progreso", installProgress: "Progreso de la instalación", licenses: "Licencias de terceros" }, qe = { installErrorGeneric: "Algo salió mal. Mantén el panel conectado y pulsa Volver a intentar.", installErrorBusy: "El panel está ocupado con otra instalación. Espera un minuto y vuelve a intentarlo.", installErrorStorage: "Este navegador no pudo guardar el progreso. Permite que este sitio almacene datos y vuelve a intentarlo.", installErrorTarget: "Se conectó un panel diferente. Conecta el mismo panel y vuelve a intentarlo.", installErrorArtifact: "No se pudo comprobar la descarga de la aplicación. Vuelve a Home Assistant y empieza de nuevo.", installErrorNotClean: "Este panel ya tiene la aplicación. Actualízala desde Home Assistant.", installErrorIncompatible: "Este panel no puede ejecutar esta versión de la aplicación.", installErrorConnection: "Se perdió la conexión con el panel. Mantenlo conectado y pulsa Volver a intentar.", installErrorHealth: "La aplicación está instalada, pero aún no ha iniciado. Espera un momento y vuelve a intentarlo." }, Qe = { version: "Versión", connect: "Conectar", install: "Instalar", setup: "Configurar" }, Ge = { update: "actualización", settings: "configuración", recovery: "recuperación", reboot: "reinicio del sistema" }, Ue = { stopped: "Detenido", cause: "Causa", error: "Error", release: "Versión", panel: "Panel", copied: "Copiado", savedProgress: "Progreso guardado", earlierCopy: "Copia anterior", stagedCopy: "Copia preparada", setAside: "Dejado de lado", alreadyInstalled: "Ya instalada", permissions: "Permisos", setupAddress: "Dirección de configuración", setupHandover: "Transferencia a la configuración", copiedDetail: "{bytes} bytes enviados; esperando la confirmación del panel", none: "ninguno", setupMissing: "no encontrada; la configuración continúa en el panel" }, Ve = {
  sidebar: Te,
  haInstall: Oe,
  installer: Re,
  errors: qe,
  journey: Qe,
  sidebarReason: Ge,
  support: Ue
}, Fe = { title: "Panel Assistant", versionLabel: "{version}, build {build}", menu: "Ouvrir la navigation", choosePanel: "Panneau", more: "Plus d’options", addPanel: "Ajouter un panneau", integrationSettings: "Paramètres de l’intégration", device: "Appareil Home Assistant de ce panneau", showDevice: "Afficher l’appareil", github: "GitHub", unreachable: "injoignable", restarting: "Redémarrage ({reason})", not_loaded: "non chargé", opening: "Ouverture de {panel}…", loadingHint: "Prend généralement quelques secondes", empty: "Aucun panneau n’est encore rattaché.", failed: "Impossible d’ouvrir le panneau. Une nouvelle tentative aura lieu sous peu.", admin: "Un administrateur doit ouvrir cette page.", unreachableBody: "Home Assistant ne peut pas joindre ce panneau pour le moment.", notLoadedBody: "Ce panneau n’est pas chargé dans Home Assistant.", closed: "Ce panneau a été fermé.", frameTitle: "Interface du panneau", picklesStory: "Pickles le panda s’est échappé et sème la pagaille. Il est lent et têtu, il faudra donc peut-être un moment pour le ramener.", unreachableNext: "Vérifiez que le panneau est allumé et connecté à votre réseau.", notLoadedNext: "Ouvrez les paramètres de l’intégration pour vérifier la connexion de ce panneau.", failedNext: "Patientez pendant la nouvelle tentative, ou choisissez un autre panneau.", closedNext: "Choisissez un autre panneau, ou attendez que celui-ci se reconnecte." }, Ye = { title: "Installer ha-paneld sur un panneau", introduction: "Branchez le panneau à cet ordinateur avec un câble USB. Une nouvelle fenêtre le trouvera et installera l’application.", release: "Version", loading: "Chargement des versions…", catalogError: "Impossible de charger la liste des versions.", empty: "Aucune version n’est encore disponible. Réessayez plus tard.", choose: "Suivre le canal de Panel Assistant (recommandé)", testing: "version de test", devBuild: "build de développement", retry: "Réessayer", start: "Continuer", cancel: "Annuler", ready: "", unavailable: "Le programme d’installation n’est pas disponible. Mettez à jour Panel Assistant, puis réessayez.", admin: "Demandez à un administrateur Home Assistant d’installer les panneaux.", waiting: "Continuez dans la nouvelle fenêtre.", preparing: "Préparation de l’application…", downloading: "Préparation de l’application…", verifying: "Préparation de l’application…", verified: "Continuez dans la nouvelle fenêtre.", cancelled: "Annulé.", popup_blocked: "Votre navigateur a bloqué la nouvelle fenêtre. Autorisez les fenêtres contextuelles pour cette page, puis appuyez sur « Continuer ».", invalid_request: "Choisissez d’abord une version.", failed: "Cela n’a pas fonctionné. Appuyez sur « Continuer » pour réessayer." }, _e = { title: "Configurer votre panneau", preparing: "Préparation de l’application…", preparingSlow: "La préparation de l’application se poursuit. Gardez l’onglet Home Assistant ouvert.", connectHeading: "Connecter votre panneau", connectBody: "Branchez le panneau à cet ordinateur avec un câble USB, puis appuyez sur « Trouver mon panneau ».", connect: "Trouver mon panneau", allowHeading: "Autoriser cet ordinateur", allowBody: "Regardez l’écran du panneau et appuyez sur « Autoriser ».", checkingPanel: "Vérification de votre panneau…", confirmHeading: "Prêt à installer", confirmBody: "L’application sera installée et deviendra l’écran d’accueil de votre panneau. Vos autres applications ne seront pas modifiées.", install: "Installer", restartedDifferentVersion: "Une tentative d’installation précédente utilisait une autre version. Cette tentative a été mise de côté.", alreadyInstalledHeading: "Déjà installée", alreadyInstalledBody: "Cette version est déjà sur votre panneau. Continuez pour terminer sa configuration ; rien ne sera copié ni réinstallé.", continueSetup: "Continuer", progressHeading: "Installation en cours", stepCopying: "Copie de l’application sur votre panneau…", stepFinishingCopy: "Fin de la copie…", stepInstalling: "Installation…", stepStarting: "Démarrage de l’application…", stepPermissions: "L’application reçoit ce qu’il lui faut pour fonctionner…", stepOpening: "Ouverture de la configuration de votre panneau…", keepConnected: "Gardez le câble branché jusqu’à la fin.", doneHeading: "Installée", doneOpening: "Ouverture de la configuration de votre panneau…", doneManual: "Terminez la configuration sur l’écran du panneau.", openSetup: "Ouvrir la configuration du panneau", errorHeading: "Cela n’a pas fonctionné", tryAgain: "Réessayer", backToHa: "Retour à Home Assistant", details: "Détails pour l’assistance", unsupported: "Ce navigateur ne peut pas communiquer avec les appareils USB. Ouvrez cette page dans Chrome ou Edge sur un ordinateur.", handoffFailure: "Impossible de récupérer l’application depuis Home Assistant. Retournez dans Home Assistant et recommencez.", noSelection: "Aucun panneau n’a été choisi. Appuyez sur « Trouver mon panneau » et sélectionnez-le dans la liste, ou essayez un autre câble : certains alimentent le panneau sans transmettre de données.", connectionTimeout: "Le panneau n’a pas répondu. Vérifiez que le câble est bien branché, ou changez-le : un câble peut alimenter le panneau sans transmettre de données.", disconnected: "Le panneau a été déconnecté. Rebranchez-le, attendez qu’il démarre, puis appuyez sur « Réessayer ».", cancelled: "Annulé.", pageClosed: "La page a été fermée.", progress: "Progression", installProgress: "Progression de l’installation", licenses: "Licences tierces" }, We = { installErrorGeneric: "Une erreur s’est produite. Gardez le panneau branché et appuyez sur « Réessayer ».", installErrorBusy: "Le panneau est occupé par une autre installation. Attendez une minute, puis réessayez.", installErrorStorage: "Ce navigateur n’a pas pu enregistrer sa progression. Autorisez ce site à stocker des données, puis réessayez.", installErrorTarget: "Un autre panneau a été connecté. Branchez le même panneau et réessayez.", installErrorArtifact: "Le téléchargement de l’application n’a pas pu être vérifié. Retournez dans Home Assistant et recommencez.", installErrorNotClean: "L’application est déjà sur ce panneau. Mettez-la plutôt à jour depuis Home Assistant.", installErrorIncompatible: "Ce panneau ne peut pas exécuter cette version de l’application.", installErrorConnection: "La connexion au panneau a été interrompue. Gardez-le branché et appuyez sur « Réessayer ».", installErrorHealth: "L’application est installée mais n’a pas encore démarré. Patientez un moment, puis réessayez." }, Ke = { version: "Version", connect: "Connecter", install: "Installer", setup: "Configurer" }, Ze = { update: "mise à jour", settings: "paramètres", recovery: "récupération", reboot: "redémarrage du système" }, Je = { stopped: "Arrêté", cause: "Cause", error: "Erreur", release: "Version", panel: "Panneau", copied: "Copié", savedProgress: "Progression enregistrée", earlierCopy: "Copie précédente", stagedCopy: "Copie préparée", setAside: "Mis de côté", alreadyInstalled: "Déjà installée", permissions: "Autorisations", setupAddress: "Adresse de configuration", setupHandover: "Passage à la configuration", copiedDetail: "{bytes} octets envoyés ; en attente de confirmation du panneau", none: "aucun", setupMissing: "introuvable ; la configuration continue sur le panneau" }, Xe = {
  sidebar: Fe,
  haInstall: Ye,
  installer: _e,
  errors: We,
  journey: Ke,
  sidebarReason: Ze,
  support: Je
}, $e = { title: "Panel Assistant", versionLabel: "{version} build {build}", menu: "Apri la navigazione", choosePanel: "Pannello", more: "Altre opzioni", addPanel: "Aggiungi pannello", integrationSettings: "Impostazioni dell’integrazione", device: "Dispositivo Home Assistant di questo pannello", showDevice: "Mostra dispositivo", github: "GitHub", unreachable: "non raggiungibile", restarting: "Riavvio in corso ({reason})", not_loaded: "non caricato", opening: "Apertura di {panel}…", loadingHint: "Di solito bastano pochi secondi", empty: "Non è ancora collegato alcun pannello.", failed: "Non è stato possibile aprire il pannello. Riproveremo tra poco.", admin: "Questa pagina deve essere aperta da un amministratore.", unreachableBody: "Home Assistant non riesce a raggiungere questo pannello al momento.", notLoadedBody: "Questo pannello non è caricato in Home Assistant.", closed: "Questo pannello è stato chiuso.", frameTitle: "Interfaccia del pannello", picklesStory: "Pickles il panda è scappato e sta creando scompiglio. È lento e testardo, quindi potrebbe volerci un momento per riportarlo indietro.", unreachableNext: "Controlla che il pannello sia acceso e connesso alla tua rete.", notLoadedNext: "Apri Impostazioni dell’integrazione per controllare la connessione di questo pannello.", failedNext: "Attendi un momento mentre riproviamo, oppure scegli un altro pannello.", closedNext: "Scegli un altro pannello, oppure attendi che questo pannello si riconnetta." }, et = { title: "Installa ha-paneld su un pannello", introduction: "Collega il pannello a questo computer con un cavo USB. Una nuova finestra lo troverà e installerà l’app.", release: "Versione", loading: "Caricamento delle versioni…", catalogError: "Non è stato possibile caricare l’elenco delle versioni.", empty: "Non ci sono ancora versioni disponibili. Riprova più tardi.", choose: "Segui il canale di Panel Assistant (consigliato)", testing: "versione di prova", devBuild: "build di sviluppo", retry: "Riprova", start: "Continua", cancel: "Annulla", ready: "", unavailable: "Il programma di installazione non è disponibile. Aggiorna Panel Assistant, poi riprova.", admin: "Chiedi a un amministratore di Home Assistant di installare i pannelli.", waiting: "Continua nella nuova finestra.", preparing: "Preparazione dell’app…", downloading: "Preparazione dell’app…", verifying: "Preparazione dell’app…", verified: "Continua nella nuova finestra.", cancelled: "Annullato.", popup_blocked: "Il browser ha bloccato la nuova finestra. Consenti i popup per questa pagina, poi premi Continua.", invalid_request: "Scegli prima una versione.", failed: "L’operazione non è riuscita. Premi Continua per riprovare." }, tt = { title: "Configura il tuo pannello", preparing: "Preparazione dell’app…", preparingSlow: "La preparazione dell’app è ancora in corso. Tieni aperta la scheda di Home Assistant.", connectHeading: "Collega il tuo pannello", connectBody: "Collega il pannello a questo computer con un cavo USB, poi premi Trova il mio pannello.", connect: "Trova il mio pannello", allowHeading: "Autorizza questo computer", allowBody: "Guarda lo schermo del pannello e tocca Consenti.", checkingPanel: "Verifica del pannello…", confirmHeading: "Pronto per l’installazione", confirmBody: "Questa operazione installa l’app e la imposta come schermata iniziale del pannello. Le altre app non vengono modificate.", install: "Installa", restartedDifferentVersion: "Un precedente tentativo di installazione usava una versione diversa. Quel tentativo è stato accantonato.", alreadyInstalledHeading: "Già installata", alreadyInstalledBody: "Questa versione è già sul pannello. Continua per completare la configurazione; non viene copiato o reinstallato nulla.", continueSetup: "Continua", progressHeading: "Installazione in corso", stepCopying: "Copia dell’app sul pannello…", stepFinishingCopy: "Completamento della copia…", stepInstalling: "Installazione…", stepStarting: "Avvio dell’app…", stepPermissions: "Assegnazione all’app di ciò che le serve per funzionare…", stepOpening: "Apertura della configurazione del pannello…", keepConnected: "Lascia il cavo collegato fino al termine dell’operazione.", doneHeading: "Installata", doneOpening: "Apertura della configurazione del pannello…", doneManual: "Completa la configurazione sullo schermo del pannello.", openSetup: "Apri la configurazione del pannello", errorHeading: "L’operazione non è riuscita", tryAgain: "Riprova", backToHa: "Torna a Home Assistant", details: "Dettagli per l’assistenza", unsupported: "Questo browser non può comunicare con dispositivi USB. Apri questa pagina in Chrome o Edge su un computer.", handoffFailure: "Non è stato possibile recuperare l’app da Home Assistant. Torna a Home Assistant e ricomincia.", noSelection: "Non è stato scelto alcun pannello. Premi Trova il mio pannello e sceglilo dall’elenco, oppure prova un altro cavo: alcuni cavi alimentano il pannello ma non trasmettono dati.", connectionTimeout: "Il pannello non ha risposto. Controlla che il cavo sia ben inserito, oppure cambialo: un cavo può alimentare il pannello senza trasmettere dati.", disconnected: "Il pannello è stato scollegato. Ricollegalo, attendi che si avvii, poi premi Riprova.", cancelled: "Annullato.", pageClosed: "La pagina è stata chiusa.", progress: "Avanzamento", installProgress: "Avanzamento dell’installazione", licenses: "Licenze di terze parti" }, nt = { installErrorGeneric: "Si è verificato un problema. Lascia il pannello collegato e premi Riprova.", installErrorBusy: "Il pannello è impegnato in un’altra installazione. Attendi un minuto, poi riprova.", installErrorStorage: "Questo browser non è riuscito a salvare l’avanzamento. Consenti a questo sito di memorizzare dati, poi riprova.", installErrorTarget: "È stato collegato un pannello diverso. Collega lo stesso pannello e riprova.", installErrorArtifact: "Non è stato possibile verificare il download dell’app. Torna a Home Assistant e ricomincia.", installErrorNotClean: "L’app è già presente su questo pannello. Aggiornala da Home Assistant.", installErrorIncompatible: "Questo pannello non può eseguire questa versione dell’app.", installErrorConnection: "La connessione al pannello si è interrotta. Lascialo collegato e premi Riprova.", installErrorHealth: "L’app è installata ma non si è ancora avviata. Attendi un momento, poi riprova." }, at = { version: "Versione", connect: "Collega", install: "Installa", setup: "Configura" }, it = { update: "aggiornamento", settings: "impostazioni", recovery: "ripristino", reboot: "riavvio di sistema" }, st = { stopped: "Interrotto", cause: "Causa", error: "Errore", release: "Versione pubblicata", panel: "Pannello", copied: "Copiato", savedProgress: "Avanzamento salvato", earlierCopy: "Copia precedente", stagedCopy: "Copia preparata", setAside: "Accantonato", alreadyInstalled: "Già installata", permissions: "Autorizzazioni", setupAddress: "Indirizzo di configurazione", setupHandover: "Passaggio alla configurazione", copiedDetail: "{bytes} byte inviati; in attesa della conferma del pannello", none: "nessuno", setupMissing: "non trovato; la configurazione prosegue sul pannello" }, rt = {
  sidebar: $e,
  haInstall: et,
  installer: tt,
  errors: nt,
  journey: at,
  sidebarReason: it,
  support: st
}, ot = { title: "Panel Assistant", versionLabel: "{version} 构建 {build}", menu: "打开导航", choosePanel: "面板", more: "更多选项", addPanel: "添加面板", integrationSettings: "集成设置", device: "此面板的 Home Assistant 设备", showDevice: "显示设备", github: "GitHub", unreachable: "无法连接", restarting: "正在重启（{reason}）", not_loaded: "未加载", opening: "正在打开 {panel}…", loadingHint: "通常需要几秒钟", empty: "尚未接入任何面板。", failed: "无法打开面板。稍后会再次尝试。", admin: "此页面必须由管理员打开。", unreachableBody: "Home Assistant 目前无法连接到此面板。", notLoadedBody: "此面板尚未在 Home Assistant 中加载。", closed: "此面板已关闭。", frameTitle: "面板界面", picklesStory: "熊猫 Pickles 逃跑了，正在到处捣乱。他动作慢又固执，要把他带回来可能需要一点时间。", unreachableNext: "请检查面板是否已开机并连接到您的网络。", notLoadedNext: "打开集成设置，检查此面板的连接。", failedNext: "请稍候，我们正在重试；您也可以选择其他面板。", closedNext: "请选择其他面板，或等待此面板重新连接。" }, lt = { title: "在面板上安装 ha-paneld", introduction: "用 USB 数据线将面板连接到此电脑。新窗口会查找面板并安装应用。", release: "版本", loading: "正在加载版本…", catalogError: "无法加载版本列表。", empty: "目前没有可用版本。请稍后重试。", choose: "跟随 Panel Assistant 的发布渠道（推荐）", testing: "测试版本", devBuild: "开发构建", retry: "重试", start: "继续", cancel: "取消", ready: "", unavailable: "安装程序不可用。请更新 Panel Assistant 后重试。", admin: "请让 Home Assistant 管理员安装面板。", waiting: "请在新窗口中继续。", preparing: "正在准备应用…", downloading: "正在准备应用…", verifying: "正在准备应用…", verified: "请在新窗口中继续。", cancelled: "已取消。", popup_blocked: "浏览器阻止了新窗口。请允许此页面弹出窗口，然后点击“继续”。", invalid_request: "请先选择版本。", failed: "未能完成。请点击“继续”重试。" }, dt = { title: "设置您的面板", preparing: "正在准备应用…", preparingSlow: "仍在准备应用。请保持 Home Assistant 标签页打开。", connectHeading: "连接您的面板", connectBody: "用 USB 数据线将面板连接到此电脑，然后点击“查找我的面板”。", connect: "查找我的面板", allowHeading: "允许此电脑连接", allowBody: "请查看面板屏幕并点击“允许”。", checkingPanel: "正在检查您的面板…", confirmHeading: "可以开始安装", confirmBody: "这会安装应用，并将其设为面板的主屏幕。您的其他应用不会受到影响。", install: "安装", restartedDifferentVersion: "先前的一次安装尝试使用了另一个发行版本。那次尝试已搁置。", alreadyInstalledHeading: "已安装", alreadyInstalledBody: "您的面板上已有此版本。请继续完成设置；不会复制或重新安装任何内容。", continueSetup: "继续", progressHeading: "正在安装", stepCopying: "正在将应用复制到您的面板…", stepFinishingCopy: "正在完成复制…", stepInstalling: "正在安装…", stepStarting: "正在启动应用…", stepPermissions: "正在为应用提供运行所需的条件…", stepOpening: "正在打开面板设置…", keepConnected: "请保持数据线连接，直到完成。", doneHeading: "已安装", doneOpening: "正在带您进入面板设置…", doneManual: "请在面板屏幕上完成设置。", openSetup: "打开面板设置", errorHeading: "未能完成", tryAgain: "重试", backToHa: "返回 Home Assistant", details: "技术支持详细信息", unsupported: "此浏览器无法与 USB 设备通信。请在电脑上使用 Chrome 或 Edge 打开此页面。", handoffFailure: "无法从 Home Assistant 获取应用。请返回 Home Assistant 重新开始。", noSelection: "未选择面板。请点击“查找我的面板”并从列表中选择，或尝试另一根数据线：有些线缆只能给面板供电，无法传输数据。", connectionTimeout: "面板未响应。请检查线缆是否插紧，或更换线缆：有些线缆能给面板供电，却无法传输数据。", disconnected: "面板已断开连接。请重新连接，等待面板启动后点击“重试”。", cancelled: "已取消。", pageClosed: "页面已关闭。", progress: "进度", installProgress: "安装进度", licenses: "第三方许可" }, ct = { installErrorGeneric: "发生了错误。请保持面板连接，然后点击“重试”。", installErrorBusy: "面板正在进行另一项安装。请等待一分钟后重试。", installErrorStorage: "此浏览器无法保存进度。请允许此网站存储数据，然后重试。", installErrorTarget: "连接了另一个面板。请连接原来的面板后重试。", installErrorArtifact: "无法校验下载的应用。请返回 Home Assistant 重新开始。", installErrorNotClean: "此面板上已有该应用。请通过 Home Assistant 更新。", installErrorIncompatible: "此面板无法运行此版本的应用。", installErrorConnection: "与面板的连接已中断。请保持面板连接，然后点击“重试”。", installErrorHealth: "应用已安装，但尚未启动。请稍候再重试。" }, pt = { version: "版本", connect: "连接", install: "安装", setup: "设置" }, ut = { update: "更新", settings: "设置", recovery: "恢复", reboot: "系统重启" }, gt = { stopped: "已停止", cause: "原因", error: "错误", release: "发行版本", panel: "面板", copied: "已复制", savedProgress: "已保存的进度", earlierCopy: "先前的副本", stagedCopy: "暂存的副本", setAside: "已搁置", alreadyInstalled: "已安装", permissions: "权限", setupAddress: "设置地址", setupHandover: "设置交接", copiedDetail: "已发送 {bytes} 字节；正在等待面板确认", none: "无", setupMissing: "未找到；设置将在面板上继续" }, ht = {
  sidebar: ot,
  haInstall: lt,
  installer: dt,
  errors: ct,
  journey: pt,
  sidebarReason: ut,
  support: gt
}, ce = Object.freeze({ en: Se, de: Be, es: Ve, fr: Xe, it: rt, "zh-Hans": ht }), V = ce.en, mt = ["en", "de", "es", "fr", "it", "nl", "pl", "uk", "zh-Hans"];
function k(n) {
  if (typeof n != "string") return "en";
  const e = n.trim().replaceAll("_", "-").toLowerCase();
  if (e === "zh" || e === "zh-cn" || e.startsWith("zh-cn-") || e === "zh-sg" || e.startsWith("zh-sg-") || e === "zh-hans" || e.startsWith("zh-hans-")) return "zh-Hans";
  const t = e.split("-")[0];
  return mt.includes(t) ? t : "en";
}
function M(n, e, t = ce) {
  const a = V[n], i = t[k(e)]?.[n], o = (l) => [...l.matchAll(/\{([A-Za-z_]+)\}/g)].map((s) => s[1]).sort().join(",");
  return Object.fromEntries(Object.entries(a).map(([l, s]) => {
    const h = i?.[l];
    return [l, typeof h == "string" && (h || !s) && o(h) === o(s) ? h : s];
  }));
}
function T(n, e) {
  return n.replace(/\{([A-Za-z_]+)\}/g, (t, a) => Object.hasOwn(e, a) ? String(e[a]) : t);
}
Object.freeze(V.sidebar);
const ft = "data:image/png;base64,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", bt = "https://github.com/panel-assistant/ha-integration", At = "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12", pe = "panel_assistant.sidebar.entry", yt = "/config/integrations/dashboard/add?domain=panel_assistant", vt = "/config/integrations/integration/panel_assistant", It = /* @__PURE__ */ new Set(["reachable", "unreachable", "not_loaded", "restarting"]), wt = /* @__PURE__ */ new Set(["update", "settings", "recovery", "reboot"]), Ct = 5e3, xt = /* @__PURE__ */ new Set(["current", "added", "updated", "removed"]);
function Mt(n) {
  if (!n || !Array.isArray(n.panels) || n.panels.length > 200) throw Error("invalid panels");
  const e = /* @__PURE__ */ new Set();
  return n.panels.map((t) => {
    if (!t || typeof t.entry_id != "string" || !/^[A-Za-z0-9_-]{1,64}$/.test(t.entry_id) || e.has(t.entry_id) || typeof t.title != "string" || t.title.length > 256 || !It.has(t.state) || t.state === "restarting" && !wt.has(t.reason) || t.device_id !== null && t.device_id !== void 0 && typeof t.device_id != "string") throw Error("invalid panel");
    return e.add(t.entry_id), {
      entry_id: t.entry_id,
      title: t.title,
      state: t.state,
      device_id: t.device_id ?? null,
      ...t.state === "restarting" ? { reason: t.reason } : {}
    };
  });
}
function Et(n) {
  return typeof n == "string" ? n.match(/^\/api\/panel_assistant\/embed\/([A-Za-z0-9_-]{43})\/$/)?.[1] ?? null : null;
}
function J(n, e = "en") {
  const t = n?.version, a = n?.build;
  return typeof t != "string" || !/^[0-9A-Za-z.+-]{1,32}$/.test(t) || !Number.isSafeInteger(a) || a < 0 ? "" : T(M("sidebar", e).versionLabel, { version: t, build: a });
}
function zt(n, e = "en") {
  return T(M("sidebar", e).opening, { panel: typeof n == "string" ? n : "" });
}
function X(n) {
  history.pushState(null, "", n), window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !1 } }));
}
function St() {
  try {
    return localStorage.getItem(pe);
  } catch {
    return null;
  }
}
function Dt(n) {
  try {
    localStorage.setItem(pe, n);
  } catch {
  }
}
class kt extends HTMLElement {
  get #e() {
    return M("sidebar", this.#t?.language);
  }
  #t;
  #d;
  #n;
  #a = !1;
  #g;
  #i = null;
  #r = "loading";
  #o = 0;
  #u = "";
  #s = null;
  #l = null;
  #m = null;
  #f = null;
  #I = () => this.#k();
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
      <img id="icon" src="${ft}" alt="">
      <h1 id="title" data-message="title"></h1><span id="version"></span>
      <label id="picker"><span data-message="choosePanel"></span><select id="panels"></select></label>
      <button id="overflow" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="more"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,16A2,2 0 0,1 14,18A2,2 0 0,1 12,20A2,2 0 0,1 10,18A2,2 0 0,1 12,16M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,4A2,2 0 0,1 14,6A2,2 0 0,1 12,8A2,2 0 0,1 10,6A2,2 0 0,1 12,4Z"/></svg></button>
      <div id="backdrop"></div>
      <div id="more">
      <a id="device"><svg class="wide-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z"/></svg><svg class="menu-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M19,18H5V6H19M21,4H3C1.89,4 1,4.89 1,6V18A2,2 0 0,0 3,20H21A2,2 0 0,0 23,18V6C23,4.89 22.1,4 21,4Z"/></svg><span class="item-label" data-message="showDevice"></span></a>
      <div id="spacer"></div>
      <a id="github" href="${bt}" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${At}"/></svg><span class="item-label" data-message="github"></span></a>
      <a id="add"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg><span id="add-label" class="item-label" data-message="addPanel"></span></a>
      <a id="settings"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.97C19.47,12.65 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.66 15.5,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.5,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.65 4.57,12.97L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.5,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.5,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.97Z"/></svg><span class="item-label" data-message="integrationSettings"></span></a>
      </div>
      <div id="slot"></div>
    </header><div id="failure" hidden><img id="pickles" src="/panel_assistant/usb/pickles.svg" alt=""><p id="pickles-story" data-message="picklesStory"></p><p id="failure-status" role="status" aria-live="polite"></p><p id="next-step"></p></div><p id="status" role="status" aria-live="polite"></p><div id="loading" hidden><svg class="spinner" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="19" stroke="var(--divider-color,#e0e0e0)" stroke-width="4" fill="none"></circle><circle cx="24" cy="24" r="19" stroke="var(--app-header-background-color,var(--primary-color,#03a9f4))" stroke-width="4" stroke-linecap="round" stroke-dasharray="119.4" stroke-dashoffset="89.5" fill="none"></circle></svg><p id="loading-text" role="status" aria-live="polite"></p><p id="loading-hint" data-message="loadingHint"></p></div><iframe id="frame"></iframe></div>`;
    const e = this.shadowRoot;
    for (const s of e.querySelectorAll("[data-message]")) s.textContent = this.#e[s.dataset.message];
    const t = e.querySelector("#menu");
    t.setAttribute("aria-label", this.#e.menu), t.hidden = !0, t.addEventListener("click", () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: !0, composed: !0 })));
    const a = e.querySelector("#overflow");
    a.setAttribute("aria-label", this.#e.more), a.setAttribute("title", this.#e.more), a.hidden = !0, e.querySelector("#backdrop").hidden = !0, e.querySelector("#dot").hidden = !0, a.addEventListener("click", () => this.#h(!this.#w())), e.querySelector("#backdrop").addEventListener("click", () => this.#h(!1)), e.querySelector("#root").addEventListener("keydown", (s) => {
      s.key !== "Escape" || !this.#w() || (this.#h(!1), a.focus?.());
    }), e.querySelector("#frame").setAttribute("title", this.#e.frameTitle);
    const i = e.querySelector("#settings");
    i.setAttribute("aria-label", this.#e.integrationSettings), i.setAttribute("title", this.#e.integrationSettings);
    const o = e.querySelector("#github");
    o.setAttribute("aria-label", this.#e.github), o.setAttribute("title", this.#e.github), o.addEventListener("click", () => this.#h(!1));
    const l = e.querySelector("#device");
    l.setAttribute("aria-label", this.#e.device), l.setAttribute("title", this.#e.device), l.addEventListener("click", (s) => {
      const h = l.getAttribute("href");
      this.#h(!1), !(!h || s.defaultPrevented || s.button !== 0 || s.metaKey || s.ctrlKey || s.shiftKey || s.altKey) && (s.preventDefault(), X(h));
    });
    for (const [s, h] of [["add", yt], ["settings", vt]]) {
      const u = e.querySelector(`#${s}`);
      u.setAttribute("href", h), u.addEventListener("click", (g) => {
        this.#h(!1), !(g.defaultPrevented || g.button !== 0 || g.metaKey || g.ctrlKey || g.shiftKey || g.altKey) && (g.preventDefault(), X(h));
      });
    }
    e.querySelector("#panels").addEventListener("change", (s) => this.#M(s.target.value)), this.#c();
  }
  get hass() {
    return this.#t;
  }
  set hass(e) {
    const t = this.#t;
    if (this.#t = e, !!this.isConnected) {
      if (this.#b(), t?.connection !== e?.connection || t?.user?.id !== e?.user?.id || t?.user?.is_admin !== e?.user?.is_admin) {
        this.#x();
        return;
      }
      (t?.language !== e?.language || !!t?.themes?.darkMode != !!e?.themes?.darkMode) && (this.#p(), this.#v());
    }
  }
  get panel() {
    return this.#d;
  }
  set panel(e) {
    this.#d = e, this.shadowRoot.querySelector("#version").textContent = J(e?.config, this.#t?.language);
  }
  get route() {
    return this.#n;
  }
  set route(e) {
    const t = this.#n?.path;
    this.#n = e, t !== e?.path && this.#M(e?.path?.slice(1));
  }
  get narrow() {
    return this.#a;
  }
  set narrow(e) {
    this.#a = e === !0;
    const t = this.shadowRoot, a = t.querySelector("#root");
    this.#a ? a.setAttribute("data-narrow", "") : a.removeAttribute("data-narrow"), t.querySelector("#menu").hidden = !this.#a, t.querySelector("#overflow").hidden = !this.#a, t.querySelector("#title").hidden = this.#a, t.querySelector("#version").hidden = this.#a;
    const i = t.querySelector("#more");
    this.#a ? i.setAttribute("role", "menu") : i.removeAttribute("role");
    for (const o of ["device", "github", "add", "settings"]) {
      const l = t.querySelector(`#${o}`);
      this.#a ? l.setAttribute("role", "menuitem") : l.removeAttribute("role");
    }
    this.#a || this.#h(!1), this.#b();
  }
  #w() {
    return this.shadowRoot.querySelector("#more").getAttribute("data-open") !== null;
  }
  #h(e) {
    const t = this.shadowRoot, a = t.querySelector("#more"), i = e && this.#a;
    i ? a.setAttribute("data-open", "") : a.removeAttribute("data-open"), t.querySelector("#backdrop").hidden = !i, t.querySelector("#overflow").setAttribute("aria-expanded", String(i));
  }
  // Home Assistant's menu button shows a dot while persistent notifications exist; on a phone this
  // header replaces it, so it keeps the dot from the same subscription.
  #b() {
    const e = this.isConnected && this.#a ? this.#t?.connection : void 0, t = this.#f;
    if (t?.connection === e || (t && (this.#f = null, t.unsubscribe?.then((i) => i()).catch(() => {
    }), this.shadowRoot.querySelector("#dot").hidden = !0), !e?.subscribeMessage)) return;
    const a = { connection: e, notifications: {}, unsubscribe: null };
    this.#f = a, a.unsubscribe = Promise.resolve().then(() => e.subscribeMessage((i) => this.#S(a, i), { type: "persistent_notification/subscribe" })), a.unsubscribe.catch(() => {
    });
  }
  #S(e, t) {
    if (!(this.#f !== e || !xt.has(t?.type) || !t.notifications || typeof t.notifications != "object")) {
      if (t.type === "current") e.notifications = { ...t.notifications };
      else if (t.type === "removed") for (const a of Object.keys(t.notifications)) delete e.notifications[a];
      else e.notifications = { ...e.notifications, ...t.notifications };
      this.shadowRoot.querySelector("#dot").hidden = Object.keys(e.notifications).length === 0;
    }
  }
  connectedCallback() {
    clearInterval(this.#m), this.#x(), this.#b(), this.#m = setInterval(() => this.#y(), Ct);
  }
  disconnectedCallback() {
    clearInterval(this.#m), this.#m = null, this.#o++, this.#C(), this.#p(), this.#b();
  }
  #A() {
    return this.#t?.user?.is_admin === !0;
  }
  #C() {
    this.#g?.removeEventListener?.("ready", this.#I), this.#g = void 0;
  }
  #x() {
    this.#C(), this.#p(), this.#i = null, this.#u = "", this.#r = "loading", this.#A() && this.#t.connection && (this.#g = this.#t.connection, this.#g.addEventListener("ready", this.#I)), this.#c(), this.#y();
  }
  async #y() {
    const e = ++this.#o;
    if (!this.#A()) {
      this.#c();
      return;
    }
    let t;
    try {
      let a;
      try {
        a = await this.#t.callWS({ type: "panel_assistant/embed_panels" });
      } catch (i) {
        if (this.#i) return;
        throw i;
      }
      t = Mt(a);
    } catch {
      if (e !== this.#o) return;
      this.#i = null, this.#r = "failed", this.#p(), this.#c();
      return;
    }
    if (e === this.#o) {
      if (this.#i = t, this.#r = t.length ? "ready" : "empty", !t.some((a) => a.entry_id === this.#s)) {
        const a = this.#n?.path?.slice(1), i = t.some((o) => o.entry_id === a) ? a : St();
        this.#s = t.some((o) => o.entry_id === i) ? i : t[0]?.entry_id ?? null;
      }
      this.#v();
    }
  }
  #M(e) {
    !this.#i?.some((t) => t.entry_id === e) || e === this.#s || (this.#s = e, Dt(e), this.#p(), this.#v());
  }
  // Opens a session when the selected panel is reachable and none is live for it.
  #v() {
    const e = this.#i?.find((a) => a.entry_id === this.#s), t = this.#l;
    !e || e.state !== "reachable" ? t && !(t.state === "closed" && t.entryId === e?.entry_id) && this.#p() : (!t || t.entryId !== e.entry_id || !["opening", "open"].includes(t.state)) && (this.#p(), this.#E(e.entry_id, null, null)), this.#c();
  }
  #E(e, t, a) {
    const i = this.#t, o = { entryId: e, token: t, url: a, state: "opening", code: null, unsubscribe: null };
    this.#l = o;
    const l = {
      type: "panel_assistant/embed_session",
      entry_id: e,
      language: i.language,
      theme: i.themes?.darkMode ? "dark" : "light",
      ...t ? { resume: t } : {}
    };
    o.unsubscribe = Promise.resolve().then(() => i.connection.subscribeMessage((s) => this.#D(o, s), l, { resubscribe: !1 })), o.unsubscribe.catch((s) => {
      this.#l === o && (o.state = "failed", o.code = s?.code ?? null, this.#z(), this.#c());
    });
  }
  #D(e, t) {
    if (!(this.#l !== e || !t))
      if (t.kind === "opened") {
        const a = Et(t.url);
        if (!a) {
          this.#p(), this.#l = { entryId: e.entryId, state: "failed", code: null, unsubscribe: null }, this.#c();
          return;
        }
        e.token = a, e.url = t.url, e.state = "open";
        const i = this.shadowRoot.querySelector("#frame");
        i.getAttribute("src") !== t.url && i.setAttribute("src", t.url), this.#c();
      } else t.kind === "closed" && (this.#p(), this.#l = { entryId: e.entryId, state: "closed", code: null, unsubscribe: null }, this.#c(), this.#y());
  }
  // The connection came back; subscriptions made with resubscribe:false are gone, and
  // their unsubscribe functions must not be called: command ids restart per socket.
  #k() {
    const e = this.#l;
    !this.isConnected || !e || !["opening", "open"].includes(e.state) || (this.#E(e.entryId, e.token, e.url), this.#c());
  }
  #p() {
    const e = this.#l;
    this.#l = null, e && (e.state = "ended", e.unsubscribe?.then((t) => t()).catch(() => {
    }), this.#z());
  }
  #z() {
    this.shadowRoot.querySelector("#frame").removeAttribute("src");
  }
  #c() {
    const e = this.shadowRoot, t = this.#t?.language;
    this.isConnected && this.setAttribute?.("lang", k(t));
    const a = this.#e;
    for (const f of e.querySelectorAll("[data-message]")) f.textContent = a[f.dataset.message];
    for (const [f, b] of [["menu", "menu"], ["overflow", "more"], ["settings", "integrationSettings"], ["github", "github"], ["device", "device"], ["add", "addPanel"]])
      e.querySelector(`#${f}`).setAttribute("aria-label", a[b]), e.querySelector(`#${f}`).setAttribute("title", a[b]);
    e.querySelector("#frame").setAttribute("title", a.frameTitle), e.querySelector("#version").textContent = J(this.#d?.config, t);
    const i = e.querySelector("#panels"), o = this.#A() ? this.#i ?? [] : [], l = JSON.stringify([o, k(t)]);
    if (l !== this.#u) {
      this.#u = l, i.replaceChildren();
      for (const f of o) {
        const b = document.createElement("option");
        b.value = f.entry_id, b.textContent = f.state === "reachable" ? f.title : f.state === "restarting" ? `${f.title} (${T(this.#e.restarting, { reason: M("sidebarReason", t)[f.reason] })})` : `${f.title} (${this.#e[f.state]})`, i.append(b);
      }
    }
    i.value = this.#s ?? "", e.querySelector("#picker").hidden = o.length === 0;
    const s = o.find((f) => f.entry_id === this.#s), h = e.querySelector("#device");
    h.hidden = !s?.device_id, s?.device_id && h.setAttribute("href", `/config/devices/device/${encodeURIComponent(s.device_id)}`);
    const u = this.#l, g = e.querySelector("#frame");
    let d = "", p = !1;
    this.#A() ? this.#r === "loading" ? p = !0 : this.#r !== "ready" ? d = this.#r : u?.state === "closed" && u.entryId === s?.entry_id ? d = "closed" : s?.state === "restarting" ? d = "restarting" : s?.state === "unreachable" ? d = "unreachableBody" : s?.state === "not_loaded" ? d = "notLoadedBody" : u?.state === "failed" ? d = u.code === "not_loaded" ? "notLoadedBody" : "failed" : u?.state !== "open" && !g.getAttribute("src") && (p = !0) : d = "admin";
    const A = e.querySelector("#status");
    A.textContent = d === "restarting" ? T(this.#e.restarting, { reason: M("sidebarReason", t)[s.reason] }) : d ? this.#e[d] : "";
    const y = ["unreachableBody", "notLoadedBody", "failed", "closed"].includes(d);
    e.querySelector("#failure").hidden = !y, e.querySelector("#failure-status").textContent = y ? A.textContent : "", e.querySelector("#next-step").textContent = y ? this.#e[{ unreachableBody: "unreachableNext", notLoadedBody: "notLoadedNext", failed: "failedNext", closed: "closedNext" }[d]] : "", A.hidden = !d || y;
    const C = e.querySelector("#loading");
    C.hidden = !p;
    const P = e.querySelector("#loading-text");
    P.hidden = !s, p && (P.textContent = s ? zt(s.title, t) : ""), g.hidden = !g.getAttribute("src");
  }
}
customElements.get("panel-assistant-sidebar") || customElements.define("panel-assistant-sidebar", kt);
const F = "io.github.maxlyth.hapaneld", Y = "io.panelassistant.android", Pt = "io.panelassistant.android/io.github.maxlyth.hapaneld.MainActivity", Lt = "io.panelassistant.android/io.panelassistant.android.MainActivity", jt = Object.freeze({
  [F]: Object.freeze(["io.github.maxlyth.hapaneld/.MainActivity"]),
  [Y]: Object.freeze([Pt, Lt])
});
jt[F][0];
const ue = 64, ge = 256 * 1024, Ht = 2147483647, Nt = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, Bt = /^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc[1-9][0-9]*$/, $ = /^build-([1-9][0-9]{0,9})(-successor)?$/, Tt = /^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$/, O = (n, e) => typeof e == "string" && e.length <= ue && n.exec(e)?.[0] === e, he = (n) => O(Nt, n), me = (n) => O(Bt, n), ee = (n) => he(n) || me(n);
function fe(n) {
  if (!O($, n)) return null;
  const e = Number($.exec(n)[1]);
  return e <= Ht ? e : null;
}
const D = (n) => fe(n) !== null, Ot = (n) => D(n) ? n.endsWith("-successor") ? Y : F : null, Rt = (n) => O(Tt, n), qt = (n, e) => `${n} build ${e}`, q = "/api/panel_assistant/usb/release", Qt = "/api/panel_assistant/usb/handover", Gt = 35e3, Ut = /(?:[0-9]{1,3}\.){3}[0-9]{1,3}/, Vt = /[a-z_]{1,48}/, te = 64 * 1024 * 1024, Ft = 1800 * 1e3, ne = [
  "id",
  "tag",
  "checksum",
  "checksum_signature",
  "descriptor",
  "descriptor_signature",
  "apk_size",
  "apk_sha256"
], ae = ["id", "tag", "feed", "feed_signature", "apk_size", "apk_sha256"], U = 8192, ie = Math.ceil(ge / 3) * 4 + U, E = (n, e) => typeof e == "string" && n.exec(e)?.[0] === e, j = (n, e) => n !== null && typeof n == "object" && !Array.isArray(n) && Object.keys(n).length === e.length && e.every((t) => Object.hasOwn(n, t));
class H extends Error {
  constructor(e) {
    super(e), this.name = "HandoffError", this.code = e;
  }
}
function m(n, e = "invalid_response") {
  if (!n) throw new H(e);
}
function S(n, e, t = !1) {
  m(typeof n == "string" && n.length <= Math.ceil(e / 3) * 4 && E(/(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?/, n));
  const a = atob(n);
  return m(btoa(a) === n && a.length > 0 && (t ? a.length === e : a.length <= e)), Uint8Array.from(a, (i) => i.charCodeAt(0));
}
async function Q(n, e, t, a = null) {
  m(n.status === 200 && !n.redirected && n.body);
  const i = n.headers.get("content-length");
  if (i !== null) {
    m(E(/0|[1-9][0-9]*/, i));
    const u = Number(i);
    m(Number.isSafeInteger(u) && u <= e && (a === null || u === a));
  }
  const o = n.body.getReader(), l = () => {
    o.cancel().catch(() => {
    });
  };
  t.addEventListener("abort", l, { once: !0 });
  const s = [];
  let h = 0;
  try {
    for (; ; ) {
      m(!t.aborted, "cancelled");
      const u = await o.read();
      if (m(!t.aborted, "cancelled"), u.done) break;
      h += u.value.byteLength, m(h <= e && (a === null || h <= a)), s.push(u.value);
    }
    return m(h > 0 && (i === null || h === Number(i)) && (a === null || h === a)), new Blob(s);
  } finally {
    t.removeEventListener("abort", l), l(), o.releaseLock();
  }
}
function Yt(n, e, {
  rcTag: t = null,
  language: a = n?.language,
  onState: i = () => {
  },
  windowObject: o = window,
  timeoutMs: l = 3e5
} = {}) {
  let s, h;
  const u = new Promise((r, v) => {
    s = r, h = v;
  }), g = new AbortController();
  let d = !1, p, A, y, C, P = !1, f = !1, b, _, W, K, R = !1;
  const N = () => {
    clearInterval(W), clearTimeout(K), b = void 0, o.removeEventListener("message", Z);
  }, L = (r) => {
    try {
      i(r);
    } catch {
    }
  }, z = (r = null) => {
    if (!d) {
      if (d = !0, g.abort(), clearTimeout(A), L(r ?? "verified"), r) {
        N(), h(new H(r));
        return;
      }
      W = setInterval(() => {
        p.closed && N();
      }, 2e3), K = setTimeout(N, Ft), s();
    }
  };
  async function be() {
    try {
      L("preparing"), m(!d, "cancelled");
      const r = await n.fetchWithAuth(q, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(t === null ? {} : { release_candidate: t }),
        redirect: "error",
        signal: g.signal
      });
      m(!d, "cancelled"), m(r.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const v = await Q(r, ie, g.signal);
      let c;
      try {
        c = JSON.parse(await v.text());
      } catch {
        throw new H("invalid_response");
      }
      const I = D(c?.tag);
      m(I || v.size <= U), m(!d, "cancelled"), m(j(c, I ? ae : ne) && E(/[0-9a-f]{32}/, c.id) && typeof c.tag == "string" && c.tag.length <= ue && (t === null ? ee(c.tag) || D(c.tag) : c.tag === t) && E(/[0-9a-f]{64}/, c.apk_sha256) && Number.isSafeInteger(c.apk_size) && c.apk_size > 0 && c.apk_size <= te);
      const w = I ? {
        tag: c.tag,
        feed: S(c.feed, ge),
        feedSignature: S(c.feed_signature, 256, !0)
      } : {
        tag: c.tag,
        checksum: S(c.checksum, 512),
        checksumSignature: S(c.checksum_signature, 256, !0),
        descriptor: S(c.descriptor, 4096),
        descriptorSignature: S(c.descriptor_signature, 256, !0)
      };
      L("downloading"), m(!d, "cancelled");
      const B = await n.fetchWithAuth(`${q}/${c.id}/apk`, {
        method: "GET",
        redirect: "error",
        signal: g.signal
      });
      m(!d, "cancelled");
      const ve = await Q(B, te, g.signal, c.apk_size);
      m(!d && !p.closed, "window_closed"), f = !0, _ = c.apk_sha256, b = { type: "ha-paneld/usb-bundle", nonce: C, bundle: w, apk: ve }, p.postMessage(b, y), L("verifying");
    } catch (r) {
      z(r instanceof H ? r.code : "delivery_failed");
    }
  }
  async function Ae(r) {
    if (!d || !b || p.closed || !j(r, ["type", "nonce", "requestId", "tag", "apkSha256"]) || !E(/[0-9a-f]{32}/, r.requestId)) return;
    let v = !1;
    try {
      m(r.tag === b.bundle.tag && r.apkSha256 === _);
      const c = AbortSignal.timeout(Math.min(l, 1e4)), I = await n.fetchWithAuth(q, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(t === null ? {} : { release_candidate: t }),
        redirect: "error",
        signal: c
      });
      m(I.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const w = JSON.parse(await (await Q(
        I,
        D(r.tag) ? ie : U,
        c
      )).text());
      m(j(w, D(r.tag) ? ae : ne) && E(/[0-9a-f]{32}/, w.id) && w.tag === r.tag && w.apk_sha256 === r.apkSha256 && w.apk_size === b.apk.size), v = !0;
    } catch {
    }
    if (!(!b || p.closed))
      try {
        p.postMessage({
          type: "ha-paneld/usb-admission-result",
          nonce: C,
          requestId: r.requestId,
          tag: r.tag,
          apkSha256: r.apkSha256,
          admitted: v
        }, y);
      } catch {
      }
  }
  async function ye(r) {
    if (R || !d || !b || p.closed || !j(r, ["type", "nonce", "address"]) || !E(Ut, r.address)) return;
    R = !0;
    const v = (I, w = {}) => {
      try {
        p.closed || p.postMessage({ type: I, nonce: C, ...w }, y);
      } catch {
      }
    };
    v("ha-paneld/usb-handover-accepted");
    let c;
    try {
      const I = await n.fetchWithAuth(Qt, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address: r.address }),
        redirect: "error",
        signal: AbortSignal.timeout(Gt)
      }), w = await I.json().catch(() => null), B = I.status === 200 ? w?.outcome : w?.error;
      c = E(Vt, B) ? B : `http_${I.status}`;
    } catch {
      c = "request_failed";
    } finally {
      R = !1;
    }
    v("ha-paneld/usb-handover-result", { outcome: c });
  }
  function Z(r) {
    if (!(r.source !== p || r.origin !== y)) {
      if (r.data?.type === "ha-paneld/usb-admission" && r.data.nonce === C) {
        Ae(r.data);
        return;
      }
      if (r.data?.type === "ha-paneld/usb-handover" && r.data.nonce === C) {
        ye(r.data);
        return;
      }
      if (!(!j(r.data, ["type", "nonce"]) || r.data.nonce !== C)) {
        if (r.data.type === "ha-paneld/usb-ready") {
          !P && !d ? (P = !0, be()) : b && !p.closed && p.postMessage(b, y);
          return;
        }
        d || (r.data.type === "ha-paneld/usb-verified" && f ? z() : r.data.type === "ha-paneld/usb-error" && z("verification_failed"));
      }
    }
  }
  try {
    m(n && typeof n.fetchWithAuth == "function" && (t === null || ee(t) || D(t)) && Number.isSafeInteger(l) && l > 0 && l <= 3e5, "invalid_request");
    const r = new URL(e);
    m(!r.username && !r.password && !r.hash && (r.protocol === "https:" || r.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(r.hostname)), "invalid_destination"), r.searchParams.set("lang", k(a)), y = r.origin;
    const v = new Uint8Array(16);
    o.crypto.getRandomValues(v), C = Array.from(v, (c) => c.toString(16).padStart(2, "0")).join(""), r.hash = new URLSearchParams({ ha_origin: o.location.origin, nonce: C, rc: t ?? "" }).toString(), o.addEventListener("message", Z), p = o.open(r.href, "_blank"), m(p, "popup_blocked"), A = setTimeout(() => z("timeout"), l), L("waiting");
  } catch (r) {
    z(r instanceof H ? r.code : "invalid_request");
  }
  return { completion: u, cancel: () => {
    z("cancelled"), N();
  } };
}
const se = 30, re = 500, oe = 128 * 1024, _t = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?$/, G = (n, e) => n !== null && typeof n == "object" && !Array.isArray(n) && Object.keys(n).length === e.length && e.every((t) => Object.hasOwn(n, t));
function x(n) {
  if (!n) throw new Error("Invalid release catalogue");
}
function Wt(n) {
  const e = fe(n.tag), t = typeof n.name == "string" ? n.name.split(" ")[0] : null, a = typeof t == "string" ? _t.exec(t) : null, i = a?.[4], o = Ot(n.tag) === Y ? " (Panel Assistant)" : "";
  return e !== null && a !== null && a[0] === t && typeof n.prerelease == "boolean" && n.prerelease === !!i && (!i || i.split(".").every((l) => l && !/^0[0-9]+$/.test(l))) && Rt(t) && n.name === `${qt(t, e)}${o}`;
}
function Kt(n) {
  x(G(n, ["releases"]) && Array.isArray(n.releases) && n.releases.length <= se + re);
  const e = /* @__PURE__ */ new Set();
  let t = 0, a = 0;
  return Object.freeze(n.releases.map((i) => G(i, ["tag", "prerelease", "name"]) ? (x(Wt(i) && !e.has(i.tag) && ++a <= re), e.add(i.tag), Object.freeze({ tag: i.tag, prerelease: i.prerelease, name: i.name })) : (x(G(i, ["tag", "prerelease"]) && typeof i.prerelease == "boolean" && (i.prerelease ? me(i.tag) : he(i.tag)) && !e.has(i.tag) && ++t <= se), e.add(i.tag), Object.freeze({ tag: i.tag, prerelease: i.prerelease }))));
}
async function Zt(n, { signal: e, timeoutMs: t = 15e3 } = {}) {
  const a = new AbortController(), i = () => a.abort();
  e?.addEventListener("abort", i, { once: !0 }), e?.aborted && i();
  const o = setTimeout(i, t);
  let l, s;
  const h = new Promise((u, g) => {
    s = () => g(new Error("Release catalogue cancelled"));
  });
  a.signal.addEventListener("abort", s, { once: !0 });
  try {
    return x(!a.signal.aborted), await Promise.race([h, (async () => {
      const u = await n.fetchWithAuth("/api/panel_assistant/usb/releases", {
        method: "GET",
        redirect: "error",
        signal: a.signal
      });
      x(!a.signal.aborted && u.status === 200 && !u.redirected && u.body && u.headers.get("content-type")?.split(";")[0].trim() === "application/json");
      const g = u.headers.get("content-length");
      x(g === null || /^(0|[1-9][0-9]*)$/.exec(g)?.[0] === g && Number(g) <= oe), l = u.body.getReader();
      const d = [];
      let p = 0;
      for (; ; ) {
        const A = await l.read();
        if (x(!a.signal.aborted), A.done) break;
        p += A.value.byteLength, x(p <= oe), d.push(A.value);
      }
      return x(p > 0 && (g === null || p === Number(g))), Kt(JSON.parse(await new Blob(d).text()));
    })()]);
  } finally {
    clearTimeout(o), e?.removeEventListener("abort", i), a.signal.removeEventListener("abort", s), a.abort(), l && l.cancel().catch(() => {
    });
  }
}
const Jt = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDgiIGhlaWdodD0iMTA4IiB2aWV3Qm94PSIwIDAgMTA4IDEwOCI+CjxwYXRoIGQ9Ik0yOCwzMiBoNTIgYTQsNCAwIDAgMSA0LDQgdjM3IGE0LDQgMCAwIDEgLTQsNCBoLTUyIGE0LDQgMCAwIDEgLTQsLTQgdi0zNyBhNCw0IDAgMCAxIDQsLTQgeiIgZmlsbD0iIzM3NDc0RiIvPgo8cGF0aCBkPSJNMjksMzUgaDUwIGEyLDIgMCAwIDEgMiwyIHYzNSBhMiwyIDAgMCAxIC0yLDIgaC01MCBhMiwyIDAgMCAxIC0yLC0yIHYtMzUgYTIsMiAwIDAgMSAyLC0yIHoiIGZpbGw9IiMwRTE2MjAiLz4KPGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoNDIuMDAsNDIuNTApIHNjYWxlKDAuMTAwMCkiPgo8cGF0aCBmaWxsPSIjRjJGNEY5IiBkPSJNMjQwIDIyNC44MTNDMjQwIDIzMy4wNjMgMjMzLjI1IDIzOS44MTMgMjI1IDIzOS44MTNIMTVDNi43NSAyMzkuODEzIDAgMjMzLjA2MyAwIDIyNC44MTNWMTM0LjgxM0MwIDEyNi41NjMgNC43NyAxMTUuMDQzIDEwLjYxIDEwOS4yMDNMMTA5LjM5IDEwLjQyM0MxMTUuMjIgNC41OTMwNCAxMjQuNzcgNC41OTMwNCAxMzAuNiAxMC40MjNMMjI5LjM5IDEwOS4yMTNDMjM1LjIyIDExNS4wNDMgMjQwIDEyNi41NzMgMjQwIDEzNC44MjNWMjI0LjgyM1YyMjQuODEzWiIvPgo8cGF0aCBmaWxsPSIjMThCQ0YyIiBkPSJNMjI5LjM5IDEwOS4yMDNMMTMwLjYxIDEwLjQyM0MxMjQuNzggNC41OTMwNCAxMTUuMjMgNC41OTMwNCAxMDkuNCAxMC40MjNMMTAuNjEgMTA5LjIwM0M0Ljc4IDExNS4wMzMgMCAxMjYuNTYzIDAgMTM0LjgxM1YyMjQuODEzQzAgMjMzLjA2MyA2Ljc1IDIzOS44MTMgMTUgMjM5LjgxM0gxMDcuMjdMNjYuNjQgMTk5LjE4M0M2NC41NSAxOTkuOTAzIDYyLjMyIDIwMC4zMTMgNjAgMjAwLjMxM0M0OC43IDIwMC4zMTMgMzkuNSAxOTEuMTEzIDM5LjUgMTc5LjgxM0MzOS41IDE2OC41MTMgNDguNyAxNTkuMzEzIDYwIDE1OS4zMTNDNzEuMyAxNTkuMzEzIDgwLjUgMTY4LjUxMyA4MC41IDE3OS44MTNDODAuNSAxODIuMTQzIDgwLjA5IDE4NC4zNzMgNzkuMzcgMTg2LjQ2M0wxMTEgMjE4LjA5M1YxMDIuMjEzQzEwNC4yIDk4Ljg3MyA5OS41IDkxLjg5MyA5OS41IDgzLjgyM0M5OS41IDcyLjUyMyAxMDguNyA2My4zMjMgMTIwIDYzLjMyM0MxMzEuMyA2My4zMjMgMTQwLjUgNzIuNTIzIDE0MC41IDgzLjgyM0MxNDAuNSA5MS44OTMgMTM1LjggOTguODczIDEyOSAxMDIuMjEzVjE4My40ODNMMTYwLjQ2IDE1Mi4wMjNDMTU5Ljg0IDE1MC4wNjMgMTU5LjUgMTQ3Ljk4MyAxNTkuNSAxNDUuODIzQzE1OS41IDEzNC41MjMgMTY4LjcgMTI1LjMyMyAxODAgMTI1LjMyM0MxOTEuMyAxMjUuMzIzIDIwMC41IDEzNC41MjMgMjAwLjUgMTQ1LjgyM0MyMDAuNSAxNTcuMTIzIDE5MS4zIDE2Ni4zMjMgMTgwIDE2Ni4zMjNDMTc3LjUgMTY2LjMyMyAxNzUuMTIgMTY1Ljg1MyAxNzIuOTEgMTY1LjAzM0wxMjkgMjA4Ljk0M1YyMzkuODIzSDIyNUMyMzMuMjUgMjM5LjgyMyAyNDAgMjMzLjA3MyAyNDAgMjI0LjgyM1YxMzQuODIzQzI0MCAxMjYuNTczIDIzNS4yMyAxMTUuMDUzIDIyOS4zOSAxMDkuMjEzVjEwOS4yMDNaIi8+CjwvZz4KPC9zdmc+Cg==", Xt = Object.freeze(Object.values(M("journey", "en")));
function $t(n) {
  return Xt.map((e, t) => t < n ? `<li class="done">${e}</li>` : t === n ? `<li class="current" aria-current="step">${e}</li>` : `<li>${e}</li>`).join("");
}
function en(n, e, t = document, a = "en") {
  const i = Object.values(M("journey", a)).map((o, l) => {
    const s = t.createElement("li");
    return s.textContent = o, l < e && (s.className = "done"), l === e && (s.className = "current", s.setAttribute("aria-current", "step")), s;
  });
  n.replaceChildren(...i);
}
const le = "--bg:#f2f3f5;--card:#fff;--card-head:#e7ebef;--card-border:#d9dde3;--divider:#e4e7ec;--input-bg:#fafbfc;--border:#c4cad2;--border-strong:#b6bec8;--text:#1b2430;--dim:#6a7480;--accent:#1e56a8;--ok:#3f7d49;--bad:#a02c20;--disabled-bg:#e2e5e9;--disabled-fg:#9aa3ad;--shadow:rgba(0,0,0,.18)", de = "--bg:#111;--card:#181818;--card-head:#222;--card-border:#242424;--divider:#2a2a2a;--input-bg:#161616;--border:#383838;--border-strong:#444;--text:#eee;--dim:#888;--accent:#9af;--ok:#8a8;--bad:#ffb3a6;--disabled-bg:#222;--disabled-fg:#666;--shadow:#000", tn = `
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
class nn extends HTMLElement {
  #e;
  get #t() {
    return M("haInstall", this.#e?.language);
  }
  #d;
  #n;
  // A finished transfer keeps answering a reloaded installer window until this
  // page goes away or a new transfer starts.
  #a;
  #g = "ready";
  #i;
  #r = "loading";
  #o = [];
  constructor() {
    super(), this.attachShadow({ mode: "open" }), this.shadowRoot.innerHTML = `<style>${tn}
      :host{display:block;min-height:100%;background:var(--bg);padding:24px 16px}
      .card p.status{color:var(--text);margin:14px 0 0}
    </style><main class="wiz">
      <div class="wiz-brand"><img src="${Jt}" alt=""><span>ha-paneld</span></div>
      <ol id="journey" class="wiz-dots">${$t(0)}</ol>
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
      e.textContent = this.#t[e.dataset.message];
    this.shadowRoot.querySelector("#start").addEventListener("click", () => this.#l()), this.shadowRoot.querySelector("#cancel").addEventListener("click", () => this.#n?.cancel()), this.shadowRoot.querySelector("#retry").addEventListener("click", () => this.#u()), this.shadowRoot.querySelector("#release").addEventListener("change", () => this.#s()), this.#s();
  }
  set hass(e) {
    const t = this.#e?.user?.id !== e?.user?.id || this.#e?.user?.is_admin !== e?.user?.is_admin || this.#e?.connection !== e?.connection || this.#e?.auth !== e?.auth;
    this.#e = e;
    const a = e?.themes?.darkMode;
    typeof a == "boolean" && this.setAttribute?.("theme", a ? "dark" : "light"), t && (this.#n?.cancel(), this.#u()), this.#s();
  }
  set panel(e) {
    const t = this.#d?.config?.installer_url !== e?.config?.installer_url;
    t && this.#n?.cancel(), this.#d = e, t && this.#u(), this.#s();
  }
  connectedCallback() {
    this.#u();
  }
  disconnectedCallback() {
    this.#n?.cancel(), this.#a?.cancel(), this.#a = void 0, this.#i?.abort(), this.#i = void 0;
  }
  async #u() {
    if (this.#i?.abort(), this.#i = void 0, this.#o = [], this.#r = "loading", this.shadowRoot.querySelector("#release").replaceChildren(), this.#s(), !this.isConnected || this.#e?.user?.is_admin !== !0 || !this.#d?.config?.installer_url) return;
    const e = new AbortController();
    this.#i = e;
    try {
      const t = await Zt(this.#e, { signal: e.signal });
      if (this.#i !== e) return;
      this.#o = t, this.#r = t.length ? "ready" : "empty";
      const a = this.shadowRoot.querySelector("#release"), i = document.createElement("option");
      i.value = "", i.textContent = this.#t.choose, i.disabled = !1, a.append(i);
      for (const o of t) {
        const l = document.createElement("option");
        l.value = o.tag;
        const s = o.name ? this.#t.devBuild : o.prerelease ? this.#t.testing : "";
        l.textContent = `${o.name ?? o.tag.replace(/^v/, "")}${s ? ` (${s})` : ""}`, a.append(l);
      }
      a.value = "";
    } catch {
      if (this.#i !== e) return;
      this.#r = "catalogError";
    } finally {
      this.#i === e && (this.#i = void 0, this.#s());
    }
  }
  #s() {
    const e = this.shadowRoot, t = this.#e?.language;
    this.isConnected && this.setAttribute?.("lang", k(t));
    for (const p of e.querySelectorAll("[data-message]")) p.textContent = this.#t[p.dataset.message];
    const a = e.querySelector("#journey");
    a.setAttribute("aria-label", M("installer", t).progress), en(a, 0, document, t);
    const i = e.querySelector("#release").children;
    if (i.length) {
      i[0].textContent = this.#t.choose;
      for (const [p, A] of this.#o.entries()) {
        const y = A.name ? this.#t.devBuild : A.prerelease ? this.#t.testing : "";
        i[p + 1].textContent = `${A.name ?? A.tag.replace(/^v/, "")}${y ? ` (${y})` : ""}`;
      }
    }
    const o = this.#e?.user?.is_admin === !0, l = typeof this.#d?.config?.installer_url == "string" && this.#d.config.installer_url.length > 0, s = this.shadowRoot.querySelector("#release").value, h = s === "" ? this.#o[0] : this.#o.find((p) => p.tag === s);
    this.shadowRoot.querySelector("#start").disabled = !o || !l || !!this.#n || !h, this.shadowRoot.querySelector("#cancel").disabled = !this.#n, this.shadowRoot.querySelector("#release").disabled = !!this.#n || this.#r !== "ready";
    const u = this.shadowRoot.querySelector("#catalog-status");
    u.textContent = o && l && this.#r !== "ready" ? this.#t[this.#r] : "", u.hidden = !u.textContent, this.shadowRoot.querySelector("#retry").hidden = !o || !l || !["catalogError", "empty"].includes(this.#r), this.shadowRoot.querySelector("#cancel").hidden = !this.#n;
    const g = o ? l ? this.#g : "unavailable" : "admin", d = this.shadowRoot.querySelector("#status");
    d.textContent = Object.hasOwn(this.#t, g) ? this.#t[g] : this.#t.failed, d.hidden = !d.textContent;
  }
  #l() {
    if (this.#n || this.#e?.user?.is_admin !== !0) return;
    const e = this.shadowRoot.querySelector("#release").value;
    if (!(e === "" ? this.#o[0] : this.#o.find((i) => i.tag === e)) || !this.isConnected) return;
    this.#a?.cancel(), this.#a = void 0;
    const a = Yt(this.#e, this.#d?.config?.installer_url, {
      rcTag: e || null,
      language: k(this.#e?.language),
      onState: (i) => {
        this.#g = i, this.#s();
      }
    });
    this.#n = a, this.#s(), a.completion.then(() => {
      this.#n === a && (this.#a = a);
    }, () => {
    }).finally(() => {
      this.#n === a && (this.#n = void 0), this.#s();
    });
  }
}
customElements.get("panel-assistant-usb-install") || customElements.define("panel-assistant-usb-install", nn);
