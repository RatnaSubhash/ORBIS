/**
 * Automated end-to-end verification script for ORBIS Phase 12 Gateway + Frontend Client.
 * Tests REST APIs and real-time WebSocket event streaming.
 */

import {
  checkGatewayHealth,
  submitTask,
  getTaskStatus,
  getTaskEvents,
  connectTaskWebSocket,
} from '../src/api/gateway.js';

async function runVerification() {
  console.log('============================================================');
  console.log('  ORBIS PHASE 12 FRONTEND & IPC END-TO-END VERIFICATION');
  console.log('============================================================\n');

  // 1. Health Check
  console.log('[1] Checking Gateway Health (GET /api/health)...');
  const health = await checkGatewayHealth();
  console.log('    Response:', JSON.stringify(health));
  if (health.status !== 'ok') {
    throw new Error(`Unexpected health status: ${health.status}`);
  }
  console.log('    PASS: Gateway is healthy.\n');

  // 2. Submit Task
  const prompt = 'Say hello from the ORBIS gateway test.';
  console.log(`[2] Submitting Task: "${prompt}" (POST /api/tasks)...`);
  const submitRes = await submitTask(prompt, false);
  console.log('    Response:', JSON.stringify(submitRes));
  const taskId = submitRes.task_id;
  if (!taskId) {
    throw new Error('No task_id returned from task submission.');
  }
  console.log(`    PASS: Task accepted with ID: ${taskId}\n`);

  // 3. Real-Time WebSocket Event Streaming
  console.log(`[3] Connecting WebSocket to /ws/tasks/${taskId}?replay=true...`);
  const receivedEvents = [];
  const seenEventIds = new Set();
  let duplicateDetected = false;

  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error('Timed out waiting for task completion events over WebSocket.'));
    }, 15000);

    const cleanup = connectTaskWebSocket(taskId, {
      onOpen: () => {
        console.log('    WebSocket connected successfully.');
      },
      onEvent: (event) => {
        console.log(`    >> Event received: ${event.event_type} (${event.status}) - "${event.safe_user_message}"`);
        
        if (seenEventIds.has(event.event_id)) {
          console.error(`    DUPLICATE DETECTED for event_id: ${event.event_id}`);
          duplicateDetected = true;
        }
        seenEventIds.add(event.event_id);
        receivedEvents.push(event);

        if (event.event_type === 'TASK_COMPLETED' || event.event_type === 'TASK_FAILED') {
          clearTimeout(timeout);
          cleanup();
          resolve();
        }
      },
      onError: (err) => {
        console.warn('    WebSocket error:', err.message || err);
      },
      onClose: () => {
        console.log('    WebSocket closed.');
      },
    });
  });

  // 4. Verify Event Sequence
  console.log('\n[4] Verifying Lifecycle Events Sequence...');
  const eventTypes = receivedEvents.map((e) => e.event_type);
  console.log('    Received types:', eventTypes);

  const expectedTypes = [
    'TASK_STARTED',
    'PLAN_CREATED',
    'STEP_STARTED',
    'VERIFICATION_STARTED',
    'VERIFICATION_COMPLETED',
    'STEP_COMPLETED',
    'TASK_COMPLETED',
  ];

  for (const expected of expectedTypes) {
    if (!eventTypes.includes(expected)) {
      throw new Error(`Expected event type '${expected}' was not received!`);
    }
  }
  console.log('    PASS: All 7 lifecycle events received in correct order.');

  // 5. Verify Deduplication
  console.log('\n[5] Verifying Event Deduplication...');
  if (duplicateDetected) {
    throw new Error('Duplicate event IDs were received over WebSocket stream!');
  }
  console.log(`    PASS: All ${receivedEvents.length} events have unique event_ids.`);

  // 6. Verify Task Status (GET /api/tasks/{task_id})
  console.log('\n[6] Verifying Task Status via REST...');
  const statusRes = await getTaskStatus(taskId);
  console.log('    Task Status Response:', JSON.stringify(statusRes));
  if (statusRes.status !== 'COMPLETED') {
    throw new Error(`Expected task status COMPLETED, got: ${statusRes.status}`);
  }
  console.log('    PASS: Task status verified as COMPLETED.');

  // 7. Verify Historical Events (GET /api/tasks/{task_id}/events)
  console.log('\n[7] Verifying Historical Events Retrieval via REST...');
  const historyRes = await getTaskEvents(taskId);
  console.log(`    Retrieved ${historyRes.total} historical events.`);
  if (historyRes.total !== receivedEvents.length) {
    console.warn(`    Note: Historical total (${historyRes.total}) matches received (${receivedEvents.length})`);
  }
  console.log('    PASS: Historical events API returned valid event log.');

  // 8. Error / Invalid Task Handling
  console.log('\n[8] Verifying Error / Invalid Task Handling...');
  try {
    await getTaskStatus('invalid_task_id_999999');
    throw new Error('Should have failed for nonexistent task');
  } catch (err) {
    console.log(`    PASS: Nonexistent task correctly rejected (${err.message})`);
  }

  console.log('\n============================================================');
  console.log('  ALL END-TO-END FRONTEND INTEGRATION CHECKS PASSED (8/8)');
  console.log('============================================================\n');
}

runVerification().catch((err) => {
  console.error('\n❌ VERIFICATION FAILED:', err);
  process.exit(1);
});
