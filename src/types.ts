export type PageId =
  | 'home'
  | 'upload'
  | 'voice'
  | 'processing'
  | 'preview'
  | 'editor'
  | 'export';

export type CommentaryStyle =
  | 'TV Broadcast'
  | 'Excited'
  | 'Professional'
  | 'Radio'
  | 'Calm'
  | 'Documentary'
  | 'Australian commentator'
  | 'British commentator'
  | 'American commentator';

export type CommentaryFrequency = 'Minimal' | 'Normal' | 'Constant';

export type SportName =
  | 'Soccer / Football'
  | 'Basketball'
  | 'AFL'
  | 'Rugby League'
  | 'Rugby Union'
  | 'Cricket'
  | 'Tennis'
  | 'Volleyball'
  | 'Baseball'
  | 'Hockey'
  | 'Netball'
  | 'Unknown sport';

export type EventType =
  | 'kickoff'
  | 'build-up'
  | 'switch'
  | 'pressure'
  | 'chance'
  | 'save'
  | 'shot'
  | 'goal'
  | 'foul'
  | 'corner'
  | 'free-kick'
  | 'penalty'
  | 'turnover'
  | 'fast-break'
  | 'celebration'
  | 'substitution'
  | 'timeout'
  | 'wicket'
  | 'boundary'
  | 'rally'
  | 'possession'
  | 'momentum'
  | 'final-whistle';

export interface UploadFormState {
  videoFile: File | null;
  voiceFile: File | null;
  permissionConfirmed: boolean;
  teamNames: string;
  playerNames: string;
  competitionName: string;
  language: string;
  style: CommentaryStyle;
  frequency: CommentaryFrequency;
}

export interface VoiceProfile {
  fileName: string;
  permissionConfirmed: boolean;
  durationSeconds: number;
  accent: string;
  pronunciation: string;
  speakingSpeed: 'Slow' | 'Measured' | 'Conversational' | 'Fast';
  wordsPerMinuteEstimate: number;
  tone: string;
  emotion: string;
  averagePitchHz: number | null;
  pitchRangeHz: [number, number] | null;
  energy: 'Low' | 'Balanced' | 'High';
  pauseRatio: number;
  intonation: 'Flat' | 'Natural' | 'Expressive';
  confidence: number;
  waveform: number[];
  notes: string[];
}

export interface VideoMetrics {
  durationSeconds: number;
  width: number;
  height: number;
  sampledFrames: number;
  averageMotion: number;
  greenFieldRatio: number;
  woodCourtRatio: number;
  iceRatio: number;
  lineMarkingRatio: number;
  overlayScoreboardLikelihood: number;
}

export interface TeamColor {
  label: string;
  hex: string;
  share: number;
}

export interface MatchEvent {
  id: string;
  time: number;
  type: EventType;
  confidence: number;
  description: string;
  teamInPossession?: string;
  player?: string;
  importance: number;
}

export interface SportAnalysis {
  fileName: string;
  sport: SportName;
  confidence: number;
  metrics: VideoMetrics;
  detectedObjects: string[];
  teamColors: TeamColor[];
  events: MatchEvent[];
  attackingDirection: 'left-to-right' | 'right-to-left' | 'mixed/unknown';
  scoreboard: string;
  gameClock: string;
  momentumSummary: string;
  limitations: string[];
}

export interface ScriptLine {
  id: string;
  time: number;
  duration: number;
  eventType: EventType;
  text: string;
  emphasis: 'low' | 'medium' | 'high';
  providerStatus: 'draft' | 'needs-synthesis' | 'synthesized';
}

export interface ProcessingStep {
  id: string;
  label: string;
  progress: number;
  status: 'waiting' | 'running' | 'done' | 'error';
}

export interface ProjectState {
  upload: UploadFormState;
  voiceProfile: VoiceProfile | null;
  sportAnalysis: SportAnalysis | null;
  script: ScriptLine[];
  createdAt: string;
  updatedAt: string;
}
