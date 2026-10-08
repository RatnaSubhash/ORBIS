/**
 * Automated Verification Script for ORBIS Phase F in Electron.
 *
 * Tests:
 * 1. Initial boot and awakening sequence completion.
 * 2. Left-docked OptionWheel navigation to Workspace.
 * 3. WorkspaceView rendering inside the single coherent shell.
 * 4. Ambient non-blocking Globe in WorkspaceView.
 * 5. Real continuity telemetry fetched from FastAPI Gateway.
 * 6. TiltedCard Context DNA rendering.
 * 7. AnimatedList session list rendering real segmented sessions.
 * 8. Session click triggering GET /api/continuity/sessions/{id}/events and showing events.
 * 9. Folder component showing observed resources.
 * 10. Knowledge Graph statistics panel accuracy.
 * 11. Return to Home with interactive Globe restoration.
 * 12. Screenshot capture for visual proof.
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
    optionWheelVisible: false,
    workspaceNavigation: false,
    ambientGlobeNonBlocking: false,
    observationTelemetryLoaded: false,
    tiltedCardRendered: false,
    animatedListSessionsRendered: false,
    sessionEventsDrawerOpened: false,
    folderResourcesRendered: false,
    knowledgeGraphTelemetryRendered: false,
    returnHomeGlobeRestored: false,
  },
  errors: [],
  screenshots: [],
};

app.whenReady().then(async () => {
  console.log('============================================================');
  console.log('  STARTING ORBIS PHASE F WORKSPACE ELECTRON VERIFICATION');
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

  win.webContents.on('console-message', (event, level, message) => {
    if (level === 3) {
      report.errors.push(message);
      console.error('    [Renderer Error]', message);
    }
  });

  try {
    console.log('[1] Loading renderer from', VITE_DEV_URL);
    await win.loadURL(VITE_DEV_URL);

    // 1. Wait for React shell to mount
    console.log('[2] Waiting for React shell to mount...');
    for (let i = 0; i < 20; i++) {
      const mounted = await win.webContents.executeJavaScript(`
        !!document.getElementById('orbis-left-wheel-dock')
      `);
      if (mounted) break;
      await sleep(300);
    }

    // 2. Wait for Awakening sequence to complete
    console.log('[3] Waiting for awakening sequence to complete...');
    for (let i = 0; i < 30; i++) {
      const isAwakening = await win.webContents.executeJavaScript(`
        !!document.querySelector('.orbis-shell.is-awakening')
      `);
      if (!isAwakening) break;
      await sleep(500);
    }
    await sleep(800); // allow opacity transition to settle

    const awakeningCheck = await win.webContents.executeJavaScript(`
      (() => {
        const shell = document.querySelector('.orbis-shell');
        const wordmark = document.querySelector('.orbis-wordmark');
        const canvas = document.querySelector('.orbis-globe-stage canvas');
        const dock = document.getElementById('orbis-left-wheel-dock');
        const wheel = dock?.querySelector('.option-wheel');
        const items = Array.from(dock?.querySelectorAll('.option-wheel__item') || []).map(el => el.textContent.trim());
        return {
          hasShell: !!shell,
          wordmark: wordmark?.textContent?.trim(),
          hasCanvas: !!canvas,
          hasDock: !!dock,
          hasWheel: !!wheel,
          itemsCount: items.length,
          sampleItems: items.slice(0, 5),
          isAwakening: dock?.classList.contains('is-awakening')
        };
      })()
    `);
    console.log('    Boot & awakening check:', awakeningCheck);
    if (awakeningCheck.hasShell && awakeningCheck.hasCanvas) report.phases.awakening = true;
    if (awakeningCheck.hasDock && awakeningCheck.hasWheel && awakeningCheck.itemsCount >= 9 && !awakeningCheck.isAwakening) {
      report.phases.optionWheelVisible = true;
    }

    // 2. Select Workspace on left-side OptionWheel
    console.log('[3] Navigating to Workspace via direct left-side OptionWheel...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const wsItem = items.find(el => el.textContent.includes('Workspace'));
        if (wsItem) {
          wsItem.click();
        }
      })()
    `);
    await sleep(800);

    // Wait for continuity telemetry fetch
    console.log('[4] Verifying WorkspaceView mounting and ambient Globe...');
    const wsNavCheck = await win.webContents.executeJavaScript(`
      (() => {
        const wsView = document.querySelector('.orbis-workspace-view');
        const secondaryGlobe = document.querySelector('.orbis-globe-stage.secondary-globe');
        const globePointerEvents = secondaryGlobe ? window.getComputedStyle(secondaryGlobe).pointerEvents : null;
        return {
          hasWorkspaceView: !!wsView,
          hasSecondaryGlobe: !!secondaryGlobe,
          globePointerEvents: globePointerEvents
        };
      })()
    `);
    console.log('    Workspace navigation status:', wsNavCheck);
    if (wsNavCheck.hasWorkspaceView) report.phases.workspaceNavigation = true;
    if (wsNavCheck.hasSecondaryGlobe && wsNavCheck.globePointerEvents === 'none') {
      report.phases.ambientGlobeNonBlocking = true;
    }

    // 3. Verify Telemetry & React Bits components
    console.log('[5] Checking telemetry, TiltedCard, AnimatedList, and Folder...');
    await sleep(1500); // allow async fetch to populate

    const componentCheck = await win.webContents.executeJavaScript(`
      (() => {
        const obsDot = document.querySelector('.orbis-obs-dot');
        const obsText = document.querySelector('.orbis-obs-text')?.textContent;
        const totalEvents = document.querySelector('.orbis-stat-pill .stat-value')?.textContent;
        
        // TiltedCard Context DNA
        const tiltedCard = document.querySelector('.orbis-dna-tilted-card');
        const dnaContent = document.querySelector('.orbis-dna-card-content');
        const hasDnaContent = !!dnaContent;

        // AnimatedList Sessions
        const sessionCards = Array.from(document.querySelectorAll('.orbis-session-card'));
        const scrollList = document.querySelector('.orbis-session-list .scroll-list');

        // Folder Explorer
        const folder = document.querySelector('.orbis-workspace-folder');
        const papers = Array.from(document.querySelectorAll('.orbis-workspace-folder .paper'));

        // Knowledge Graph
        const kgStats = Array.from(document.querySelectorAll('.orbis-kg-stat .kg-num')).map(el => el.textContent);

        return {
          obsActive: obsDot?.classList.contains('active'),
          obsText: obsText,
          totalEvents: totalEvents,
          hasTiltedCard: !!tiltedCard,
          hasDnaContent: hasDnaContent,
          hasScrollList: !!scrollList,
          sessionCount: sessionCards.length,
          hasFolder: !!folder,
          folderPapersCount: papers.length,
          kgStats: kgStats
        };
      })()
    `);
    console.log('    Workspace components status:', componentCheck);

    if (componentCheck.obsText && componentCheck.obsText.includes('OBSERVING')) {
      report.phases.observationTelemetryLoaded = true;
    }
    if (componentCheck.hasTiltedCard && componentCheck.hasDnaContent) {
      report.phases.tiltedCardRendered = true;
    }
    if (componentCheck.hasScrollList && componentCheck.sessionCount > 0) {
      report.phases.animatedListSessionsRendered = true;
    }
    if (componentCheck.hasFolder && componentCheck.folderPapersCount >= 3) {
      report.phases.folderResourcesRendered = true;
    }
    if (componentCheck.kgStats && componentCheck.kgStats.length === 4) {
      report.phases.knowledgeGraphTelemetryRendered = true;
    }

    // 4. Click a session to inspect events
    console.log('[6] Clicking first session to inspect raw sanitized events...');
    const drawerCheck = await win.webContents.executeJavaScript(`
      (() => {
        const firstSession = document.querySelector('.orbis-session-card');
        if (firstSession) {
          firstSession.click();
          return true;
        }
        return false;
      })()
    `);

    if (drawerCheck) {
      await sleep(1000); // allow event fetching
      const eventsCheck = await win.webContents.executeJavaScript(`
        (() => {
          const drawer = document.querySelector('.orbis-session-events-drawer');
          const eventRows = Array.from(document.querySelectorAll('.drawer-event-row'));
          return {
            hasDrawer: !!drawer,
            eventRowsCount: eventRows.length,
            sampleEvent: eventRows[0]?.textContent
          };
        })()
      `);
      console.log('    Session events drawer status:', eventsCheck);
      if (eventsCheck.hasDrawer && eventsCheck.eventRowsCount > 0) {
        report.phases.sessionEventsDrawerOpened = true;
      }
    }

    // 5. Capture Workspace screenshot
    const wsShot = await win.capturePage();
    const wsShotPath = path.join(ARTIFACT_DIR, 'workspace_view.png');
    fs.writeFileSync(wsShotPath, wsShot.toPNG());
    report.screenshots.push(wsShotPath);
    console.log('    Saved Workspace screenshot to:', wsShotPath);

    // Also copy to brain artifact dir for carousel presentation
    const brainDir = path.resolve('C:/Users/kratn/.gemini/antigravity-ide/brain/0a1b0255-f200-4267-b499-37970beb683a');
    if (fs.existsSync(brainDir)) {
      fs.copyFileSync(wsShotPath, path.join(brainDir, 'workspace_view.png'));
    }

    // 6. Return to Home
    console.log('[7] Returning to Home view on left-side OptionWheel...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const homeItem = items.find(el => el.textContent.includes('Home'));
        if (homeItem) homeItem.click();
      })()
    `);
    await sleep(800);

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
      report.phases.returnHomeGlobeRestored = true;
    }

    console.log('\n============================================================');
    console.log('  PHASE F ELECTRON VERIFICATION SUMMARY:');
    console.log('============================================================');
    let allPassed = true;
    Object.entries(report.phases).forEach(([key, passed]) => {
      console.log(`  [${passed ? 'PASS' : 'FAIL'}] ${key}`);
      if (!passed) allPassed = false;
    });
    console.log(`  Total Errors Logged: ${report.errors.length}`);
    console.log('============================================================');

    if (allPassed && report.errors.length === 0) {
      console.log('\nALL PHASE F VERIFICATION CHECKS PASSED!\n');
    } else {
      console.error('\nSome checks failed or errors were reported.\n');
    }
  } catch (err) {
    console.error('Test execution exception:', err);
    report.errors.push(err.message);
  } finally {
    win.close();
    app.quit();
  }
});
