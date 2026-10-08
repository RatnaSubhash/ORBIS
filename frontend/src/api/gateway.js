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
export async function submitTask(prompt, sync = false, conversationId = null) {
  if (!prompt || !prompt.trim()) {
    throw new Error('Task prompt cannot be empty.');
  }

  const payload = {
    prompt: prompt.trim(),
    sync,
    metadata: { client: 'orbis-electron-shell' },
  };
  if (conversationId) {
    payload.conversation_id = conversationId;
    payload.metadata.conversation_id = conversationId;
  }

  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/tasks`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify(payload),
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
 * Confirms or denies a pending high-risk action requiring user approval.
 * POST /api/tasks/{task_id}/approve
 * @param {string} taskId - Target task identifier.
 * @param {boolean} approved - True to approve execution, false to deny and cancel.
 * @param {string} [reason] - Optional explanation.
 * @returns {Promise<{ task_id: string, status: string, message: string }>}
 */
export async function approveTaskStep(taskId, approved, reason = '') {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/tasks/${encodeURIComponent(taskId)}/approve`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      approved: Boolean(approved),
      reason: reason || (approved ? 'Approved by user via UI' : 'Denied by user via UI'),
    }),
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || errJson.error || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(`Approval action failed: ${errorDetail}`);
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

/**
 * Retrieves real workspace continuity context, detected sessions, Context DNA,
 * and Knowledge Graph telemetry.
 * GET /api/continuity/context
 * @returns {Promise<{
 *   observation: { active: boolean, polling_interval: number, total_events: number },
 *   current_dna: object|null,
 *   recent_sessions: Array<object>,
 *   knowledge_graph: { total_nodes: number, total_edges: number, application_count: number, file_count: number, topic_count: number, top_entities: Array<object> }
 * }>}
 */
export async function getContinuityContext() {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/continuity/context`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(`Failed to fetch continuity context: ${errorDetail}`);
  }

  return await response.json();
}

/**
 * Retrieves sanitized workspace observation events for a specific recorded session.
 * GET /api/continuity/sessions/{session_id}/events
 * @param {string} sessionId
 * @returns {Promise<{ session_id: string, events: Array<object>, total: number }>}
 */
export async function getSessionEvents(sessionId) {
  if (!sessionId) throw new Error('Session ID is required.');

  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/continuity/sessions/${encodeURIComponent(sessionId)}/events`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(`Failed to fetch session events: ${errorDetail}`);
  }

  return await response.json();
}

/**
 * Retrieves real authoritative registered tools, risk levels, and categories from ORBIS ToolRegistry.
 * GET /api/tools
 * @returns {Promise<{
 *   tools: Array<{
 *     id: string,
 *     name: string,
 *     description: string,
 *     version: string,
 *     risk_level: string,
 *     category: string,
 *     status: string,
 *     parameters_schema: object,
 *     actions: Array<string>,
 *     example_prompt: string|null
 *   }>,
 *   total_tools: number,
 *   categories: Array<string>
 * }>}
 */
export async function getTools() {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/tools`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore json parse error
    }
    throw new Error(`Failed to fetch tools registry: ${errorDetail}`);
  }

  return await response.json();
}

/**
 * Resets active multi-turn conversational context on the Gateway.
 * POST /api/conversation/reset
 * @param {string|null} [conversationId=null]
 */
export async function resetConversation(conversationId = null) {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/conversation/reset`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({ conversation_id: conversationId }),
  });
  if (!response.ok) {
    throw new Error(`Failed to reset conversation: HTTP ${response.status}`);
  }
  return await response.json();
}

/**
 * Creates and registers a new isolated conversation session.
 * POST /api/conversation/new
 */
export async function startNewConversation() {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/conversation/new`, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error(`Failed to create new conversation: HTTP ${response.status}`);
  }
  return await response.json();
}

/**
 * Retrieves the current conversational state (active topic, entities, turns).
 * GET /api/conversation/context
 * @param {string|null} [conversationId=null]
 */
export async function getConversationContext(conversationId = null) {
  const url = conversationId
    ? `${GATEWAY_HTTP_BASE}/api/conversation/context?conversation_id=${encodeURIComponent(conversationId)}`
    : `${GATEWAY_HTTP_BASE}/api/conversation/context`;
  const response = await fetch(url, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Failed to fetch conversation context: HTTP ${response.status}`);
  }
  return await response.json();
}

/**
 * Retrieves candidate workspace sessions for restoration.
 * GET /api/restoration/sessions?limit={limit}
 * @param {number} [limit=10]
 * @returns {Promise<{ sessions: Array<object>, total: number }>}
 */
export async function getRestorationSessions(limit = 10) {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/restoration/sessions?limit=${encodeURIComponent(limit)}`, {
    method: 'GET',
    headers: { 'Accept': 'application/json' },
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore
    }
    throw new Error(`Failed to fetch restoration sessions: ${errorDetail}`);
  }

  return await response.json();
}

/**
 * Previews a declarative workspace restoration plan without launching processes.
 * POST /api/restoration/preview
 * @param {object} params - { sessionId, query, targetResource }
 * @returns {Promise<{ status: string, plan: object|null, message: string }>}
 */
export async function previewRestorationPlan({ sessionId = null, query = null, targetResource = null } = {}) {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/restoration/preview`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      query,
      target_resource: targetResource,
    }),
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore
    }
    throw new Error(`Failed to preview restoration plan: ${errorDetail}`);
  }

  return await response.json();
}

/**
 * Executes a declarative workspace restoration plan with failure isolation.
 * POST /api/restoration/execute
 * @param {object} params - { sessionId, query, targetResource, selectedStepIds, approved }
 * @returns {Promise<object>}
 */
export async function executeRestorationPlan({
  sessionId = null,
  query = null,
  targetResource = null,
  selectedStepIds = null,
  approved = true,
} = {}) {
  const response = await fetch(`${GATEWAY_HTTP_BASE}/api/restoration/execute`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      session_id: sessionId,
      query,
      target_resource: targetResource,
      selected_step_ids: selectedStepIds,
      approved: Boolean(approved),
    }),
  });

  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
    try {
      const errJson = await response.json();
      errorDetail = errJson.error || errJson.detail || errorDetail;
    } catch {
      // ignore
    }
    throw new Error(`Failed to execute restoration: ${errorDetail}`);
  }

  return await response.json();
}


