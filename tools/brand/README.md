# Packaged brand assets

The website at `https://panel-assistant.io/brand/` owns the brand artwork. Run `node tools/brand/sync.mjs sync` to download the published PNG icons, which are copied without re-encoding. The manifest records each source URL, packaged path and SHA-256. The frontend's header mark and name come from `custom_components/panel_assistant/frontend/src/tokens.mjs`, synced from the app by `frontend/sync-tokens.mjs`.

Run `node tools/brand/sync.mjs check` to compare packaged bytes with the latest published website. CI runs this check and fails when website artwork changes. `check --offline` checks packaged hashes without network access, and runs before frontend builds. README imagery uses the website URLs directly.
