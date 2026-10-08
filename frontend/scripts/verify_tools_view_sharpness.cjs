/**
 * Electron Verification: Verify Tools View Right-Side Clarity (No Blur)
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
    const indexPath = path.join(__dirname, '../dist/index.html');
    await win.loadFile(indexPath);
    await sleep(5500);

    // Navigate to Tools View
    console.log('Navigating to Tools View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const toolsItem = items.find(el => el.textContent.includes('Tools'));
        if (toolsItem) toolsItem.click();
      })()
    `);
    await sleep(1500);

    // Check right side inspector styles
    const styles = await win.webContents.executeJavaScript(`
      (() => {
        const inspector = document.querySelector('.orbis-tool-inspector');
        const tilted = document.querySelector('.orbis-tool-tilted-card');
        const title = document.querySelector('.inspector-title');
        const desc = document.querySelector('.inspector-desc');
        const cs = inspector ? window.getComputedStyle(inspector) : null;

        return {
          inspectorPresent: !!inspector,
          hasTiltedCard: !!tilted,
          backdropFilter: cs ? cs.backdropFilter : null,
          backgroundColor: cs ? cs.backgroundColor : null,
          titleText: title ? title.textContent : null,
          descText: desc ? desc.textContent : null
        };
      })()
    `);
    console.log('Right-side inspector styles:', styles);

    // Capture screenshot
    const image = await win.webContents.capturePage();
    const screenshotPath = path.join(SCRATCH_DIR, 'verified_tools_view_cleared.png');
    fs.writeFileSync(screenshotPath, image.toPNG());
    console.log('Screenshot saved to:', screenshotPath);

    process.exit(0);
  } catch (err) {
    console.error('Verification failed:', err);
    process.exit(1);
  } finally {
    if (!win.isDestroyed()) win.close();
  }
});
