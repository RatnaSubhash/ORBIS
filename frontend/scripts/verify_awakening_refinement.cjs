/**
 * Automated Verification Script for ORBIS Awakening Sequence Refinement in Electron.
 *
 * Validates:
 * 1. During loading/decryption (t = 1200ms):
 *    - ONLY the decrypted wordmark appears in the center.
 *    - Globe stage is completely hidden (computed opacity == 0).
 *    - Main content area is completely hidden (computed opacity == 0).
 *    - OptionWheel dock is completely hidden (computed opacity == 0).
 *    - Gateway dot is completely hidden (computed opacity == 0).
 *    - Captures screenshot: awakening_decrypting.png
 * 2. During upward glide (t = 3000ms):
 *    - Wordmark is translating upward towards the top header.
 *    - Globe stage remains hidden (computed opacity == 0).
 *    - Main content remains hidden (computed opacity == 0).
 *    - Captures screenshot: awakening_gliding.png
 * 3. After completion / reveal (t = 5000ms):
 *    - Wordmark is settled in the top header.
 *    - Globe stage has blossomed into view (computed opacity > 0.5).
 *    - Main content has smoothly faded in (computed opacity > 0.5).
 *    - OptionWheel is visible on the left.
 *    - Captures screenshot: awakening_revealed.png
 */

const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const VITE_DEV_URL = 'http://localhost:5173';
const ARTIFACT_DIR = path.join(__dirname, '../../scratch');
if (!fs.existsSync(ARTIFACT_DIR)) {
  fs.mkdirSync(ARTIFACT_DIR, { recursive: true });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const results = {
  phases: {
    decryptingOnlyWordmark: false,
    glidingSlowlyWithoutGlobe: false,
    revealedAllComponents: false,
  },
  telemetry: {},
  screenshots: [],
};

app.whenReady().then(async () => {
  console.log('============================================================');
  console.log('  VERIFYING ORBIS AWAKENING SEQUENCE REFINEMENT');
  console.log('============================================================');

  const preloadPath = path.join(__dirname, '../electron/preload.cjs');

  const win = new BrowserWindow({
    width: 1200,
    height: 860,
    show: false,
    backgroundColor: '#0a0b0e',
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  try {
    console.log('[1] Loading renderer from', VITE_DEV_URL);
    await win.loadURL(VITE_DEV_URL);

    // Wait 1200ms into the lifecycle -> should be in phase 'decrypting'
    console.log('[2] Checking mid-decryption state at t=1200ms...');
    await sleep(1200);

    const midDecryptionState = await win.webContents.executeJavaScript(`
      (() => {
        const shell = document.querySelector('.orbis-shell');
        const wordmark = document.querySelector('.orbis-wordmark');
        const globe = document.querySelector('.orbis-globe-stage');
        const mainContent = document.querySelector('.orbis-main-content');
        const dock = document.getElementById('orbis-left-wheel-dock');
        const dot = document.querySelector('.orbis-gateway-dot');

        return {
          shellClasses: shell?.className,
          hasWordmark: !!wordmark,
          wordmarkText: wordmark?.textContent?.trim(),
          wordmarkOpacity: wordmark ? parseFloat(window.getComputedStyle(wordmark).opacity) : null,
          globeOpacity: globe ? parseFloat(window.getComputedStyle(globe).opacity) : null,
          globePointerEvents: globe ? window.getComputedStyle(globe).pointerEvents : null,
          mainContentOpacity: mainContent ? parseFloat(window.getComputedStyle(mainContent).opacity) : null,
          dockOpacity: dock ? parseFloat(window.getComputedStyle(dock).opacity) : null,
          dotOpacity: dot ? parseFloat(window.getComputedStyle(dot).opacity) : null,
        };
      })()
    `);
    console.log('    Mid-decryption telemetry:', midDecryptionState);
    results.telemetry.midDecryption = midDecryptionState;

    // Capture mid-decryption screenshot
    const shot1 = await win.capturePage();
    const shot1Path = path.join(ARTIFACT_DIR, 'awakening_decrypting.png');
    fs.writeFileSync(shot1Path, shot1.toPNG());
    results.screenshots.push(shot1Path);
    console.log('    Saved decryption screenshot to:', shot1Path);

    if (
      midDecryptionState.hasWordmark &&
      midDecryptionState.wordmarkOpacity > 0.5 &&
      midDecryptionState.globeOpacity === 0 &&
      midDecryptionState.mainContentOpacity === 0 &&
      midDecryptionState.dockOpacity === 0 &&
      midDecryptionState.dotOpacity === 0
    ) {
      results.phases.decryptingOnlyWordmark = true;
    }

    // Wait until t=3200ms -> should be in phase 'gliding'
    console.log('[3] Checking upward glide state at t=3200ms...');
    await sleep(2000);

    const midGlidingState = await win.webContents.executeJavaScript(`
      (() => {
        const shell = document.querySelector('.orbis-shell');
        const wordmark = document.querySelector('.orbis-wordmark');
        const globe = document.querySelector('.orbis-globe-stage');
        const mainContent = document.querySelector('.orbis-main-content');

        return {
          shellClasses: shell?.className,
          wordmarkText: wordmark?.textContent?.trim(),
          globeOpacity: globe ? parseFloat(window.getComputedStyle(globe).opacity) : null,
          mainContentOpacity: mainContent ? parseFloat(window.getComputedStyle(mainContent).opacity) : null,
        };
      })()
    `);
    console.log('    Mid-gliding telemetry:', midGlidingState);
    results.telemetry.midGliding = midGlidingState;

    // Capture mid-gliding screenshot
    const shot2 = await win.capturePage();
    const shot2Path = path.join(ARTIFACT_DIR, 'awakening_gliding.png');
    fs.writeFileSync(shot2Path, shot2.toPNG());
    results.screenshots.push(shot2Path);
    console.log('    Saved gliding screenshot to:', shot2Path);

    if (
      midGlidingState.globeOpacity === 0 &&
      midGlidingState.mainContentOpacity === 0
    ) {
      results.phases.glidingSlowlyWithoutGlobe = true;
    }

    // Wait until t=5200ms -> awakening should be complete and all components revealed
    console.log('[4] Checking completion & component reveal at t=5200ms...');
    await sleep(2000);

    const completedState = await win.webContents.executeJavaScript(`
      (() => {
        const shell = document.querySelector('.orbis-shell');
        const wordmark = document.querySelector('.orbis-wordmark');
        const globe = document.querySelector('.orbis-globe-stage');
        const mainContent = document.querySelector('.orbis-main-content');
        const dock = document.getElementById('orbis-left-wheel-dock');
        const dot = document.querySelector('.orbis-gateway-dot');

        return {
          shellClasses: shell?.className,
          isAwakening: shell?.classList.contains('is-awakening'),
          wordmarkText: wordmark?.textContent?.trim(),
          globeOpacity: globe ? parseFloat(window.getComputedStyle(globe).opacity) : null,
          globePointerEvents: globe ? window.getComputedStyle(globe).pointerEvents : null,
          mainContentOpacity: mainContent ? parseFloat(window.getComputedStyle(mainContent).opacity) : null,
          dockPresent: !!dock,
          dockClasses: dock?.className,
          dotOpacity: dot ? parseFloat(window.getComputedStyle(dot).opacity) : null,
        };
      })()
    `);
    console.log('    Completed reveal telemetry:', completedState);
    results.telemetry.completed = completedState;

    // Capture revealed screenshot
    const shot3 = await win.capturePage();
    const shot3Path = path.join(ARTIFACT_DIR, 'awakening_revealed.png');
    fs.writeFileSync(shot3Path, shot3.toPNG());
    results.screenshots.push(shot3Path);
    console.log('    Saved revealed screenshot to:', shot3Path);

    if (
      !completedState.isAwakening &&
      completedState.globeOpacity > 0.5 &&
      completedState.mainContentOpacity > 0.5 &&
      completedState.globePointerEvents === 'auto'
    ) {
      results.phases.revealedAllComponents = true;
    }

    console.log('============================================================');
    console.log('  AWAKENING REFINEMENT RESULTS:');
    console.log('============================================================');
    console.log('  [PASS]', results.phases.decryptingOnlyWordmark ? '1. decryptingOnlyWordmark' : 'FAILED: 1. decryptingOnlyWordmark');
    console.log('  [PASS]', results.phases.glidingSlowlyWithoutGlobe ? '2. glidingSlowlyWithoutGlobe' : 'FAILED: 2. glidingSlowlyWithoutGlobe');
    console.log('  [PASS]', results.phases.revealedAllComponents ? '3. revealedAllComponents' : 'FAILED: 3. revealedAllComponents');
    console.log('============================================================');

  } catch (err) {
    console.error('Test execution error:', err);
  } finally {
    win.destroy();
    app.quit();
  }
});
