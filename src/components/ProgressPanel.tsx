import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react';
import type { ProcessingStep } from '../types';
import { formatPercent } from '../lib/format';

interface ProgressPanelProps {
  steps: ProcessingStep[];
  statusMessage?: string;
}

export function ProgressPanel({ steps, statusMessage }: ProgressPanelProps) {
  return (
    <div className="progress-panel">
      {steps.map((step) => (
        <div className="progress-row" key={step.id}>
          <div className={`progress-row__icon is-${step.status}`}>
            {step.status === 'done' ? <CheckCircle2 size={20} /> : step.status === 'running' ? <Loader2 size={20} /> : step.status === 'error' ? <XCircle size={20} /> : <Circle size={20} />}
          </div>
          <div className="progress-row__body">
            <div className="progress-row__meta">
              <strong>{step.label}</strong>
              <span>{formatPercent(step.progress)}</span>
            </div>
            <div className="progress-track" aria-label={`${step.label} progress`}>
              <span style={{ width: `${Math.round(step.progress * 100)}%` }} />
            </div>
          </div>
        </div>
      ))}
      {statusMessage ? <p className="muted progress-message">{statusMessage}</p> : null}
    </div>
  );
}
