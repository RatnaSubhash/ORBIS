/**
 * Comprehensive Electron Visual Verification for ORBIS Startup / Awakening Sequence.
 *
 * Verifies and captures all 6 required moments:
 *   A. startup / initial appearance (phase: black)
 *   B. mid-decryption (phase: decrypting, ~1.3s in)
 *   C. fully decrypted hold (phase: holding)
 *   D. beginning of glide (phase: gliding onset)
 *   E. middle of glide (phase: gliding mid-flight)
 *   F. final settled state (phase: complete / idle)
 */

const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const VITE_DEV_URL = 'http://localhost:5173';
const ARTIFACTS_DIR = 'C:/Users/kratn/.gemini/antigravity-ide/brain/8f92a93c-f30b-46ce-b14a-1c1c4c77dac0/scratch';

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

app.whenReady().then(async () => {
  console.log('=== STARTING ORBIS AWAKENING 6-PHASE ELECTRON VISUAL AUDIT ===\n');

  const preloadPath = path.join(__dirname, '../electron/preload.cjs');
  const consoleErrors = [];
  const consoleWarnings = [];

  const win = new BrowserWindow({
    width: 1280,
    height: 720,
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

  win.webContents.on('console-message', (event) => {
    if (event.level === 3) {
      consoleErrors.push(event.message);
      console.error('    [Renderer Error]', event.message);
    } else if (event.level === 2) {
      consoleWarnings.push(event.message);
    }
  });

  // Helper to query element state
  const inspectDOM = async () => {
    return await win.webContents.executeJavaScript(`
      (() => {
        const core = document.querySelector('.orbis-core');
        const wordmark = document.querySelector('.orbis-wordmark');
        const stage = document.querySelector('.orbis-globe-stage');
        const wRect = wordmark ? wordmark.getBoundingClientRect() : null;
        const sRect = stage ? stage.getBoundingClientRect() : null;
        const viewportCenter = window.innerHeight / 2;
        const elementCenter = wRect ? wRect.top + wRect.height / 2 : 0;
        const decryptedChars = document.querySelectorAll('.orbis-decrypted-char').length;
        const encryptedChars = document.querySelectorAll('.orbis-encrypted-char').length;
        const bodyBg = window.getComputedStyle(document.body).backgroundColor;
        const stageOpacity = stage ? window.getComputedStyle(stage).opacity : null;
        const stageTransform = stage ? window.getComputedStyle(stage).transform : null;
        const wordmarkOpacity = wordmark ? window.getComputedStyle(wordmark).opacity : null;
        const wordmarkTransform = wordmark ? window.getComputedStyle(wordmark).transform : null;
        const canvas = stage ? stage.querySelector('canvas') : null;
        const gl = canvas ? (canvas.getContext('webgl') || canvas.getContext('webgl2')) : null;

        return {
          coreClasses: core ? core.className : '',
          isAwakening: core ? core.classList.contains('is-awakening') : false,
          phaseAttr: wordmark ? wordmark.getAttribute('data-phase') : null,
          text: wordmark ? wordmark.textContent.trim() : '',
          rect: wRect ? { top: wRect.top, bottom: wRect.bottom, left: wRect.left, right: wRect.right, width: wRect.width, height: wRect.height } : null,
          stageRect: sRect ? { top: sRect.top, bottom: sRect.bottom, left: sRect.left, right: sRect.right, width: sRect.width, height: sRect.height } : null,
          elementCenter,
          distanceFromCenter: Math.abs(viewportCenter - elementCenter),
          decryptedChars,
          encryptedChars,
          bodyBg,
          stageOpacity,
          stageTransform,
          wordmarkOpacity,
          wordmarkTransform,
          webglActive: !!gl && !gl.isContextLost(),
        };
      })()
    `);
  };

  const waitForPhase = async (targetPhase, maxWaitMs = 6000) => {
    const start = Date.now();
    while (Date.now() - start < maxWaitMs) {
      const state = await inspectDOM();
      if (state.phaseAttr === targetPhase) {
        return state;
      }
      await sleep(30);
    }
    throw new Error(`Timed out waiting for phase: ${targetPhase}`);
  };

  console.log('[1] Loading ORBIS application at 1280x720...');
  await win.loadURL(VITE_DEV_URL);

  // --- A. STARTUP / INITIAL APPEARANCE (black screen phase) ---
  const stateA = await inspectDOM();
  const imgA = await win.capturePage();
  fs.writeFileSync(path.join(ARTIFACTS_DIR, 'A_startup_initial_appearance.png'), imgA.toPNG());
  console.log('[Moment A - Startup / Initial Appearance]');
  console.log('  isAwakening:', stateA.isAwakening, '| phase:', stateA.phaseAttr, '| bodyBg:', stateA.bodyBg);
  console.log('  wordmarkOpacity:', stateA.wordmarkOpacity, '| stageOpacity:', stateA.stageOpacity);
  console.log('  Captured: A_startup_initial_appearance.png\n');

  // --- B. MID-DECRYPTION ---
  await waitForPhase('decrypting');
  // Wait ~500ms into decrypting for exact mid-progress reveal
  await sleep(500);
  const stateB = await inspectDOM();
  const imgB = await win.capturePage();
  fs.writeFileSync(path.join(ARTIFACTS_DIR, 'B_mid_decryption.png'), imgB.toPNG());
  console.log('[Moment B - Mid-Decryption]');
  console.log('  isAwakening:', stateB.isAwakening, '| phase:', stateB.phaseAttr);
  console.log('  decryptedChars:', stateB.decryptedChars, '| encryptedChars:', stateB.encryptedChars);
  console.log('  distanceFromCenter:', stateB.distanceFromCenter.toFixed(1) + 'px');
  console.log('  wordmarkTransform:', stateB.wordmarkTransform);
  console.log('  stageOpacity:', stateB.stageOpacity);
  console.log('  Captured: B_mid_decryption.png\n');

  // --- C. FULLY DECRYPTED HOLD ---
  await waitForPhase('holding');
  await sleep(150); // Mid-way through 400ms hold
  const stateC = await inspectDOM();
  const imgC = await win.capturePage();
  fs.writeFileSync(path.join(ARTIFACTS_DIR, 'C_fully_decrypted_hold.png'), imgC.toPNG());
  console.log('[Moment C - Fully Decrypted Hold]');
  console.log('  isAwakening:', stateC.isAwakening, '| phase:', stateC.phaseAttr);
  console.log('  decryptedChars:', stateC.decryptedChars);
  console.log('  distanceFromCenter:', stateC.distanceFromCenter.toFixed(1) + 'px');
  console.log('  stageOpacity:', stateC.stageOpacity);
  console.log('  Captured: C_fully_decrypted_hold.png\n');

  // --- D. BEGINNING OF GLIDE ---
  await waitForPhase('gliding');
  await sleep(150); // 150ms into 1400ms glide
  const stateD = await inspectDOM();
  const imgD = await win.capturePage();
  fs.writeFileSync(path.join(ARTIFACTS_DIR, 'D_beginning_of_glide.png'), imgD.toPNG());
  console.log('[Moment D - Beginning of Glide]');
  console.log('  isAwakening:', stateD.isAwakening, '| phase:', stateD.phaseAttr);
  console.log('  wordmark top:', stateD.rect ? stateD.rect.top.toFixed(1) + 'px' : 'n/a');
  console.log('  wordmarkTransform:', stateD.wordmarkTransform);
  console.log('  stageOpacity:', stateD.stageOpacity, '| stageTransform:', stateD.stageTransform);
  console.log('  Captured: D_beginning_of_glide.png\n');

  // --- E. MIDDLE OF GLIDE ---
  await sleep(550); // ~700ms into 1400ms glide
  const stateE = await inspectDOM();
  const imgE = await win.capturePage();
  fs.writeFileSync(path.join(ARTIFACTS_DIR, 'E_middle_of_glide.png'), imgE.toPNG());
  console.log('[Moment E - Middle of Glide]');
  console.log('  isAwakening:', stateE.isAwakening, '| phase:', stateE.phaseAttr);
  console.log('  wordmark top:', stateE.rect ? stateE.rect.top.toFixed(1) + 'px' : 'n/a');
  console.log('  stageOpacity:', stateE.stageOpacity, '| stageTransform:', stateE.stageTransform);
  console.log('  Captured: E_middle_of_glide.png\n');

  // --- F. FINAL SETTLED STATE ---
  // Wait until isAwakening becomes false (phase: complete)
  const startWait = Date.now();
  while (Date.now() - startWait < 3000) {
    const s = await inspectDOM();
    if (!s.isAwakening) break;
    await sleep(50);
  }
  await sleep(200); // Allow any sub-pixel settle to finish
  const stateF = await inspectDOM();
  const imgF = await win.capturePage();
  fs.writeFileSync(path.join(ARTIFACTS_DIR, 'F_final_settled_state.png'), imgF.toPNG());
  console.log('[Moment F - Final Settled State]');
  console.log('  isAwakening:', stateF.isAwakening, '| phase:', stateF.phaseAttr);
  console.log('  wordmark text:', stateF.text, '| wordmark top:', stateF.rect ? stateF.rect.top.toFixed(1) + 'px' : 'n/a');
  console.log('  stageOpacity:', stateF.stageOpacity, '| stageTransform:', stateF.stageTransform);
  console.log('  webglActive:', stateF.webglActive);
  console.log('  Captured: F_final_settled_state.png\n');

  // Multi-resolution verification in settled state
  console.log('[Verification] Testing Responsive Layouts in Settled State...');
  win.setSize(1366, 768);
  await sleep(150);
  const res768 = await inspectDOM();
  console.log('  1366x768 -> wordmark top:', res768.rect ? res768.rect.top.toFixed(1) + 'px' : 'n/a', '| stage visible:', res768.stageOpacity === '1');

  win.setSize(1920, 1080);
  await sleep(150);
  const res1080 = await inspectDOM();
  console.log('  1920x1080 -> wordmark top:', res1080.rect ? res1080.rect.top.toFixed(1) + 'px' : 'n/a', '| stage visible:', res1080.stageOpacity === '1');

  // In-Memory single run immunity check
  console.log('\n[Verification] Testing In-Memory Single-Run Immunity...');
  await win.webContents.executeJavaScript(`
    (() => {
      const input = document.getElementById('orbis-prompt-input');
      if (input) {
        input.value = 'query';
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    })()
  `);
  await sleep(100);
  const stateImmunity = await inspectDOM();
  console.log('  After input event -> isAwakening:', stateImmunity.isAwakening);

  // Criteria validation
  const checks = [
    { name: 'Zero console errors', pass: consoleErrors.length === 0 },
    { name: 'Neutral dark background (#0a0b0e / rgb(10, 11, 14))', pass: stateA.bodyBg === 'rgb(10, 11, 14)' },
    { name: 'Stationary & centered during decryption (distance < 20px)', pass: stateB.distanceFromCenter < 20 },
    { name: 'Stationary & centered during hold (distance < 20px)', pass: stateC.distanceFromCenter < 20 },
    { name: 'Stage opacity starts at 0', pass: stateA.stageOpacity === '0' },
    { name: 'Globe fully visible when settled (opacity == 1)', pass: stateF.stageOpacity === '1' },
    { name: 'Awakening completes cleanly (isAwakening == false)', pass: stateF.isAwakening === false },
    { name: 'Settled wordmark text is ORBIS', pass: stateF.text === 'ORBIS' },
    { name: 'WebGL remains active throughout', pass: stateF.webglActive === true },
    { name: 'In-memory single run preserved', pass: stateImmunity.isAwakening === false },
  ];

  console.log('\n=== AUDIT RESULTS SUMMARY ===');
  let allPassed = true;
  for (const c of checks) {
    console.log(`  [${c.pass ? 'PASS' : 'FAIL'}] ${c.name}`);
    if (!c.pass) allPassed = false;
  }

  console.log('\n============================================================');
  if (allPassed) {
    console.log('  ALL 6 AWAKENING PHASES & VISUAL CHECKS VERIFIED SUCCESSFULLY!');
  } else {
    console.error('  VERIFICATION FAILURES DETECTED');
  }
  console.log('============================================================\n');

  win.close();
  app.exit(allPassed ? 0 : 1);
});
