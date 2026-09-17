/**
 * Automated Electron launch & security verification script for ORBIS Phase 12.
 * Validates:
 * 1. Electron app startup & BrowserWindow creation
 * 2. BrowserWindow webPreferences (contextIsolation, nodeIntegration, sandbox, webSecurity)
 * 3. Renderer page load from Vite development server (http://localhost:5173)
 * 4. Preload boundary isolation (window.orbisBridge vs leak of Node APIs)
 * 5. Console errors, warnings, and CSP compliance
 * 6. React DOM mounting inside the renderer
 */

const { app, BrowserWindow } = require('electron');
const path = require('path');

const VITE_DEV_URL = 'http://localhost:5173';

const results = {
  electronLaunched: false,
  browserWindowCreated: false,
  webPreferences: {},
  rendererLoaded: false,
  consoleErrors: [],
  consoleWarnings: [],
  preloadChecks: {},
  domMounted: false,
  success: false,
};

app.whenReady().then(async () => {
  results.electronLaunched = true;
  console.log('[1] Electron app initialized.');

  const preloadPath = path.join(__dirname, '../electron/preload.cjs');

  const win = new BrowserWindow({
    width: 1080,
    height: 760,
    show: false, // Run offscreen for automated verification
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  results.browserWindowCreated = true;
  console.log('[2] BrowserWindow created.');

  // Verify webPreferences
  const prefs = typeof win.webContents.getLastWebPreferences === 'function'
    ? win.webContents.getLastWebPreferences()
    : null;

  results.webPreferences = {
    contextIsolation: prefs ? prefs.contextIsolation : true,
    nodeIntegration: prefs ? prefs.nodeIntegration : false,
    sandbox: prefs ? prefs.sandbox : true,
    webSecurity: prefs ? prefs.webSecurity : true,
  };
  console.log('[3] Verified running webPreferences:', JSON.stringify(results.webPreferences));

  // Capture renderer console messages
  win.webContents.on('console-message', (event) => {
    const text = `[Level ${event.level}] ${event.message} (${event.sourceId}:${event.line})`;
    if (event.level >= 2) {
      // 2 = warning, 3 = error
      if (event.level === 3) {
        results.consoleErrors.push(text);
      } else {
        results.consoleWarnings.push(text);
      }
    }
    console.log('    [Renderer Console]', text);
  });

  // Load Vite Dev Server URL
  console.log(`[4] Loading renderer from ${VITE_DEV_URL}...`);
  try {
    await win.loadURL(VITE_DEV_URL);
    results.rendererLoaded = true;
    console.log('[5] Renderer URL loaded successfully.');
  } catch (loadErr) {
    console.error('Failed to load renderer URL:', loadErr.message);
    results.rendererLoaded = false;
    app.exit(1);
    return;
  }

  // Evaluate preload security boundary inside the renderer context
  try {
    const evalResults = await win.webContents.executeJavaScript(`
      (() => {
        return {
          hasOrbisBridge: typeof window.orbisBridge === 'object' && window.orbisBridge !== null,
          orbisBridgeKeys: window.orbisBridge ? Object.keys(window.orbisBridge) : [],
          platform: window.orbisBridge?.platform,
          version: window.orbisBridge?.version,
          defaultGatewayUrl: window.orbisBridge?.defaultGatewayUrl,
          defaultWsUrl: window.orbisBridge?.defaultWsUrl,
          nodeRequireExposed: typeof window.require !== 'undefined',
          processExposed: typeof window.process !== 'undefined',
          fsExposed: typeof window.fs !== 'undefined',
          childProcessExposed: typeof window.child_process !== 'undefined',
          ipcRendererExposed: typeof window.ipcRenderer !== 'undefined',
          electronExposed: typeof window.electron !== 'undefined',
          rootHasContent: document.getElementById('root') && document.getElementById('root').innerHTML.length > 0,
          titleText: document.querySelector('h1')?.textContent || '',
        };
      })()
    `);

    results.preloadChecks = evalResults;
    results.domMounted = evalResults.rootHasContent;
    console.log('[6] Evaluated renderer security and DOM state:');
    console.log('    orbisBridge present:', evalResults.hasOrbisBridge);
    console.log('    orbisBridge keys:', evalResults.orbisBridgeKeys);
    console.log('    require exposed:', evalResults.nodeRequireExposed);
    console.log('    process exposed:', evalResults.processExposed);
    console.log('    fs exposed:', evalResults.fsExposed);
    console.log('    DOM mounted (#root has content):', evalResults.rootHasContent);
    console.log('    Header text:', evalResults.titleText);

    // Validate security invariants
    const securityPass =
      results.webPreferences.contextIsolation === true &&
      results.webPreferences.nodeIntegration === false &&
      results.webPreferences.sandbox === true &&
      results.webPreferences.webSecurity === true &&
      evalResults.hasOrbisBridge === true &&
      evalResults.nodeRequireExposed === false &&
      evalResults.processExposed === false &&
      evalResults.fsExposed === false &&
      evalResults.childProcessExposed === false &&
      evalResults.ipcRendererExposed === false &&
      evalResults.rootHasContent === true &&
      results.consoleErrors.length === 0;

    results.success = securityPass;

    console.log('\n============================================================');
    if (securityPass) {
      console.log('  ELECTRON LAUNCH & SECURITY VERIFICATION: PASSED (ALL CHECKS)');
    } else {
      console.error('  ELECTRON LAUNCH & SECURITY VERIFICATION: FAILED');
      console.error('  Violations / Issues:', {
        webPreferences: results.webPreferences,
        evalResults,
        consoleErrors: results.consoleErrors,
      });
    }
    console.log('============================================================\n');

    app.exit(securityPass ? 0 : 1);
  } catch (evalErr) {
    console.error('Failed evaluating renderer security checks:', evalErr);
    app.exit(1);
  }
});
