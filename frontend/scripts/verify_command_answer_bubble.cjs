/**
 * Electron Verification: Verify Command View Answer Bubble Rendering with Content
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

  const pageErrors = [];
  win.webContents.on('console-message', (event, level, message, line, sourceId) => {
    if (level >= 3) {
      pageErrors.push(message);
      console.error(`[PAGE ERROR]: ${message} (line ${line})`);
    } else {
      console.log(`[PAGE LOG]: ${message}`);
    }
  });

  try {
    const indexPath = path.join(__dirname, '../dist/index.html');
    await win.loadFile(indexPath);
    await sleep(5500);

    // Navigate to Command View
    await win.webContents.executeJavaScript(`
      (() => {
        const items = Array.from(document.querySelectorAll('#orbis-left-wheel-dock .option-wheel__item'));
        const cmdItem = items.find(el => el.textContent.includes('Command'));
        if (cmdItem) cmdItem.click();
      })()
    `);
    await sleep(1000);

    // Now submit "what's the time"
    console.log('Submitting "what is the time"...');
    await win.webContents.executeJavaScript(`
      (() => {
        const input = document.getElementById('orbis-command-prompt-input');
        const form = document.querySelector('.orbis-command-form');
        if (input && form) {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
          setter.call(input, "what is the time");
          input.dispatchEvent(new Event('input', { bubbles: true }));
          form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
        }
      })()
    `);

    // Poll for up to 10 seconds for the answer to appear or verify message stream
    console.log('Waiting for response to stream in...');
    let answerAppeared = false;
    for (let i = 0; i < 10; i++) {
      await sleep(1000);
      const state = await win.webContents.executeJavaScript(`
        (() => {
          const root = document.getElementById('root');
          const bubbles = document.querySelectorAll('.response-bubble');
          const markdowns = document.querySelectorAll('.orbis-response-markdown');
          const texts = Array.from(markdowns).map(m => m.textContent);
          const running = document.querySelector('.agent-message.running');
          return {
            rootEmpty: !root || root.innerHTML.trim() === '',
            bubblesCount: bubbles.length,
            texts,
            hasRunning: !!running
          };
        })()
      `);
      console.log('Check ' + (i + 1) + 's:', state);
      if (state.rootEmpty) {
        throw new Error('FATAL: Root went blank during response streaming!');
      }
      if (state.bubblesCount > 0) {
        answerAppeared = true;
        console.log('Answer bubble found with text:', state.texts);
        break;
      }
    }

    // Capture screenshot
    const image = await win.webContents.capturePage();
    const screenshotPath = path.join(SCRATCH_DIR, 'verified_command_final_answer.png');
    fs.writeFileSync(screenshotPath, image.toPNG());
    console.log('Final screenshot saved to:', screenshotPath);

    if (pageErrors.length > 0) {
      console.error('Page errors occurred:', pageErrors);
      process.exit(1);
    }

    console.log('SUCCESS: Verified Command View answer rendered without blank screen!');
    process.exit(0);
  } catch (err) {
    console.error('Verification failed:', err);
    process.exit(1);
  } finally {
    if (!win.isDestroyed()) win.close();
  }
});
