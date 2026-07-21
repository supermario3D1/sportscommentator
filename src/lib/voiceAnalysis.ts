import type { VoiceProfile } from '../types';
import { clamp } from './format';

interface BrowserWindowWithAudio extends Window {
  webkitAudioContext?: typeof AudioContext;
}

const FRAME_SECONDS = 0.05;
const HOP_SECONDS = 0.025;

export async function analyzeVoiceSample(
  file: File,
  permissionConfirmed: boolean,
  language: string,
  onProgress?: (progress: number, message: string) => void,
): Promise<VoiceProfile> {
  if (!permissionConfirmed) {
    throw new Error('Voice analysis requires confirmation that the speaker gave permission.');
  }

  onProgress?.(0.05, 'Reading voice sample');
  const arrayBuffer = await file.arrayBuffer();
  const AudioContextCtor = window.AudioContext ?? (window as BrowserWindowWithAudio).webkitAudioContext;
  if (!AudioContextCtor) {
    throw new Error('This browser does not support Web Audio analysis.');
  }

  const audioContext = new AudioContextCtor();
  try {
    onProgress?.(0.2, 'Decoding audio waveform');
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
    const mono = mixToMono(audioBuffer);
    const durationSeconds = audioBuffer.duration;

    onProgress?.(0.38, 'Measuring energy, pauses, and speaking speed');
    const envelope = buildEnergyEnvelope(mono, audioBuffer.sampleRate);
    const activeThreshold = Math.max(0.012, percentile(envelope, 0.68) * 0.52);
    const activeFrames = envelope.filter((value) => value > activeThreshold);
    const pauseRatio = envelope.length === 0 ? 0 : 1 - activeFrames.length / envelope.length;
    const averageEnergy = activeFrames.length > 0 ? mean(activeFrames) : mean(envelope);
    const energy = averageEnergy > 0.11 ? 'High' : averageEnergy < 0.045 ? 'Low' : 'Balanced';
    const syllablesPerMinute = estimateSyllablesPerMinute(envelope, activeThreshold, durationSeconds);
    const wordsPerMinuteEstimate = Math.round(syllablesPerMinute / 1.45);
    const speakingSpeed =
      wordsPerMinuteEstimate < 105
        ? 'Slow'
        : wordsPerMinuteEstimate < 145
          ? 'Measured'
          : wordsPerMinuteEstimate < 185
            ? 'Conversational'
            : 'Fast';

    onProgress?.(0.62, 'Estimating pitch and intonation');
    const pitches = estimatePitchTrack(mono, audioBuffer.sampleRate, envelope, activeThreshold);
    const averagePitchHz = pitches.length > 0 ? Math.round(mean(pitches)) : null;
    const pitchRangeHz = pitches.length > 2 ? ([Math.round(percentile(pitches, 0.1)), Math.round(percentile(pitches, 0.9))] as [number, number]) : null;
    const pitchVariance = pitchRangeHz ? pitchRangeHz[1] - pitchRangeHz[0] : 0;
    const intonation = pitchVariance > 95 ? 'Expressive' : pitchVariance < 35 ? 'Flat' : 'Natural';

    onProgress?.(0.82, 'Building narrator profile');
    const tone = inferTone(energy, pauseRatio, intonation);
    const emotion = inferEmotion(energy, intonation, wordsPerMinuteEstimate);
    const confidence = clamp(0.5 + Math.min(durationSeconds, 45) / 90 + (pitches.length > 5 ? 0.12 : 0) - (pauseRatio > 0.82 ? 0.12 : 0));
    const waveform = normalizeWaveform(envelope, 96);

    onProgress?.(1, 'Voice profile ready');
    return {
      fileName: file.name,
      permissionConfirmed,
      durationSeconds,
      accent: inferAccent(language),
      pronunciation: pronunciationNote(language),
      speakingSpeed,
      wordsPerMinuteEstimate,
      tone,
      emotion,
      averagePitchHz,
      pitchRangeHz,
      energy,
      pauseRatio: clamp(pauseRatio),
      intonation,
      confidence,
      waveform,
      notes: [
        'Consent gate passed before analysis.',
        'Browser analysis measures acoustic traits only; production voice cloning should use a licensed provider and retain an auditable consent record.',
        durationSeconds < 20 ? 'For better voice fidelity, upload at least 30-60 seconds of clear speech.' : 'Sample length is suitable for a voice profile draft.',
      ],
    };
  } finally {
    void audioContext.close();
  }
}

function mixToMono(audioBuffer: AudioBuffer): Float32Array {
  const { numberOfChannels, length } = audioBuffer;
  const mono = new Float32Array(length);
  for (let channel = 0; channel < numberOfChannels; channel += 1) {
    const data = audioBuffer.getChannelData(channel);
    for (let i = 0; i < length; i += 1) {
      mono[i] += data[i] / numberOfChannels;
    }
  }
  return mono;
}

function buildEnergyEnvelope(samples: Float32Array, sampleRate: number): number[] {
  const frameSize = Math.max(256, Math.floor(sampleRate * FRAME_SECONDS));
  const hopSize = Math.max(128, Math.floor(sampleRate * HOP_SECONDS));
  const envelope: number[] = [];
  for (let start = 0; start + frameSize <= samples.length; start += hopSize) {
    let sumSquares = 0;
    for (let i = start; i < start + frameSize; i += 1) {
      sumSquares += samples[i] * samples[i];
    }
    envelope.push(Math.sqrt(sumSquares / frameSize));
  }
  return envelope;
}

function estimateSyllablesPerMinute(envelope: number[], threshold: number, durationSeconds: number): number {
  if (durationSeconds <= 0 || envelope.length < 3) return 0;
  let peaks = 0;
  let lastPeak = -Infinity;
  const minimumGapFrames = Math.ceil(0.12 / HOP_SECONDS);
  for (let i = 1; i < envelope.length - 1; i += 1) {
    const value = envelope[i];
    if (value > threshold && value > envelope[i - 1] && value >= envelope[i + 1] && i - lastPeak >= minimumGapFrames) {
      peaks += 1;
      lastPeak = i;
    }
  }
  return (peaks / durationSeconds) * 60;
}

function estimatePitchTrack(samples: Float32Array, sampleRate: number, envelope: number[], threshold: number): number[] {
  const frameSize = Math.floor(sampleRate * 0.046);
  const hopSize = Math.floor(sampleRate * 0.22);
  const pitches: number[] = [];
  const maxFrames = 140;

  for (let start = 0; start + frameSize <= samples.length && pitches.length < maxFrames; start += hopSize) {
    const envelopeIndex = Math.floor(start / (sampleRate * HOP_SECONDS));
    if (envelope[envelopeIndex] !== undefined && envelope[envelopeIndex] < threshold) continue;
    const frame = samples.subarray(start, start + frameSize);
    const pitch = estimatePitch(frame, sampleRate);
    if (pitch !== null) pitches.push(pitch);
  }

  return pitches;
}

function estimatePitch(frame: Float32Array, sampleRate: number): number | null {
  const minHz = 70;
  const maxHz = 360;
  const minLag = Math.floor(sampleRate / maxHz);
  const maxLag = Math.floor(sampleRate / minHz);
  let bestLag = -1;
  let bestCorrelation = 0;
  let frameEnergy = 0;

  for (let i = 0; i < frame.length; i += 1) {
    frameEnergy += frame[i] * frame[i];
  }
  if (frameEnergy < 0.001) return null;

  for (let lag = minLag; lag <= maxLag; lag += 1) {
    let correlation = 0;
    let lagEnergy = 0;
    for (let i = 0; i < frame.length - lag; i += 1) {
      correlation += frame[i] * frame[i + lag];
      lagEnergy += frame[i + lag] * frame[i + lag];
    }
    const normalized = correlation / Math.sqrt(frameEnergy * Math.max(lagEnergy, 1e-9));
    if (normalized > bestCorrelation) {
      bestCorrelation = normalized;
      bestLag = lag;
    }
  }

  if (bestLag < 0 || bestCorrelation < 0.34) return null;
  return sampleRate / bestLag;
}

function normalizeWaveform(envelope: number[], buckets: number): number[] {
  if (envelope.length === 0) return Array.from({ length: buckets }, () => 0.08);
  const bucketSize = envelope.length / buckets;
  const max = Math.max(...envelope, 0.001);
  return Array.from({ length: buckets }, (_, bucket) => {
    const start = Math.floor(bucket * bucketSize);
    const end = Math.max(start + 1, Math.floor((bucket + 1) * bucketSize));
    const value = mean(envelope.slice(start, end)) / max;
    return clamp(value, 0.04, 1);
  });
}

function inferTone(energy: VoiceProfile['energy'], pauseRatio: number, intonation: VoiceProfile['intonation']): string {
  if (energy === 'High' && intonation === 'Expressive') return 'Bright, animated broadcast tone';
  if (energy === 'Low' && pauseRatio > 0.45) return 'Calm, deliberate studio tone';
  if (intonation === 'Flat') return 'Controlled, even delivery';
  return 'Natural conversational tone';
}

function inferEmotion(energy: VoiceProfile['energy'], intonation: VoiceProfile['intonation'], wordsPerMinute: number): string {
  if (energy === 'High' && wordsPerMinute > 175) return 'Excited and urgent';
  if (intonation === 'Expressive') return 'Engaged and reactive';
  if (energy === 'Low') return 'Composed';
  return 'Confident and balanced';
}

function inferAccent(language: string): string {
  const normalized = language.toLowerCase();
  if (normalized.includes('australian') || normalized.includes('en-au')) return 'Australian English accent target';
  if (normalized.includes('british') || normalized.includes('en-gb') || normalized.includes('uk')) return 'British English accent target';
  if (normalized.includes('american') || normalized.includes('en-us') || normalized.includes('us english')) return 'American English accent target';
  if (normalized.includes('spanish') || normalized.startsWith('es')) return 'Spanish-language accent target';
  if (normalized.includes('french') || normalized.startsWith('fr')) return 'French-language accent target';
  if (normalized.includes('arabic') || normalized.startsWith('ar')) return 'Arabic-language accent target';
  if (!normalized.trim()) return 'Language not specified; accent handled by voice provider';
  return `${language} accent target`;
}

function pronunciationNote(language: string): string {
  if (!language.trim()) return 'Preserve pronunciation from uploaded sample where provider permits.';
  return `Prioritize ${language} pronunciation while matching sample rhythm and vowels.`;
}

function percentile(values: number[], quantile: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = clamp(quantile, 0, 1) * (sorted.length - 1);
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower];
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (index - lower);
}

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}
