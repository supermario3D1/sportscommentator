import type { ProjectState, ScriptLine, SportAnalysis, UploadFormState, VoiceProfile } from '../types';
import { formatDuration, formatSrtTimestamp, slugify } from './format';
import { buildConsentReceipt } from './providerContracts';

export function buildTranscript(script: ScriptLine[]): string {
  return script
    .map((line) => `[${formatDuration(line.time)}] ${line.text}`)
    .join('\n');
}

export function buildSrt(script: ScriptLine[]): string {
  return script
    .map((line, index) => {
      const start = formatSrtTimestamp(line.time);
      const end = formatSrtTimestamp(line.time + line.duration);
      return `${index + 1}\n${start} --> ${end}\n${line.text}`;
    })
    .join('\n\n');
}

export function buildProjectFile(
  upload: UploadFormState,
  voiceProfile: VoiceProfile | null,
  sportAnalysis: SportAnalysis | null,
  script: ScriptLine[],
): ProjectState {
  const now = new Date().toISOString();
  return {
    upload: {
      ...upload,
      videoFile: upload.videoFile,
      voiceFile: upload.voiceFile,
    },
    voiceProfile,
    sportAnalysis,
    script,
    createdAt: now,
    updatedAt: now,
  };
}

export function buildPortableProjectJson(
  upload: UploadFormState,
  voiceProfile: VoiceProfile | null,
  sportAnalysis: SportAnalysis | null,
  script: ScriptLine[],
): string {
  const project = buildProjectFile(upload, voiceProfile, sportAnalysis, script);
  return JSON.stringify(
    {
      ...project,
      upload: {
        ...project.upload,
        videoFile: upload.videoFile
          ? { name: upload.videoFile.name, type: upload.videoFile.type, size: upload.videoFile.size, lastModified: upload.videoFile.lastModified }
          : null,
        voiceFile: upload.voiceFile
          ? { name: upload.voiceFile.name, type: upload.voiceFile.type, size: upload.voiceFile.size, lastModified: upload.voiceFile.lastModified }
          : null,
      },
      consentReceipt: voiceProfile?.permissionConfirmed ? buildConsentReceipt(voiceProfile) : null,
      providerNotes: {
        voiceSynthesis: 'Use src/lib/providerContracts.ts to attach a licensed voice-cloning/TTS provider. Speaker consent is required.',
        videoRender: 'Attach a renderer such as FFmpeg/WASM, cloud render, or a desktop worker for mixed-down video export.',
      },
    },
    null,
    2,
  );
}

export function buildRenderManifest(
  upload: UploadFormState,
  voiceProfile: VoiceProfile | null,
  sportAnalysis: SportAnalysis | null,
  script: ScriptLine[],
): string {
  return JSON.stringify(
    {
      type: 'sports-commentary-render-manifest',
      generatedAt: new Date().toISOString(),
      sourceVideo: upload.videoFile ? { name: upload.videoFile.name, type: upload.videoFile.type, size: upload.videoFile.size } : null,
      requestedExports: ['video-with-commentary', 'commentary-audio-only'],
      voiceConsent: voiceProfile?.permissionConfirmed ? buildConsentReceipt(voiceProfile) : null,
      voiceProfileSummary: voiceProfile
        ? {
            accent: voiceProfile.accent,
            speed: voiceProfile.speakingSpeed,
            tone: voiceProfile.tone,
            emotion: voiceProfile.emotion,
            pitchHz: voiceProfile.averagePitchHz,
            energy: voiceProfile.energy,
          }
        : null,
      sport: sportAnalysis?.sport ?? 'Unknown sport',
      script: script.map((line) => ({
        id: line.id,
        startSeconds: line.time,
        endSeconds: line.time + line.duration,
        text: line.text,
        emphasis: line.emphasis,
      })),
      nextStep: 'Send this manifest, the original video, and the voice sample to configured synthesis/render providers.',
    },
    null,
    2,
  );
}

export function downloadTextFile(contents: string, fileName: string, type = 'text/plain;charset=utf-8'): void {
  const blob = new Blob([contents], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}


export function downloadBlobFile(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function projectBaseName(upload: UploadFormState): string {
  const source = upload.competitionName || upload.videoFile?.name.replace(/\.[^.]+$/, '') || 'sports-commentary';
  return slugify(source);
}
