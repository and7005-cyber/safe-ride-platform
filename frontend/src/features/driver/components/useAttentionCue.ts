// The attention cue for a safety prompt (GPS plan U3: R15, R17).
//
// A bypassed stop and a remote absent carry a tone or a vibration; the routine
// custody confirm (U9) is silent. The policy is keyed by kind here so U9 and
// U10 add nothing but a card.
//
// The tone is a short generated WAV played through one <audio> element. Mobile
// browsers only let media play after a user gesture on that element, so Start
// Run — the first tap of every run — primes it (a muted play/pause), and the
// prompt can sound minutes later without a gesture. No audio asset ships: the
// bytes are built once at runtime.

import { useCallback } from "react";

export interface CuePolicy {
  tone: boolean;
  vibrate: boolean;
}

export const CUE_POLICY: Record<string, CuePolicy> = {
  "stop-bypassed": { tone: true, vibrate: true },
  "absent-remote": { tone: true, vibrate: true },
  "custody-away": { tone: false, vibrate: false },
};

const SILENT: CuePolicy = { tone: false, vibrate: false };

export function cuePolicy(kind: string): CuePolicy {
  return CUE_POLICY[kind] ?? SILENT;
}

/** Two short pulses — distinct from a notification buzz, brief enough not to
 * startle at the wheel. */
export const VIBRATION_PATTERN = [200, 100, 200];

export interface ToneOptions {
  /** Hz. 880 is an A5: clear on a phone speaker, not shrill. */
  frequency?: number;
  durationMs?: number;
  sampleRate?: number;
}

/** 16-bit mono PCM WAV of a sine with a short fade in and out (no click). */
export function toneWav({
  frequency = 880,
  durationMs = 350,
  sampleRate = 16_000,
}: ToneOptions = {}): Uint8Array {
  const samples = Math.max(1, Math.round((sampleRate * durationMs) / 1000));
  const fade = Math.min(samples, Math.round(sampleRate * 0.01));
  const dataBytes = samples * 2;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, dataBytes, true);
  for (let i = 0; i < samples; i++) {
    const envelope =
      i < fade ? i / fade : i >= samples - fade ? (samples - 1 - i) / fade : 1;
    const value = Math.sin((2 * Math.PI * frequency * i) / sampleRate) * envelope * 0.8;
    view.setInt16(44 + i * 2, Math.round(value * 32767), true);
  }
  return new Uint8Array(buffer);
}

export function toneWavDataUri(options?: ToneOptions): string {
  const bytes = toneWav(options);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return `data:audio/wav;base64,${btoa(binary)}`;
}

let element: HTMLAudioElement | null = null;

function audioElement(): HTMLAudioElement | null {
  if (typeof Audio === "undefined") return null;
  if (!element) {
    element = new Audio(toneWavDataUri());
    element.preload = "auto";
  }
  return element;
}

/** Prime the element inside a user gesture (the Start Run tap). A muted
 * play-then-pause is enough to count as activation on iOS and Android. */
export function unlockAttentionAudio(): void {
  const audio = audioElement();
  if (!audio) return;
  audio.muted = true;
  try {
    const attempt = audio.play();
    if (attempt && typeof attempt.then === "function") {
      attempt
        .then(() => {
          audio.pause();
          audio.currentTime = 0;
          audio.muted = false;
        })
        .catch(() => {
          audio.muted = false;
        });
    } else {
      audio.pause();
      audio.currentTime = 0;
      audio.muted = false;
    }
  } catch {
    audio.muted = false;
  }
}

/** Sound and vibrate per the kind's policy; returns what was attempted. Never
 * throws: a blocked autoplay or a missing vibration API is not an error. */
export function playAttentionCue(kind: string): CuePolicy {
  const policy = cuePolicy(kind);
  if (policy.tone) {
    const audio = audioElement();
    if (audio) {
      try {
        audio.muted = false;
        audio.currentTime = 0;
        const attempt = audio.play();
        if (attempt && typeof attempt.catch === "function") attempt.catch(() => {});
      } catch {
        // Autoplay refused — the vibration and the card still land.
      }
    }
  }
  if (policy.vibrate && typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
    try {
      navigator.vibrate(VIBRATION_PATTERN);
    } catch {
      // Some browsers throw when vibration is disallowed; the card still lands.
    }
  }
  return policy;
}

export function useAttentionCue(): (kind: string) => CuePolicy {
  return useCallback((kind: string) => playAttentionCue(kind), []);
}
