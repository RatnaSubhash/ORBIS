/**
 * ORBIS Phase 12 Electron Main Process.
 * Manages the desktop application lifecycle and window security boundaries.
 */

const { app, BrowserWindow } = require('electron');
const path = require('path');

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1080,
    height: 760,
    minWidth: 800,
    minHeight: 600,
    backgroundColor: '#0a0b0e',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  // Load Vite dev server URL if in development, otherwise load built index.html
  const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173';
  const isDev = process.env.NODE_ENV !== 'production' && !app.isPackaged;

  if (isDev) {
    mainWindow.loadURL(devServerUrl).catch((err) => {
      if (!process.env.ELECTRON_TEST_EXIT && !process.argv.includes('--test-exit')) {
        console.warn('Could not connect to Vite dev server, falling back to dist/index.html:', err.message);
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.loadFile(path.join(__dirname, '../dist/index.html')).catch(() => {});
        }
      }
    });
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html')).catch(() => {});
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  if (process.env.ELECTRON_TEST_EXIT || process.argv.includes('--test-exit')) {
    console.log('Electron main process started successfully with secure webPreferences.');
    setTimeout(() => {
      app.quit();
    }, 500);
  }
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
