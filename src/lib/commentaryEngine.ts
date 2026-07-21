import type {
  CommentaryFrequency,
  CommentaryStyle,
  EventType,
  MatchEvent,
  ScriptLine,
  SportAnalysis,
  UploadFormState,
  VoiceProfile,
} from '../types';
import { clamp } from './format';
import { hashString, mulberry32, pick, uniqueId } from './random';

interface CommentaryContext {
  style: CommentaryStyle;
  frequency: CommentaryFrequency;
  teams: string[];
  players: string[];
  competitionName: string;
  sport: string;
  voiceProfile?: VoiceProfile | null;
}

const DEFAULT_TEAMS = ['the home side', 'the visitors'];

export function generateCommentaryScript(
  upload: UploadFormState,
  analysis: SportAnalysis,
  voiceProfile: VoiceProfile | null,
): ScriptLine[] {
  const context: CommentaryContext = {
    style: upload.style,
    frequency: upload.frequency,
    teams: splitNames(upload.teamNames, DEFAULT_TEAMS),
    players: splitNames(upload.playerNames, []),
    competitionName: upload.competitionName.trim(),
    sport: analysis.sport,
    voiceProfile,
  };
  const seed = hashString(`${analysis.fileName}-${analysis.sport}-${upload.teamNames}-${upload.playerNames}-${upload.style}-${upload.frequency}`);
  const random = mulberry32(seed);
  const duration = analysis.metrics.durationSeconds;
  const selectedEvents = selectEvents(analysis.events, duration, upload.frequency);
  const lines: ScriptLine[] = [];
  let lastText = '';

  selectedEvents.forEach((event, index) => {
    const text = createLine(event, context, random, index, lastText);
    lastText = text;
    const durationEstimate = estimateLineDuration(text, voiceProfile);
    lines.push({
      id: uniqueId('line', `${event.id}-${index}-${text}`),
      time: Math.max(0, event.time - (event.importance > 0.72 ? 0.4 : 0)),
      duration: durationEstimate,
      eventType: event.type,
      text,
      emphasis: event.importance > 0.72 ? 'high' : event.importance > 0.5 ? 'medium' : 'low',
      providerStatus: 'needs-synthesis',
    });
  });

  return removeCrowding(lines, duration);
}

export function regenerateScriptLine(
  line: ScriptLine,
  upload: UploadFormState,
  analysis: SportAnalysis,
  voiceProfile: VoiceProfile | null,
  variantOffset = 1,
): ScriptLine {
  const matchingEvent = analysis.events.find((event) => Math.abs(event.time - line.time) < Math.max(4, line.duration + 2)) ?? {
    id: line.id,
    time: line.time,
    type: line.eventType,
    confidence: 0.6,
    description: 'Edited line',
    importance: line.emphasis === 'high' ? 0.8 : line.emphasis === 'medium' ? 0.58 : 0.38,
  } satisfies MatchEvent;
  const seed = hashString(`${line.id}-${line.text}-${variantOffset}`);
  const random = mulberry32(seed);
  const context: CommentaryContext = {
    style: upload.style,
    frequency: upload.frequency,
    teams: splitNames(upload.teamNames, DEFAULT_TEAMS),
    players: splitNames(upload.playerNames, []),
    competitionName: upload.competitionName.trim(),
    sport: analysis.sport,
    voiceProfile,
  };
  const text = createLine(matchingEvent, context, random, variantOffset, line.text);
  return {
    ...line,
    id: uniqueId('line', `${line.id}-${variantOffset}-${text}`),
    text,
    duration: estimateLineDuration(text, voiceProfile),
    providerStatus: 'needs-synthesis',
  };
}

function selectEvents(events: MatchEvent[], duration: number, frequency: CommentaryFrequency): MatchEvent[] {
  const targetSpacing = frequency === 'Minimal' ? 58 : frequency === 'Normal' ? 28 : 12;
  const maxLines = frequency === 'Minimal' ? 18 : frequency === 'Normal' ? 70 : 220;
  const targetLines = Math.min(maxLines, Math.max(4, Math.ceil(duration / targetSpacing)));
  const sorted = [...events].sort((a, b) => b.importance - a.importance || a.time - b.time);
  const selected: MatchEvent[] = [];
  const minimumGap = frequency === 'Minimal' ? 24 : frequency === 'Normal' ? 10 : 4.5;

  for (const event of sorted) {
    if (selected.length >= targetLines) break;
    if (selected.every((candidate) => Math.abs(candidate.time - event.time) > minimumGap)) {
      selected.push(event);
    }
  }

  if (frequency !== 'Minimal' && selected.length < targetLines && events.length > 0) {
    const ordered = [...events].sort((a, b) => a.time - b.time);
    for (const event of ordered) {
      if (selected.length >= targetLines) break;
      if (!selected.some((candidate) => candidate.id === event.id)) selected.push(event);
    }
  }

  return selected.sort((a, b) => a.time - b.time);
}

function createLine(
  event: MatchEvent,
  context: CommentaryContext,
  random: () => number,
  index: number,
  previousText: string,
): string {
  const team = event.teamInPossession ?? pick(context.teams, random);
  const opponent = context.teams.find((candidate) => candidate !== team) ?? 'the opposition';
  const player = event.player ?? (context.players.length > 0 && random() > 0.54 ? pick(context.players, random) : undefined);
  const name = player ?? team;
  const templates = templatesFor(event.type, context.style);
  let template = templates[index % templates.length];
  if (previousText) {
    const previousStart = previousText.split(' ').slice(0, 3).join(' ');
    const alternate = templates.find((candidate) => !candidate.startsWith(previousStart));
    if (alternate) template = alternate;
  }

  const competitionPrefix = context.competitionName && index === 0 ? `Here in the ${context.competitionName}, ` : '';
  const stylePrefix = styleLeadIn(context.style, event, random);
  const voiceCadence = context.voiceProfile?.speakingSpeed === 'Fast' ? '' : context.voiceProfile?.speakingSpeed === 'Slow' ? ' ' : '';
  const line = template
    .replaceAll('{team}', team)
    .replaceAll('{opponent}', opponent)
    .replaceAll('{player}', name)
    .replaceAll('{sport}', context.sport.toLowerCase())
    .replaceAll('{styleLead}', stylePrefix)
    .trim();

  return cleanLine(`${competitionPrefix}${voiceCadence}${line}`);
}

function templatesFor(eventType: EventType, style: CommentaryStyle): string[] {
  const common: Record<EventType, string[]> = {
    kickoff: [
      '{styleLead}We are underway, and both sides are just feeling their way into the contest.',
      '{styleLead}Early touches here as {team} look to settle into rhythm.',
      '{styleLead}A measured opening, with {team} trying to set the tone.',
    ],
    'build-up': [
      '{styleLead}{team} are patient in the build-up, waiting for the right gap to open.',
      '{styleLead}Good composure from {team}; they are not forcing it.',
      '{styleLead}This is tidy work from {team}, drawing {opponent} across.',
    ],
    switch: [
      '{styleLead}Excellent switch of play, and suddenly there is space to work with.',
      '{styleLead}{team} move it across quickly, changing the point of attack.',
      '{styleLead}That switch stretches {opponent} and opens up the far side.',
    ],
    pressure: [
      '{styleLead}Great pressure from the defence, and {team} have to solve this quickly.',
      '{styleLead}{opponent} are squeezing hard here; there is no easy outlet.',
      '{styleLead}The pressure is building, and this is where mistakes can appear.',
    ],
    chance: [
      '{styleLead}This is a real chance now — {player} has found a pocket of space.',
      '{styleLead}Danger here for {opponent}; {team} are asking serious questions.',
      '{styleLead}That could change the game if {team} make the final action count.',
    ],
    save: [
      '{styleLead}Fantastic save! That looked destined to make the breakthrough.',
      '{styleLead}Brilliant stop under pressure, and {opponent} survive the moment.',
      '{styleLead}That is exactly the kind of save that lifts a team.',
    ],
    shot: [
      '{styleLead}{player} gets the shot away, but the defence reacts sharply.',
      '{styleLead}An ambitious attempt from {team}, and it had the crowd interested.',
      '{styleLead}They worked the opening well, even if the finish was not quite there.',
    ],
    goal: [
      '{styleLead}What a finish! {team} have their moment and this place would be roaring.',
      '{styleLead}There it is — a clinical touch when it mattered most.',
      '{styleLead}{player} makes no mistake, and that could swing the whole contest.',
    ],
    foul: [
      '{styleLead}The whistle goes, and that will give everyone a chance to reset.',
      '{styleLead}A little too much contact there, and {team} earn the stoppage.',
      '{styleLead}The referee steps in; the tempo just breaks for a moment.',
    ],
    corner: [
      '{styleLead}Set-piece opportunity now, and {team} can load the dangerous areas.',
      '{styleLead}This corner is a chance to put real pressure on {opponent}.',
      '{styleLead}Bodies forward for {team}; delivery is everything from here.',
    ],
    'free-kick': [
      '{styleLead}Free kick in a useful position, and {team} will fancy this delivery.',
      '{styleLead}{opponent} need to stay organised as {team} prepare the restart.',
      '{styleLead}This is the sort of set piece that can decide tight games.',
    ],
    penalty: [
      '{styleLead}Huge moment if this is given — the pressure is immense.',
      '{styleLead}{team} are asking the question in the most dangerous area.',
      '{styleLead}All eyes on the official here; that could be decisive.',
    ],
    turnover: [
      '{styleLead}Turnover, and now {opponent} can break into open space.',
      '{styleLead}{team} lose it in a risky area, and the transition is on.',
      '{styleLead}That is sharp defensive work, turning pressure into possession.',
    ],
    'fast-break': [
      '{styleLead}Here they come on the break — {team} have numbers and pace.',
      '{styleLead}Fast transition from {team}, and {opponent} are scrambling back.',
      '{styleLead}This is where {team} can be so dangerous in open play.',
    ],
    celebration: [
      '{styleLead}You can feel the emotion in that reaction; it means plenty.',
      '{styleLead}The celebration tells you how important that moment felt.',
      '{styleLead}That has lifted the whole group, and the energy changes immediately.',
    ],
    substitution: [
      '{styleLead}A change is coming, and fresh legs might alter the rhythm.',
      '{styleLead}{team} look to the bench, trying to shift the momentum.',
      '{styleLead}This substitution could be about control as much as energy.',
    ],
    timeout: [
      '{styleLead}A pause in play, and both sides can take stock.',
      '{styleLead}The coaches get a moment to reset the plan.',
      '{styleLead}That stoppage comes at a useful time after a frantic spell.',
    ],
    wicket: [
      '{styleLead}Big appeal and a huge moment — that could be the wicket they wanted.',
      '{styleLead}The bowler has asked a serious question there.',
      '{styleLead}That chance could change the shape of the innings.',
    ],
    boundary: [
      '{styleLead}Timed beautifully, and that races away for a valuable score.',
      '{styleLead}{player} finds the gap and gets full value for the stroke.',
      '{styleLead}That is confident batting, picking the moment perfectly.',
    ],
    rally: [
      '{styleLead}Excellent rally, with both sides refusing to give up court position.',
      '{styleLead}{team} stay patient through the exchange and wait for the opening.',
      '{styleLead}The quality of this rally is lifting the tempo.',
    ],
    possession: [
      '{styleLead}{team} keep possession and slow the game to their preferred rhythm.',
      '{styleLead}They are protecting the ball well and making {opponent} chase.',
      '{styleLead}This is a smart spell of control from {team}.',
    ],
    momentum: [
      '{styleLead}Momentum is starting to tilt; {team} have had the better of this spell.',
      '{styleLead}You sense the game leaning toward {team} right now.',
      '{styleLead}The balance is shifting, and {opponent} need a response.',
    ],
    'final-whistle': [
      '{styleLead}That brings this passage to a close, with plenty for both sides to review.',
      '{styleLead}A compelling spell ends, and the key moments will be talked about.',
      '{styleLead}The final moments are managed well, and the contest winds down.',
    ],
  };

  const selected = common[eventType] ?? common['build-up'];
  if (style === 'Radio') {
    return selected.map((line) => `${line} We'll keep painting the picture as it develops.`);
  }
  if (style === 'Documentary') {
    return selected.map((line) => line.replace('{styleLead}', 'In this passage, '));
  }
  if (style === 'Calm') {
    return selected.map((line) => line.replace('!', '.').replace('Huge', 'Important'));
  }
  if (style === 'Excited') {
    return selected.map((line) => line.replace('.', '!'));
  }
  return selected;
}

function styleLeadIn(style: CommentaryStyle, event: MatchEvent, random: () => number): string {
  if (style === 'Australian commentator') return pick(['Goodness me, ', 'Have a look at this, ', 'That is clever footy — ', ''], random);
  if (style === 'British commentator') return pick(['Lovely bit of play, ', 'That is a proper test, ', 'Really tidy, ', ''], random);
  if (style === 'American commentator') return pick(['Right on cue, ', 'Big-time moment, ', 'That is clutch, ', ''], random);
  if (style === 'Professional') return event.importance > 0.7 ? 'Important moment: ' : '';
  if (style === 'TV Broadcast') return pick(['', 'Now then, ', 'Watch the movement here — '], random);
  if (style === 'Excited') return event.importance > 0.65 ? pick(['Oh, what a moment! ', 'Here we go! ', 'Listen to the noise! '], random) : '';
  if (style === 'Radio') return pick(['As we see it, ', 'For those just joining, ', 'Picture this: '], random);
  if (style === 'Documentary') return 'In the rhythm of the match, ';
  return '';
}

function cleanLine(line: string): string {
  return line
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.!?;:])/g, '$1')
    .replace(/—\s+—/g, '—')
    .trim();
}

function estimateLineDuration(text: string, voiceProfile: VoiceProfile | null): number {
  const words = text.split(/\s+/).filter(Boolean).length;
  const wpm = voiceProfile?.wordsPerMinuteEstimate && voiceProfile.wordsPerMinuteEstimate > 70 ? voiceProfile.wordsPerMinuteEstimate : 152;
  return clamp((words / wpm) * 60 + 0.35, 1.4, 7.5);
}

function removeCrowding(lines: ScriptLine[], duration: number): ScriptLine[] {
  const sorted = [...lines].sort((a, b) => a.time - b.time);
  const result: ScriptLine[] = [];
  for (const line of sorted) {
    const previous = result.at(-1);
    if (previous && line.time < previous.time + previous.duration + 1.2) {
      if (line.emphasis === 'high' && previous.emphasis !== 'high') {
        result[result.length - 1] = line;
      }
      continue;
    }
    if (line.time < duration - 0.5) result.push(line);
  }
  return result;
}

function splitNames(value: string, fallback: string[]): string[] {
  const names = value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return names.length > 0 ? names : fallback;
}
