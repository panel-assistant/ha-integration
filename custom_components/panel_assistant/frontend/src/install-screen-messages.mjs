import { ENGLISH_MESSAGES } from './frontend-localization.mjs';
// Everything the installer says to the person using it. They may never have
// used a terminal, so every string is one plain sentence about what to do or
// what is happening. Technical detail belongs behind "Details for support".
export const INSTALL_SCREEN_MESSAGES = Object.freeze(ENGLISH_MESSAGES.installer);
