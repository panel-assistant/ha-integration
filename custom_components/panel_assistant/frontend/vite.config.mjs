import { defineConfig } from 'vite';
import { BRAND_HTML, WIZARD_CSS, journeyHtml } from './src/wizard-look.mjs';

// The shared wizard look is written into the page itself, so it paints styled
// from the first frame, from the same definition the Home Assistant page uses.
const wizardLook = {
  name: 'wizard-look',
  transformIndexHtml: html => html
    .replace('<!-- wizard-look -->', () => `<style>${WIZARD_CSS}</style>`)
    .replace('<!-- brand -->', () => BRAND_HTML)
    .replace('<!-- journey -->', () => journeyHtml(1)),
};

export default defineConfig({
  base: './',
  plugins: [wizardLook],
  build: { target: 'es2022', sourcemap: false, modulePreload: { polyfill: false } },
});
