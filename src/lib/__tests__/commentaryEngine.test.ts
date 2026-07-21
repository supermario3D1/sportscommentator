import { describe, expect, it } from 'vitest';
import { generateCommentaryScript, regenerateScriptLine } from '../commentaryEngine';
import type { SportAnalysis, UploadFormState, VoiceProfile } from '../../types';

const upload: UploadFormState = {
  videoFile: null,
  voiceFile: null,
  permissionConfirmed: true,
  teamNames: 'Falcons, City',
  playerNames: 'Alex Morgan, Sam Kerr',
  competitionName: 'Arena Cup',
  language: 'English',
  style: 'TV Broadcast',
  frequency: 'Normal',
};

const analysis: SportAnalysis = {
  fileName: 'match.mp4',
  sport: 'Soccer / Football',
  confidence: 0.82,
  metrics: {
    durationSeconds: 180,
    width: 1920,
    height: 1080,
    sampledFrames: 18,
    averageMotion: 0.4,
    greenFieldRatio: 0.55,
    woodCourtRatio: 0.02,
    iceRatio: 0.04,
    lineMarkingRatio: 0.12,
    overlayScoreboardLikelihood: 0.4,
  },
  detectedObjects: ['players', 'ball'],
  teamColors: [],
  attackingDirection: 'left-to-right',
  scoreboard: 'Likely',
  gameClock: 'Likely',
  momentumSummary: 'Falcons pushing.',
  limitations: [],
  events: [
    { id: 'e1', time: 3, type: 'kickoff', confidence: 0.7, description: 'Start', importance: 0.4, teamInPossession: 'Falcons' },
    { id: 'e2', time: 24, type: 'switch', confidence: 0.7, description: 'Switch', importance: 0.5, teamInPossession: 'City' },
    { id: 'e3', time: 48, type: 'chance', confidence: 0.8, description: 'Chance', importance: 0.8, teamInPossession: 'Falcons', player: 'Sam Kerr' },
    { id: 'e4', time: 88, type: 'save', confidence: 0.8, description: 'Save', importance: 0.85, teamInPossession: 'City' },
    { id: 'e5', time: 122, type: 'pressure', confidence: 0.7, description: 'Pressure', importance: 0.6, teamInPossession: 'Falcons' },
    { id: 'e6', time: 170, type: 'final-whistle', confidence: 0.7, description: 'End', importance: 0.4 },
  ],
};

const voice: VoiceProfile = {
  fileName: 'voice.wav',
  permissionConfirmed: true,
  durationSeconds: 45,
  accent: 'Australian English accent target',
  pronunciation: 'Prioritize English pronunciation',
  speakingSpeed: 'Conversational',
  wordsPerMinuteEstimate: 155,
  tone: 'Natural conversational tone',
  emotion: 'Confident',
  averagePitchHz: 180,
  pitchRangeHz: [120, 230],
  energy: 'Balanced',
  pauseRatio: 0.22,
  intonation: 'Natural',
  confidence: 0.8,
  waveform: [0.1, 0.4, 0.8],
  notes: [],
};

describe('commentaryEngine', () => {
  it('generates ordered, non-empty commentary lines', () => {
    const lines = generateCommentaryScript(upload, analysis, voice);
    expect(lines.length).toBeGreaterThan(3);
    expect(lines.every((line) => line.text.length > 20)).toBe(true);
    expect([...lines].sort((a, b) => a.time - b.time)).toEqual(lines);
  });

  it('regenerates a line with a new id and synthesis status', () => {
    const [line] = generateCommentaryScript(upload, analysis, voice);
    const regenerated = regenerateScriptLine(line, upload, analysis, voice, 4);
    expect(regenerated.id).not.toEqual(line.id);
    expect(regenerated.providerStatus).toBe('needs-synthesis');
    expect(regenerated.text.length).toBeGreaterThan(10);
  });
});
