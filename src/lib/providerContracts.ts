import type { ScriptLine, SportAnalysis, UploadFormState, VoiceProfile } from '../types';

export interface ConsentReceipt {
  speakerPermissionConfirmed: boolean;
  confirmedAtIso: string;
  voiceSampleFileName: string;
  intendedUse: 'sports-commentary-project';
}

export interface VoiceSynthesisRequest {
  consent: ConsentReceipt;
  voiceSample: File;
  voiceProfile: VoiceProfile;
  script: ScriptLine[];
  language: string;
  style: string;
}

export interface VoiceSynthesisResult {
  audioBlob: Blob;
  lineAudio: Array<{ lineId: string; startTime: number; endTime: number }>;
  providerName: string;
}

export interface VoiceSynthesisProvider {
  name: string;
  synthesize(request: VoiceSynthesisRequest): Promise<VoiceSynthesisResult>;
}

export interface VideoRenderRequest {
  videoFile: File;
  commentaryAudio: Blob;
  script: ScriptLine[];
  analysis: SportAnalysis;
  options: UploadFormState;
}

export interface VideoRenderResult {
  videoBlob: Blob;
  providerName: string;
}

export interface VideoRenderProvider {
  name: string;
  render(request: VideoRenderRequest): Promise<VideoRenderResult>;
}

export class ProviderNotConfiguredError extends Error {
  constructor(providerType: string) {
    super(`${providerType} provider is not configured. Connect a licensed, consent-aware provider to enable production export.`);
    this.name = 'ProviderNotConfiguredError';
  }
}

export class ConsentRequiredError extends Error {
  constructor() {
    super('Speaker permission must be confirmed before voice synthesis can run.');
    this.name = 'ConsentRequiredError';
  }
}

export function buildConsentReceipt(voiceProfile: VoiceProfile): ConsentReceipt {
  if (!voiceProfile.permissionConfirmed) {
    throw new ConsentRequiredError();
  }
  return {
    speakerPermissionConfirmed: true,
    confirmedAtIso: new Date().toISOString(),
    voiceSampleFileName: voiceProfile.fileName,
    intendedUse: 'sports-commentary-project',
  };
}

export const unconfiguredVoiceProvider: VoiceSynthesisProvider = {
  name: 'Unconfigured voice synthesis adapter',
  async synthesize() {
    throw new ProviderNotConfiguredError('Voice synthesis');
  },
};

export const unconfiguredVideoRenderProvider: VideoRenderProvider = {
  name: 'Unconfigured video render adapter',
  async render() {
    throw new ProviderNotConfiguredError('Video render');
  },
};
