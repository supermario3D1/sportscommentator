import cors from 'cors';
import express from 'express';
import multer from 'multer';
import { spawn } from 'node:child_process';
import { createReadStream } from 'node:fs';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');
const app = express();
const port = Number(process.env.PORT ?? process.env.VOICE_API_PORT ?? 8787);
const maxVoiceBytes = Number(process.env.VOICE_SAMPLE_MAX_BYTES ?? 80 * 1024 * 1024);
const maxScriptChars = Number(process.env.VOICE_CLONE_MAX_CHARS ?? 24_000);
const maxScriptLines = Number(process.env.VOICE_CLONE_MAX_LINES ?? 300);
const synthesisTimeoutMs = Number(process.env.VOICE_CLONE_TIMEOUT_MS ?? 30 * 60 * 1000);

const multipart = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: maxVoiceBytes,
    files: 1,
    fields: 8,
    fieldSize: 12 * 1024 * 1024,
  },
});

app.use(cors({ origin: true }));
app.use(express.json({ limit: '1mb' }));

app.get('/api/health', (_request, response) => {
  response.json({
    ok: true,
    service: 'sportscommentator-voice-clone-api',
    model: process.env.VOICE_MODEL_NAME ?? 'tts_models/multilingual/multi-dataset/xtts_v2',
    python: process.env.PYTHON_BIN ?? 'python3',
    dryRun: process.env.VOICE_CLONE_DRY_RUN === '1',
  });
});

app.post('/api/synthesize/voice-clone', multipart.single('voiceSample'), async (request, response) => {
  const workingDir = await mkdtemp(path.join(tmpdir(), 'sportscommentator-voice-'));
  try {
    if (!request.file) {
      return response.status(400).json({ error: 'Upload a voice sample in the voiceSample multipart field.' });
    }

    const consent = parseJsonFieldOrResponse(request.body.consent, 'consent', response);
    if (!consent) return undefined;
    if (consent?.speakerPermissionConfirmed !== true) {
      return response.status(403).json({ error: 'Speaker permission must be confirmed before voice cloning synthesis.' });
    }

    const script = parseJsonFieldOrResponse(request.body.script, 'script', response);
    if (!script) return undefined;
    if (!Array.isArray(script) || script.length === 0) {
      return response.status(400).json({ error: 'A non-empty script array is required.' });
    }

    const lines = normalizeScriptLines(script);
    const totalCharacters = lines.reduce((sum, line) => sum + line.text.length, 0);
    if (lines.length > maxScriptLines) {
      return response.status(413).json({ error: `Too many script lines. Limit is ${maxScriptLines}.` });
    }
    if (totalCharacters > maxScriptChars) {
      return response.status(413).json({ error: `Script is too long for one synthesis request. Limit is ${maxScriptChars} characters.` });
    }

    const extension = extensionFromFile(request.file.originalname, request.file.mimetype);
    const voiceSamplePath = path.join(workingDir, `voice-sample${extension}`);
    const requestPath = path.join(workingDir, 'request.json');
    const outputPath = path.join(workingDir, 'commentary.wav');

    await writeFile(voiceSamplePath, request.file.buffer);
    await writeFile(
      requestPath,
      JSON.stringify(
        {
          consent,
          voiceSamplePath,
          language: request.body.language || 'en',
          style: request.body.style || 'TV Broadcast',
          modelName: process.env.VOICE_MODEL_NAME ?? 'tts_models/multilingual/multi-dataset/xtts_v2',
          voiceProfile: safeParseJsonField(request.body.voiceProfile),
          script: lines,
        },
        null,
        2,
      ),
    );

    const workerPath = path.join(projectRoot, 'server', 'voice', 'xtts_worker.py');
    const result = await runPythonWorker(workerPath, requestPath, outputPath);
    if (!result.ok) {
      return response.status(result.statusCode).json({ error: result.error, details: result.details });
    }

    response.setHeader('Content-Type', 'audio/wav');
    response.setHeader('Content-Disposition', 'attachment; filename="commentary-voice-clone.wav"');
    response.setHeader('X-Voice-Clone-Provider', 'local-xtts-v2');
    createReadStream(outputPath).pipe(response);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Voice cloning synthesis failed.';
    response.status(500).json({ error: message });
  } finally {
    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      void rm(workingDir, { recursive: true, force: true });
    };
    if (response.writableEnded) {
      cleanup();
    } else {
      response.once('finish', cleanup);
      response.once('close', cleanup);
    }
  }
});

app.use((error, _request, response, _next) => {
  if (error instanceof multer.MulterError) {
    response.status(413).json({ error: error.message });
    return;
  }
  response.status(500).json({ error: error instanceof Error ? error.message : 'Unexpected server error.' });
});

app.listen(port, () => {
  console.log(`Voice clone API listening on http://localhost:${port}`);
});

function parseJsonFieldOrResponse(value, label, response) {
  if (typeof value !== 'string') {
    response.status(400).json({ error: `Missing ${label} JSON field.` });
    return null;
  }
  try {
    return JSON.parse(value);
  } catch {
    response.status(400).json({ error: `Invalid ${label} JSON field.` });
    return null;
  }
}

function safeParseJsonField(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function normalizeScriptLines(script) {
  return script
    .map((line, index) => ({
      id: sanitizeId(line.id ?? `line-${index + 1}`),
      time: finiteNumber(line.time, 0),
      duration: Math.max(0.5, finiteNumber(line.duration, 3)),
      text: String(line.text ?? '').replace(/\s+/g, ' ').trim(),
      emphasis: ['low', 'medium', 'high'].includes(line.emphasis) ? line.emphasis : 'medium',
    }))
    .filter((line) => line.text.length > 0)
    .sort((a, b) => a.time - b.time);
}

function sanitizeId(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]/g, '-').slice(0, 80) || 'line';
}

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : fallback;
}

function extensionFromFile(fileName, mimeType) {
  const extension = path.extname(fileName || '').toLowerCase();
  if (['.wav', '.mp3', '.m4a', '.aac', '.ogg', '.flac'].includes(extension)) return extension;
  if (mimeType === 'audio/wav') return '.wav';
  if (mimeType === 'audio/mpeg') return '.mp3';
  return '.wav';
}

function runPythonWorker(workerPath, requestPath, outputPath) {
  const python = process.env.PYTHON_BIN ?? 'python3';
  const args = [workerPath, '--request', requestPath, '--output', outputPath];
  return new Promise((resolve) => {
    const child = spawn(python, args, {
      cwd: projectRoot,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    let settled = false;
    const timeout = setTimeout(() => {
      if (!settled) {
        child.kill('SIGTERM');
        settled = true;
        resolve({
          ok: false,
          statusCode: 504,
          error: 'Voice cloning synthesis timed out.',
          details: `Timeout after ${Math.round(synthesisTimeoutMs / 1000)} seconds.`,
        });
      }
    }, synthesisTimeoutMs);

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      resolve({ ok: false, statusCode: 500, error: error.message, details: '' });
    });
    child.on('close', async (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (code !== 0) {
        resolve({
          ok: false,
          statusCode: code === 12 ? 501 : 500,
          error: code === 12 ? 'Local XTTS voice cloning model is not installed.' : 'Voice cloning worker failed.',
          details: compactLogs(stdout, stderr),
        });
        return;
      }
      try {
        await readFile(outputPath);
        resolve({ ok: true });
      } catch {
        resolve({ ok: false, statusCode: 500, error: 'Voice cloning worker did not produce audio.', details: compactLogs(stdout, stderr) });
      }
    });
  });
}

function compactLogs(stdout, stderr) {
  return [stdout, stderr]
    .filter(Boolean)
    .join('\n')
    .split('\n')
    .slice(-28)
    .join('\n')
    .trim();
}
