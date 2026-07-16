import { useCallback, useEffect, useRef, useState } from 'react';
import { getVoiceConfig, type VoiceConfig } from '@/api/bridge';

export type VoiceRelayState =
  | 'idle'
  | 'fetching'
  | 'connecting'
  | 'ready'
  | 'listening'
  | 'stopping';

/** Thrown when the backend reports voice is OFF — caller should fall back to
 *  the Web Speech path. Distinct from a transport/ASR failure (plain Error). */
export class VoiceDisabledError extends Error {
  constructor(message = 'voice disabled') {
    super(message);
    this.name = 'VoiceDisabledError';
  }
}

interface Options {
  /** Live interim text (replaces the trailing partial). */
  onPartial: (text: string) => void;
  /** A committed sentence segment (append). */
  onFinal: (text: string) => void;
  /** Fatal relay error after start. */
  onError: (msg: string) => void;
}

/**
 * Streaming voice via the bridge WSS relay: prepare() -> start() -> stop()
 * (per utterance) -> teardown(). Mirrors the VoiceProbe capture core; the
 * relay path needs no SpeechRecognition.
 *
 * prepare() (on voice-mode enter): fetch config -> WSS connect + auth ->
 * getUserMedia + ScriptProcessor (armed, NOT streaming). Moves the heavy
 * setup off the press-hold path so press-hold only pays the ASR-connect cost.
 * start() (pointer-down): send {start}, await {started}, enable streaming.
 * stop() (pointer-up): stop streaming + {stop}, keep WSS/mic warm for reuse.
 * teardown() (voice-mode exit): close WSS + mic + AudioContext.
 *
 * prepare() throws VoiceDisabledError when voice is off (caller falls back
 * to Web Speech) or Error on transport failure.
 */
export function useVoiceRelay({ onPartial, onFinal, onError }: Options) {
  const [state, setState] = useState<VoiceRelayState>('idle');

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const spRef = useRef<ScriptProcessorNode | null>(null);
  const downRef = useRef<DownsampleTo16k | null>(null);
  const cfgRef = useRef<VoiceConfig | null>(null);
  // True only while a {start}'d session is active — onaudioprocess downsamples
  // always (keeps the graph pulling) but only sends PCM when this is true, so
  // the mic can be armed during voice mode without streaming to the ASR backend
  // until the user actually press-holds.
  const streamingRef = useRef(false);
  // Latest callbacks in refs so the async loop always sees fresh closures
  // without re-creating start/stop (which would abort the in-flight session).
  const onPartialRef = useRef(onPartial);
  const onFinalRef = useRef(onFinal);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onPartialRef.current = onPartial;
  }, [onPartial]);
  useEffect(() => {
    onFinalRef.current = onFinal;
  }, [onFinal]);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const cleanup = useCallback(() => {
    try {
      spRef.current?.disconnect();
    } catch {
      /* ignore */
    }
    spRef.current = null;
    try {
      ctxRef.current?.close();
    } catch {
      /* ignore */
    }
    ctxRef.current = null;
    streamRef.current?.getTracks().forEach((t) => {
      try {
        t.stop();
      } catch {
        /* ignore */
      }
    });
    streamRef.current = null;
    const ws = wsRef.current;
    if (
      ws &&
      (ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING)
    ) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    wsRef.current = null;
    downRef.current = null;
  }, []);

  /**
   * Preconnect: fetch config -> open WSS -> auth -> getUserMedia -> AudioContext
   * -> ScriptProcessor (armed, NOT streaming). Called when the user ENTERS
   * voice mode, so the heavy setup (WSS roundtrip + mic open + audio graph)
   * happens once per voice-mode session instead of on every press-hold.
   * streaming stays false until start(). Throws VoiceDisabledError if voice
   * is off (caller falls back to Web Speech) or Error on transport failure.
   */
  const prepare = useCallback(async () => {
    setState('fetching');
    let cfg: VoiceConfig;
    try {
      cfg = await getVoiceConfig();
    } catch (e: any) {
      setState('idle');
      throw new Error(`voice config fetch failed: ${e?.message ?? e}`);
    }
    cfgRef.current = cfg;
    if (!cfg.enabled || !cfg.wssUrl || !cfg.token) {
      setState('idle');
      throw new VoiceDisabledError();
    }

    setState('connecting');
    const ws: WebSocket = new WebSocket(cfg.wssUrl);
    ws.binaryType = 'arraybuffer';
    wsRef.current = ws;

    // open + auth handshake.
    await new Promise<void>((resolve, reject) => {
      const to = setTimeout(() => reject(new Error('wss open timeout')), 8000);
      ws.onopen = () => {
        clearTimeout(to);
        ws.send(JSON.stringify({ type: 'auth', token: cfg.token }));
      };
      ws.onerror = () => {
        clearTimeout(to);
        reject(new Error('wss connect failed'));
      };
      const onMsg = (ev: MessageEvent) => {
        let m: any;
        try {
          m = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
        } catch {
          return;
        }
        if (m.type === 'authed') {
          clearTimeout(to);
          ws.removeEventListener('message', onMsg);
          resolve();
        } else if (m.type === 'error') {
          clearTimeout(to);
          ws.removeEventListener('message', onMsg);
          reject(new Error(m.message || 'auth rejected'));
        }
      };
      ws.addEventListener('message', onMsg);
    });

    // Persistent results handler for the life of the connection. Control acks
    // ({started}/{ended}) are consumed by start()/stop() via ad-hoc listeners.
    ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data !== 'string') return;
      let m: any;
      try {
        m = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (m.type === 'partial') onPartialRef.current(m.text || '');
      else if (m.type === 'final') onFinalRef.current(m.text || '');
      else if (m.type === 'error')
        onErrorRef.current(m.message || 'relay error');
    };
    ws.onerror = () => onErrorRef.current('wss connection lost');
    ws.onclose = () => {
      /* teardown() or unmount handles cleanup */
    };

    // mic + ScriptProcessor (armed; streaming=false so PCM isn't sent yet).
    const stream = await getUserMediaWithTimeout(6000);
    streamRef.current = stream;
    const AC =
      (window as any).AudioContext || (window as any).webkitAudioContext;
    const ctx: AudioContext = new AC();
    ctxRef.current = ctx;
    const src = ctx.createMediaStreamSource(stream);
    const sp = ctx.createScriptProcessor(4096, 1, 1);
    spRef.current = sp;
    const sink = ctx.createGain();
    sink.gain.value = 0; // silent sink so the node graph pulls audio
    src.connect(sp);
    sp.connect(sink);
    sink.connect(ctx.destination);

    const targetRate = cfg.sampleRate || 16000;
    downRef.current = new DownsampleTo16k(ctx.sampleRate, targetRate);
    sp.onaudioprocess = (ev: AudioProcessingEvent) => {
      if (!streamingRef.current) return; // armed, not streaming
      const ws2 = wsRef.current;
      if (!ws2 || ws2.readyState !== WebSocket.OPEN) return;
      const input = ev.inputBuffer.getChannelData(0);
      const pcm = downRef.current!.push(input);
      if (pcm.length) ws2.send(pcm.buffer);
    };
    streamingRef.current = false;
    setState('ready');
  }, []);

  /**
   * Press-hold: send {start}, await the bridge's {started} ack (ASR backend
   * connected), then enable streaming. Assumes prepare() succeeded (state
   * 'ready'). Throws if not prepared or the ASR start fails/times out.
   */
  const start = useCallback(async () => {
    const ws = wsRef.current;
    const cfg = cfgRef.current;
    const ctx = ctxRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !cfg || !ctx) {
      throw new Error('relay not prepared');
    }
    setState('connecting');
    await new Promise<void>((resolve, reject) => {
      const to = setTimeout(() => reject(new Error('asr start timeout')), 8000);
      const onMsg = (ev: MessageEvent) => {
        let m: any;
        try {
          m = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
        } catch {
          return;
        }
        if (m.type === 'started') {
          clearTimeout(to);
          ws.removeEventListener('message', onMsg);
          resolve();
        } else if (m.type === 'error') {
          clearTimeout(to);
          ws.removeEventListener('message', onMsg);
          reject(new Error(m.message || 'asr start failed'));
        }
      };
      ws.addEventListener('message', onMsg);
      try {
        ws.send(
          JSON.stringify({
            type: 'start',
            sampleRate: ctx.sampleRate,
            lang: cfg.lang || 'zh-CN',
          }),
        );
      } catch {
        clearTimeout(to);
        reject(new Error('send failed'));
      }
    });
    streamingRef.current = true;
    setState('listening');
  }, []);

  /** Release: stop streaming + send {stop}, await {ended}. Keeps the WSS +
   *  mic warm (state back to 'ready') so the next press-hold only pays the
   *  ASR-connect cost. */
  const stop = useCallback(async () => {
    streamingRef.current = false;
    setState('stopping');
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      await new Promise<void>((resolve) => {
        const to = setTimeout(resolve, 2000);
        const onMsg = (ev: MessageEvent) => {
          let m: any;
          try {
            m = JSON.parse(typeof ev.data === 'string' ? ev.data : '');
          } catch {
            return;
          }
          if (m.type === 'ended' || m.type === 'error') {
            clearTimeout(to);
            ws.removeEventListener('message', onMsg);
            resolve();
          }
        };
        ws.addEventListener('message', onMsg);
        try {
          ws.send(JSON.stringify({ type: 'stop' }));
        } catch {
          clearTimeout(to);
          resolve();
        }
      });
    }
    setState('ready'); // armed again — WSS/mic stay warm for next press
  }, []);

  /** Exit voice mode: tear down WSS + mic + AudioContext. */
  const teardown = useCallback(() => {
    streamingRef.current = false;
    cleanup();
    setState('idle');
  }, [cleanup]);

  useEffect(() => () => cleanup(), [cleanup]);

  return { state, prepare, start, stop, teardown };
}

/** getUserMedia with a hard timeout — Feishu WebView can silently never
 *  resolve (no permission prompt). Pattern lifted from VoiceProbe. */
function getUserMediaWithTimeout(timeoutMs: number): Promise<MediaStream> {
  return new Promise<MediaStream>((resolve, reject) => {
    let done = false;
    const t = setTimeout(() => {
      if (done) return;
      done = true;
      reject(new Error(`getUserMedia timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    navigator.mediaDevices
      ?.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
      })
      .then((s) => {
        if (done) {
          try {
            s.getTracks().forEach((tr) => tr.stop());
          } catch {
            /* ignore */
          }
          return;
        }
        done = true;
        clearTimeout(t);
        resolve(s);
      })
      .catch((e) => {
        if (done) return;
        done = true;
        clearTimeout(t);
        reject(e);
      });
  });
}

/** Downsample a Float32 PCM stream to 16kHz 16-bit mono via linear
 *  interpolation with carryover. No deps. If srcRate === dstRate, just
 *  converts float→int16. One instance per voice session. */
class DownsampleTo16k {
  private buf: number[] = [];
  private pos = 0; // fractional read position within buf
  constructor(
    private srcRate: number,
    private dstRate = 16000,
  ) {}

  push(src: Float32Array): Int16Array {
    const ratio = this.srcRate / this.dstRate;
    for (let i = 0; i < src.length; i++) this.buf.push(src[i]);
    const outs: number[] = [];
    // Need two surrounding samples to interpolate; stop while pos+1 < len.
    while (this.pos + 1 < this.buf.length) {
      const i0 = Math.floor(this.pos);
      const frac = this.pos - i0;
      const s = this.buf[i0] * (1 - frac) + this.buf[i0 + 1] * frac;
      outs.push(s);
      this.pos += ratio;
    }
    // Drop consumed whole samples, keep the tail + fractional remainder.
    const consumed = Math.floor(this.pos);
    if (consumed > 0) {
      this.buf = this.buf.slice(consumed);
      this.pos -= consumed;
    }
    const pcm = new Int16Array(outs.length);
    for (let i = 0; i < outs.length; i++) {
      let s = outs[i];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      pcm[i] = s < 0 ? (s * 32768) | 0 : (s * 32767) | 0;
    }
    return pcm;
  }
}
