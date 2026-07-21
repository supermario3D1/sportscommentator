import type { ScriptLine, UploadFormState, VoiceProfile } from '../types';
import { buildConsentReceipt } from './providerContracts';

export interface VoiceCloneSynthesisResult {
  audioBlob: Blob;
  fileName: string;
  providerName: string;
}

export async function synthesizeClonedCommentary(
  upload: UploadFormState,
  voiceProfile: VoiceProfile,
  script: ScriptLine[],
): Promise<VoiceCloneSynthesisResult> {
  if (!upload.voiceFile) {
    throw new Error('Upload a voice sample before voice cloning synthesis.');
  }
  if (!voiceProfile.permissionConfirmed || !upload.permissionConfirmed) {
    throw new Error('Speaker permission must be confirmed before voice cloning synthesis.');
  }
  if (script.length === 0) {
    throw new Error('Generate or add at least one commentary line before synthesis.');
  }

  const formData = new FormData();
  formData.append('voiceSample', upload.voiceFile, upload.voiceFile.name);
  formData.append('language', upload.language || 'en');
  formData.append('style', upload.style);
  formData.append('consent', JSON.stringify(buildConsentReceipt(voiceProfile)));
  formData.append(
    'voiceProfile',
    JSON.stringify({
      accent: voiceProfile.accent,
      pronunciation: voiceProfile.pronunciation,
      speakingSpeed: voiceProfile.speakingSpeed,
      tone: voiceProfile.tone,
      emotion: voiceProfile.emotion,
      averagePitchHz: voiceProfile.averagePitchHz,
      energy: voiceProfile.energy,
      intonation: voiceProfile.intonation,
    }),
  );
  formData.append(
    'script',
    JSON.stringify(
      script.map((line) => ({
        id: line.id,
        time: line.time,
        duration: line.duration,
        text: line.text,
        emphasis: line.emphasis,
      })),
    ),
  );

  const response = await fetch(`${apiBaseUrl()}/api/synthesize/voice-clone`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error(await readApiError(response));
  }

  const audioBlob = await response.blob();
  const providerName = response.headers.get('X-Voice-Clone-Provider') ?? 'local-xtts-v2';
  return {
    audioBlob,
    fileName: fileNameFromDisposition(response.headers.get('Content-Disposition')) ?? 'commentary-voice-clone.wav',
    providerName,
  };
}

function apiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
}

async function readApiError(response: Response): Promise<string> {
  const contentType = response.headers.get('Content-Type') ?? '';
  if (contentType.includes('application/json')) {
    const body = (await response.json()) as { error?: string; details?: string };
    const details = body.details ? `\n\n${body.details}` : '';
    return `${body.error ?? `Voice clone API failed with HTTP ${response.status}`}${details}`;
  }
  const text = await response.text();
  if (response.status === 404) {
    return 'Voice clone API is not running. Start it with `npm run dev:server`, then retry synthesis.';
  }
  return text || `Voice clone API failed with HTTP ${response.status}`;
}

function fileNameFromDisposition(disposition: string | null): string | null {
  if (!disposition) return null;
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  return match?.[1] ?? null;
}
