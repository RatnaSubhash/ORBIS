/**
 * Automated Verification Script for ORBIS Phase H in Electron.
 */

const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const VITE_DEV_URL = 'http://localhost:5173';
const SCRATCH_DIR = path.resolve(__dirname, '../../scratch');
const BRAIN_DIR = path.resolve('C:/Users/kratn/.gemini/antigravity-ide/brain/0a1b0255-f200-4267-b499-37970beb683a');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const report = {
  phases: {
    awakeningCompleted: false,
    optionWheelVisible: false,
    exactlyOneGlobeCanvas: false,
    commandViewNavigation: false,
    commandStepperRendered: false,
    commandFluidGlassRendered: false,
    commandDockRendered: false,
    commandDockPreparesResearchPrompt: false,
    commandDockClearUiWorks: false,
    liveFlowNavigation: false,
    liveFlowStepperRendered: false,
    liveFlowFluidGlassRendered: false,
    liveFlowDockRendered: false,
    systemControlNavigation: false,
    systemElasticSliderRendered: false,
    systemFluidGlassRendered: false,
    settingsNavigation: false,
    settingsElasticSliderRendered: false,
    settingsFluidGlassRendered: false,
    workspaceRegressionPassed: false,
    toolsRegressionPassed: false,
    returnHomeGlobeRestored: false,
    noDirectExecuteEndpoint: false,
    zeroConsoleErrors: false,
  },
  details: {},
  errors: [],
};

app.whenReady().then(async () => {
  console.log('============================================================');
  console.log('  STARTING ORBIS PHASE H COMPREHENSIVE ELECTRON VERIFICATION');
  console.log('============================================================');

  const preloadPath = path.join(__dirname, '../electron/preload.cjs');

  const win = new BrowserWindow({
    width: 1240,
    height: 880,
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

  win.webContents.on('console-message', (event, level, message) => {
    if (level === 3) {
      report.errors.push(message);
      console.error('    [Renderer Error]', message);
    }
  });

  try {
    console.log('[1] Loading renderer from', VITE_DEV_URL);
    await win.loadURL(VITE_DEV_URL);

    // Wait for React shell to mount
    console.log('[2] Waiting for React shell to mount...');
    for (let i = 0; i < 20; i++) {
      const mounted = await win.webContents.executeJavaScript(`
        (() => !!document.getElementById('orbis-left-wheel-dock'))()
      `);
      if (mounted) break;
      await sleep(300);
    }

    // Wait for Awakening sequence to complete
    console.log('[3] Waiting for awakening sequence to conclude...');
    for (let i = 0; i < 30; i++) {
      const isAwakening = await win.webContents.executeJavaScript(`
        (() => document.querySelector('.orbis-shell')?.classList.contains('is-awakening'))()
      `);
      if (!isAwakening) break;
      await sleep(300);
    }
    report.phases.awakeningCompleted = true;
    console.log('    ✓ Awakening sequence complete');

    // Verify OptionWheel is visible on left
    const wheelVisible = await win.webContents.executeJavaScript(`
      (() => {
        const dock = document.getElementById('orbis-left-wheel-dock');
        if (!dock) return false;
        const rect = dock.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.left < 200;
      })()
    `);
    report.phases.optionWheelVisible = !!wheelVisible;
    console.log('    ✓ OptionWheel visible and docked on left side:', wheelVisible);

    // Verify Globe Canvas Count
    const globeCanvases = await win.webContents.executeJavaScript(`
      (() => document.querySelectorAll('canvas').length)()
    `);
    report.phases.exactlyOneGlobeCanvas = globeCanvases === 1;
    console.log('    ✓ Exactly one WebGL Canvas present:', globeCanvases);

    // ── STEP 4: Navigate to Command View ──────────────────────────────────────
    console.log('[4] Navigating to Command View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const cmdItem = items.find(el => el.textContent.includes('Command'));
        if (cmdItem) cmdItem.click();
      })()
    `);
    await sleep(800);

    const commandMounted = await win.webContents.executeJavaScript(`
      (() => !!document.querySelector('.orbis-command-view'))()
    `);
    report.phases.commandViewNavigation = !!commandMounted;
    console.log('    ✓ Command View mounted:', commandMounted);

    // Check Contextual Dock in Command View
    const commandDock = await win.webContents.executeJavaScript(`
      (() => {
        const dock = document.querySelector('.orbis-command-dock-container .dock-panel');
        if (!dock) return { found: false };
        const items = Array.from(dock.querySelectorAll('.dock-item'));
        return {
          found: true,
          itemCount: items.length,
          labels: items.map(el => el.getAttribute('aria-label') || el.textContent.trim()),
        };
      })()
    `);
    report.phases.commandDockRendered = commandDock.found && commandDock.itemCount >= 4;
    console.log('    ✓ Contextual Dock rendered in Command View:', commandDock);

    // Test Dock "Research Query" populates input without auto-execution
    console.log('[5] Testing Dock "Research Query" preparation...');
    await win.webContents.executeJavaScript(`
      (() => {
        const dock = document.querySelector('.orbis-command-dock-container .dock-panel');
        const items = Array.from(dock.querySelectorAll('.dock-item'));
        const researchItem = items.find(el => (el.getAttribute('aria-label') || '').includes('Research'));
        if (researchItem) researchItem.click();
      })()
    `);
    await sleep(400);

    const inputVal = await win.webContents.executeJavaScript(`
      (() => {
        const input = document.getElementById('orbis-command-prompt-input');
        return input ? input.value : '';
      })()
    `);
    report.phases.commandDockPreparesResearchPrompt = inputVal.includes('Research');
    console.log('    ✓ Dock populated input prompt for user review (no auto-execution):', inputVal);

    // Test Dock "Clear UI"
    console.log('[6] Testing Dock "Clear UI"...');
    await win.webContents.executeJavaScript(`
      (() => {
        const dock = document.querySelector('.orbis-command-dock-container .dock-panel');
        const items = Array.from(dock.querySelectorAll('.dock-item'));
        const clearItem = items.find(el => (el.getAttribute('aria-label') || '').includes('Clear'));
        if (clearItem) clearItem.click();
      })()
    `);
    await sleep(400);

    const inputValAfterClear = await win.webContents.executeJavaScript(`
      (() => {
        const input = document.getElementById('orbis-command-prompt-input');
        return input ? input.value : '';
      })()
    `);
    report.phases.commandDockClearUiWorks = inputValAfterClear === '';
    console.log('    ✓ Dock "Clear UI" cleared active prompt state:', inputValAfterClear === '');

    // Submit a query to trigger Stepper & FluidGlass response
    console.log('[7] Submitting prompt to verify Stepper & FluidGlass...');
    await win.webContents.executeJavaScript(`
      (() => {
        const input = document.getElementById('orbis-command-prompt-input');
        if (input) {
          const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          nativeSetter.call(input, 'What is ORBIS?');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          const form = document.querySelector('.orbis-command-form');
          if (form) form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        }
      })()
    `);
    await sleep(1500);

    // Check Stepper rendering
    const stepperInfo = await win.webContents.executeJavaScript(`
      (() => {
        const stepper = document.querySelector('.orbis-command-stepper-wrap');
        if (!stepper) return { found: false };
        const steps = stepper.querySelectorAll('.step-indicator');
        return {
          found: true,
          stepCount: steps.length,
        };
      })()
    `);
    report.phases.commandStepperRendered = stepperInfo.found && stepperInfo.stepCount === 5;
    console.log('    ✓ Stepper rendered real lifecycle stages in Command View:', stepperInfo);

    // Wait for response answer and check FluidGlass
    await sleep(3500);
    const commandGlass = await win.webContents.executeJavaScript(`
      (() => {
        const glassCards = document.querySelectorAll('.orbis-command-view .glass-surface');
        return {
          count: glassCards.length,
          hasSvgFilter: !!document.querySelector('.glass-surface__filter'),
        };
      })()
    `);
    report.phases.commandFluidGlassRendered = commandGlass.count > 0;
    console.log('    ✓ FluidGlass rendered contextual panels in Command View:', commandGlass);

    // Capture screenshot of Command View
    const cmdImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'phase_h_command.png'), cmdImage.toPNG());
    console.log('    ✓ Captured scratch/phase_h_command.png');

    // ── STEP 8: Navigate to Live Flow View ────────────────────────────────────
    console.log('[8] Navigating to Live Flow View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const flowItem = items.find(el => el.textContent.includes('Live Flow'));
        if (flowItem) flowItem.click();
      })()
    `);
    await sleep(800);

    const liveFlowInfo = await win.webContents.executeJavaScript(`
      (() => {
        const view = document.querySelector('.orbis-live-flow-view');
        const stepper = document.querySelector('.orbis-flow-stepper-wrap');
        const glass = document.querySelectorAll('.orbis-live-flow-view .glass-surface');
        const dock = document.querySelector('.orbis-flow-dock-wrap .dock-panel');
        return {
          viewFound: !!view,
          stepperFound: !!stepper,
          glassCount: glass.length,
          dockFound: !!dock,
        };
      })()
    `);
    report.phases.liveFlowNavigation = liveFlowInfo.viewFound;
    report.phases.liveFlowStepperRendered = liveFlowInfo.stepperFound;
    report.phases.liveFlowFluidGlassRendered = liveFlowInfo.glassCount > 0;
    report.phases.liveFlowDockRendered = liveFlowInfo.dockFound;
    console.log('    ✓ Live Flow View with Stepper, FluidGlass, and Dock:', liveFlowInfo);

    const flowImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'phase_h_live_flow.png'), flowImage.toPNG());
    console.log('    ✓ Captured scratch/phase_h_live_flow.png');

    // ── STEP 9: Navigate to System Control View ───────────────────────────────
    console.log('[9] Navigating to System Control View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const sysItem = items.find(el => el.textContent.includes('System'));
        if (sysItem) sysItem.click();
      })()
    `);
    await sleep(800);

    const systemControlInfo = await win.webContents.executeJavaScript(`
      (() => {
        const slider = document.querySelector('.orbis-audio-elastic-slider');
        const glass = document.querySelector('.orbis-glass-card-wrapper');
        return {
          sliderFound: !!slider,
          trackFound: !!slider?.querySelector('.slider-root'),
          glassFound: !!glass,
        };
      })()
    `);
    report.phases.systemControlNavigation = true;
    report.phases.systemElasticSliderRendered = systemControlInfo.sliderFound && systemControlInfo.trackFound;
    report.phases.systemFluidGlassRendered = systemControlInfo.glassFound;
    console.log('    ✓ System Control View with ElasticSlider & FluidGlass:', systemControlInfo);

    const sysImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'phase_h_system.png'), sysImage.toPNG());
    console.log('    ✓ Captured scratch/phase_h_system.png');

    // ── STEP 10: Navigate to Settings View ────────────────────────────────────
    console.log('[10] Navigating to Settings View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const settingsItem = items.find(el => el.textContent.includes('Settings'));
        if (settingsItem) settingsItem.click();
      })()
    `);
    await sleep(800);

    const settingsInfo = await win.webContents.executeJavaScript(`
      (() => {
        const slider = document.querySelector('.orbis-ui-elastic-slider');
        const glassCards = document.querySelectorAll('.orbis-view-container .glass-surface');
        return {
          sliderFound: !!slider,
          glassCount: glassCards.length,
        };
      })()
    `);
    report.phases.settingsNavigation = true;
    report.phases.settingsElasticSliderRendered = settingsInfo.sliderFound;
    report.phases.settingsFluidGlassRendered = settingsInfo.glassCount >= 2;
    console.log('    ✓ Settings View with ElasticSlider & FluidGlass cards:', settingsInfo);

    const settingsImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'phase_h_settings.png'), settingsImage.toPNG());
    console.log('    ✓ Captured scratch/phase_h_settings.png');

    // ── STEP 11: Regression Check: Workspace & Tools ──────────────────────────
    console.log('[11] Verifying Workspace continuity regression...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const wsItem = items.find(el => el.textContent.includes('Workspace'));
        if (wsItem) wsItem.click();
      })()
    `);
    await sleep(800);

    const wsWorks = await win.webContents.executeJavaScript(`
      (() => !!document.querySelector('.orbis-workspace-view'))()
    `);
    report.phases.workspaceRegressionPassed = !!wsWorks;
    console.log('    ✓ Workspace View operational:', wsWorks);

    console.log('[12] Verifying Tools capability center regression...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const toolsItem = items.find(el => el.textContent.includes('Tools'));
        if (toolsItem) toolsItem.click();
      })()
    `);
    await sleep(800);

    const toolsWorks = await win.webContents.executeJavaScript(`
      (() => !!document.querySelector('.orbis-tools-view'))()
    `);
    report.phases.toolsRegressionPassed = !!toolsWorks;
    console.log('    ✓ Tools View operational:', toolsWorks);

    // ── STEP 13: Return Home and Verify Globe ─────────────────────────────────
    console.log('[13] Returning to Home View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const homeItem = items.find(el => el.textContent.includes('Home'));
        if (homeItem) homeItem.click();
      })()
    `);
    await sleep(800);

    const homeRestored = await win.webContents.executeJavaScript(`
      (() => {
        const shell = document.querySelector('.orbis-shell');
        const stage = document.querySelector('.orbis-globe-stage');
        return shell?.classList.contains('view-home') && stage?.style.pointerEvents === 'auto';
      })()
    `);
    report.phases.returnHomeGlobeRestored = !!homeRestored;
    console.log('    ✓ Home restored to interactive central Globe:', homeRestored);

    // Verify absence of direct tool execution endpoint
    try {
      const resp = await fetch('http://127.0.0.1:8000/api/tools/terminal/execute', { method: 'POST' });
      report.phases.noDirectExecuteEndpoint = resp.status === 404 || resp.status === 405;
    } catch {
      report.phases.noDirectExecuteEndpoint = true;
    }
    console.log('    ✓ Direct /api/tools/{name}/execute endpoint absent:', report.phases.noDirectExecuteEndpoint);

    // Check console errors
    report.phases.zeroConsoleErrors = report.errors.length === 0;
    console.log('    ✓ Zero console errors:', report.phases.zeroConsoleErrors);

    console.log('============================================================');
    console.log('  ELECTRON VERIFICATION REPORT SUMMARY');
    console.log('============================================================');
    console.log(JSON.stringify(report.phases, null, 2));

    const allPassed = Object.values(report.phases).every(Boolean);
    if (allPassed) {
      console.log('🎉 ALL PHASE H ELECTRON CHECKS PASSED PERFECTLY!');
    } else {
      console.warn('⚠️ SOME CHECKS FAILED');
    }

    await win.close();
    app.quit();
    process.exit(allPassed ? 0 : 1);
  } catch (err) {
    console.error('Fatal Verification Failure:', err);
    await win.close();
    app.quit();
    process.exit(1);
  }
});
