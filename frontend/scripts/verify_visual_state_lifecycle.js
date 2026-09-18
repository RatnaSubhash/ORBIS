/**
 * Live E2E Integration Verification of the ORBIS Visual State Lifecycle.
 *
 * Connects to the running FastAPI Gateway, submits a task, streams real-time
 * AgentEvents over WebSocket, feeds each event into resolveVisualState,
 * and asserts that the visual state transitions through:
 *   idle -> thinking -> planning -> executing -> responding -> completed -> idle (settled)
 */

import assert from 'node:assert';
import {
  checkGatewayHealth,
  submitTask,
  connectTaskWebSocket,
} from '../src/api/gateway.js';
import {
  resolveVisualState,
  TaskSettleTimer,
} from '../src/state/visualState.js';

async function verifyLiveVisualLifecycle() {
  console.log('============================================================');
  console.log('  ORBIS LIVE VISUAL STATE LIFECYCLE E2E VERIFICATION');
  console.log('============================================================\n');

  // 1. Health check
  console.log('[1] Checking Gateway Health...');
  const health = await checkGatewayHealth();
  if (health.status !== 'ok') {
    throw new Error(`Gateway not healthy: ${JSON.stringify(health)}`);
  }
  console.log('    PASS: Gateway is online.\n');

  // 2. Track visual state transitions
  let currentVisualState = 'idle';
  const observedStates = ['idle'];
  let activeTaskId = null;
  let settledToIdle = false;

  const settleManager = new TaskSettleTimer({
    onSettle: (settledTaskId) => {
      if (activeTaskId === settledTaskId) {
        currentVisualState = 'idle';
        observedStates.push('idle');
        settledToIdle = true;
        console.log('    >> Settle timer fired: visual state returned to "idle"');
      }
    },
    delay: 200, // short delay for fast test execution
  });

  // 3. Submit Task
  const prompt = 'Test ORBIS visual state progression';
  console.log(`[2] Submitting Task: "${prompt}"...`);
  const submitRes = await submitTask(prompt, false);
  const taskId = submitRes.task_id;
  activeTaskId = taskId;
  currentVisualState = 'thinking';
  observedStates.push('thinking');
  console.log(`    PASS: Task created (ID: ${taskId}), state: thinking\n`);

  // 4. Stream events and resolve visual states
  console.log('[3] Streaming WebSocket events and resolving visual states...');
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error('Timed out waiting for task completion over WebSocket.'));
    }, 15000);

    const cleanup = connectTaskWebSocket(taskId, {
      onOpen: () => {
        console.log('    WebSocket connected.');
      },
      onEvent: (event) => {
        const prev = currentVisualState;
        const resolution = resolveVisualState(currentVisualState, event, activeTaskId);

        if (resolution.updated) {
          currentVisualState = resolution.state;
          observedStates.push(resolution.state);
          console.log(`    [Visual State Transition] ${prev} -> ${resolution.state} (via ${event.event_type})`);

          if (resolution.needsSettle && resolution.taskId) {
            console.log(`    [Settle Scheduled] Task ${resolution.taskId} will settle in 200ms`);
            settleManager.schedule(resolution.taskId);
          }
        }

        if (event.event_type === 'TASK_COMPLETED' || event.event_type === 'TASK_FAILED') {
          clearTimeout(timeout);
          cleanup();
          // Allow settle timer to fire
          setTimeout(() => {
            resolve();
          }, 350);
        }
      },
      onError: (err) => {
        console.warn('    WebSocket error:', err.message || err);
      },
    });
  });

  // 5. Verify observed sequence
  console.log('\n[4] Verifying Observed Visual State Progression...');
  console.log('    Observed sequence:', observedStates);

  assert(observedStates.includes('thinking'), 'Must observe thinking state');
  assert(observedStates.includes('planning'), 'Must observe planning state');
  assert(observedStates.includes('executing'), 'Must observe executing state');
  assert(observedStates.includes('responding'), 'Must observe responding state');
  assert(observedStates.includes('completed'), 'Must observe completed state');
  assert(settledToIdle, 'Must observe automatic settle to idle');

  console.log('    PASS: Visual state accurately tracked all lifecycle phases.');
  console.log('\n============================================================');
  console.log('  LIVE VISUAL STATE E2E VERIFICATION COMPLETED SUCCESSFULLY');
  console.log('============================================================');
}

verifyLiveVisualLifecycle().catch((err) => {
  console.error('\nFAIL:', err);
  process.exit(1);
});
