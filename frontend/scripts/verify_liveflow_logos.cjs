/**
 * Verification Script: Validate Visible Logos in Live Flow LogoLoop
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

    // Wait for awakening to conclude
    console.log('Waiting for awakening sequence...');
    await sleep(5500);

    // Navigate to Live Flow View
    console.log('Navigating to Live Flow View...');
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const flowItem = items.find(el => el.textContent.includes('Live Flow'));
        if (flowItem) flowItem.click();
      })()
    `);
    await sleep(1500);

    // Inspect the capability logos
    const logoMetrics = await win.webContents.executeJavaScript(`
      (() => {
        const container = document.querySelector('.orbis-telemetry-strip-container');
        const loop = document.querySelector('.orbis-flow-logoloop');
        const badges = Array.from(document.querySelectorAll('.orbis-telemetry-badge'));
        const svgLogos = Array.from(document.querySelectorAll('.orbis-telemetry-logo svg'));

        const sampleBadges = badges.slice(0, 5).map(b => {
          const nameEl = b.querySelector('.orbis-telemetry-name');
          const logoEl = b.querySelector('.orbis-telemetry-logo');
          const svgEl = logoEl?.querySelector('svg');
          const rect = b.getBoundingClientRect();
          return {
            name: nameEl ? nameEl.textContent : '',
            hasSvg: !!svgEl,
            width: rect.width,
            height: rect.height,
            visible: rect.width > 0 && rect.height > 0,
          };
        });

        return {
          containerFound: !!container,
          loopFound: !!loop,
          totalBadges: badges.length,
          totalSvgs: svgLogos.length,
          sampleBadges,
        };
      })()
    `);

    console.log('Live Flow Logo Metrics:', JSON.stringify(logoMetrics, null, 2));

    // Capture screenshot of Live Flow
    const flowImage = await win.webContents.capturePage();
    fs.writeFileSync(path.join(SCRATCH_DIR, 'verified_liveflow_logos.png'), flowImage.toPNG());
    console.log('Captured scratch/verified_liveflow_logos.png');

    const passed =
      logoMetrics.containerFound &&
      logoMetrics.loopFound &&
      logoMetrics.totalBadges >= 10 &&
      logoMetrics.totalSvgs >= 10 &&
      logoMetrics.sampleBadges.every(s => s.hasSvg && s.visible);

    console.log('Evaluation:');
    console.log('- Are capability logos rendered and visible?', passed);

    await win.close();
    app.quit();
    process.exit(passed ? 0 : 1);
  } catch (err) {
    console.error('Test error:', err);
    await win.close();
    app.quit();
    process.exit(1);
  }
});
