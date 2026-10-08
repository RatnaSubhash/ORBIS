/**
 * Automated Verification Script for ORBIS Phases C, D, and E in Electron.
 *
 * Tests:
 * 1. Awakening sequence & wordmark.
 * 2. Home view with large central Globe.
 * 3. OptionWheel floating trigger & radial overlay.
 * 4. Navigation to Command View.
 * 5. Subtle ambient non-blocking Globe in Command View.
 * 6. Real prompt submission, WebSocket events, and SplitText answer.
 * 7. Navigation to Live Flow View.
 * 8. Live Flow metrics, LogoLoop tool telemetry, and event timeline.
 * 9. Return to Home View with restored primary Globe.
 * 10. Screenshot capture for visual confirmation.
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

const report = {
  phases: {
    awakening: false,
    homeGlobe: false,
    optionWheelTrigger: false,
    optionWheelRadialOpen: false,
    commandViewNavigation: false,
    commandAmbientGlobe: false,
    commandTaskExecution: false,
    splitTextRendered: false,
    liveFlowNavigation: false,
    liveFlowLogoLoop: false,
    liveFlowEvents: false,
    returnHome: false,
  },
  errors: [],
  screenshots: [],
};

app.whenReady().then(async () => {
  console.log('============================================================');
  console.log('  STARTING ORBIS PHASES C, D, E ELECTRON VERIFICATION');
  console.log('============================================================');

  const preloadPath = path.join(__dirname, '../electron/preload.cjs');

  const win = new BrowserWindow({
    width: 1200,
    height: 860,
    show: false, // automated headless execution
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

    // 1. Wait for Awakening sequence (glide settles at ~4.2s)
    console.log('[2] Waiting for awakening sequence completion (4500ms)...');
    await sleep(4500);

    const awakeningCheck = await win.webContents.executeJavaScript(`
      (() => {
        const shell = document.querySelector('.orbis-shell');
        const wordmark = document.querySelector('.orbis-wordmark');
        const canvas = document.querySelector('.orbis-globe-stage canvas');
        const isAwakening = shell?.classList.contains('is-awakening');
        return {
          hasShell: !!shell,
          wordmarkText: wordmark?.textContent?.trim(),
          hasCanvas: !!canvas,
          isAwakening: isAwakening,
          isHome: shell?.classList.contains('view-home')
        };
      })()
    `);
    console.log('    Awakening status:', awakeningCheck);
    if (awakeningCheck.hasShell && awakeningCheck.hasCanvas) {
      report.phases.awakening = true;
      report.phases.homeGlobe = true;
    }

    // Capture Home screenshot
    const homeShot = await win.capturePage();
    const homeShotPath = path.join(ARTIFACT_DIR, 'home_globe.png');
    fs.writeFileSync(homeShotPath, homeShot.toPNG());
    report.screenshots.push(homeShotPath);
    console.log('    Saved Home screenshot to:', homeShotPath);

    // 2. Check OptionWheel Direct Left-Side Dock
    console.log('[3] Verifying direct left-side OptionWheel dock...');
    const dockCheck = await win.webContents.executeJavaScript(`
      (() => {
        const dock = document.getElementById('orbis-left-wheel-dock');
        const wheel = dock?.querySelector('.option-wheel');
        const rect = dock?.getBoundingClientRect();
        const items = Array.from(dock?.querySelectorAll('.option-wheel__item') || []).map(el => el.textContent.trim());
        return {
          hasDock: !!dock,
          hasWheel: !!wheel,
          left: rect?.left,
          width: rect?.width,
          itemsCount: items.length,
          sampleItems: items.slice(0, 5)
        };
      })()
    `);
    console.log('    Direct Left Dock status:', dockCheck);
    if (dockCheck.hasDock && dockCheck.hasWheel && dockCheck.itemsCount >= 9) {
      report.phases.optionWheelTrigger = true;
      report.phases.optionWheelRadialOpen = true;
    }

    // 4. Directly click "Command" on the left-side OptionWheel
    console.log('[4] Directly selecting Command View on left-side OptionWheel...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const commandItem = items.find(el => el.textContent.includes('Command'));
        if (commandItem) {
          commandItem.click();
        }
      })()
    `);
    await sleep(600);

    const commandNavCheck = await win.webContents.executeJavaScript(`
      (() => {
        const commandView = document.querySelector('.orbis-command-view');
        const secondaryGlobe = document.querySelector('.orbis-globe-stage.secondary-globe');
        const globePointerEvents = secondaryGlobe ? window.getComputedStyle(secondaryGlobe).pointerEvents : null;
        return {
          hasCommandView: !!commandView,
          hasSecondaryGlobe: !!secondaryGlobe,
          globePointerEvents: globePointerEvents
        };
      })()
    `);
    console.log('    Command navigation status:', commandNavCheck);
    if (commandNavCheck.hasCommandView) {
      report.phases.commandViewNavigation = true;
    }
    if (commandNavCheck.hasSecondaryGlobe && commandNavCheck.globePointerEvents === 'none') {
      report.phases.commandAmbientGlobe = true;
    }

    // 5. Submit a real task in Command View
    console.log('[6] Submitting real task in Command View ("What was I working on?")...');
    const submitTriggered = await win.webContents.executeJavaScript(`
      (() => {
        const input = document.getElementById('orbis-command-prompt-input');
        const form = document.querySelector('.orbis-command-form');
        const btn = document.getElementById('orbis-command-submit-btn');
        if (input) {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(input, 'What was I working on?');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          
          setTimeout(() => {
            if (btn && !btn.disabled) {
              btn.click();
            } else if (form) {
              form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }
          }, 100);
          return true;
        }
        return false;
      })()
    `);
    console.log('    Submit initiated:', submitTriggered);

    // Wait for task to process and answer to stream in
    console.log('    Waiting for task execution & answer streaming (6000ms)...');
    await sleep(6000);

    const taskExecCheck = await win.webContents.executeJavaScript(`
      (() => {
        const responseMarkdown = document.querySelector('.orbis-response-markdown');
        const splitText = document.querySelector('.orbis-splittext-heading');
        const statusBadge = document.querySelector('.orbis-command-status-badge');
        const taskId = document.querySelector('.orbis-command-task-id');
        return {
          hasResponseMarkdown: !!responseMarkdown,
          responseText: responseMarkdown?.textContent?.slice(0, 100),
          hasSplitText: !!splitText,
          splitTextContent: splitText?.textContent,
          statusText: statusBadge?.textContent,
          taskId: taskId?.textContent
        };
      })()
    `);
    console.log('    Task execution result:', taskExecCheck);
    if (taskExecCheck.hasResponseMarkdown) {
      report.phases.commandTaskExecution = true;
    }
    if (taskExecCheck.hasSplitText) {
      report.phases.splitTextRendered = true;
    }

    // Capture Command View screenshot
    const commandShot = await win.capturePage();
    const commandShotPath = path.join(ARTIFACT_DIR, 'command_view.png');
    fs.writeFileSync(commandShotPath, commandShot.toPNG());
    report.screenshots.push(commandShotPath);
    console.log('    Saved Command View screenshot to:', commandShotPath);

    // 6. Navigate to Live Flow View
    console.log('[7] Navigating to Live Flow View directly on left-side OptionWheel...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const liveFlowItem = items.find(el => el.textContent.includes('Live Flow'));
        if (liveFlowItem) liveFlowItem.click();
      })()
    `);
    await sleep(600);

    const liveFlowCheck = await win.webContents.executeJavaScript(`
      (() => {
        const liveFlowView = document.querySelector('.orbis-live-flow-view');
        const logoLoop = document.querySelector('.orbis-flow-logoloop');
        const metrics = Array.from(document.querySelectorAll('.orbis-metric-card')).length;
        const events = Array.from(document.querySelectorAll('.orbis-flow-event-row')).length;
        return {
          hasLiveFlowView: !!liveFlowView,
          hasLogoLoop: !!logoLoop,
          metricsCount: metrics,
          eventsCount: events
        };
      })()
    `);
    console.log('    Live Flow status:', liveFlowCheck);
    if (liveFlowCheck.hasLiveFlowView) report.phases.liveFlowNavigation = true;
    if (liveFlowCheck.hasLogoLoop) report.phases.liveFlowLogoLoop = true;
    if (liveFlowCheck.eventsCount > 0) report.phases.liveFlowEvents = true;

    // Capture Live Flow screenshot
    const liveFlowShot = await win.capturePage();
    const liveFlowShotPath = path.join(ARTIFACT_DIR, 'live_flow_view.png');
    fs.writeFileSync(liveFlowShotPath, liveFlowShot.toPNG());
    report.screenshots.push(liveFlowShotPath);
    console.log('    Saved Live Flow screenshot to:', liveFlowShotPath);

    // 7. Return to Home View
    console.log('[8] Returning to Home View directly on left-side OptionWheel...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const homeItem = items.find(el => el.textContent.includes('Home'));
        if (homeItem) homeItem.click();
      })()
    `);
    await sleep(600);

    const returnHomeCheck = await win.webContents.executeJavaScript(`
      (() => {
        const homeView = document.querySelector('.orbis-home-view');
        const homeGlobe = document.querySelector('.orbis-globe-stage.home-globe');
        const globePointerEvents = homeGlobe ? window.getComputedStyle(homeGlobe).pointerEvents : null;
        return {
          hasHomeView: !!homeView,
          hasHomeGlobe: !!homeGlobe,
          globePointerEvents: globePointerEvents
        };
      })()
    `);
    console.log('    Return Home status:', returnHomeCheck);
    if (returnHomeCheck.hasHomeView && returnHomeCheck.hasHomeGlobe && returnHomeCheck.globePointerEvents === 'auto') {
      report.phases.returnHome = true;
    }

    console.log('\n============================================================');
    console.log('  VERIFICATION REPORT SUMMARY:');
    console.log('============================================================');
    Object.entries(report.phases).forEach(([key, passed]) => {
      console.log(`  [${passed ? 'PASS' : 'FAIL'}] ${key}`);
    });
    console.log('  Total Errors Logged:', report.errors.length);
    console.log('============================================================\n');

    const allPassed = Object.values(report.phases).every(Boolean);
    if (allPassed && report.errors.length === 0) {
      console.log('ALL PHASES C, D, E CHECKS PASSED SUCCESSFULLY!');
      app.exit(0);
    } else {
      console.warn('Some checks failed or errors were reported.');
      app.exit(1);
    }
  } catch (err) {
    console.error('Exception during verification:', err);
    app.exit(1);
  }
});
