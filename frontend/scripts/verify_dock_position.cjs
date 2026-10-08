/**
 * Electron Verification: Verify Dock Fixed Bottom Center & No Overlap
 */
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const SCRATCH_DIR = path.resolve(__dirname, '../../scratch');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
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
    // Load from built dist
    const indexPath = path.join(__dirname, '../dist/index.html');
    console.log('Loading dist from:', indexPath);
    await win.loadFile(indexPath);

    // Wait for Awakening sequence to genuinely conclude (4000ms lifecycle)
    console.log('Waiting for awakening sequence to conclude (5000ms)...');
    await sleep(5500);
    console.log('Awakening concluded.');

    const shellState = await win.webContents.executeJavaScript(`
      (() => {
        const shell = document.querySelector('.orbis-shell');
        const main = document.querySelector('.orbis-main-content');
        return {
          shellClass: shell?.className,
          mainOpacity: main ? window.getComputedStyle(main).opacity : null,
          mainDisplay: main ? window.getComputedStyle(main).display : null,
        };
      })()
    `);
    console.log('Post-awakening shell state:', shellState);

    // Navigate to Command View
    console.log('Navigating to Command View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const cmdItem = items.find(el => el.textContent.includes('Command'));
        if (cmdItem) cmdItem.click();
      })()
    `);
    await sleep(1000);

    // Check Command View positions
    const cmdMetrics = await win.webContents.executeJavaScript(`
      (() => {
        const dockOuter = document.querySelector('.dock-outer');
        const dockPanel = document.querySelector('.dock-panel');
        const inputZone = document.querySelector('.orbis-command-input-zone');
        const winH = window.innerHeight;
        const winW = window.innerWidth;

        const dRect = dockOuter ? dockOuter.getBoundingClientRect() : null;
        const pRect = dockPanel ? dockPanel.getBoundingClientRect() : null;
        const iRect = inputZone ? inputZone.getBoundingClientRect() : null;

        return {
          winH,
          winW,
          dockOuterRect: dRect ? { top: dRect.top, bottom: dRect.bottom, left: dRect.left, right: dRect.right, width: dRect.width, height: dRect.height } : null,
          dockPanelRect: pRect ? { top: pRect.top, bottom: pRect.bottom, left: pRect.left, right: pRect.right, width: pRect.width, height: pRect.height } : null,
          inputRect: iRect ? { top: iRect.top, bottom: iRect.bottom, left: iRect.left, right: iRect.right, width: iRect.width, height: iRect.height } : null,
        };
      })()
    `);

    console.log('Command View Metrics:', JSON.stringify(cmdMetrics, null, 2));

    // Capture screenshot of Command View
    const cmdImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'verified_command_dock.png'), cmdImage.toPNG());
    console.log('Captured scratch/verified_command_dock.png');

    // Navigate to Live Flow View
    console.log('Navigating to Live Flow View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const flowItem = items.find(el => el.textContent.includes('Live Flow'));
        if (flowItem) flowItem.click();
      })()
    `);
    await sleep(1000);

    // Check Live Flow positions
    const flowMetrics = await win.webContents.executeJavaScript(`
      (() => {
        const dockOuter = document.querySelector('.dock-outer');
        const dockPanel = document.querySelector('.dock-panel');
        const winH = window.innerHeight;
        const winW = window.innerWidth;

        const dRect = dockOuter ? dockOuter.getBoundingClientRect() : null;
        const pRect = dockPanel ? dockPanel.getBoundingClientRect() : null;

        return {
          winH,
          winW,
          dockOuterRect: dRect ? { top: dRect.top, bottom: dRect.bottom, left: dRect.left, right: dRect.right, width: dRect.width, height: dRect.height } : null,
          dockPanelRect: pRect ? { top: pRect.top, bottom: pRect.bottom, left: pRect.left, right: pRect.right, width: pRect.width, height: pRect.height } : null,
        };
      })()
    `);

    console.log('Live Flow View Metrics:', JSON.stringify(flowMetrics, null, 2));

    // Capture screenshot of Live Flow View
    const flowImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'verified_liveflow_dock.png'), flowImage.toPNG());
    console.log('Captured scratch/verified_liveflow_dock.png');

    // Evaluations
    const dBottom = cmdMetrics.dockOuterRect?.bottom || 0;
    const isAtBottom = dBottom >= (cmdMetrics.winH - 30);
    const dCenter = (cmdMetrics.dockPanelRect?.left || 0) + (cmdMetrics.dockPanelRect?.width || 0) / 2;
    const isCentered = Math.abs(dCenter - (cmdMetrics.winW / 2)) < 15;
    const noOverlap = (cmdMetrics.inputRect?.bottom || 0) <= (cmdMetrics.dockPanelRect?.top || 9999);

    console.log('Evaluation:');
    console.log('- Is Dock fixed at bottom?', isAtBottom, `(bottom: ${dBottom}px, window: ${cmdMetrics.winH}px)`);
    console.log('- Is Dock horizontally centered?', isCentered, `(dock center: ${dCenter}px, win center: ${cmdMetrics.winW / 2}px)`);
    console.log('- No overlap between input and dock?', noOverlap, `(input bottom: ${cmdMetrics.inputRect?.bottom}px, dock top: ${cmdMetrics.dockPanelRect?.top}px)`);

    await win.close();
    app.quit();
    process.exit(isAtBottom && isCentered && noOverlap ? 0 : 1);
  } catch (err) {
    console.error('Test error:', err);
    await win.close();
    app.quit();
    process.exit(1);
  }
});
