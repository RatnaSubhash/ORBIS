/**
 * Unit Test Suite for ORBIS Visual State System
 * Tests:
 *   1. Event-aware resolution across all 9 visual states
 *   2. Non-linear transitions (approval hold/resume, cancellation)
 *   3. Stale-event rejection within the same task
 *   4. Task boundary isolation
 *   5. Deterministic race test for Task-Scoped Cancellable Settle Timers:
 *      Task A -> completed, then Task B -> TASK_STARTED;
 *      Task A's delayed callback must NOT set Task B's state to idle.
 */

import assert from 'node:assert';
import {
  ORBIS_VISUAL_STATES,
  STATUS_LABELS,
  STATE_SPEEDS,
  resolveVisualState,
  TaskSettleTimer,
} from '../src/state/visualState.js';

console.log('--- Starting ORBIS Visual State Tests ---');

// ── Test 1: All 9 Visual States Defined ─────────────────────────────────────
console.log('\n[Test 1] Verifying 9 Visual States & Config');
const expectedStates = [
  'idle',
  'listening',
  'thinking',
  'planning',
  'executing',
  'responding',
  'completed',
  'failed',
  'approval',
];

for (const state of expectedStates) {
  assert(
    Object.values(ORBIS_VISUAL_STATES).includes(state),
    `Missing state in ORBIS_VISUAL_STATES: ${state}`
  );
  assert(typeof STATUS_LABELS[state] === 'string', `Missing label for state: ${state}`);
  assert(typeof STATE_SPEEDS[state] === 'number', `Missing speed for state: ${state}`);
}
assert.strictEqual(STATE_SPEEDS.approval, 0.0, 'Approval state rotation speed must be 0.0 (frozen)');
console.log('✓ All 9 visual states, labels, and speeds correctly defined');

// ── Test 2: Required Event-to-State Mappings ────────────────────────────────
console.log('\n[Test 2] Verifying Required Lifecycle Mappings');
const taskId = 'task-100';

// 1. TASK_STARTED -> thinking
let res = resolveVisualState('idle', { event_type: 'TASK_STARTED', task_id: taskId }, null);
assert.strictEqual(res.state, 'thinking');
assert.strictEqual(res.updated, true);
assert.strictEqual(res.isNewTask, true);
assert.strictEqual(res.taskId, taskId);

// 2. PLAN_CREATED -> planning
res = resolveVisualState('thinking', { event_type: 'PLAN_CREATED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'planning');
assert.strictEqual(res.updated, true);

// 3. STEP_STARTED -> executing
res = resolveVisualState('planning', { event_type: 'STEP_STARTED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'executing');
assert.strictEqual(res.updated, true);

// 4. APPROVAL_REQUIRED -> approval
res = resolveVisualState('executing', { event_type: 'APPROVAL_REQUIRED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'approval');
assert.strictEqual(res.updated, true);

// 5. APPROVAL_GRANTED -> executing (non-linear resume)
res = resolveVisualState('approval', { event_type: 'APPROVAL_GRANTED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'executing');
assert.strictEqual(res.updated, true);

// 6. VERIFICATION_COMPLETED -> responding
res = resolveVisualState('executing', { event_type: 'VERIFICATION_COMPLETED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'responding');
assert.strictEqual(res.updated, true);

// 7. TASK_COMPLETED -> completed
res = resolveVisualState('responding', { event_type: 'TASK_COMPLETED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'completed');
assert.strictEqual(res.updated, true);
assert.strictEqual(res.needsSettle, true);

// 8. TASK_FAILED -> failed
res = resolveVisualState('executing', { event_type: 'TASK_FAILED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'failed');
assert.strictEqual(res.updated, true);
assert.strictEqual(res.needsSettle, true);

// 9. TASK_CANCELLED -> idle
res = resolveVisualState('approval', { event_type: 'TASK_CANCELLED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'idle');
assert.strictEqual(res.updated, true);

console.log('✓ All 9 required event mappings verified');

// ── Test 3: Non-Linear Approval Hold & Resume ───────────────────────────────
console.log('\n[Test 3] Verifying Non-Linear Approval Hold');
// When in approval hold, stale step events must not break the hold
res = resolveVisualState('approval', { event_type: 'STEP_STARTED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'approval');
assert.strictEqual(res.updated, false);
assert.strictEqual(res.reason, 'approval_hold');

res = resolveVisualState('approval', { event_type: 'PLAN_CREATED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'approval');
assert.strictEqual(res.updated, false);
assert.strictEqual(res.reason, 'approval_hold');

// Approval granted legitimately resumes executing
res = resolveVisualState('approval', { event_type: 'APPROVAL_GRANTED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'executing');
assert.strictEqual(res.updated, true);

console.log('✓ Approval hold and non-linear resume verified');

// ── Test 4: Stale-Event Protection Within Same Task ──────────────────────────
console.log('\n[Test 4] Verifying Stale-Event Protection (No Backward Regression)');
// If in executing, a delayed earlier event (TASK_STARTED or PLAN_CREATED) must be rejected
res = resolveVisualState('executing', { event_type: 'PLAN_CREATED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'executing');
assert.strictEqual(res.updated, false);
assert.strictEqual(res.reason, 'stale_earlier_phase');

// If in responding, a delayed STEP_STARTED must be rejected
res = resolveVisualState('responding', { event_type: 'STEP_STARTED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'responding');
assert.strictEqual(res.updated, false);
assert.strictEqual(res.reason, 'stale_earlier_phase');

// If in completed, any earlier lifecycle event must be rejected
res = resolveVisualState('completed', { event_type: 'VERIFICATION_COMPLETED', task_id: taskId }, taskId);
assert.strictEqual(res.state, 'completed');
assert.strictEqual(res.updated, false);
assert.strictEqual(res.reason, 'task_already_terminal');

console.log('✓ Stale event protection verified');

// ── Test 5: Task Boundary Isolation ─────────────────────────────────────────
console.log('\n[Test 5] Verifying Task Boundary Isolation');
// Event belonging to a different task must be ignored
res = resolveVisualState('executing', { event_type: 'STEP_STARTED', task_id: 'task-old' }, 'task-current');
assert.strictEqual(res.state, 'executing');
assert.strictEqual(res.updated, false);
assert.strictEqual(res.reason, 'task_boundary_mismatch');

console.log('✓ Task boundary isolation verified');

// ── Test 6: Deterministic Race Test for Task-Scoped Settle Timers ───────────
console.log('\n[Test 6] Deterministic Race Test: Task A completed -> Task B starts');
{
  let currentVisualState = 'idle';
  let activeTaskId = null;

  const settleManager = new TaskSettleTimer({
    onSettle: (settledTaskId) => {
      // Settle handler only transitions if the settled task is still active
      if (activeTaskId === settledTaskId) {
        currentVisualState = 'idle';
      }
    },
    delay: 50, // 50ms delay for async test
  });

  // 1. Task A starts and completes
  activeTaskId = 'task-A';
  currentVisualState = 'executing';

  // Task A completes
  const compRes = resolveVisualState(currentVisualState, { event_type: 'TASK_COMPLETED', task_id: 'task-A' }, activeTaskId);
  assert.strictEqual(compRes.state, 'completed');
  currentVisualState = compRes.state;

  // Settle timer scheduled for Task A
  settleManager.schedule('task-A');
  assert.strictEqual(settleManager.isScheduledFor('task-A'), true);

  // 2. Before Task A's timer fires, Task B starts!
  // Cancellation condition: new task starts
  settleManager.cancel();
  activeTaskId = 'task-B';
  const startRes = resolveVisualState('completed', { event_type: 'TASK_STARTED', task_id: 'task-B' }, 'task-A');
  assert.strictEqual(startRes.state, 'thinking');
  assert.strictEqual(startRes.isNewTask, true);
  assert.strictEqual(startRes.taskId, 'task-B');
  currentVisualState = startRes.state;

  // Task B proceeds to executing
  const stepRes = resolveVisualState(currentVisualState, { event_type: 'STEP_STARTED', task_id: 'task-B' }, activeTaskId);
  currentVisualState = stepRes.state;
  assert.strictEqual(currentVisualState, 'executing');

  // Wait 70ms to ensure Task A's timer would have fired if not cancelled
  await new Promise((resolve) => setTimeout(resolve, 70));

  // Assert: Task B's state was NOT overwritten to 'idle'
  assert.strictEqual(
    currentVisualState,
    'executing',
    'Task A delayed settle callback MUST NOT reset Task B to idle'
  );
  console.log('✓ Task A settle callback cleanly prevented from resetting Task B');
}

// ── Test 7: Double Protection: Delayed callback with mismatched task ID ─────
console.log('\n[Test 7] Verifying Settle Guard when Active Task ID Changes');
{
  let currentVisualState = 'completed';
  let activeTaskId = 'task-A';
  let settleFiredFor = null;

  const settleManager = new TaskSettleTimer({
    onSettle: (settledTaskId) => {
      settleFiredFor = settledTaskId;
      if (activeTaskId === settledTaskId) {
        currentVisualState = 'idle';
      }
    },
    delay: 30,
  });

  settleManager.schedule('task-A');

  // Simulate activeTaskId shifting to task-B without explicitly calling cancel()
  activeTaskId = 'task-B';
  currentVisualState = 'thinking';

  // Wait for timer
  await new Promise((resolve) => setTimeout(resolve, 50));

  // The timer fired, but callback detected activeTaskId !== 'task-A'
  assert.strictEqual(currentVisualState, 'thinking', 'Visual state must remain thinking');
  console.log('✓ Task ID guard in onSettle successfully blocked stale transition');
}

console.log('\n=============================================');
console.log('ALL ORBIS VISUAL STATE TESTS PASSED CLEANLY');
console.log('=============================================');
