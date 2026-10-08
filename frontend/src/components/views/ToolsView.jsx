/**
 * ToolsView — Integrated Capabilities & Runtime Tool Registry (Phase G).
 *
 * Implements the capability discovery and inspection center for ORBIS:
 *   - Real data sourced strictly from backend ToolRegistry via GET /api/tools
 *   - Dynamic category filter generated from authoritative API categories
 *   - AnimatedList capability explorer with safe selection
 *   - TiltedCard 3D perspective presentation for tool inspection & parameter schemas
 *   - Explicit "Try Prompt" execution that routes strictly through executeTaskPrompt() and Command view
 *   - Zero fake tools, zero mock executions, zero direct tool bypasses
 */

import React, { useState, useMemo, useCallback } from 'react';
import { useAgent } from '../../state/AgentContext';
import { ViewType } from '../../types';
import AnimatedList from '../ui/AnimatedList';

export default function ToolsView() {
  const {
    tools,
    totalTools,
    categories,
    isLoadingTools,
    refreshTools,
    executeTaskPrompt,
    setActiveView,
    gatewayOnline,
  } = useAgent();

  const [selectedToolId, setSelectedToolId] = useState(null);
  const [selectedCategory, setSelectedCategory] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  // 1. Authoritative Categories:
  // Build category list from authoritative backend response + any unlisted categories from tools.
  const categoryFilters = useMemo(() => {
    const set = new Set();
    if (Array.isArray(categories)) {
      categories.forEach((c) => c && set.add(c));
    }
    if (Array.isArray(tools)) {
      tools.forEach((t) => t.category && set.add(t.category));
    }
    return ['ALL', ...Array.from(set)];
  }, [categories, tools]);

  // Filter tools by category and search term
  const filteredTools = useMemo(() => {
    let list = tools || [];
    if (selectedCategory !== 'ALL') {
      list = list.filter((t) => t.category === selectedCategory);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      list = list.filter(
        (t) =>
          (t.name && t.name.toLowerCase().includes(q)) ||
          (t.description && t.description.toLowerCase().includes(q)) ||
          (t.id && t.id.toLowerCase().includes(q))
      );
    }
    return list;
  }, [tools, selectedCategory, searchQuery]);

  // Deterministically select the active tool
  const activeTool = useMemo(() => {
    if (!tools || tools.length === 0) return null;
    if (selectedToolId) {
      const match = tools.find((t) => t.id === selectedToolId);
      if (match) return match;
    }
    return filteredTools.length > 0 ? filteredTools[0] : tools[0];
  }, [tools, filteredTools, selectedToolId]);

  // Selecting a tool MUST NEVER execute anything — only inspects it
  const handleSelectTool = useCallback((tool) => {
    if (tool && tool.id) {
      setSelectedToolId(tool.id);
    }
  }, []);

  // Try Prompt requires an explicit user click and routes through the standard task execution pipeline
  const handleTryPrompt = useCallback(() => {
    if (!activeTool || !activeTool.example_prompt) return;
    executeTaskPrompt(activeTool.example_prompt);
    setActiveView(ViewType.COMMAND);
  }, [activeTool, executeTaskPrompt, setActiveView]);

  // Helper for risk level display and safety explanation
  const getRiskDetails = (riskLevel) => {
    const raw = String(riskLevel || 'LOW').toUpperCase();
    switch (raw) {
      case 'LOW':
        return {
          label: 'LOW RISK',
          color: 'low',
          badgeClass: 'risk-badge-low',
          explanation: 'Safe read-only or informational capability operating within read sandbox.',
        };
      case 'MEDIUM':
        return {
          label: 'MEDIUM RISK',
          color: 'medium',
          badgeClass: 'risk-badge-medium',
          explanation: 'Standard operational capability executing within bounded operating system limits.',
        };
      case 'HIGH':
        return {
          label: 'HIGH RISK',
          color: 'high',
          badgeClass: 'risk-badge-high',
          explanation: 'Elevated capability requiring explicit operator approval before execution.',
        };
      case 'BLOCKED':
        return {
          label: 'BLOCKED',
          color: 'blocked',
          badgeClass: 'risk-badge-blocked',
          explanation: 'Safety restricted capability blocked by current system security policy.',
        };
      default:
        return {
          label: raw,
          color: 'neutral',
          badgeClass: 'risk-badge-neutral',
          explanation: 'Guarded tool execution subject to safety verification.',
        };
    }
  };

  const activeRisk = activeTool ? getRiskDetails(activeTool.risk_level) : null;

  return (
    <div className="orbis-tools-view orbis-tools-container">
      {/* ── View Header ── */}
      <div className="orbis-view-header">
        <div className="header-meta-row">
          <h2>Orbital Capabilities</h2>
          <span className="orbis-badge count-badge">
            {totalTools || tools.length} REGISTERED
          </span>
          <button
            type="button"
            className="orbis-refresh-btn"
            onClick={refreshTools}
            disabled={isLoadingTools || !gatewayOnline}
            title="Refresh tool registry from backend"
          >
            {isLoadingTools ? 'Syncing…' : '↻ Sync'}
          </button>
        </div>
        <span className="orbis-view-sub">
          Authoritative tool registry and safety verified execution interfaces
        </span>
      </div>

      {/* ── Category Filters & Search ── */}
      <div className="orbis-tools-controls">
        <div className="orbis-category-pills" role="tablist">
          {categoryFilters.map((cat) => (
            <button
              key={cat}
              type="button"
              role="tab"
              aria-selected={selectedCategory === cat}
              className={`orbis-category-pill ${selectedCategory === cat ? 'active' : ''}`}
              onClick={() => setSelectedCategory(cat)}
            >
              {cat}
            </button>
          ))}
        </div>

        <div className="orbis-search-wrap">
          <input
            type="text"
            className="orbis-tool-search-input"
            placeholder="Filter capabilities by name, description…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <button
              type="button"
              className="orbis-search-clear"
              onClick={() => setSearchQuery('')}
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* ── Main Layout: Explorer List & Detail Inspector ── */}
      <div className="orbis-tools-workspace">
        {/* Left Column: Animated Tool List */}
        <div className="orbis-tools-list-col">
          {filteredTools.length > 0 ? (
            <AnimatedList
              items={filteredTools}
              onItemSelect={handleSelectTool}
              className="orbis-animated-tool-list"
              itemClassName="orbis-tool-item-wrap"
              renderItem={(tool, idx, isSelected) => {
                const isCurrent = activeTool?.id === tool.id;
                const risk = getRiskDetails(tool.risk_level);
                return (
                  <div
                    className={`orbis-tool-card ${isCurrent ? 'selected-tool' : ''}`}
                    data-tool-id={tool.id}
                  >
                    <div className="tool-card-head">
                      <div className="tool-card-title-group">
                        <span className="tool-title">{tool.name}</span>
                        <span className="tool-version">v{tool.version}</span>
                      </div>
                      <span className={`tool-risk-badge ${risk.badgeClass}`}>
                        {risk.label}
                      </span>
                    </div>

                    <p className="tool-desc-short">{tool.description}</p>

                    <div className="tool-card-foot">
                      <span className="tool-category-tag">{tool.category}</span>
                      <div
                        className="tool-status-indicator"
                        title="The tool is registered and available through the ORBIS ToolRegistry."
                      >
                        <span className="tool-status-dot available" />
                        <span className="tool-status-text">AVAILABLE</span>
                      </div>
                    </div>
                  </div>
                );
              }}
            />
          ) : (
            <div className="orbis-tools-empty">
              <span className="empty-icon">⚙</span>
              <p>No tools found matching current filter.</p>
              <button
                type="button"
                className="orbis-action-btn secondary small"
                onClick={() => {
                  setSelectedCategory('ALL');
                  setSearchQuery('');
                }}
              >
                Reset Filters
              </button>
            </div>
          )}
        </div>

        {/* Right Column: Tool Detail Inspector (Crystal Clear Solid Presentation) */}
        <div className="orbis-tools-detail-col">
          {activeTool ? (
            <div className="orbis-tool-inspector">
              {/* Inspector Header */}
              <div className="inspector-head">
                <div className="inspector-title-row">
                  <div>
                    <h3 className="inspector-title">{activeTool.name}</h3>
                    <span className="inspector-identifier">id: {activeTool.id}</span>
                  </div>
                  <div className="inspector-badges">
                    <span className="inspector-category-badge">{activeTool.category}</span>
                    <span className={`inspector-risk-badge ${activeRisk.badgeClass}`}>
                      {activeRisk.label}
                    </span>
                  </div>
                </div>

                <p className="inspector-desc">{activeTool.description}</p>

                <div className="inspector-safety-box">
                  <span className="safety-title">SAFETY POLICY</span>
                  <span className="safety-desc">{activeRisk.explanation}</span>
                </div>
              </div>

              {/* Actions & Capabilities */}
              {activeTool.actions && activeTool.actions.length > 0 && (
                <div className="inspector-section">
                  <span className="inspector-section-label">EXPOSED ACTIONS</span>
                  <div className="inspector-actions-tags">
                    {activeTool.actions.map((act) => (
                      <span key={act} className="inspector-action-pill font-mono">
                        {act}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Parameters Schema Breakdown */}
              <div className="inspector-section schema-section">
                <span className="inspector-section-label">PARAMETER SCHEMA</span>
                {activeTool.parameters_schema?.properties &&
                Object.keys(activeTool.parameters_schema.properties).length > 0 ? (
                  <div className="inspector-schema-list">
                    {Object.entries(activeTool.parameters_schema.properties).map(([pName, pMeta]) => {
                      const isReq = (activeTool.parameters_schema.required || []).includes(pName);
                      return (
                        <div key={pName} className="inspector-param-item">
                          <div className="param-header">
                            <span className="param-name font-mono">{pName}</span>
                            <span className="param-type">{pMeta.type || 'any'}</span>
                            <span className={`param-req-badge ${isReq ? 'req' : 'opt'}`}>
                              {isReq ? 'REQUIRED' : 'OPTIONAL'}
                            </span>
                          </div>
                          {pMeta.description && (
                            <p className="param-description">{pMeta.description}</p>
                          )}
                          {pMeta.enum && (
                            <div className="param-meta-row">
                              <span className="param-meta-k">enum:</span>
                              <span className="param-meta-v font-mono">
                                [{pMeta.enum.join(', ')}]
                              </span>
                            </div>
                          )}
                          {pMeta.default !== undefined && (
                            <div className="param-meta-row">
                              <span className="param-meta-k">default:</span>
                              <span className="param-meta-v font-mono">
                                {JSON.stringify(pMeta.default)}
                              </span>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="inspector-empty-schema">
                    <span>No additional input parameters required.</span>
                  </div>
                )}
              </div>

              {/* Try Prompt Section */}
              <div className="inspector-action-footer">
                <div className="try-prompt-info">
                  <span className="try-label">VERIFIED AGENT PROMPT</span>
                  <p className="try-prompt-text font-mono">
                    "{activeTool.example_prompt}"
                  </p>
                  <span className="try-hint">
                    Execution runs through the unified ORBIS planner and approval pipeline.
                  </span>
                </div>

                <button
                  id="try-prompt-btn"
                  type="button"
                  className="orbis-action-btn primary try-prompt-btn"
                  onClick={handleTryPrompt}
                  title="Execute this prompt through the ORBIS task pipeline"
                >
                  Try Prompt in Command ↵
                </button>
              </div>
            </div>
          ) : (
            <div className="orbis-tool-inspector-placeholder">
              <span className="placeholder-icon">◈</span>
              <p>Select a capability from the registry to inspect parameters and safety profiles.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
