import { useEffect, useMemo, useState } from 'react';
import type { ReactElement } from 'react';
import {
  Activity,
  BadgeCheck,
  Download,
  FileAudio,
  FileJson,
  FileText,
  Film,
  Mic2,
  Play,
  Radio,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  StopCircle,
  Subtitles,
  Trash2,
  Wand2,
} from 'lucide-react';
import './App.css';
import { FileDrop } from './components/FileDrop';
import { MetricCard } from './components/MetricCard';
import { PageNav } from './components/PageShell';
import { ProgressPanel } from './components/ProgressPanel';
import { Timeline } from './components/Timeline';
import { Waveform } from './components/Waveform';
import { generateCommentaryScript, regenerateScriptLine } from './lib/commentaryEngine';
import {
  buildPortableProjectJson,
  buildRenderManifest,
  buildSrt,
  buildTranscript,
  downloadBlobFile,
  downloadTextFile,
  projectBaseName,
} from './lib/exporters';
import { formatDuration, formatPercent } from './lib/format';
import { buildConsentReceipt } from './lib/providerContracts';
import { analyzeSportsVideo } from './lib/videoAnalysis';
import { analyzeVoiceSample } from './lib/voiceAnalysis';
import { synthesizeClonedCommentary } from './lib/voiceCloneApi';
import type {
  CommentaryFrequency,
  CommentaryStyle,
  PageId,
  ProcessingStep,
  ScriptLine,
  SportAnalysis,
  UploadFormState,
  VoiceProfile,
} from './types';

const commentaryStyles: CommentaryStyle[] = [
  'TV Broadcast',
  'Excited',
  'Professional',
  'Radio',
  'Calm',
  'Documentary',
  'Australian commentator',
  'British commentator',
  'American commentator',
];

const frequencies: CommentaryFrequency[] = ['Minimal', 'Normal', 'Constant'];

const initialUpload: UploadFormState = {
  videoFile: null,
  voiceFile: null,
  permissionConfirmed: false,
  teamNames: '',
  playerNames: '',
  competitionName: '',
  language: 'English',
  style: 'TV Broadcast',
  frequency: 'Normal',
};

const initialSteps: ProcessingStep[] = [
  { id: 'upload', label: 'Upload validation', progress: 0, status: 'waiting' },
  { id: 'voice', label: 'Voice analysis', progress: 0, status: 'waiting' },
  { id: 'video', label: 'Video analysis', progress: 0, status: 'waiting' },
  { id: 'commentary', label: 'Commentary generation', progress: 0, status: 'waiting' },
  { id: 'audio', label: 'Audio synthesis timing plan', progress: 0, status: 'waiting' },
  { id: 'export', label: 'Export preparation', progress: 0, status: 'waiting' },
];

function App() {
  const [page, setPage] = useState<PageId>('home');
  const [upload, setUpload] = useState<UploadFormState>(initialUpload);
  const [voiceProfile, setVoiceProfile] = useState<VoiceProfile | null>(null);
  const [sportAnalysis, setSportAnalysis] = useState<SportAnalysis | null>(null);
  const [script, setScript] = useState<ScriptLine[]>([]);
  const [steps, setSteps] = useState<ProcessingStep[]>(initialSteps);
  const [statusMessage, setStatusMessage] = useState('Ready for a new project.');
  const [error, setError] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [selectedLineId, setSelectedLineId] = useState<string | undefined>();
  const [lineVariantCounter, setLineVariantCounter] = useState(1);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isSynthesizing, setIsSynthesizing] = useState(false);

  const videoUrl = useObjectUrl(upload.videoFile);
  const canOpenWorkflow = Boolean(voiceProfile || sportAnalysis || script.length > 0 || isProcessing);
  const selectedLine = script.find((line) => line.id === selectedLineId) ?? script[0];
  const projectName = projectBaseName(upload);

  useEffect(() => {
    return () => {
      window.speechSynthesis?.cancel();
    };
  }, []);

  const updateUpload = <K extends keyof UploadFormState>(key: K, value: UploadFormState[K]) => {
    setUpload((current) => ({ ...current, [key]: value }));
    if (key === 'videoFile' || key === 'voiceFile') {
      setVoiceProfile(null);
      setSportAnalysis(null);
      setScript([]);
      setSelectedLineId(undefined);
      setSteps(initialSteps);
      setStatusMessage('Source media changed. Run analysis again.');
      setError(null);
    }
  };

  const setStep = (id: string, patch: Partial<ProcessingStep>) => {
    setSteps((current) => current.map((step) => (step.id === id ? { ...step, ...patch } : step)));
  };

  const resetAnalysis = () => {
    setUpload(initialUpload);
    setVoiceProfile(null);
    setSportAnalysis(null);
    setScript([]);
    setSelectedLineId(undefined);
    setSteps(initialSteps);
    setStatusMessage('Ready for a new project.');
    setError(null);
  };

  const startPipeline = async () => {
    if (!upload.videoFile) {
      setError('Upload a sports video before processing.');
      setPage('upload');
      return;
    }
    if (!upload.voiceFile) {
      setError('Upload an MP3/WAV voice sample before processing.');
      setPage('upload');
      return;
    }
    if (!upload.permissionConfirmed) {
      setError('Confirm speaker permission before voice analysis or synthesis.');
      setPage('upload');
      return;
    }

    setIsProcessing(true);
    setError(null);
    setSteps(initialSteps.map((step) => ({ ...step, progress: 0, status: step.id === 'upload' ? 'done' : 'waiting' })));
    setStatusMessage('Validation complete. Voice analysis starting.');
    setPage('voice');

    try {
      setStep('voice', { status: 'running', progress: 0.02 });
      const voice = await analyzeVoiceSample(upload.voiceFile, upload.permissionConfirmed, upload.language, (progress, message) => {
        setStep('voice', { status: 'running', progress });
        setStatusMessage(message);
      });
      setVoiceProfile(voice);
      setStep('voice', { status: 'done', progress: 1 });

      setPage('processing');
      setStep('video', { status: 'running', progress: 0.02 });
      setStatusMessage('Starting computer-vision analysis.');
      const videoAnalysis = await analyzeSportsVideo(upload.videoFile, upload, (progress, message) => {
        setStep('video', { status: 'running', progress });
        setStatusMessage(message);
      });
      setSportAnalysis(videoAnalysis);
      setStep('video', { status: 'done', progress: 1 });

      setStep('commentary', { status: 'running', progress: 0.35 });
      setStatusMessage('Writing broadcast-style commentary around high-value moments.');
      await shortDelay(350);
      const generated = generateCommentaryScript(upload, videoAnalysis, voice);
      setScript(generated);
      setSelectedLineId(generated[0]?.id);
      setStep('commentary', { status: 'done', progress: 1 });

      setStep('audio', { status: 'running', progress: 0.48 });
      setStatusMessage('Preparing consent receipt and synthesis timing manifest.');
      await shortDelay(320);
      buildConsentReceipt(voice);
      setStep('audio', { status: 'done', progress: 1 });

      setStep('export', { status: 'done', progress: 1 });
      setStatusMessage('Draft project ready. Connect a licensed voice provider for final cloned narration export.');
      setPage('preview');
    } catch (pipelineError) {
      const message = pipelineError instanceof Error ? pipelineError.message : 'Processing failed unexpectedly.';
      setError(message);
      setStatusMessage(message);
      setSteps((current) => current.map((step) => (step.status === 'running' ? { ...step, status: 'error' } : step)));
    } finally {
      setIsProcessing(false);
    }
  };

  const regenerateAll = () => {
    if (!sportAnalysis) return;
    const generated = generateCommentaryScript(upload, sportAnalysis, voiceProfile);
    setScript(generated);
    setSelectedLineId(generated[0]?.id);
    setStatusMessage('Commentary script regenerated.');
  };

  const regenerateLine = (lineId: string) => {
    if (!sportAnalysis) return;
    setLineVariantCounter((counter) => counter + 1);
    setScript((current) =>
      current.map((line) =>
        line.id === lineId ? regenerateScriptLine(line, upload, sportAnalysis, voiceProfile, lineVariantCounter + 1) : line,
      ),
    );
    setStatusMessage('Line regenerated.');
  };

  const updateLine = (lineId: string, patch: Partial<ScriptLine>) => {
    setScript((current) => current.map((line) => (line.id === lineId ? { ...line, ...patch, providerStatus: 'needs-synthesis' } : line)));
  };

  const deleteLine = (lineId: string) => {
    setScript((current) => current.filter((line) => line.id !== lineId));
    if (selectedLineId === lineId) setSelectedLineId(undefined);
  };

  const addLine = () => {
    const duration = sportAnalysis?.metrics.durationSeconds ?? 120;
    const time = selectedLine ? Math.min(duration - 2, selectedLine.time + selectedLine.duration + 3) : Math.min(10, duration / 2);
    const newLine: ScriptLine = {
      id: `line-custom-${Date.now()}`,
      time,
      duration: 3,
      eventType: 'momentum',
      text: 'Add your custom commentary line here.',
      emphasis: 'medium',
      providerStatus: 'needs-synthesis',
    };
    setScript((current) => [...current, newLine].sort((a, b) => a.time - b.time));
    setSelectedLineId(newLine.id);
  };

  const playBrowserPreview = () => {
    if (!('speechSynthesis' in window)) {
      setError('This browser does not support speech synthesis preview.');
      return;
    }
    if (isSpeaking) {
      window.speechSynthesis.cancel();
      setIsSpeaking(false);
      return;
    }
    const previewLines = script.slice(0, 8);
    if (previewLines.length === 0) return;
    setIsSpeaking(true);
    const utterance = new SpeechSynthesisUtterance(previewLines.map((line) => line.text).join(' '));
    utterance.rate = voiceProfile?.speakingSpeed === 'Fast' ? 1.08 : voiceProfile?.speakingSpeed === 'Slow' ? 0.88 : 0.98;
    utterance.pitch = voiceProfile?.averagePitchHz && voiceProfile.averagePitchHz > 190 ? 1.08 : 0.96;
    utterance.onend = () => setIsSpeaking(false);
    utterance.onerror = () => setIsSpeaking(false);
    window.speechSynthesis.speak(utterance);
  };

  const exportText = (kind: 'txt' | 'srt' | 'project' | 'manifest') => {
    if (kind === 'txt') {
      downloadTextFile(buildTranscript(script), `${projectName}-transcript.txt`);
    } else if (kind === 'srt') {
      downloadTextFile(buildSrt(script), `${projectName}-subtitles.srt`, 'application/x-subrip;charset=utf-8');
    } else if (kind === 'project') {
      downloadTextFile(buildPortableProjectJson(upload, voiceProfile, sportAnalysis, script), `${projectName}-project.json`, 'application/json;charset=utf-8');
    } else {
      downloadTextFile(buildRenderManifest(upload, voiceProfile, sportAnalysis, script), `${projectName}-render-manifest.json`, 'application/json;charset=utf-8');
    }
    setStep('export', { status: 'done', progress: 1 });
    setStatusMessage(`Exported ${kind.toUpperCase()} file.`);
  };

  const synthesizeClonedAudio = async () => {
    if (!voiceProfile || !upload.voiceFile) {
      setError('Run voice analysis with an approved voice sample before cloned audio synthesis.');
      setStatusMessage('Run voice analysis with an approved voice sample before cloned audio synthesis.');
      return;
    }
    if (!upload.permissionConfirmed || !voiceProfile.permissionConfirmed) {
      setError('Speaker permission must be confirmed before cloned audio synthesis.');
      setStatusMessage('Speaker permission must be confirmed before cloned audio synthesis.');
      return;
    }
    if (script.length === 0) {
      setError('Generate or add commentary lines before cloned audio synthesis.');
      setStatusMessage('Generate or add commentary lines before cloned audio synthesis.');
      return;
    }

    setIsSynthesizing(true);
    setError(null);
    setStep('audio', { status: 'running', progress: 0.18 });
    setStatusMessage('Sending consent receipt, voice sample, and script to the local XTTS voice cloning model.');
    try {
      const result = await synthesizeClonedCommentary(upload, voiceProfile, script);
      const fileName = `${projectName}-${result.fileName}`;
      downloadBlobFile(result.audioBlob, fileName);
      setScript((current) => current.map((line) => ({ ...line, providerStatus: 'synthesized' })));
      setStep('audio', { status: 'done', progress: 1 });
      setStep('export', { status: 'done', progress: 1 });
      setStatusMessage(`Generated cloned commentary WAV with ${result.providerName}.`);
    } catch (synthesisError) {
      const message = synthesisError instanceof Error ? synthesisError.message : 'Cloned voice synthesis failed.';
      setError(message);
      setStatusMessage(message);
      setStep('audio', { status: 'error', progress: 0 });
    } finally {
      setIsSynthesizing(false);
    }
  };

  const renderPage = () => {
    switch (page) {
      case 'home':
        return <HomePage onStart={() => setPage('upload')} />;
      case 'upload':
        return (
          <UploadPage
            upload={upload}
            updateUpload={updateUpload}
            onStart={startPipeline}
            onReset={resetAnalysis}
            isProcessing={isProcessing}
            error={error}
          />
        );
      case 'voice':
        return <VoicePage voiceProfile={voiceProfile} steps={steps} statusMessage={statusMessage} isProcessing={isProcessing} />;
      case 'processing':
        return <ProcessingPage analysis={sportAnalysis} steps={steps} statusMessage={statusMessage} />;
      case 'preview':
        return (
          <PreviewPage
            upload={upload}
            videoUrl={videoUrl}
            analysis={sportAnalysis}
            voiceProfile={voiceProfile}
            script={script}
            selectedLineId={selectedLine?.id}
            onSelectLine={setSelectedLineId}
            onPlayPreview={playBrowserPreview}
            isSpeaking={isSpeaking}
          />
        );
      case 'editor':
        return (
          <EditorPage
            script={script}
            analysis={sportAnalysis}
            selectedLineId={selectedLine?.id}
            onSelectLine={setSelectedLineId}
            onUpdateLine={updateLine}
            onRegenerateLine={regenerateLine}
            onDeleteLine={deleteLine}
            onAddLine={addLine}
            onRegenerateAll={regenerateAll}
          />
        );
      case 'export':
        return (
          <ExportPage
            upload={upload}
            voiceProfile={voiceProfile}
            analysis={sportAnalysis}
            script={script}
            steps={steps}
            onExport={exportText}
            onGenerateClonedAudio={synthesizeClonedAudio}
            isSynthesizing={isSynthesizing}
            statusMessage={statusMessage}
          />
        );
      default:
        return null;
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark">
            <Radio size={24} />
          </span>
          <div>
            <strong>AI Sports Commentary Generator</strong>
            <small>Consent-first broadcast narration studio</small>
          </div>
        </div>
        <PageNav page={page} setPage={setPage} canOpenWorkflow={canOpenWorkflow} />
      </header>
      <main>{renderPage()}</main>
      <footer className="app-footer">
        <span>Voice cloning requires explicit speaker permission.</span>
        <span>Computer vision outputs are draft edit cues until verified by a production model.</span>
      </footer>
    </div>
  );
}

function HomePage({ onStart }: { onStart: () => void }) {
  return (
    <section className="hero page-grid">
      <div className="hero__content panel panel--glow">
        <span className="eyebrow"><Sparkles size={16} /> AI broadcast workflow</span>
        <h1>Turn sports footage into natural, timed commentary.</h1>
        <p>
          Upload a match clip, add an approved voice sample, detect the flow of play, generate a professional script, edit the timeline,
          and export production-ready assets.
        </p>
        <div className="hero__actions">
          <button className="primary-button" type="button" onClick={onStart}>
            Start a project <Play size={18} />
          </button>
          <a className="secondary-button" href="#capabilities">
            View capabilities
          </a>
        </div>
      </div>
      <div className="panel scoreboard-card">
        <div className="scoreboard-card__top">
          <span>LIVE PREVIEW</span>
          <strong>Sports AI Studio</strong>
        </div>
        <div className="scoreboard-card__score">
          <span>HOME</span>
          <strong>2</strong>
          <em>:</em>
          <strong>1</strong>
          <span>AWAY</span>
        </div>
        <Waveform values={[0.3, 0.5, 0.2, 0.74, 0.66, 0.32, 0.9, 0.46, 0.24, 0.72, 0.42, 0.6, 0.2, 0.5]} />
        <p>“Fantastic save! That could change the game.”</p>
      </div>
      <div id="capabilities" className="feature-grid full-span">
        {[
          ['Upload', 'Sports video, voice sample, teams, players, language, style, and commentary frequency.'],
          ['Voice Analysis', 'Measures pitch, energy, speed, pauses, tone, intonation, and consent status.'],
          ['Sport Detection', 'Samples frames over time to infer field/court type, motion peaks, team colours, overlays, and events.'],
          ['Timeline Editing', 'Edit lines, regenerate individual moments, adjust timing, and export subtitles or transcript.'],
          ['Provider Ready', 'Includes consent-aware contracts for licensed voice synthesis and render providers.'],
          ['Broadcast Theme', 'Modern control-room UI with progress indicators for every workflow stage.'],
        ].map(([title, body]) => (
          <article className="feature-card" key={title}>
            <h3>{title}</h3>
            <p>{body}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

interface UploadPageProps {
  upload: UploadFormState;
  updateUpload: <K extends keyof UploadFormState>(key: K, value: UploadFormState[K]) => void;
  onStart: () => void;
  onReset: () => void;
  isProcessing: boolean;
  error: string | null;
}

function UploadPage({ upload, updateUpload, onStart, onReset, isProcessing, error }: UploadPageProps) {
  return (
    <section className="page-grid">
      <div className="panel page-heading full-span">
        <span className="eyebrow"><ShieldCheck size={16} /> Step 1 · Upload</span>
        <h2>Set up your commentary project</h2>
        <p>Upload a browser-supported sports video and an MP3/WAV voice sample from a speaker who has given permission.</p>
      </div>

      <div className="panel upload-panel">
        <FileDrop
          label="Sports video"
          description="MP4, MOV, AVI, WebM, or another browser-supported video file."
          accept="video/*,.mp4,.mov,.avi,.mkv,.webm"
          file={upload.videoFile}
          onChange={(file) => updateUpload('videoFile', file)}
        />
        <FileDrop
          label="Voice sample"
          description="MP3/WAV/M4A sample with clear speech. 30-60 seconds recommended."
          accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg"
          file={upload.voiceFile}
          onChange={(file) => updateUpload('voiceFile', file)}
        />
        <label className="consent-box">
          <input
            type="checkbox"
            checked={upload.permissionConfirmed}
            onChange={(event) => updateUpload('permissionConfirmed', event.target.checked)}
          />
          <span>
            <strong>I confirm the speaker has given permission.</strong>
            <small>The app will not analyze or synthesize a voice until this consent confirmation is checked.</small>
          </span>
        </label>
        {error ? <div className="error-box">{error}</div> : null}
      </div>

      <div className="panel form-panel">
        <div className="form-grid">
          <label>
            Team names
            <input
              value={upload.teamNames}
              placeholder="e.g. Falcons, City FC"
              onChange={(event) => updateUpload('teamNames', event.target.value)}
            />
          </label>
          <label>
            Player names
            <input
              value={upload.playerNames}
              placeholder="Comma-separated key players"
              onChange={(event) => updateUpload('playerNames', event.target.value)}
            />
          </label>
          <label>
            Competition name
            <input
              value={upload.competitionName}
              placeholder="e.g. Grand Final"
              onChange={(event) => updateUpload('competitionName', event.target.value)}
            />
          </label>
          <label>
            Language
            <input
              value={upload.language}
              placeholder="English, en-AU, Spanish..."
              onChange={(event) => updateUpload('language', event.target.value)}
            />
          </label>
          <label>
            Commentary style
            <select value={upload.style} onChange={(event) => updateUpload('style', event.target.value as CommentaryStyle)}>
              {commentaryStyles.map((style) => (
                <option key={style}>{style}</option>
              ))}
            </select>
          </label>
          <label>
            Commentary frequency
            <select value={upload.frequency} onChange={(event) => updateUpload('frequency', event.target.value as CommentaryFrequency)}>
              {frequencies.map((frequency) => (
                <option key={frequency}>{frequency}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="button-row">
          <button className="primary-button" type="button" onClick={onStart} disabled={isProcessing}>
            {isProcessing ? 'Processing…' : 'Analyze and generate'} <Wand2 size={18} />
          </button>
          <button className="secondary-button" type="button" onClick={onReset}>
            Reset project
          </button>
        </div>
      </div>
    </section>
  );
}

function VoicePage({ voiceProfile, steps, statusMessage, isProcessing }: { voiceProfile: VoiceProfile | null; steps: ProcessingStep[]; statusMessage: string; isProcessing: boolean }) {
  return (
    <section className="page-grid">
      <div className="panel page-heading full-span">
        <span className="eyebrow"><Mic2 size={16} /> Step 2 · Voice Analysis</span>
        <h2>Speaker profile and consent status</h2>
        <p>Acoustic analysis estimates vocal characteristics for a consent-aware synthesis provider.</p>
      </div>
      <div className="panel">
        <ProgressPanel steps={steps} statusMessage={statusMessage} />
      </div>
      <div className="panel">
        {voiceProfile ? (
          <>
            <div className="section-title">
              <h3>{voiceProfile.fileName}</h3>
              <span className="status-pill"><BadgeCheck size={14} /> Permission confirmed</span>
            </div>
            <Waveform values={voiceProfile.waveform} />
            <div className="metrics-grid">
              <MetricCard label="Accent" value={voiceProfile.accent} />
              <MetricCard label="Speaking speed" value={voiceProfile.speakingSpeed} detail={`${voiceProfile.wordsPerMinuteEstimate} WPM estimate`} />
              <MetricCard label="Tone" value={voiceProfile.tone} />
              <MetricCard label="Emotion" value={voiceProfile.emotion} />
              <MetricCard label="Pitch" value={voiceProfile.averagePitchHz ? `${voiceProfile.averagePitchHz} Hz` : 'Not detected'} detail={voiceProfile.pitchRangeHz ? `${voiceProfile.pitchRangeHz[0]}-${voiceProfile.pitchRangeHz[1]} Hz range` : undefined} />
              <MetricCard label="Pauses" value={formatPercent(voiceProfile.pauseRatio)} detail="Estimated pause/silence share" />
              <MetricCard label="Energy" value={voiceProfile.energy} />
              <MetricCard label="Intonation" value={voiceProfile.intonation} />
            </div>
            <ul className="note-list">
              {voiceProfile.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          </>
        ) : (
          <EmptyState icon={<FileAudio size={40} />} title={isProcessing ? 'Analyzing voice…' : 'No voice profile yet'} body="Start from the Upload page to analyze an approved voice sample." />
        )}
      </div>
    </section>
  );
}

function ProcessingPage({ analysis, steps, statusMessage }: { analysis: SportAnalysis | null; steps: ProcessingStep[]; statusMessage: string }) {
  return (
    <section className="page-grid">
      <div className="panel page-heading full-span">
        <span className="eyebrow"><Activity size={16} /> Steps 3-6 · Processing</span>
        <h2>Sport detection, match understanding, and script generation</h2>
        <p>Temporal sampling identifies likely sport context and high-value commentary moments.</p>
      </div>
      <div className="panel">
        <ProgressPanel steps={steps} statusMessage={statusMessage} />
      </div>
      <div className="panel">
        {analysis ? (
          <>
            <div className="section-title">
              <h3>{analysis.sport}</h3>
              <span className="status-pill">Confidence {formatPercent(analysis.confidence)}</span>
            </div>
            <div className="metrics-grid">
              <MetricCard label="Duration" value={formatDuration(analysis.metrics.durationSeconds)} />
              <MetricCard label="Resolution" value={`${analysis.metrics.width}×${analysis.metrics.height}`} />
              <MetricCard label="Frames sampled" value={analysis.metrics.sampledFrames} />
              <MetricCard label="Average motion" value={formatPercent(analysis.metrics.averageMotion)} />
              <MetricCard label="Field/court green" value={formatPercent(analysis.metrics.greenFieldRatio)} />
              <MetricCard label="Wood court" value={formatPercent(analysis.metrics.woodCourtRatio)} />
              <MetricCard label="Ice/white surface" value={formatPercent(analysis.metrics.iceRatio)} />
              <MetricCard label="Scoreboard overlay" value={formatPercent(analysis.metrics.overlayScoreboardLikelihood)} />
            </div>
            <Timeline duration={analysis.metrics.durationSeconds} events={analysis.events} script={[]} />
            <div className="two-column-list">
              <div>
                <h4>Detected features</h4>
                <ul className="tag-list">
                  {analysis.detectedObjects.map((object) => <li key={object}>{object}</li>)}
                </ul>
              </div>
              <div>
                <h4>Team colours</h4>
                <div className="color-list">
                  {analysis.teamColors.length > 0 ? analysis.teamColors.map((color) => (
                    <span key={color.label}>
                      <i style={{ background: color.hex }} /> {color.label} · {formatPercent(color.share)}
                    </span>
                  )) : <span className="muted">No clear kit colours detected.</span>}
                </div>
              </div>
            </div>
            <div className="analysis-summary">
              <p><strong>Attacking direction:</strong> {analysis.attackingDirection}</p>
              <p><strong>Scoreboard:</strong> {analysis.scoreboard}</p>
              <p><strong>Game clock:</strong> {analysis.gameClock}</p>
              <p><strong>Momentum:</strong> {analysis.momentumSummary}</p>
            </div>
          </>
        ) : (
          <EmptyState icon={<Film size={40} />} title="Waiting for video analysis" body="The sport detector will populate this panel once processing starts." />
        )}
      </div>
    </section>
  );
}

interface PreviewPageProps {
  upload: UploadFormState;
  videoUrl: string | null;
  analysis: SportAnalysis | null;
  voiceProfile: VoiceProfile | null;
  script: ScriptLine[];
  selectedLineId?: string;
  onSelectLine: (id: string) => void;
  onPlayPreview: () => void;
  isSpeaking: boolean;
}

function PreviewPage({ upload, videoUrl, analysis, voiceProfile, script, selectedLineId, onSelectLine, onPlayPreview, isSpeaking }: PreviewPageProps) {
  const waveform = useMemo(() => scriptToWaveform(script), [script]);
  return (
    <section className="page-grid">
      <div className="panel page-heading full-span">
        <span className="eyebrow"><Radio size={16} /> Step 7 · Live Commentary Preview</span>
        <h2>Review video, generated script, and commentary timing</h2>
        <p>Preview uses the browser's default voice. Final cloned narration requires a configured licensed voice provider and consent receipt.</p>
      </div>
      <div className="panel video-panel">
        {videoUrl ? <video src={videoUrl} controls playsInline /> : <EmptyState icon={<Film size={40} />} title="No video loaded" body="Upload a video to preview it here." />}
        <div className="video-panel__meta">
          <strong>{upload.videoFile?.name ?? 'No video'}</strong>
          <span>{analysis ? `${analysis.sport} · ${formatDuration(analysis.metrics.durationSeconds)}` : 'Awaiting analysis'}</span>
        </div>
      </div>
      <div className="panel commentary-panel">
        <div className="section-title">
          <h3>Generated commentary</h3>
          <button className="secondary-button" type="button" onClick={onPlayPreview} disabled={script.length === 0}>
            {isSpeaking ? <StopCircle size={17} /> : <Play size={17} />} {isSpeaking ? 'Stop preview' : 'Browser preview'}
          </button>
        </div>
        <Waveform values={waveform} />
        <div className="voice-mini-card">
          <Mic2 size={18} />
          <span>{voiceProfile ? `${voiceProfile.tone} · ${voiceProfile.speakingSpeed}` : 'No voice profile'}</span>
        </div>
        <div className="script-list compact">
          {script.slice(0, 10).map((line) => (
            <button
              key={line.id}
              type="button"
              className={selectedLineId === line.id ? 'is-selected' : undefined}
              onClick={() => onSelectLine(line.id)}
            >
              <span>{formatDuration(line.time)}</span>
              <p>{line.text}</p>
            </button>
          ))}
        </div>
      </div>
      {analysis ? (
        <div className="full-span">
          <Timeline duration={analysis.metrics.durationSeconds} events={analysis.events} script={script} selectedLineId={selectedLineId} onSelectLine={onSelectLine} />
        </div>
      ) : null}
    </section>
  );
}

interface EditorPageProps {
  script: ScriptLine[];
  analysis: SportAnalysis | null;
  selectedLineId?: string;
  onSelectLine: (id: string) => void;
  onUpdateLine: (id: string, patch: Partial<ScriptLine>) => void;
  onRegenerateLine: (id: string) => void;
  onDeleteLine: (id: string) => void;
  onAddLine: () => void;
  onRegenerateAll: () => void;
}

function EditorPage({ script, analysis, selectedLineId, onSelectLine, onUpdateLine, onRegenerateLine, onDeleteLine, onAddLine, onRegenerateAll }: EditorPageProps) {
  return (
    <section className="page-grid editor-grid">
      <div className="panel page-heading full-span">
        <span className="eyebrow"><FileText size={16} /> Step 7 · Script Editor</span>
        <h2>Edit lines, regenerate moments, and change timing</h2>
        <p>Use concise broadcast lines and leave natural gaps so commentary only appears when something matters.</p>
      </div>
      <div className="panel full-span editor-toolbar">
        <button className="primary-button" type="button" onClick={onAddLine}>Add line</button>
        <button className="secondary-button" type="button" onClick={onRegenerateAll} disabled={!analysis}>Regenerate all</button>
        <span className="muted">{script.length} line{script.length === 1 ? '' : 's'} · {analysis ? formatDuration(analysis.metrics.durationSeconds) : 'No video duration'}</span>
      </div>
      <div className="panel full-span script-editor-table">
        {script.length === 0 ? (
          <EmptyState icon={<FileText size={40} />} title="No script yet" body="Run analysis to create draft commentary lines." />
        ) : (
          script.map((line, index) => (
            <article key={line.id} className={`script-row ${selectedLineId === line.id ? 'is-selected' : ''}`} onFocus={() => onSelectLine(line.id)}>
              <div className="script-row__index">#{index + 1}</div>
              <label>
                Start
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={Number(line.time.toFixed(1))}
                  onChange={(event) => onUpdateLine(line.id, { time: Number(event.target.value) })}
                />
              </label>
              <label>
                Duration
                <input
                  type="number"
                  min={0.5}
                  step={0.1}
                  value={Number(line.duration.toFixed(1))}
                  onChange={(event) => onUpdateLine(line.id, { duration: Number(event.target.value) })}
                />
              </label>
              <label className="script-row__text">
                Commentary line
                <textarea value={line.text} onChange={(event) => onUpdateLine(line.id, { text: event.target.value })} />
              </label>
              <label>
                Emphasis
                <select value={line.emphasis} onChange={(event) => onUpdateLine(line.id, { emphasis: event.target.value as ScriptLine['emphasis'] })}>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </label>
              <div className="script-row__actions">
                <button type="button" className="icon-button" onClick={() => onRegenerateLine(line.id)} title="Regenerate line">
                  <RefreshCcw size={17} />
                </button>
                <button type="button" className="icon-button danger" onClick={() => onDeleteLine(line.id)} title="Delete line">
                  <Trash2 size={17} />
                </button>
              </div>
            </article>
          ))
        )}
      </div>
      {analysis ? (
        <div className="full-span">
          <Timeline duration={analysis.metrics.durationSeconds} events={analysis.events} script={script} selectedLineId={selectedLineId} onSelectLine={onSelectLine} />
        </div>
      ) : null}
    </section>
  );
}

interface ExportPageProps {
  upload: UploadFormState;
  voiceProfile: VoiceProfile | null;
  analysis: SportAnalysis | null;
  script: ScriptLine[];
  steps: ProcessingStep[];
  onExport: (kind: 'txt' | 'srt' | 'project' | 'manifest') => void;
  onGenerateClonedAudio: () => void;
  isSynthesizing: boolean;
  statusMessage: string;
}

function ExportPage({ upload, voiceProfile, analysis, script, steps, onExport, onGenerateClonedAudio, isSynthesizing, statusMessage }: ExportPageProps) {
  const canExportText = script.length > 0;
  const hasConsent = Boolean(voiceProfile?.permissionConfirmed);
  const canGenerateClonedAudio = canExportText && hasConsent && Boolean(upload.voiceFile);
  return (
    <section className="page-grid">
      <div className="panel page-heading full-span">
        <span className="eyebrow"><Download size={16} /> Step 8 · Export</span>
        <h2>Export transcript, subtitles, project, or render manifest</h2>
        <p>Generate commentary audio with the optional local XTTS voice cloning model, or export production handoff files for other providers.</p>
      </div>
      <div className="panel">
        <ProgressPanel steps={steps} statusMessage={statusMessage || (hasConsent ? 'Consent receipt available for synthesis providers.' : 'Speaker permission missing.')} />
      </div>
      <div className="panel export-grid">
        <ExportCard icon={<Film />} title="Video with commentary" body="Final MP4/MOV with generated commentary mixed into the original video." status="Renderer required" disabled />
        <ExportCard
          icon={<FileAudio />}
          title="Commentary audio only"
          body="Generate a timeline-aligned WAV with the local XTTS voice cloning model and the approved speaker sample."
          status={isSynthesizing ? 'Generating…' : canGenerateClonedAudio ? 'Ready: Local XTTS' : 'Run analysis + consent'}
          onClick={onGenerateClonedAudio}
          disabled={!canGenerateClonedAudio || isSynthesizing}
        />
        <ExportCard icon={<Subtitles />} title="Subtitle (.srt)" body="Timed commentary captions for editors and review." status={canExportText ? 'Ready' : 'Run analysis'} onClick={() => onExport('srt')} disabled={!canExportText} />
        <ExportCard icon={<FileText />} title="Transcript (.txt)" body="Plain-text commentary script with timestamps." status={canExportText ? 'Ready' : 'Run analysis'} onClick={() => onExport('txt')} disabled={!canExportText} />
        <ExportCard icon={<FileJson />} title="Project file" body="Portable JSON with metadata, analysis, script, and consent receipt." status={canExportText ? 'Ready' : 'Run analysis'} onClick={() => onExport('project')} disabled={!canExportText} />
        <ExportCard icon={<FileJson />} title="Render manifest" body="Production handoff for voice synthesis and video rendering adapters." status={canExportText ? 'Ready' : 'Run analysis'} onClick={() => onExport('manifest')} disabled={!canExportText} />
      </div>
      <div className="panel full-span provider-note">
        <h3>Production integration checklist</h3>
        <ul>
          <li>Install the optional XTTS dependencies with <code>npm run voice:install</code> and run <code>npm run dev:server</code> before generating cloned WAV audio.</li>
          <li>Store the speaker consent receipt and only submit approved voice samples to your licensed synthesis provider or local model.</li>
          <li>Swap heuristic CV with trained ball/player/referee/scoreboard detectors for verified match understanding.</li>
          <li>Render mixed audio/video in a background worker, FFmpeg/WASM pipeline, desktop process, or cloud renderer.</li>
          <li>Review generated commentary before publishing, especially names, scores, fouls, cards, and clock references.</li>
        </ul>
        <p className="muted">Current project: {upload.videoFile?.name ?? 'No video'} · {analysis?.sport ?? 'Unknown sport'} · {script.length} lines.</p>
      </div>
    </section>
  );
}

interface ExportCardProps {
  icon: ReactElement;
  title: string;
  body: string;
  status: string;
  disabled?: boolean;
  onClick?: () => void;
}

function ExportCard({ icon, title, body, status, disabled, onClick }: ExportCardProps) {
  return (
    <button type="button" className="export-card" disabled={disabled} onClick={onClick}>
      <span className="export-card__icon">{icon}</span>
      <strong>{title}</strong>
      <p>{body}</p>
      <em>{status}</em>
    </button>
  );
}

function EmptyState({ icon, title, body }: { icon: ReactElement; title: string; body: string }) {
  return (
    <div className="empty-state">
      {icon}
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}

function scriptToWaveform(lines: ScriptLine[]): number[] {
  if (lines.length === 0) return Array.from({ length: 56 }, () => 0.08);
  const values = lines.flatMap((line) => {
    const base = line.emphasis === 'high' ? 0.92 : line.emphasis === 'medium' ? 0.64 : 0.42;
    const words = Math.max(3, line.text.split(/\s+/).length);
    return Array.from({ length: Math.min(10, Math.ceil(words / 3)) }, (_, index) => Math.max(0.12, base * (0.72 + ((index % 3) * 0.11))));
  });
  return values.slice(0, 96);
}

function useObjectUrl(file: File | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file) {
      setUrl(null);
      return undefined;
    }
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  return url;
}

function shortDelay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export default App;
