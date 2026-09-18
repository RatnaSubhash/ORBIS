/**
 * ORBIS Phase 12 Gateway Client.
 * Handles REST communication and real-time WebSocket IPC with the ORBIS FastAPI Gateway.
 */

export const GATEWAY_HTTP_BASE = (typeof window !== 'undefined' && window.orbisBridge?.defaultGatewayUrl)
  ? window.orbisBridge.defaultGatewayUrl
  : 'http://127.0.0.1:8000';

export const GATEWAY_WS_BASE = (typeof window !== 'undefined' && window.orbisBridge?.defaultWsUrl)
  ? window.orbisBridge.defaultWsUrl
  : 'ws://127.0.0.1:8000';

/**
 * Checks Gateway connectivity and version.
 * GET /api/health
 */
export async function checkGatewayHealth() {
  try {
    const response = await fetch(`${GATEWAY_HTTP_BASE}/api/health`, {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
    });
    if (!response.ok) {
      throw new Error(`Gateway returned HTTP ${response.status}`);
    }
    return await response.json();
  } catch (err) {
    throw new Error(`Gateway unavailable (${err.message})`);
  }
}

/**
 * Submits a new agent task prompt.
 * POST /api/tasks
 * @param {string} prompt - User request or command.
 * @param {boolean} [sync=false] - If true, blocks until completion; if false, runs asynchronously.
 * @returns {Promise<{ task_id: string, correlation_id: string, status: string, message: string }>}
 */
export async function submitTask(prompt, sync = false) {
  if (!prompt || !prompt.trim()) {
    throw new Error('Task prompt cannot be empty.');
  }

  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/tasks`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      prompt: prompt.trim(),
      sync,
      metadata: { client: 'orbis-electron-shell' },
    }),
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(`Failed to submit task: ${errorDetail}`);
  }

  return await response.json();
}

/**
 * Retrieves the current operational status of an agent task.
 * GET /api/tasks/{task_id}
 */
export async function getTaskStatus(taskId) {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/tasks/${encodeURIComponent(taskId)}`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    throw new Error(`Task status request failed: HTTP ${response.status}`);
  }

  return await response.json();
}

/**
 * Retrieves bounded historical AgentEvents for a specific task.
 * GET /api/tasks/{task_id}/events
 */
export async function getTaskEvents(taskId) {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/tasks/${encodeURIComponent(taskId)}/events`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    throw new Error(`Task events request failed: HTTP ${response.status}`);
  }

  return await response.json();
}

/**
 * Connects to the real-time WebSocket event stream for a specific task.
 * WS /ws/tasks/{task_id}?replay=true
 *
 * @param {string} taskId - Target task identifier.
 * @param {object} callbacks - { onEvent, onError, onClose, onOpen }
 * @param {boolean} [replay=true] - Whether to request historical events replay.
 * @returns {() => void} Cleanup function to close the WebSocket.
 */
export function connectTaskWebSocket(taskId, { onEvent, onError, onClose, onOpen } = {}, replay = true) {
  const url = `${GATEWAY_WS_BASE}/ws/tasks/${encodeURIComponent(taskId)}?replay=${replay}`;
  let socket = null;
  let isManuallyClosed = false;

  try {
    socket = new WebSocket(url);
  } catch (err) {
    if (onError) onError(err);
    return () => {};
  }

  socket.onopen = () => {
    if (onOpen) onOpen();
  };

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (onEvent) {
        onEvent(data);
      }
    } catch (parseErr) {
      console.warn('Failed to parse WebSocket AgentEvent:', parseErr);
    }
  };

  socket.onerror = (err) => {
    if (onError) onError(err);
  };

  socket.onclose = (event) => {
    if (!isManuallyClosed && onClose) {
      onClose(event);
    }
  };

  return () => {
    isManuallyClosed = true;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      socket.close();
    }
  };
}

/**
 * Starts Push-to-Talk voice capture using the canonical backend VoiceService.
 * POST /api/voice/start
 * @returns {Promise<{ status: string, message: string }>}
 */
export async function startVoiceCapture() {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/voice/start`, {
    method: 'POST',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    let errorDetail = 'Microphone access is unavailable.';
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(errorDetail);
  }

  return await response.json();
}

/**
 * Stops Push-to-Talk voice capture and receives the transcribed prompt.
 * POST /api/voice/stop
 * @returns {Promise<{ transcript: string, empty: boolean, status: string }>}
 */
export async function stopVoiceCapture() {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/voice/stop`, {
    method: 'POST',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    let errorDetail = 'Voice capture failed.';
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(errorDetail);
  }

  return await response.json();
}

/**
 * Cancels active Push-to-Talk voice capture and releases in-memory audio.
 * POST /api/voice/cancel
 * @returns {Promise<{ status: string, message: string }>}
 */
export async function cancelVoiceCapture() {
  try {
    const response = await fetch(`${GATEWAY_HTTP_BASE}/api/voice/cancel`, {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
    });
    if (response.ok) {
      return await response.json();
    }
  } catch {
    // Ignore network failure on cancel
  }
  return { status: 'idle', message: 'Cancelled' };
}

/**
 * Vocalizes user-facing answer via canonical backend VoiceService SAPI5 TTS.
 * POST /api/voice/speak
 * @param {string} text - Clean, human-readable user answer to speak.
 * @returns {Promise<{ status: string, message: string }>}
 */
export async function speakText(text) {
  if (!text || !text.trim()) {
    return { status: 'skipped', message: 'Empty text' };
  }

  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/voice/speak`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({ text: text.trim() }),
  });

  if (!response.ok) {
    let errorDetail = 'Speech synthesis failed.';
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(errorDetail);
  }

  return await response.json();
}
