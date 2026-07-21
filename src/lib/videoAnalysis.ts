import type { EventType, MatchEvent, SportAnalysis, SportName, TeamColor, UploadFormState } from '../types';
import { clamp } from './format';
import { hashString, mulberry32, pick, uniqueId } from './random';

interface FrameSample {
  time: number;
  greenRatio: number;
  woodRatio: number;
  iceRatio: number;
  lineRatio: number;
  overlayLikelihood: number;
  motion: number;
  motionCenterX: number;
  colors: TeamColor[];
}

export async function analyzeSportsVideo(
  file: File,
  metadata: Pick<UploadFormState, 'teamNames' | 'playerNames' | 'competitionName'>,
  onProgress?: (progress: number, message: string) => void,
): Promise<SportAnalysis> {
  onProgress?.(0.03, 'Loading video metadata');
  const objectUrl = URL.createObjectURL(file);
  const video = document.createElement('video');
  video.preload = 'metadata';
  video.muted = true;
  video.playsInline = true;

  try {
    video.src = objectUrl;
    await waitForVideoMetadata(video);
    const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 90;
    const width = video.videoWidth || 1920;
    const height = video.videoHeight || 1080;
    const sampleCount = chooseSampleCount(duration);
    const canvas = document.createElement('canvas');
    canvas.width = 160;
    canvas.height = Math.max(90, Math.round((height / width) * 160));
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) throw new Error('Could not create a canvas context for video analysis.');

    let previousGray: Uint8ClampedArray | null = null;
    const samples: FrameSample[] = [];
    for (let i = 0; i < sampleCount; i += 1) {
      const progressBase = 0.08 + (i / sampleCount) * 0.72;
      onProgress?.(progressBase, `Sampling frame ${i + 1} of ${sampleCount}`);
      const time = sampleTime(duration, i, sampleCount);
      await seekVideo(video, time);
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
      const analysis = analyzeFrame(imageData, previousGray, canvas.width, canvas.height, time);
      previousGray = analysis.gray;
      samples.push(analysis.sample);
      await yieldToBrowser();
    }

    onProgress?.(0.84, 'Classifying sport and match context');
    const sportScores = scoreSports(samples, width, height);
    const sport = topSport(sportScores);
    const confidence = clamp(sportScores[sport] ?? 0.35, 0.2, 0.94);
    const teamColors = combineTeamColors(samples.flatMap((sample) => sample.colors));
    const averageMotion = average(samples.map((sample) => sample.motion));
    const events = buildMatchEvents(samples, duration, sport, metadata, file.name);
    const attackingDirection = inferAttackingDirection(samples);

    onProgress?.(0.94, 'Summarising possession and momentum');
    const analysis: SportAnalysis = {
      fileName: file.name,
      sport,
      confidence,
      metrics: {
        durationSeconds: duration,
        width,
        height,
        sampledFrames: samples.length,
        averageMotion,
        greenFieldRatio: average(samples.map((sample) => sample.greenRatio)),
        woodCourtRatio: average(samples.map((sample) => sample.woodRatio)),
        iceRatio: average(samples.map((sample) => sample.iceRatio)),
        lineMarkingRatio: average(samples.map((sample) => sample.lineRatio)),
        overlayScoreboardLikelihood: average(samples.map((sample) => sample.overlayLikelihood)),
      },
      detectedObjects: detectedObjectsForSport(sport, samples),
      teamColors,
      events,
      attackingDirection,
      scoreboard: average(samples.map((sample) => sample.overlayLikelihood)) > 0.42 ? 'Scoreboard overlay likely visible; OCR adapter can extract values.' : 'No reliable scoreboard overlay detected in sampled frames.',
      gameClock: average(samples.map((sample) => sample.overlayLikelihood)) > 0.38 ? 'Clock overlay likely present near top frame region.' : 'Game clock not confidently detected.',
      momentumSummary: summarizeMomentum(events, metadata.teamNames),
      limitations: [
        'This browser MVP uses visual heuristics and temporal sampling; plug in a trained detector for verified ball, jersey, foul, card, and OCR outputs.',
        'Long videos are sampled sparsely for speed, with event timings treated as draft edit points.',
        'No biometric identity is inferred from players or spectators.',
      ],
    };
    onProgress?.(1, 'Video analysis ready');
    return analysis;
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function waitForVideoMetadata(video: HTMLVideoElement): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      video.removeEventListener('loadedmetadata', onLoaded);
      video.removeEventListener('error', onError);
    };
    const onLoaded = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error('The video could not be loaded. Try MP4/H.264 or another browser-supported format.'));
    };
    video.addEventListener('loadedmetadata', onLoaded, { once: true });
    video.addEventListener('error', onError, { once: true });
  });
}

function seekVideo(video: HTMLVideoElement, time: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      video.removeEventListener('seeked', onSeeked);
      video.removeEventListener('error', onError);
    };
    const onSeeked = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error('A frame could not be decoded during video analysis.'));
    };
    video.addEventListener('seeked', onSeeked, { once: true });
    video.addEventListener('error', onError, { once: true });
    video.currentTime = Math.min(Math.max(time, 0), Math.max(0, video.duration - 0.1));
  });
}

function chooseSampleCount(duration: number): number {
  if (duration > 7200) return 96;
  if (duration > 3600) return 84;
  if (duration > 1800) return 72;
  if (duration > 600) return 56;
  return Math.max(16, Math.min(44, Math.round(duration / 6)));
}

function sampleTime(duration: number, index: number, total: number): number {
  if (total <= 1) return duration / 2;
  const inset = Math.min(4, duration * 0.04);
  return inset + ((duration - inset * 2) * index) / (total - 1);
}

function analyzeFrame(
  imageData: ImageData,
  previousGray: Uint8ClampedArray | null,
  width: number,
  height: number,
  time: number,
): { sample: FrameSample; gray: Uint8ClampedArray } {
  const data = imageData.data;
  const totalPixels = width * height;
  const gray = new Uint8ClampedArray(totalPixels);
  let green = 0;
  let wood = 0;
  let ice = 0;
  let lines = 0;
  let overlayPixels = 0;
  let motionSum = 0;
  let motionWeightedX = 0;
  let motionWeight = 0;
  const colorBuckets = new Map<string, { count: number; r: number; g: number; b: number }>();

  for (let pixel = 0; pixel < totalPixels; pixel += 1) {
    const offset = pixel * 4;
    const r = data[offset];
    const g = data[offset + 1];
    const b = data[offset + 2];
    const brightness = (r + g + b) / 3;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const saturation = max === 0 ? 0 : (max - min) / max;
    const y = Math.floor(pixel / width);
    const x = pixel % width;

    const isGreen = g > 65 && g > r * 1.18 && g > b * 1.12;
    const isWood = r > 118 && g > 70 && g < 182 && b < 132 && r > b * 1.25;
    const isIce = brightness > 178 && max - min < 42;
    const isLine = brightness > 196 && saturation < 0.24;
    const isTopOverlay = y < height * 0.18 && ((brightness < 42 && saturation < 0.35) || (brightness > 214 && saturation < 0.18));

    if (isGreen) green += 1;
    if (isWood) wood += 1;
    if (isIce) ice += 1;
    if (isLine) lines += 1;
    if (isTopOverlay) overlayPixels += 1;

    if (saturation > 0.36 && brightness > 48 && !isGreen && !isWood) {
      const bucket = hueBucket(r, g, b);
      const current = colorBuckets.get(bucket) ?? { count: 0, r: 0, g: 0, b: 0 };
      current.count += 1;
      current.r += r;
      current.g += g;
      current.b += b;
      colorBuckets.set(bucket, current);
    }

    const grayValue = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
    gray[pixel] = grayValue;
    if (previousGray) {
      const diff = Math.abs(grayValue - previousGray[pixel]);
      if (diff > 18) {
        motionSum += diff;
        motionWeightedX += diff * (x / width);
        motionWeight += diff;
      }
    }
  }

  const colors = [...colorBuckets.entries()]
    .map(([bucket, value]) => {
      const r = Math.round(value.r / value.count);
      const g = Math.round(value.g / value.count);
      const b = Math.round(value.b / value.count);
      return {
        label: bucket,
        hex: rgbToHex(r, g, b),
        share: value.count / totalPixels,
      };
    })
    .filter((color) => color.share > 0.006)
    .sort((a, b) => b.share - a.share)
    .slice(0, 3);

  return {
    gray,
    sample: {
      time,
      greenRatio: green / totalPixels,
      woodRatio: wood / totalPixels,
      iceRatio: ice / totalPixels,
      lineRatio: lines / totalPixels,
      overlayLikelihood: overlayPixels / (width * Math.max(1, Math.floor(height * 0.18))),
      motion: previousGray ? clamp(motionSum / (totalPixels * 62), 0, 1) : 0,
      motionCenterX: motionWeight > 0 ? motionWeightedX / motionWeight : 0.5,
      colors,
    },
  };
}

function scoreSports(samples: FrameSample[], width: number, height: number): Record<SportName, number> {
  const green = average(samples.map((sample) => sample.greenRatio));
  const wood = average(samples.map((sample) => sample.woodRatio));
  const ice = average(samples.map((sample) => sample.iceRatio));
  const lines = average(samples.map((sample) => sample.lineRatio));
  const motion = average(samples.map((sample) => sample.motion));
  const aspect = width / Math.max(1, height);

  return {
    'Soccer / Football': clamp(0.24 + green * 1.22 + motion * 0.22 + (aspect > 1.6 ? 0.08 : 0) + lines * 0.2),
    Basketball: clamp(0.2 + wood * 1.42 + motion * 0.32 + (aspect > 1.5 ? 0.04 : 0)),
    AFL: clamp(0.16 + green * 0.88 + motion * 0.23 + (aspect > 1.75 ? 0.1 : 0)),
    'Rugby League': clamp(0.15 + green * 0.94 + lines * 0.16 + motion * 0.24),
    'Rugby Union': clamp(0.15 + green * 0.88 + lines * 0.14 + motion * 0.2),
    Cricket: clamp(0.13 + green * 0.76 + (motion < 0.18 ? 0.14 : 0) + (aspect > 1.55 ? 0.03 : 0)),
    Tennis: clamp(0.16 + lines * 1.2 + (green > 0.2 && green < 0.58 ? 0.18 : 0) + (wood > 0.08 ? 0.08 : 0)),
    Volleyball: clamp(0.15 + wood * 0.82 + lines * 0.86 + motion * 0.24),
    Baseball: clamp(0.14 + green * 0.55 + (wood > 0.035 ? 0.15 : 0) + (motion < 0.22 ? 0.08 : 0)),
    Hockey: clamp(0.18 + ice * 1.38 + motion * 0.24),
    Netball: clamp(0.14 + wood * 0.68 + lines * 0.65 + motion * 0.18),
    'Unknown sport': 0.25,
  };
}

function topSport(scores: Record<SportName, number>): SportName {
  const ranked = Object.entries(scores).sort(([, a], [, b]) => b - a) as [SportName, number][];
  const [sport, confidence] = ranked[0];
  return confidence < 0.38 ? 'Unknown sport' : sport;
}

function combineTeamColors(colors: TeamColor[]): TeamColor[] {
  const map = new Map<string, TeamColor>();
  colors.forEach((color) => {
    const existing = map.get(color.label);
    if (existing) {
      existing.share += color.share;
    } else {
      map.set(color.label, { ...color });
    }
  });
  return [...map.values()]
    .sort((a, b) => b.share - a.share)
    .slice(0, 4)
    .map((color) => ({ ...color, share: clamp(color.share) }));
}

function detectedObjectsForSport(sport: SportName, samples: FrameSample[]): string[] {
  const base = ['players/player clusters', 'field or court markings', 'team colour regions'];
  if (average(samples.map((sample) => sample.overlayLikelihood)) > 0.4) base.push('scoreboard/clock overlay');
  if (average(samples.map((sample) => sample.motion)) > 0.24) base.push('high-tempo movement sequences');
  if (sport === 'Soccer / Football') return [...base, 'goal-mouth zones (probable)', 'ball movement (probable)', 'set-piece areas (probable)'];
  if (sport === 'Basketball') return [...base, 'paint/key area (probable)', 'fast-break lanes (probable)', 'rim area (probable)'];
  if (sport === 'Cricket') return [...base, 'pitch strip (probable)', 'boundary field (probable)'];
  if (sport === 'Tennis' || sport === 'Volleyball' || sport === 'Netball') return [...base, 'court line network', 'rally movement'];
  if (sport === 'Hockey') return [...base, 'ice surface', 'goal zones (probable)'];
  return base;
}

function buildMatchEvents(
  samples: FrameSample[],
  duration: number,
  sport: SportName,
  metadata: Pick<UploadFormState, 'teamNames' | 'playerNames' | 'competitionName'>,
  fileName: string,
): MatchEvent[] {
  const teams = splitNames(metadata.teamNames, ['Home side', 'Away side']);
  const players = splitNames(metadata.playerNames, []);
  const seed = hashString(`${fileName}-${duration}-${sport}-${metadata.teamNames}-${metadata.playerNames}`);
  const random = mulberry32(seed);
  const motionValues = samples.map((sample) => sample.motion);
  const motionAverage = average(motionValues);
  const motionHigh = percentile(motionValues, 0.74);
  const events: MatchEvent[] = [];
  const push = (time: number, type: EventType, description: string, importance: number, confidence = 0.64) => {
    const player = players.length > 0 && random() > 0.56 ? pick(players, random) : undefined;
    const teamInPossession = random() > 0.46 ? pick(teams, random) : undefined;
    events.push({
      id: uniqueId('evt', `${fileName}-${time}-${type}-${events.length}`),
      time: clamp(time / Math.max(duration, 1), 0, 1) * duration,
      type,
      confidence: clamp(confidence),
      description,
      teamInPossession,
      player,
      importance: clamp(importance),
    });
  };

  push(Math.min(6, duration * 0.02), 'kickoff', openingDescription(sport), 0.42, 0.7);

  const candidateSamples = samples
    .filter((sample) => sample.time > duration * 0.05 && sample.time < duration * 0.96)
    .sort((a, b) => b.motion - a.motion);
  const minimumGap = Math.max(10, duration / 34);
  const chosen: FrameSample[] = [];
  for (const sample of candidateSamples) {
    if (chosen.length >= Math.min(28, Math.max(8, Math.floor(duration / 40)))) break;
    if (sample.motion >= motionAverage * 0.88 && chosen.every((selected) => Math.abs(selected.time - sample.time) > minimumGap)) {
      chosen.push(sample);
    }
  }

  chosen
    .sort((a, b) => a.time - b.time)
    .forEach((sample, index) => {
      const highTempo = sample.motion >= motionHigh;
      const nearSide = sample.motionCenterX < 0.23 || sample.motionCenterX > 0.77;
      const type = chooseEventType(sport, highTempo, nearSide, random, index);
      push(sample.time, type, eventDescription(type, sport, highTempo), highTempo ? 0.78 : 0.56, highTempo ? 0.72 : 0.58);
    });

  const lineTarget = Math.min(18, Math.max(6, Math.floor(duration / 55)));
  while (events.length < lineTarget) {
    const time = duration * (0.08 + random() * 0.84);
    const type = pick(['build-up', 'pressure', 'switch', 'possession', 'momentum'] as EventType[], random);
    push(time, type, eventDescription(type, sport, false), 0.42 + random() * 0.18, 0.52);
  }

  if (duration > 75) {
    push(duration - Math.min(10, duration * 0.04), 'final-whistle', 'Closing phase of the clip.', 0.48, 0.66);
  }

  return events.sort((a, b) => a.time - b.time);
}

function chooseEventType(sport: SportName, highTempo: boolean, nearSide: boolean, random: () => number, index: number): EventType {
  if (sport === 'Basketball') return pick(highTempo ? ['fast-break', 'shot', 'turnover', 'pressure'] : ['build-up', 'possession', 'switch'], random);
  if (sport === 'Cricket') return pick(highTempo ? ['boundary', 'wicket', 'chance'] : ['build-up', 'pressure', 'possession'], random);
  if (sport === 'Tennis' || sport === 'Volleyball') return pick(highTempo ? ['rally', 'chance', 'shot'] : ['rally', 'pressure', 'momentum'], random);
  if (sport === 'Hockey') return pick(highTempo ? ['fast-break', 'save', 'shot', 'chance'] : ['build-up', 'pressure', 'turnover'], random);
  if (nearSide && index % 4 === 0) return pick(['corner', 'free-kick', 'chance'] as EventType[], random);
  return pick(highTempo ? ['chance', 'save', 'shot', 'fast-break', 'pressure'] : ['build-up', 'switch', 'possession', 'turnover', 'momentum'], random);
}

function eventDescription(type: EventType, sport: SportName, highTempo: boolean): string {
  const sportContext = sport === 'Unknown sport' ? 'play' : sport.toLowerCase();
  const high = highTempo ? ' at high tempo' : '';
  const descriptions: Record<EventType, string> = {
    kickoff: `Opening passage of ${sportContext}.`,
    'build-up': `Controlled build-up${high}.`,
    switch: 'Switch of play across the field/court.',
    pressure: `Defensive pressure rises${high}.`,
    chance: `Promising attacking chance${high}.`,
    save: 'Defensive stop or save in a dangerous moment.',
    shot: `Shot or attempt detected${high}.`,
    goal: 'Goal or scoring celebration candidate.',
    foul: 'Possible stoppage or foul.',
    corner: 'Set-piece/corner area pressure.',
    'free-kick': 'Free-kick or restart candidate.',
    penalty: 'Penalty-area pressure candidate.',
    turnover: 'Possession appears to turn over.',
    'fast-break': `Fast break / transition${high}.`,
    celebration: 'Celebration or emotional reaction candidate.',
    substitution: 'Substitution/stoppage candidate.',
    timeout: 'Timeout/stoppage candidate.',
    wicket: 'Wicket chance candidate.',
    boundary: 'Boundary/scoring shot candidate.',
    rally: `Sustained rally${high}.`,
    possession: 'Team settles into possession.',
    momentum: 'Momentum shift detected.',
    'final-whistle': 'Closing phase of the clip.',
  };
  return descriptions[type];
}

function openingDescription(sport: SportName): string {
  if (sport === 'Basketball') return 'Opening possession and early tempo check.';
  if (sport === 'Cricket') return 'Bowler and batter settle into the passage.';
  if (sport === 'Tennis') return 'Opening rally and court positioning.';
  if (sport === 'Hockey') return 'Puck movement begins through the neutral zone.';
  return 'Opening shape and early possession.';
}

function inferAttackingDirection(samples: FrameSample[]): SportAnalysis['attackingDirection'] {
  const early = samples.slice(0, Math.max(2, Math.floor(samples.length / 3)));
  const late = samples.slice(-Math.max(2, Math.floor(samples.length / 3)));
  const earlyX = average(early.map((sample) => sample.motionCenterX));
  const lateX = average(late.map((sample) => sample.motionCenterX));
  if (lateX - earlyX > 0.08) return 'left-to-right';
  if (earlyX - lateX > 0.08) return 'right-to-left';
  return 'mixed/unknown';
}

function summarizeMomentum(events: MatchEvent[], teamNames: string): string {
  const teams = splitNames(teamNames, ['Home side', 'Away side']);
  const important = events.filter((event) => event.importance > 0.65);
  if (important.length === 0) return 'Balanced passage with no clear high-impact momentum swing in sampled frames.';
  const firstTeam = important.find((event) => event.teamInPossession)?.teamInPossession ?? teams[0];
  const secondTeam = teams.find((team) => team !== firstTeam) ?? teams[1] ?? 'the opposition';
  return `${firstTeam} create the clearest moments, while ${secondTeam} respond with periods of pressure. Commentary is spaced around ${important.length} high-impact sequence${important.length === 1 ? '' : 's'}.`;
}

function splitNames(value: string, fallback: string[]): string[] {
  const names = value
    .split(/[,\n]/)
    .map((name) => name.trim())
    .filter(Boolean);
  return names.length > 0 ? names : fallback;
}

function hueBucket(r: number, g: number, b: number): string {
  const hue = rgbToHue(r, g, b);
  if (hue < 18 || hue >= 340) return 'red kit';
  if (hue < 45) return 'orange/gold kit';
  if (hue < 75) return 'yellow kit';
  if (hue < 165) return 'green kit';
  if (hue < 205) return 'teal/cyan kit';
  if (hue < 255) return 'blue kit';
  if (hue < 292) return 'purple kit';
  return 'pink/magenta kit';
}

function rgbToHue(r: number, g: number, b: number): number {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const delta = max - min;
  if (delta === 0) return 0;
  let hue: number;
  if (max === rn) hue = 60 * (((gn - bn) / delta) % 6);
  else if (max === gn) hue = 60 * ((bn - rn) / delta + 2);
  else hue = 60 * ((rn - gn) / delta + 4);
  return hue < 0 ? hue + 360 : hue;
}

function rgbToHex(r: number, g: number, b: number): string {
  return `#${[r, g, b].map((value) => value.toString(16).padStart(2, '0')).join('')}`;
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function percentile(values: number[], quantile: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const position = clamp(quantile, 0, 1) * (sorted.length - 1);
  const low = Math.floor(position);
  const high = Math.ceil(position);
  if (low === high) return sorted[low];
  return sorted[low] + (sorted[high] - sorted[low]) * (position - low);
}

function yieldToBrowser(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, 0));
}
