import type { MatchEvent, ScriptLine } from '../types';
import { formatDuration } from '../lib/format';

interface TimelineProps {
  duration: number;
  events: MatchEvent[];
  script: ScriptLine[];
  selectedLineId?: string;
  onSelectLine?: (id: string) => void;
}

export function Timeline({ duration, events, script, selectedLineId, onSelectLine }: TimelineProps) {
  const safeDuration = Math.max(1, duration);
  return (
    <div className="timeline-card">
      <div className="timeline-card__header">
        <strong>Match timeline</strong>
        <span>{formatDuration(duration)}</span>
      </div>
      <div className="timeline" role="img" aria-label="Video analysis and commentary timeline">
        <div className="timeline__lane timeline__lane--events">
          {events.map((event) => (
            <span
              key={event.id}
              className={`timeline-event is-${event.type}`}
              title={`${formatDuration(event.time)} · ${event.type} · ${event.description}`}
              style={{ left: `${(event.time / safeDuration) * 100}%`, opacity: 0.45 + event.importance * 0.5 }}
            />
          ))}
        </div>
        <div className="timeline__lane timeline__lane--script">
          {script.map((line) => (
            <button
              key={line.id}
              type="button"
              className={`timeline-line ${selectedLineId === line.id ? 'is-selected' : ''} is-${line.emphasis}`}
              title={`${formatDuration(line.time)} · ${line.text}`}
              onClick={() => onSelectLine?.(line.id)}
              style={{
                left: `${(line.time / safeDuration) * 100}%`,
                width: `${Math.max(0.8, (line.duration / safeDuration) * 100)}%`,
              }}
            />
          ))}
        </div>
        <div className="timeline__ticks">
          {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
            <span key={tick} style={{ left: `${tick * 100}%` }}>
              {formatDuration(duration * tick)}
            </span>
          ))}
        </div>
      </div>
      <div className="timeline-legend">
        <span><i className="legend-dot event" /> CV event</span>
        <span><i className="legend-dot line" /> Commentary line</span>
        <span><i className="legend-dot high" /> High emphasis</span>
      </div>
    </div>
  );
}
