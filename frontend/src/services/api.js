/**
 * ORBIS Frontend API Service Layer.
 *
 * Provides a clean, typed boundary between UI components and the FastAPI Gateway.
 * Components must consume backend data exclusively through this service layer.
 */

import * as gateway from '../api/gateway';

export const agentApi = {
  // ── Gateway Health & Diagnostics ──
  checkHealth: async () => {
    return await gateway.checkGatewayHealth();
  },

  // ── Task Management & Real-time Stream ──
  submitTask: async (prompt, sync = false, conversationId = null) => {
    return await gateway.submitTask(prompt, sync, conversationId);
  },

  getTaskStatus: async (taskId) => {
    return await gateway.getTaskStatus(taskId);
  },

  getTaskEvents: async (taskId) => {
    return await gateway.getTaskEvents(taskId);
  },

  subscribeTaskEvents: (taskId, callbacks, replay = true) => {
    return gateway.connectTaskWebSocket(taskId, callbacks, replay);
  },

  // ── Safety & Approval Resolution ──
  approveStep: async (taskId, approved, reason = '') => {
    return await gateway.approveTaskStep(taskId, approved, reason);
  },

  // ── Push-to-Talk Voice Engine ──
  startVoice: async () => {
    return await gateway.startVoiceCapture();
  },

  stopVoice: async () => {
    return await gateway.stopVoiceCapture();
  },

  cancelVoice: async () => {
    return await gateway.cancelVoiceCapture();
  },

  speakText: async (text) => {
    return await gateway.speakText(text);
  },

  // ── System Control (Hardware Volume) ──
  setVolume: async (level) => {
    // Submit real task to backend volume tool
    return await gateway.submitTask(`Set system volume to ${Math.round(level)}%`, false);
  },

  setMute: async (mute) => {
    return await gateway.submitTask(mute ? 'Mute system audio' : 'Unmute system audio', false);
  },

  // ── Workspace Continuity & Semantic Memory (Phase F) ──
  getContinuityContext: async () => {
    return await gateway.getContinuityContext();
  },

  getSessionEvents: async (sessionId) => {
    return await gateway.getSessionEvents(sessionId);
  },

  restoreWorkspace: async () => {
    return await gateway.submitTask('Continue where I left off', false);
  },

  // ── Declarative Workspace Restoration (Phase 10/11) ──
  getRestorationSessions: async (limit = 10) => {
    return await gateway.getRestorationSessions(limit);
  },

  previewRestorationPlan: async (params) => {
    return await gateway.previewRestorationPlan(params);
  },

  executeRestorationPlan: async (params) => {
    return await gateway.executeRestorationPlan(params);
  },

  // ── Tools & Capability Center (Phase G) ──
  getTools: async () => {
    return await gateway.getTools();
  },

  // ── Conversational Continuity & Sessions ──
  resetConversation: async (conversationId = null) => {
    return await gateway.resetConversation(conversationId);
  },

  startNewConversation: async () => {
    return await gateway.startNewConversation();
  },

  getConversationContext: async (conversationId = null) => {
    return await gateway.getConversationContext(conversationId);
  },
};

export default agentApi;

