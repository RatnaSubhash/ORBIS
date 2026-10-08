/**
 * ErrorBoundary — Resilient React Error Barrier.
 *
 * Prevents unhandled render-phase exceptions from crashing the entire
 * Electron window to a blank / black screen. Catches errors in child components,
 * logs diagnostic telemetry, and provides an in-place recovery interface.
 */
import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('[ORBIS ErrorBoundary caught an unhandled exception]:', error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    if (this.props.onReset) {
      this.props.onReset();
    }
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return typeof this.props.fallback === 'function'
          ? this.props.fallback({ error: this.state.error, reset: this.handleReset })
          : this.props.fallback;
      }

      return (
        <div className="orbis-error-boundary-card">
          <div className="orbis-error-boundary-header">
            <AlertTriangle size={20} className="orbis-error-icon" />
            <span className="orbis-error-title">Interface Recovery Guard</span>
          </div>
          <p className="orbis-error-description">
            {this.props.title || 'A presentation error was caught and isolated to keep the workspace operational.'}
          </p>
          {this.state.error?.message && (
            <div className="orbis-error-snippet">
              <code>{this.state.error.message}</code>
            </div>
          )}
          <button
            type="button"
            className="orbis-error-retry-btn"
            onClick={this.handleReset}
          >
            <RefreshCw size={14} />
            <span>Restore Component</span>
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
