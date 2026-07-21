import { describe, expect, it } from 'vitest';
import { buildSrt, buildTranscript } from '../exporters';
import type { ScriptLine } from '../../types';

const lines: ScriptLine[] = [
  {
    id: 'line-1',
    time: 4.2,
    duration: 2.3,
    eventType: 'chance',
    text: 'Fantastic save from the keeper.',
    emphasis: 'high',
    providerStatus: 'needs-synthesis',
  },
  {
    id: 'line-2',
    time: 12,
    duration: 3,
    eventType: 'build-up',
    text: 'They settle back into possession.',
    emphasis: 'medium',
    providerStatus: 'needs-synthesis',
  },
];

describe('exporters', () => {
  it('builds a timestamped transcript', () => {
    expect(buildTranscript(lines)).toContain('[00:04] Fantastic save from the keeper.');
    expect(buildTranscript(lines)).toContain('[00:12] They settle back into possession.');
  });

  it('builds valid SRT blocks', () => {
    const srt = buildSrt(lines);
    expect(srt).toContain('1\n00:00:04,200 --> 00:00:06,500');
    expect(srt).toContain('2\n00:00:12,000 --> 00:00:15,000');
  });
});
