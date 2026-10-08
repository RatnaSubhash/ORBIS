/**
 * Controlled Fallback & Demo Data for ORBIS Frontend.
 *
 * CRITICAL RULE:
 * This data is used ONLY when the backend gateway is disconnected or
 * when previewing views during frontend development.
 * It is clearly flagged with `isDemo: true` so the UI never misrepresents
 * mock states as live backend execution.
 */

export const MOCK_WORKSPACE_MEMORY = {
  isDemo: true,
  project: {
    name: 'ORBIS',
    path: 'C:\\Projects\\ORBIS',
    activeBranch: 'main',
    confidenceScore: 0.96,
  },
  activeApplications: [
    { name: 'Antigravity IDE', pid: 14208, status: 'Focused', memory: '1.2 GB', executable: 'antigravity.exe' },
    { name: 'Google Chrome', pid: 9812, status: 'Active (3 tabs)', memory: '850 MB', executable: 'chrome.exe' },
    { name: 'Spotify', pid: 6140, status: 'Background Audio', memory: '280 MB', executable: 'spotify.exe' },
  ],
  recentResources: [
    { name: 'demo_phase6.py', type: 'Python Script', path: 'C:\\Projects\\ORBIS\\demo_phase6.py', time: '12m ago' },
    { name: 'README.md', type: 'Documentation', path: 'C:\\Projects\\ORBIS\\README.md', time: '45m ago' },
    { name: 'pyproject.toml', type: 'Config', path: 'C:\\Projects\\ORBIS\\pyproject.toml', time: '2h ago' },
    { name: 'phase12_gateway_architecture.md', type: 'Architecture Spec', path: 'C:\\Projects\\ORBIS\\docs\\architecture\\phase12_gateway_architecture.md', time: 'Yesterday' },
  ],
  session: {
    id: 'sess-2026-09-22-01',
    startedAt: 'Today at 10:15 AM',
    duration: '9h 30m',
    contextConfidence: 'High (0.94)',
  },
};

export const MOCK_RESTORATION_PLAN = {
  isDemo: true,
  planId: 'plan-restore-001',
  project: 'ORBIS (C:\\Projects\\ORBIS)',
  targetApps: [
    { id: 'antigravity', name: 'Antigravity IDE', args: ['C:\\Projects\\ORBIS'], verified: true },
    { id: 'chrome', name: 'Google Chrome', args: ['https://reactbits.dev'], verified: true },
  ],
  targetResources: [
    'C:\\Projects\\ORBIS\\README.md',
    'C:\\Projects\\ORBIS\\demo_phase6.py',
  ],
  riskLevel: 'LOW',
  requiresApproval: true,
  approvalReason: 'Restoring multi-application workspace: Antigravity IDE and Google Chrome.',
};

export const MOCK_RESEARCH_RESULTS = {
  isDemo: true,
  query: 'Quantum Computing Fault Tolerance Benchmarks 2026',
  sources: [
    { title: 'Nature Quantum Information', domain: 'nature.com', date: '2026-08', trust: 0.98 },
    { title: 'IBM Quantum Systems Roadmap', domain: 'ibm.com', date: '2026-07', trust: 0.95 },
    { title: 'Google Quantum AI Lab Report', domain: 'quantumai.google', date: '2026-06', trust: 0.97 },
    { title: 'arXiv:2605.10928v2 [quant-ph]', domain: 'arxiv.org', date: '2026-05', trust: 0.92 },
  ],
  keyFindings: [
    'Neutral atom qubit architectures demonstrated 99.9% two-qubit gate fidelities across 1,000+ physical qubits.',
    'Surface codes with rotated lattices reached threshold distances of d=7, suppressing logical error rates below 1e-6.',
    'Hybrid classical-quantum error mitigation algorithms reduced syndrome measurement overhead by 42%.',
  ],
  evidence: 'Cross-validated against 14 peer-reviewed datasets with zero synthetic extrapolation.',
};

export const MOCK_DOCUMENT_RESULT = {
  isDemo: true,
  title: 'Quantum_Computing_Fault_Tolerance_Report_2026.pdf',
  location: 'C:\\Projects\\ORBIS\\artifacts\\Quantum_Report_2026.pdf',
  format: 'PDF (Generated Artifact)',
  fileSize: '1.4 MB',
  createdAt: 'Just now',
};
