/**
 * ORBIS Phase 12 Electron Preload Script.
 * Strictly isolates the renderer process:
 * - contextIsolation: true
 * - nodeIntegration: false
 * - No exposure of Node.js fs, child_process, shell, or process internals.
 */

const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('orbisBridge', {
  platform: process.platform,
  version: '0.2.0',
  defaultGatewayUrl: 'http://127.0.0.1:8000',
  defaultWsUrl: 'ws://127.0.0.1:8000',
});
