/**
 * Verification Script for Unified Workspace & Restoration Center in Electron.
 */
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const SCRATCH_DIR = path.resolve(__dirname, '../../scratch');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1240,
    height: 880,
    show: true,
    backgroundColor: '#0a0b0e',
    webPreferences: {
      preload: path.join(__dirname, '../electron/preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: false,
    },
  });

  try {
    const indexPath = path.join(__dirname, '../dist/index.html');
    console.log('Loading dist from:', indexPath);
    await win.loadFile(indexPath);

    // Wait for awakening sequence to conclude (5000ms)
    console.log('Waiting for awakening sequence...');
    await sleep(5500);

    // 1. Verify OptionWheel items
    const wheelItems = await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        return items.map(el => el.textContent.trim());
      })()
    `);
    console.log('OptionWheel items:', wheelItems);

    const hasWorkspace = wheelItems.some(item => item.includes('Workspace'));
    const hasSeparateRestoration = wheelItems.some(item => item === 'Restoration');

    console.log('- Has Workspace in OptionWheel?', hasWorkspace);
    console.log('- Has Separate Restoration?', hasSeparateRestoration);

    // 2. Navigate to Workspace View
    console.log('Navigating to Unified Workspace View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const wsItem = items.find(el => el.textContent.includes('Workspace'));
        if (wsItem) wsItem.click();
      })()
    `);
    await sleep(1500);

    // 3. Inspect Unified Workspace View components
    const viewMetrics = await win.webContents.executeJavaScript(`
      (() => {
        const view = document.querySelector('.orbis-workspace-view');
        const promptBar = document.querySelector('.orbis-workspace-prompt-bar');
        const resumeBtn = document.querySelector('.orbis-resume-btn');
        const dnaCard = document.querySelector('.orbis-dna-tilted-card');
        const kgPanel = document.querySelector('.orbis-kg-panel');
        const folder = document.querySelector('.orbis-workspace-folder');
        const sessionsList = document.querySelector('.orbis-session-list');
        const tabs = document.querySelector('.orbis-workspace-tabs');
        const restorationConsole = document.querySelector('.orbis-restoration-console');

        return {
          viewMounted: !!view,
          promptBarFound: !!promptBar,
          resumeBtnFound: !!resumeBtn,
          dnaCardFound: !!dnaCard,
          kgPanelFound: !!kgPanel,
          folderFound: !!folder,
          sessionsListFound: !!sessionsList,
          tabsFound: !!tabs,
          restorationConsoleFound: !!restorationConsole,
        };
      })()
    `);
    console.log('Unified Workspace Metrics:', JSON.stringify(viewMetrics, null, 2));

    // Capture screenshot
    const wsImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'verified_unified_workspace.png'), wsImage.toPNG());
    console.log('Captured scratch/verified_unified_workspace.png');

    const allPassed =
      hasWorkspace &&
      !hasSeparateRestoration &&
      viewMetrics.viewMounted &&
      viewMetrics.promptBarFound &&
      viewMetrics.resumeBtnFound &&
      viewMetrics.dnaCardFound &&
      viewMetrics.kgPanelFound &&
      viewMetrics.folderFound &&
      viewMetrics.tabsFound;

    console.log('Evaluation:');
    console.log('Overall Unified Workspace Verification Passed:', allPassed);

    await win.close();
    app.quit();
    process.exit(allPassed ? 0 : 1);
  } catch (err) {
    console.error('Test error:', err);
    await win.close();
    app.quit();
    process.exit(1);
  }
});
