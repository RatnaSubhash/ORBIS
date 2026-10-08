/**
 * Verification Script for Clean, Calm Workspace View in Electron.
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

    // Wait for awakening sequence
    console.log('Waiting for awakening sequence...');
    await sleep(5500);

    // Navigate to Workspace View
    console.log('Navigating to Workspace View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const wsItem = items.find(el => el.textContent.includes('Workspace'));
        if (wsItem) wsItem.click();
      })()
    `);
    await sleep(2000);

    // Inspect Workspace View details
    const details = await win.webContents.executeJavaScript(`
      (() => {
        const title = document.querySelector('.orbis-workspace-title')?.textContent;
        const badge = document.querySelector('.orbis-workspace-badge')?.textContent;
        const obsText = document.querySelector('.orbis-obs-text')?.textContent;
        const eventCount = document.querySelector('.orbis-stat-pill .stat-value')?.textContent;
        const dnaTitle = document.querySelector('.orbis-card-label')?.textContent;
        const dnaDesc = document.querySelector('.task-desc')?.textContent;
        const sessions = Array.from(document.querySelectorAll('.orbis-session-card')).map(card => {
          return {
            time: card.querySelector('.orbis-session-time')?.textContent,
            duration: card.querySelector('.session-duration')?.textContent,
            apps: Array.from(card.querySelectorAll('.orbis-app-pill')).map(p => p.textContent.trim()),
            foot: card.querySelector('.orbis-session-foot')?.textContent,
          };
        });
        const steps = Array.from(document.querySelectorAll('.orbis-step-card')).map(card => {
          return {
            app: card.querySelector('.orbis-step-app-name')?.textContent.trim(),
            checked: card.querySelector('.orbis-step-checkbox')?.checked,
          };
        });
        const executeBtnText = document.querySelector('.orbis-restoration-btn.primary')?.textContent.trim();

        return {
          title,
          badge,
          obsText,
          eventCount,
          dnaTitle,
          dnaDesc,
          sessionCount: sessions.length,
          topSession: sessions[0],
          stepsCount: steps.length,
          steps,
          executeBtnText,
        };
      })()
    `);
    console.log('Clean Workspace Inspection:', JSON.stringify(details, null, 2));

    // Capture screenshot
    const wsImage = await win.webContents.capturePage();
    const screenshotPath = path.join(SCRATCH_DIR, 'clean_workspace_verified.png');
    fs.writeFileSync(screenshotPath, wsImage.toPNG());
    console.log('Saved screenshot to:', screenshotPath);

    console.log('Verification completed successfully!');
  } catch (err) {
    console.error('Verification failed:', err);
  } finally {
    win.close();
    app.quit();
  }
});
