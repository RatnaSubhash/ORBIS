/**
 * ORBIS Central Agent State Provider.
 *
 * Exposes a cohesive React context that manages:
 * - Active view navigation (Home, Command, Live Flow, Workspace, Tools, Restoration, System, Research, Settings)
 * - Real-time task submission, event streams, and status updates
 * - Visual state resolution for the central ORBIS Globe
 * - High-risk action approval gates
 * - Push-to-Talk voice lifecycle
 * - Hardware system control (volume & mute)
 */

import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react';
import { ViewType, AgentVisualState, STATUS_LABELS } from '../types';
import agentApi from '../services/api';
import { resolveVisualState, TaskSettleTimer } from './visualState';

const AgentContext = createContext(null);

export function AgentProvider({ children }) {
  // Navigation & View State
  const [activeView, setActiveView] = useState(ViewType.HOME);

  // Gateway Connection State
  const [gatewayOnline, setGatewayOnline] = useState(false);

  // Operational Task State
  const [currentTaskId, setCurrentTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState('IDLE');
  const [taskSummary, setTaskSummary] = useState('');
  const [responseAnswer, setResponseAnswer] = useState('');
  const [events, setEvents] = useState([]);
  const [pendingApproval, setPendingApproval] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  // Multi-Turn Conversational Continuity State
  const [conversationId, setConversationId] = useState(() => `conv_${Date.now()}`);
  const [messages, setMessages] = useState([]);

  // Visual state for the ORBIS Globe (idle / listening / thinking / planning / executing / responding / completed / failed / approval)
  const [visualState, setVisualState] = useState(AgentVisualState.IDLE);

  // Voice Interaction State
  const [isSpeaking, setIsSpeaking] = useState(false);
  const isVoiceTaskRef = useRef(false);

  // System Control State
  const [volume, setVolumeState] = useState(75);
  const [isMuted, setIsMuted] = useState(false);

  // Workspace Continuity State (Phase F)
  const [continuityContext, setContinuityContext] = useState(null);
  const [isLoadingContinuity, setIsLoadingContinuity] = useState(false);

  const refreshContinuity = useCallback(async () => {
    if (!gatewayOnline) return;
    setIsLoadingContinuity(true);
    try {
      const data = await agentApi.getContinuityContext();
      setContinuityContext(data);
    } catch (err) {
      console.warn('Failed to load continuity context:', err.message);
    } finally {
      setIsLoadingContinuity(false);
    }
  }, [gatewayOnline]);

  // Tools & Capability Center State (Phase G)
  const [toolsData, setToolsData] = useState({ tools: [], total_tools: 0, categories: [] });
  const [isLoadingTools, setIsLoadingTools] = useState(false);

  const refreshTools = useCallback(async () => {
    if (!gatewayOnline) return;
    setIsLoadingTools(true);
    try {
      const data = await agentApi.getTools();
      if (data) {
        setToolsData(data);
      }
    } catch (err) {
      console.warn('Failed to load tool registry:', err.message);
    } finally {
      setIsLoadingTools(false);
    }
  }, [gatewayOnline]);

  // Automatically & continuously refresh continuity context when in WORKSPACE view
  useEffect(() => {
    if (activeView === ViewType.WORKSPACE && gatewayOnline) {
      refreshContinuity();
      const interval = setInterval(() => {
        refreshContinuity();
      }, 3000);
      return () => clearInterval(interval);
    }
  }, [activeView, gatewayOnline, refreshContinuity]);

  // Automatically refresh tools when entering TOOLS view
  useEffect(() => {
    if (activeView === ViewType.TOOLS && gatewayOnline) {
      refreshTools();
    }
  }, [activeView, gatewayOnline, refreshTools]);

  // Refs for deduplication, settle timers, and WebSocket cleanup
  const seenEventIdsRef = useRef(new Set());
  const wsCleanupRef = useRef(null);
  const currentTaskIdRef = useRef(null);
  const visualStateRef = useRef(AgentVisualState.IDLE);
  const settleTimerRef = useRef(null);

  visualStateRef.current = visualState;
  const orbisState = gatewayOnline ? visualState : AgentVisualState.IDLE;

  // ── Gateway health polling ──────────────────────────────────────────────────
  useEffect(() => {
    let isMounted = true;
    async function verifyHealth() {
      try {
        await agentApi.checkHealth();
        if (isMounted) setGatewayOnline(true);
      } catch {
        if (isMounted) setGatewayOnline(false);
      }
    }
    verifyHealth();
    const interval = setInterval(verifyHealth, 5000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  // ── Task Settle Timer ───────────────────────────────────────────────────────
  useEffect(() => {
    settleTimerRef.current = new TaskSettleTimer({
      onSettle: (settledTaskId) => {
        if (currentTaskIdRef.current === settledTaskId || !currentTaskIdRef.current) {
          setVisualState(AgentVisualState.IDLE);
        }
      },
      delay: 1500,
    });

    return () => {
      settleTimerRef.current?.cancel();
    };
  }, []);

  // ── Unmount / Cleanup ──────────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (wsCleanupRef.current) {
        wsCleanupRef.current();
        wsCleanupRef.current = null;
      }
      agentApi.cancelVoice().catch(() => {});
    };
  }, []);

  // ── Central Task Execution Pipeline ─────────────────────────────────────────
  const executeTaskPrompt = useCallback(
    async (promptText, isVoice = false) => {
      const cleanPrompt = (promptText || '').trim();
      if (!cleanPrompt || isSubmitting) return;

      const userMsgId = `msg_u_${Date.now()}`;
      const agentMsgId = `msg_a_${Date.now()}`;

      // Append user prompt and initial agent message to continuous conversation stream
      setMessages((prev) => [
        ...prev,
        {
          id: userMsgId,
          role: 'user',
          content: cleanPrompt,
          timestamp: new Date().toISOString(),
        },
        {
          id: agentMsgId,
          role: 'agent',
          content: '',
          summary: 'Formulating action plan…',
          status: 'RUNNING',
          timestamp: new Date().toISOString(),
          taskId: null,
          pendingApproval: null,
        },
      ]);

      isVoiceTaskRef.current = isVoice;
      setErrorMessage(null);
      setEvents([]);
      seenEventIdsRef.current.clear();
      setPendingApproval(null);
      setTaskSummary('Formulating action plan…');
      setResponseAnswer('');
      setIsSubmitting(true);
      setTaskStatus('SUBMITTING');

      settleTimerRef.current?.cancel();

      if (wsCleanupRef.current) {
        wsCleanupRef.current();
        wsCleanupRef.current = null;
      }

      try {
        const submitRes = await agentApi.submitTask(cleanPrompt, false, conversationId);
        const taskId = submitRes.task_id;
        currentTaskIdRef.current = taskId;
        setCurrentTaskId(taskId);
        setTaskStatus(submitRes.status || 'ACCEPTED');

        setMessages((prev) =>
          prev.map((m) => (m.id === agentMsgId ? { ...m, taskId } : m))
        );

        const cleanup = agentApi.subscribeTaskEvents(taskId, {
          onOpen: () => setErrorMessage(null),
          onEvent: (event) => {
            if (!event?.event_id) return;
            if (seenEventIdsRef.current.has(event.event_id)) return;
            seenEventIdsRef.current.add(event.event_id);

            setEvents((prev) => [...prev, event]);
            if (event.status) setTaskStatus(event.status);
            if (event.safe_user_message) {
              setTaskSummary(event.safe_user_message);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === agentMsgId && m.status === 'RUNNING'
                    ? { ...m, summary: event.safe_user_message }
                    : m
                )
              );
            }

            if (event.event_type === 'APPROVAL_REQUIRED') {
              const appInfo = {
                taskId,
                message: event.safe_user_message,
                tool: event.tool_name,
                riskLevel: event.risk_level || 'HIGH',
              };
              setPendingApproval(appInfo);
              setVisualState(AgentVisualState.APPROVAL_REQUIRED);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === agentMsgId
                    ? { ...m, status: 'APPROVAL', pendingApproval: appInfo, summary: event.safe_user_message }
                    : m
                )
              );
            } else if (
              event.event_type === 'APPROVAL_GRANTED' ||
              event.event_type === 'APPROVAL_DENIED'
            ) {
              setPendingApproval(null);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === agentMsgId
                    ? { ...m, status: 'RUNNING', pendingApproval: null }
                    : m
                )
              );
            }

            // User-facing answer on TASK_COMPLETED
            if (event.event_type === 'TASK_COMPLETED') {
              const answer = event.metadata?.answer || event.safe_user_message || '';
              if (answer) {
                setResponseAnswer(answer);
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === agentMsgId
                      ? { ...m, content: answer, status: 'COMPLETED', summary: '' }
                      : m
                  )
                );
              }

              if (isVoiceTaskRef.current) {
                settleTimerRef.current?.cancel();
                setVisualState(AgentVisualState.RESPONDING);
                setIsSpeaking(true);

                agentApi
                  .speakText(answer)
                  .catch((err) => console.warn('TTS vocalization error:', err))
                  .finally(() => {
                    setIsSpeaking(false);
                    setVisualState(AgentVisualState.IDLE);
                    isVoiceTaskRef.current = false;
                  });
                return;
              }
            }

            // Event-aware visual state resolution
            const resolution = resolveVisualState(
              visualStateRef.current,
              event,
              currentTaskIdRef.current
            );

            if (resolution.updated) {
              if (resolution.isNewTask || (resolution.taskId && resolution.taskId !== currentTaskIdRef.current)) {
                settleTimerRef.current?.cancel();
                currentTaskIdRef.current = resolution.taskId;
                setCurrentTaskId(resolution.taskId);
              }
              setVisualState(resolution.state);

              if (resolution.needsSettle && resolution.taskId) {
                settleTimerRef.current?.schedule(resolution.taskId);
              }
            }
          },
          onError: () => {
            setErrorMessage('Event stream error. Re-querying task status…');
            agentApi
              .getTaskStatus(taskId)
              .then((st) => {
                if (st.status) setTaskStatus(st.status);
                if (st.status === 'FAILED') {
                  setVisualState(AgentVisualState.FAILED);
                  settleTimerRef.current?.schedule(taskId);
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === agentMsgId
                        ? { ...m, content: st.error || st.summary || 'Task failed.', status: 'FAILED' }
                        : m
                    )
                  );
                }
              })
              .catch(() => {});
          },
          onClose: () => {
            agentApi
              .getTaskStatus(taskId)
              .then((st) => {
                if (st.status) setTaskStatus(st.status);
                if (st.summary) setTaskSummary(st.summary);
                const answer = st.answer || st.summary;
                if (st.status === 'COMPLETED') {
                  if (answer) {
                    setResponseAnswer(answer);
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id === agentMsgId
                          ? { ...m, content: answer, status: 'COMPLETED', summary: '' }
                          : m
                      )
                    );
                  }
                  if (isVoiceTaskRef.current) {
                    settleTimerRef.current?.cancel();
                    setVisualState(AgentVisualState.RESPONDING);
                    setIsSpeaking(true);
                    agentApi
                      .speakText(answer)
                      .catch(() => {})
                      .finally(() => {
                        setIsSpeaking(false);
                        setVisualState(AgentVisualState.IDLE);
                        isVoiceTaskRef.current = false;
                      });
                  } else if (visualStateRef.current !== AgentVisualState.COMPLETED) {
                    setVisualState(AgentVisualState.COMPLETED);
                    settleTimerRef.current?.schedule(taskId);
                  }
                } else if (st.status === 'FAILED') {
                  if (visualStateRef.current !== AgentVisualState.FAILED) {
                    setVisualState(AgentVisualState.FAILED);
                    settleTimerRef.current?.schedule(taskId);
                  }
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === agentMsgId
                        ? { ...m, content: st.error || st.summary || 'Task execution failed.', status: 'FAILED' }
                        : m
                    )
                  );
                }
              })
              .catch(() => {});
          },
        });

        wsCleanupRef.current = cleanup;
      } catch (err) {
        setErrorMessage(err.message);
        setTaskStatus('FAILED');
        setVisualState(AgentVisualState.FAILED);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === agentMsgId
              ? { ...m, content: err.message, status: 'FAILED' }
              : m
          )
        );
        if (currentTaskIdRef.current) {
          settleTimerRef.current?.schedule(currentTaskIdRef.current);
        }
      } finally {
        setIsSubmitting(false);
      }
    },
    [isSubmitting, conversationId]
  );

  // ── Clear Active Conversation UI State (Phase H) ───────────────────────────
  const clearConversationUi = useCallback(() => {
    setMessages([]);
    setEvents([]);
    seenEventIdsRef.current.clear();
    setPendingApproval(null);
    setTaskSummary('');
    setResponseAnswer('');
    setCurrentTaskId(null);
    setTaskStatus('IDLE');
    setVisualState(AgentVisualState.IDLE);
  }, []);

  // ── Start New Chat Session Workflow ─────────────────────────────────────────
  const startNewChat = useCallback(async () => {
    const newConvId = `conv_${Date.now()}`;
    setConversationId(newConvId);
    setMessages([]);
    setEvents([]);
    seenEventIdsRef.current.clear();
    setPendingApproval(null);
    setTaskSummary('');
    setResponseAnswer('');
    setCurrentTaskId(null);
    setTaskStatus('IDLE');
    setErrorMessage(null);
    setVisualState(AgentVisualState.IDLE);

    try {
      await agentApi.resetConversation(newConvId);
    } catch (err) {
      console.warn('Failed to reset conversation on gateway:', err.message);
    }
  }, []);

  // ── Approval Gate Resolution ────────────────────────────────────────────────
  const resolveApproval = useCallback(async (approved, reason = '') => {
    if (!pendingApproval?.taskId) return;
    const targetTaskId = pendingApproval.taskId;

    try {
      await agentApi.approveStep(targetTaskId, approved, reason);
      setPendingApproval(null);
      setMessages((prev) =>
        prev.map((m) =>
          m.pendingApproval && m.pendingApproval.taskId === targetTaskId
            ? { ...m, status: 'RUNNING', pendingApproval: null }
            : m
        )
      );
      if (!approved) {
        setVisualState(AgentVisualState.IDLE);
      }
    } catch (err) {
      setErrorMessage(`Failed to resolve approval: ${err.message}`);
    }
  }, [pendingApproval]);

  // ── Push-to-Talk Voice Wake Interaction ─────────────────────────────────────
  const handleGlobeWake = useCallback(async () => {
    const activeStates = [
      AgentVisualState.THINKING,
      AgentVisualState.PLANNING,
      AgentVisualState.EXECUTING,
      AgentVisualState.RESPONDING,
      AgentVisualState.APPROVAL_REQUIRED,
      AgentVisualState.COMPLETED,
    ];

    if (activeStates.includes(orbisState) || isSubmitting) return;

    if (orbisState === AgentVisualState.LISTENING) {
      try {
        const result = await agentApi.stopVoice();
        if (result && !result.empty && result.transcript) {
          executeTaskPrompt(result.transcript, true);
        } else {
          setVisualState(AgentVisualState.IDLE);
          setTaskSummary('');
        }
      } catch (err) {
        setVisualState(AgentVisualState.IDLE);
        setErrorMessage(err.message || 'Microphone access is unavailable.');
      }
      return;
    }

    if (orbisState === AgentVisualState.IDLE) {
      if (!gatewayOnline) {
        setErrorMessage('ORBIS Gateway offline. Please ensure the local service is running.');
        return;
      }

      setErrorMessage(null);
      setVisualState(AgentVisualState.LISTENING);
      setTaskSummary('');

      try {
        await agentApi.startVoice();
      } catch (err) {
        setVisualState(AgentVisualState.IDLE);
        setErrorMessage(err.message || 'Microphone access is unavailable.');
      }
    }
  }, [orbisState, isSubmitting, gatewayOnline, executeTaskPrompt]);

  // ── System Control Actions ──────────────────────────────────────────────────
  const updateVolume = useCallback((newVol) => {
    const val = Math.max(0, Math.min(100, Math.round(newVol)));
    setVolumeState(val);
    if (gatewayOnline) {
      agentApi.setVolume(val).catch(() => {});
    }
  }, [gatewayOnline]);

  const toggleMute = useCallback(() => {
    setIsMuted((prev) => {
      const next = !prev;
      if (gatewayOnline) {
        agentApi.setMute(next).catch(() => {});
      }
      return next;
    });
  }, [gatewayOnline]);

  const contextValue = {
    // Navigation
    activeView,
    setActiveView,

    // Gateway & Task
    gatewayOnline,
    currentTaskId,
    taskStatus,
    taskSummary,
    responseAnswer,
    events,
    pendingApproval,
    isSubmitting,
    errorMessage,
    setErrorMessage,

    // Visual State & Labels
    visualState: orbisState,
    statusLabel: STATUS_LABELS[orbisState] || STATUS_LABELS.idle,

    // Conversational Continuity & Multi-Turn Thread
    conversationId,
    messages,
    startNewChat,

    // Pipeline Callbacks
    executeTaskPrompt,
    clearConversationUi,
    resolveApproval,
    handleGlobeWake,

    // Voice & TTS
    isSpeaking,

    // System Control
    volume,
    isMuted,
    updateVolume,
    toggleMute,

    // Workspace Continuity (Phase F)
    continuityContext,
    isLoadingContinuity,
    refreshContinuity,

    // Tools & Capability Center (Phase G)
    toolsData,
    tools: toolsData.tools || [],
    totalTools: toolsData.total_tools || 0,
    categories: toolsData.categories || [],
    isLoadingTools,
    refreshTools,
  };

  return <AgentContext.Provider value={contextValue}>{children}</AgentContext.Provider>;
}

export function useAgent() {
  const context = useContext(AgentContext);
  if (!context) {
    throw new Error('useAgent must be used within an AgentProvider');
  }
  return context;
}

export default AgentContext;
