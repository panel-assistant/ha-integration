// Panel Assistant updated while this page was open: Home Assistant loads the new build's module, whose
// address names its build, but the browser keeps the elements the old module defined, so the page would go
// on running the old sidebar (one that may not even relay a panel's requests). Only a reload runs this
// build. It is asked for once per build, so a reload that cannot help never repeats.
const ELEMENTS = ['panel-assistant-sidebar', 'panel-assistant-usb-install'];
const RELOADED = 'panel-assistant-module-reloaded';

if (ELEMENTS.some(name => customElements.get(name))) {
  let reloaded = null;
  try {
    reloaded = sessionStorage.getItem(RELOADED);
    sessionStorage.setItem(RELOADED, import.meta.url);
  } catch {
    // Without session storage the page is not reloaded: an endless reload is worse than an old sidebar.
    reloaded = import.meta.url;
  }
  if (reloaded !== import.meta.url) location.reload();
}
