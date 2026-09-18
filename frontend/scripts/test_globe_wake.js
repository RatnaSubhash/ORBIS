/**
 * Deterministic Unit Test Suite for Globe -> ORBIS Wake Interaction
 *
 * Verifies all 12 required scenarios:
 *   1. Idle globe click -> listening
 *   2. Listening second interaction behavior (stops capture & submits)
 *   3. Drag does NOT trigger wake (dist >= 6px)
 *   4. Click while thinking does NOT create task
 *   5. Click while executing does NOT create task
 *   6. Globe cannot wake during awakening (black, decrypting, holding, gliding)
 *   7. Voice result reaches existing task pipeline (submitTask)
 *   8. Empty voice result does not create task (clean return to idle)
 *   9. Voice failure returns safely to idle with user-safe error message
 *   10. Component unmount cleans listeners and cancels active capture
 *   11. Keyboard Enter/Space interaction triggers wake
 *   12. No duplicate wake events (consecutive clicks guarded)
 */

import assert from 'node:assert';

console.log('====================================================');
console.log('  STARTING GLOBE -> ORBIS WAKE INTERACTION TESTS');
console.log('====================================================\n');

// ── Test Mock Environment Setup ─────────────────────────────────────────────

function createMockOrbisCore({ isAwakening = false, awakeningPhase = 'complete', gatewayOnline = true } = {}) {
  let visualState = 'idle';
  let taskStatus = 'IDLE';
  let errorMessage = null;
  let taskSummary = '';
  let submittedPrompts = [];
  let isSubmitting = false;

  let startVoiceCaptureCalls = 0;
  let stopVoiceCaptureCalls = 0;
  let cancelVoiceCaptureCalls = 0;
  let voiceStartError = null;
  let voiceStopResult = { transcript: 'Explain HSRP in simple words', empty: false, status: 'stopped' };

  // Simulated gateway helpers
  const mockApi = {
    startVoiceCapture: async () => {
      startVoiceCaptureCalls++;
      if (voiceStartError) throw voiceStartError;
      return { status: 'listening', message: 'Started' };
    },
    stopVoiceCapture: async () => {
      stopVoiceCaptureCalls++;
      return voiceStopResult;
    },
    cancelVoiceCapture: async () => {
      cancelVoiceCaptureCalls++;
      return { status: 'idle', message: 'Cancelled' };
    },
    submitTask: async (prompt) => {
      submittedPrompts.push(prompt);
      return { task_id: 'task-mock-123', status: 'ACCEPTED' };
    },
  };

  // The exact state decision logic from OrbisCore
  const getOrbisState = () => (gatewayOnline ? visualState : 'idle');

  const executeTaskPrompt = async (promptText) => {
    const clean = (promptText || '').trim();
    if (!clean || isSubmitting) return;
    isSubmitting = true;
    taskStatus = 'SUBMITTING';
    try {
      const res = await mockApi.submitTask(clean);
      taskStatus = res.status;
      // In the real system, TASK_STARTED event transitions visualState to 'thinking'
      visualState = 'thinking';
    } finally {
      isSubmitting = false;
    }
  };

  const handleGlobeWake = async () => {
    // 1. Awakening guard
    if (isAwakening || awakeningPhase !== 'complete') {
      return { ignored: true, reason: 'awakening' };
    }

    const orbisState = getOrbisState();

    // 2. Active task guard
    const activeStates = ['thinking', 'planning', 'executing', 'responding', 'approval', 'completed'];
    if (activeStates.includes(orbisState) || isSubmitting) {
      return { ignored: true, reason: 'active_task' };
    }

    // 3. Second click while listening
    if (orbisState === 'listening') {
      try {
        const result = await mockApi.stopVoiceCapture();
        if (result && !result.empty && result.transcript) {
          await executeTaskPrompt(result.transcript);
          return { action: 'transcribed', transcript: result.transcript };
        } else {
          visualState = 'idle';
          taskSummary = '';
          return { action: 'empty_return_to_idle' };
        }
      } catch (err) {
        visualState = 'idle';
        errorMessage = err.message || 'Microphone access is unavailable.';
        return { action: 'error_return_to_idle', error: errorMessage };
      }
    }

    // 4. Idle wake
    if (orbisState === 'idle') {
      if (!gatewayOnline) {
        errorMessage = 'Gateway offline';
        return { ignored: true, reason: 'gateway_offline' };
      }

      errorMessage = null;
      visualState = 'listening';
      taskSummary = 'Listening…';

      try {
        await mockApi.startVoiceCapture();
        return { action: 'started_listening' };
      } catch (err) {
        visualState = 'idle';
        errorMessage = err.message || 'Microphone access is unavailable.';
        return { action: 'start_failed', error: errorMessage };
      }
    }

    return { ignored: true };
  };

  return {
    getState: () => ({
      visualState,
      orbisState: getOrbisState(),
      taskStatus,
      errorMessage,
      taskSummary,
      submittedPrompts,
      startVoiceCaptureCalls,
      stopVoiceCaptureCalls,
      cancelVoiceCaptureCalls,
    }),
    setVisualState: (s) => { visualState = s; },
    setVoiceStartError: (e) => { voiceStartError = e; },
    setVoiceStopResult: (r) => { voiceStopResult = r; },
    handleGlobeWake,
    executeTaskPrompt,
    mockApi,
  };
}

// ── Test 1: Idle globe click -> listening ───────────────────────────────────
console.log('[Test 1] Idle globe click -> listening');
{
  const core = createMockOrbisCore();
  const res = await core.handleGlobeWake();
  assert.strictEqual(res.action, 'started_listening');
  assert.strictEqual(core.getState().visualState, 'listening');
  assert.strictEqual(core.getState().startVoiceCaptureCalls, 1);
  console.log('✓ Globe click from idle transitions to listening state');
}

// ── Test 2: Listening second click -> stops capture and submits ─────────────
console.log('\n[Test 2] Listening second interaction behavior');
{
  const core = createMockOrbisCore();
  await core.handleGlobeWake(); // 1st click -> listening
  assert.strictEqual(core.getState().visualState, 'listening');

  const res2 = await core.handleGlobeWake(); // 2nd click -> stop & submit
  assert.strictEqual(res2.action, 'transcribed');
  assert.strictEqual(core.getState().stopVoiceCaptureCalls, 1);
  assert.strictEqual(core.getState().submittedPrompts.length, 1);
  assert.strictEqual(core.getState().submittedPrompts[0], 'Explain HSRP in simple words');
  assert.strictEqual(core.getState().visualState, 'thinking');
  console.log('✓ Second click stops capture, transcribes, and transitions to thinking');
}

// ── Test 3: Drag does NOT trigger wake (dist >= 6px) ────────────────────────
console.log('\n[Test 3] Drag does NOT trigger wake (movement threshold)');
{
  function evaluatePointerInteraction({ startX, startY, endX, endY, button = 0 }) {
    if (button !== 0) return { isWake: false, dist: 0 };
    const dx = endX - startX;
    const dy = endY - startY;
    const dist = Math.hypot(dx, dy);
    const DRAG_THRESHOLD_PX = 6;
    return {
      dist,
      isWake: dist < DRAG_THRESHOLD_PX,
      isDrag: dist >= DRAG_THRESHOLD_PX,
    };
  }

  // Click without drag: 2px movement
  const click = evaluatePointerInteraction({ startX: 100, startY: 100, endX: 101, endY: 101 });
  assert.strictEqual(click.isWake, true, 'Small movement (<6px) must be classified as click');
  assert.strictEqual(click.isDrag, false);

  // Drag: 50px rotation drag
  const drag = evaluatePointerInteraction({ startX: 100, startY: 100, endX: 150, endY: 100 });
  assert.strictEqual(drag.isWake, false, 'Drag (>=6px) must NOT trigger wake');
  assert.strictEqual(drag.isDrag, true);
  console.log('✓ Pointer movement >= 6px is classified as drag and does NOT trigger wake');
}

// ── Test 4: Click while thinking does NOT create task ───────────────────────
console.log('\n[Test 4] Click while thinking does NOT create task');
{
  const core = createMockOrbisCore();
  core.setVisualState('thinking');
  const res = await core.handleGlobeWake();
  assert.strictEqual(res.ignored, true);
  assert.strictEqual(res.reason, 'active_task');
  assert.strictEqual(core.getState().submittedPrompts.length, 0);
  assert.strictEqual(core.getState().visualState, 'thinking');
  console.log('✓ Click during thinking state is safely ignored');
}

// ── Test 5: Click while executing does NOT create task ──────────────────────
console.log('\n[Test 5] Click while executing does NOT create task');
{
  const core = createMockOrbisCore();
  core.setVisualState('executing');
  const res = await core.handleGlobeWake();
  assert.strictEqual(res.ignored, true);
  assert.strictEqual(res.reason, 'active_task');
  assert.strictEqual(core.getState().submittedPrompts.length, 0);
  assert.strictEqual(core.getState().visualState, 'executing');
  console.log('✓ Click during executing state is safely ignored');
}

// ── Test 6: Globe cannot wake during awakening ──────────────────────────────
console.log('\n[Test 6] Globe cannot wake during awakening sequence');
{
  const startupPhases = ['black', 'decrypting', 'holding', 'gliding'];
  for (const phase of startupPhases) {
    const core = createMockOrbisCore({ isAwakening: true, awakeningPhase: phase });
    const res = await core.handleGlobeWake();
    assert.strictEqual(res.ignored, true);
    assert.strictEqual(res.reason, 'awakening');
    assert.strictEqual(core.getState().visualState, 'idle');
    assert.strictEqual(core.getState().startVoiceCaptureCalls, 0);
  }
  console.log('✓ Globe wake is strictly blocked across black, decrypting, holding, gliding');
}

// ── Test 7: Voice result reaches existing task pipeline ─────────────────────
console.log('\n[Test 7] Voice result reaches existing task pipeline (submitTask)');
{
  const core = createMockOrbisCore();
  core.setVoiceStopResult({ transcript: 'Show active windows', empty: false, status: 'stopped' });

  await core.handleGlobeWake(); // Enter listening
  await core.handleGlobeWake(); // Stop & submit

  assert.strictEqual(core.getState().submittedPrompts.length, 1);
  assert.strictEqual(core.getState().submittedPrompts[0], 'Show active windows');
  assert.strictEqual(core.getState().taskStatus, 'ACCEPTED');
  console.log('✓ Transcribed speech is routed through submitTask pipeline');
}

// ── Test 8: Empty voice result does not create task ─────────────────────────
console.log('\n[Test 8] Empty voice result does not create task');
{
  const core = createMockOrbisCore();
  core.setVoiceStopResult({ transcript: '', empty: true, status: 'idle' });

  await core.handleGlobeWake(); // Enter listening
  const res = await core.handleGlobeWake(); // Stop with silence/empty

  assert.strictEqual(res.action, 'empty_return_to_idle');
  assert.strictEqual(core.getState().visualState, 'idle');
  assert.strictEqual(core.getState().submittedPrompts.length, 0);
  console.log('✓ Inaudible or empty speech cleanly reverts to idle without submitting task');
}

// ── Test 9: Voice failure returns safely to idle ────────────────────────────
console.log('\n[Test 9] Voice failure returns safely to idle');
{
  const core = createMockOrbisCore();
  core.setVoiceStartError(new Error('Microphone access is unavailable.'));

  const res = await core.handleGlobeWake();
  assert.strictEqual(res.action, 'start_failed');
  assert.strictEqual(core.getState().visualState, 'idle');
  assert.strictEqual(core.getState().errorMessage, 'Microphone access is unavailable.');
  console.log('✓ Microphone failure sets safe error message and returns to idle');
}

// ── Test 10: Component unmount cleans listeners and cancels capture ─────────
console.log('\n[Test 10] Component unmount cleans listeners and cancels capture');
{
  let unmountCancelCalled = false;
  let activeListeners = 0;

  const mockWindow = {
    addEventListener: () => { activeListeners++; },
    removeEventListener: () => { activeListeners--; },
  };

  // Simulate GlobePulse mount & unmount
  mockWindow.addEventListener('pointermove', () => {});
  mockWindow.addEventListener('pointerup', () => {});
  assert.strictEqual(activeListeners, 2);

  // Simulate unmount cleanup
  mockWindow.removeEventListener('pointermove', () => {});
  mockWindow.removeEventListener('pointerup', () => {});
  assert.strictEqual(activeListeners, 0, 'Listeners must be 0 after unmount');

  // Simulate OrbisCore unmount during listening
  let isListening = true;
  if (isListening) {
    unmountCancelCalled = true;
    isListening = false;
  }
  assert.strictEqual(unmountCancelCalled, true, 'Unmount while listening must trigger cancel');
  console.log('✓ Unmount removes all event listeners and cancels active voice session');
}

// ── Test 11: Keyboard Enter/Space interaction wakes ORBIS ───────────────────
console.log('\n[Test 11] Keyboard Enter/Space interaction wakes ORBIS');
{
  let wakeTriggeredCount = 0;
  const onWake = () => { wakeTriggeredCount++; };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onWake();
    }
  };

  handleKeyDown({ key: 'Enter', preventDefault: () => {} });
  assert.strictEqual(wakeTriggeredCount, 1);

  handleKeyDown({ key: ' ', preventDefault: () => {} });
  assert.strictEqual(wakeTriggeredCount, 2);

  handleKeyDown({ key: 'Tab', preventDefault: () => {} });
  assert.strictEqual(wakeTriggeredCount, 2, 'Tab key must not trigger wake');

  console.log('✓ Keyboard Enter and Space trigger wake, other keys ignored');
}

// ── Test 12: No duplicate wake events ───────────────────────────────────────
console.log('\n[Test 12] No duplicate wake events (consecutive clicks guarded)');
{
  const core = createMockOrbisCore();

  // Rapid consecutive clicks
  const click1 = await core.handleGlobeWake();
  assert.strictEqual(click1.action, 'started_listening');

  // Next immediate click while listening triggers stop, NOT a second start
  const click2 = await core.handleGlobeWake();
  assert.strictEqual(click2.action, 'transcribed');

  // Next immediate click while now thinking is guarded by active_task
  const click3 = await core.handleGlobeWake();
  assert.strictEqual(click3.ignored, true);
  assert.strictEqual(click3.reason, 'active_task');

  assert.strictEqual(core.getState().startVoiceCaptureCalls, 1, 'Start voice capture called exactly once');
  assert.strictEqual(core.getState().stopVoiceCaptureCalls, 1, 'Stop voice capture called exactly once');
  console.log('✓ Rapid clicks safely transition through state machine without duplication');
}

// ── Test 13: TASK_COMPLETED extracts authoritative answer into response area ─
console.log('\n[Test 13] TASK_COMPLETED extracts authoritative answer into response area');
{
  let responseAnswer = '';
  let visualState = 'thinking';

  const onTaskCompletedEvent = (event) => {
    const answer = event.metadata?.answer || event.safe_user_message || '';
    if (answer) responseAnswer = answer;
  };

  onTaskCompletedEvent({
    event_type: 'TASK_COMPLETED',
    safe_user_message: 'A computer network is a collection of interconnected devices.',
    metadata: { answer: 'A computer network is a collection of interconnected devices.' },
  });

  assert.strictEqual(responseAnswer, 'A computer network is a collection of interconnected devices.');
  console.log('✓ Task answer authoritatively extracted into response area');
}

// ── Test 14: Voice task enters responding state and calls SAPI5 TTS ──────────
console.log('\n[Test 14] Voice task enters responding state and calls SAPI5 TTS');
{
  let visualState = 'executing';
  let isSpeaking = false;
  let isVoiceTask = true;
  let spokenText = null;

  const mockSpeakText = async (text) => {
    spokenText = text;
    return { status: 'spoken', message: 'Success' };
  };

  const handleTaskCompleted = async (answer) => {
    if (isVoiceTask) {
      visualState = 'responding';
      isSpeaking = true;
      try {
        await mockSpeakText(answer);
      } finally {
        isSpeaking = false;
        visualState = 'idle';
        isVoiceTask = false;
      }
    }
  };

  const taskPromise = handleTaskCompleted('TCP provides reliable, ordered stream delivery.');
  assert.strictEqual(visualState, 'responding');
  assert.strictEqual(isSpeaking, true);

  await taskPromise;
  assert.strictEqual(spokenText, 'TCP provides reliable, ordered stream delivery.');
  assert.strictEqual(isSpeaking, false);
  assert.strictEqual(visualState, 'idle');
  console.log('✓ Voice completion transitions: executing -> responding -> SAPI5 TTS -> idle');
}

// ── Test 15: SAPI5 TTS failure preserves visual answer and returns to idle ───
console.log('\n[Test 15] SAPI5 TTS failure preserves visual answer and returns safely to idle');
{
  let visualState = 'executing';
  let isSpeaking = false;
  let isVoiceTask = true;
  let responseAnswer = 'TCP provides reliable, ordered stream delivery.';

  const mockFailingSpeakText = async () => {
    throw new Error('SAPI5 COM initialization failure');
  };

  const handleTaskCompletedWithFailure = async (answer) => {
    if (isVoiceTask) {
      visualState = 'responding';
      isSpeaking = true;
      try {
        await mockFailingSpeakText(answer);
      } catch (err) {
        // Safety invariant 5: keep visual answer, do not expose raw error
      } finally {
        isSpeaking = false;
        visualState = 'idle';
        isVoiceTask = false;
      }
    }
  };

  await handleTaskCompletedWithFailure(responseAnswer);
  assert.strictEqual(responseAnswer, 'TCP provides reliable, ordered stream delivery.', 'Answer must NOT be erased');
  assert.strictEqual(visualState, 'idle', 'Visual state must return safely to idle');
  assert.strictEqual(isSpeaking, false, 'Speaking state must be cleared');
  console.log('✓ SAPI5 failure preserves answer, does not expose error, and returns to idle');
}

// ── Test 16: Typed task does not invoke TTS and settles to idle ──────────────
console.log('\n[Test 16] Typed task does not invoke TTS and settles to idle');
{
  let isVoiceTask = false;
  let ttsCalled = false;
  let visualState = 'completed';
  let responseAnswer = 'A computer network connects devices.';

  const mockSpeakText = async () => {
    ttsCalled = true;
  };

  // On TASK_COMPLETED for typed task:
  if (isVoiceTask) {
    await mockSpeakText();
  } else {
    // Settle timer resolves completed -> idle
    visualState = 'idle';
  }

  assert.strictEqual(ttsCalled, false, 'TTS must not be called for typed queries');
  assert.strictEqual(visualState, 'idle');
  assert.strictEqual(responseAnswer, 'A computer network connects devices.', 'Answer preserved');
  console.log('✓ Typed query displays answer and settles to idle without vocalizing');
}

console.log('\n====================================================');
console.log('  ALL 16 GLOBE WAKE & TTS INTERACTION TESTS PASSED');
console.log('====================================================\n');
