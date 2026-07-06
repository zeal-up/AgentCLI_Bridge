import React, { useEffect, useRef, useState } from 'react';
import { cn } from '../lib/utils';

/**
 * Voice-input capability probe (for Feishu/Lark WKWebView).
 *
 * The two requirements we need for the sherpa-onnx WASM voice plan:
 *   1. getUserMedia mic permission + ScriptProcessorNode PCM capture
 *      (AudioWorklet is NOT supported in WKWebView, so we must use the
 *      deprecated ScriptProcessorNode).
 *   2. WASM + WASM SIMD (sherpa-onnx single-threaded build needs SIMD on
 *      iOS 16.4+; without SIMD it runs ~2-4x slower and may not be real-time).
 *   3. Web Worker (sherpa-onnx runs in a worker via postMessage; does NOT
 *      need SharedArrayBuffer in the single-threaded build — which matters
 *      because WKWebView lacks SharedArrayBuffer).
 *
 * This page runs all checks on a button tap (mic needs a user gesture) and
 * shows a checklist + a live 6s mic capture (volume meter + PCM chunk count)
 * so we can empirically confirm mic + ScriptProcessorNode work in the actual
 * Feishu WKWebView. Results stay on-screen for a screenshot.
 */

type Status = 'pending' | 'running' | 'ok' | 'fail';

interface Check {
  key: string;
  label: string;
  status: Status;
  detail?: string;
}

const initChecks: Check[] = [
  { key: 'ua', label: 'User agent / iOS version', status: 'pending' },
  { key: 'h5sdk', label: 'Feishu h5sdk / tt present', status: 'pending' },
  { key: 'wasm', label: 'WebAssembly', status: 'pending' },
  { key: 'simd', label: 'WASM SIMD (need iOS 16.4+)', status: 'pending' },
  { key: 'sab', label: 'SharedArrayBuffer (expect ✗ in WKWebView)', status: 'pending' },
  { key: 'coi', label: 'crossOriginIsolated', status: 'pending' },
  { key: 'worker', label: 'Web Worker (postMessage)', status: 'pending' },
  { key: 'speech', label: 'Web Speech API (current approach)', status: 'pending' },
  { key: 'audioWorklet', label: 'AudioWorklet (expect ✗ in WKWebView)', status: 'pending' },
  { key: 'mic', label: 'getUserMedia mic permission', status: 'pending' },
  { key: 'scriptProc', label: 'ScriptProcessorNode PCM capture', status: 'pending' },
];

// A WASM module that uses a SIMD instruction. validate() returns true only if
// the engine supports SIMD.
const SIMD_MODULE = new Uint8Array([
  0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1,
  8, 0, 65, 0, 253, 15, 26, 11,
]);

function iosVer(ua: string): string {
  // iPhone OS 17_2 like, Mac OS X 17_2
  const m = ua.match(/(?:iPhone |CPU iPhone )?OS (\d+)[_\.](\d+)/) || ua.match(/OS (\d+)[_\.](\d+)/);
  if (m) return `${m[1]}.${m[2]}`;
  return ua.match(/OS (\d+)/)?.[1] ? `${ua.match(/OS (\d+)/)[1]}` : '(non-iOS)';
}

const VoiceProbe: React.FC = () => {
  const [checks, setChecks] = useState<Check[]>(initChecks);
  const [running, setRunning] = useState(false);
  const [volume, setVolume] = useState(0);
  const [chunkCount, setChunkCount] = useState(0);
  const [sampleRate, setSampleRate] = useState<number | null>(null);
  const [captureLog, setCaptureLog] = useState<string[]>([]);
  const [raw, setRaw] = useState<string>('');
  const [copied, setCopied] = useState(false);
  const [probeErr, setProbeErr] = useState<string>('');
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const stopRef = useRef(false);
  const safetyRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => () => {
    stopRef.current = true;
    if (safetyRef.current) { clearTimeout(safetyRef.current); safetyRef.current = null; }
    try { streamRef.current?.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
    try { ctxRef.current?.close(); } catch { /* ignore */ }
  }, []);

  const set = (key: string, status: Status, detail?: string) =>
    setChecks((cs) => cs.map((c) => (c.key === key ? { ...c, status, detail } : c)));

  const log = (s: string) => setCaptureLog((l) => [...l, s]);

  const runStaticChecks = () => {
    const UA = navigator.userAgent;
    set('ua', 'ok', `${iosVer(UA)} · ${UA.slice(0, 120)}`);
    const h5 = typeof (window as any).h5sdk !== 'undefined';
    const tt = typeof (window as any).tt !== 'undefined';
    set('h5sdk', h5 || tt ? 'ok' : 'fail', `h5sdk=${h5} tt=${tt}`);
    const hasWasm = typeof WebAssembly === 'object' && !!WebAssembly;
    set('wasm', hasWasm ? 'ok' : 'fail', hasWasm ? 'WebAssembly present' : 'absent');
    let simd = false;
    try { simd = hasWasm ? WebAssembly.validate(SIMD_MODULE) : false; } catch (e: any) { simd = false; }
    set('simd', simd ? 'ok' : 'fail', simd ? 'SIMD supported' : 'SIMD NOT supported (sherpa-onnx would run slow)');
    const hasSab = typeof SharedArrayBuffer === 'function';
    set('sab', hasSab ? 'ok' : 'fail', hasSab ? 'present (unexpected in WKWebView)' : 'absent (expected — single-thread sherpa build is fine)');
    const coi = !!(window as any).crossOriginIsolated;
    set('coi', coi ? 'ok' : 'fail', String(coi));
    set('speech', 'ok', String(typeof (window as any).SpeechRecognition !== 'undefined' || typeof (window as any).webkitSpeechRecognition !== 'undefined'));
    // AudioWorklet: object may exist but addModule typically fails in WKWebView.
    const aw = !!(window as any).AudioContext && typeof (window as any).AudioContext?.prototype?.audioWorklet !== 'undefined';
    set('audioWorklet', aw ? 'ok' : 'fail', aw ? 'audioWorklet object present (addModule still may fail)' : 'absent');
  };

  const testWorker = (): Promise<void> => new Promise((resolve) => {
    try {
      const blob = new Blob([`onmessage=(e)=>postMessage({pong:e.data.ping});`], { type: 'application/javascript' });
      const url = URL.createObjectURL(blob);
      const w = new Worker(url);
      const t = setTimeout(() => { set('worker', 'fail', 'no pong in 2s'); w.terminate(); resolve(); }, 2000);
      w.onmessage = (e) => {
        clearTimeout(t);
        set('worker', 'ok', `pong received: ${JSON.stringify(e.data)}`);
        w.terminate();
        URL.revokeObjectURL(url);
        resolve();
      };
      w.onerror = (e) => {
        clearTimeout(t);
        set('worker', 'fail', String((e as any).message || e));
        w.terminate();
        resolve();
      };
      w.postMessage({ ping: 1 });
    } catch (e: any) {
      set('worker', 'fail', String(e?.message || e));
      resolve();
    }
  });

  const runProbe = async () => {
    if (running) return;
    setRunning(true);
    setProbeErr('');
    setChecks(initChecks.map((c) => ({ ...c, status: 'pending' })));
    setVolume(0); setChunkCount(0); setSampleRate(null); setCaptureLog([]); setRaw('');
    stopRef.current = false;

    // Overall safety net: no matter which step hangs in the WebView, the button
    // never stays "运行中" forever. Cleared by finish().
    safetyRef.current = setTimeout(() => {
      setProbeErr('整体超时 25s：某一步 (worker / permissions.query / getUserMedia) 在 WebView 里挂住不返回。请截图此页发我。');
      setChecks((cs) => cs.map((c) => (c.key === 'mic' && c.status !== 'ok') ? { ...c, status: 'fail', detail: 'safety timeout — probe hung at this step' } : c));
      finish();
    }, 25000);

    try {
      runStaticChecks();
      await testWorker();

      // --- mic + ScriptProcessorNode (the make-or-break test) ---
      set('mic', 'running', 'requesting getUserMedia…');
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        set('mic', 'fail', 'navigator.mediaDevices.getUserMedia 不存在 (WebView 不支持 WebRTC)');
        set('scriptProc', 'fail', 'no mic stream');
        finish();
        return;
      }
      // Best-effort permission-state probe (may be unsupported OR may hang in
      // WebView — race it with a 3s timeout so it can't block the probe).
      try {
        const perm: any = await Promise.race([
          Promise.resolve((navigator as any).permissions?.query?.({ name: 'microphone' as any })),
          new Promise((_r, rej) => setTimeout(() => rej(new Error('perm-query 3s timeout')), 3000)),
        ]);
        if (perm) set('mic', 'running', `permissions.query → state=${perm.state} (prompt=可弹窗, denied=硬拒, granted=已授)`);
      } catch (e: any) { set('mic', 'running', `permissions.query 不可用/超时: ${e?.message || e}`); }
      let stream: MediaStream;
      try {
        stream = await new Promise<MediaStream>((resolve, reject) => {
          let done = false;
          const t = setTimeout(() => {
            if (done) return; done = true;
            reject(new Error('TIMEOUT: getUserMedia 6s 无响应 (WebView 静默忽略 — 权限被拦，无弹窗)'));
          }, 6000);
          navigator.mediaDevices!.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
          }).then((s) => { if (done) { try { s.getTracks().forEach((tr) => tr.stop()); } catch { /* ignore */ } return; } done = true; clearTimeout(t); resolve(s); })
            .catch((e) => { if (done) return; done = true; clearTimeout(t); reject(e); });
        });
        streamRef.current = stream;
        set('mic', 'ok', `got stream: ${stream.getAudioTracks().length} track(s)`);
      } catch (e: any) {
        set('mic', 'fail', `${e?.name || 'Error'}: ${e?.message || ''}`);
        set('scriptProc', 'fail', 'no mic stream');
        finish();
        return;
      }

      set('scriptProc', 'running');
      let ctx: AudioContext;
      const Ctor = (window as any).AudioContext || (window as any).webkitAudioContext;
      ctx = new Ctor();
      ctxRef.current = ctx;
      if (ctx.state === 'suspended') { try { await ctx.resume(); } catch { /* ignore */ } }
      setSampleRate(ctx.sampleRate);
      const src = ctx.createMediaStreamSource(stream);
      const sp = ctx.createScriptProcessor(4096, 1, 1);
      let chunks = 0;
      let maxVol = 0;
      sp.onaudioprocess = (ev: AudioProcessingEvent) => {
        if (stopRef.current) return;
        const data = ev.inputBuffer.getChannelData(0);
        let sum = 0;
        for (let i = 0; i < data.length; i++) { const v = data[i]; sum += v * v; }
        const rms = Math.sqrt(sum / data.length);
        maxVol = Math.max(maxVol, rms);
        chunks++;
        setChunkCount(chunks);
        setVolume(rms);
        if (chunks === 1) log(`first PCM chunk: ${data.length} samples @ ${ctx.sampleRate}Hz`);
        if (chunks % 30 === 0) log(`chunk ${chunks}: rms=${rms.toFixed(4)} maxSoFar=${maxVol.toFixed(4)}`);
      };
      src.connect(sp);
      const sink = ctx.createGain();
      sink.gain.value = 0;
      sp.connect(sink);
      sink.connect(ctx.destination);
      set('scriptProc', 'ok', `capturing @ ${ctx.sampleRate}Hz`);
      setTimeout(() => {
        stopRef.current = true;
        try { sp.disconnect(); sink.disconnect(); src.disconnect(); } catch { /* ignore */ }
        try { stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
        try { ctx.close(); } catch { /* ignore */ }
        if (chunks > 0 && maxVol > 0.001) {
          set('scriptProc', 'ok', `captured ${chunks} chunks (~${(chunks * 4096 / ctx.sampleRate).toFixed(1)}s PCM), max RMS ${maxVol.toFixed(4)} @ ${ctx.sampleRate}Hz`);
        } else if (chunks > 0) {
          set('scriptProc', 'fail', `got ${chunks} chunks but SILENT (max RMS ${maxVol.toFixed(4)}) — mic permission OK but no audio (muted? wrong device?)`);
        } else {
          set('scriptProc', 'fail', 'onaudioprocess never fired');
        }
        finish();
      }, 6000);
    } catch (e: any) {
      // Any unexpected throw (e.g. runStaticChecks / AudioContext ctor) — surface
      // it on screen instead of leaving the button spinning forever.
      setProbeErr(`PROBE ERROR: ${e?.name || 'Error'}: ${e?.message || String(e)}`);
      setChecks((cs) => cs.map((c) => c.status === 'pending' ? { ...c, status: 'fail', detail: 'aborted by probe error' } : c));
      finish();
    }
  };

  const finish = () => {
    if (safetyRef.current) { clearTimeout(safetyRef.current); safetyRef.current = null; }
    setRunning(false);
  };

  const copyAll = async () => {
    const text = JSON.stringify(checks, null, 2);
    try {
      await navigator.clipboard?.writeText?.(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
      return;
    } catch { /* fall through to textarea select */ }
    const ta = taRef.current;
    if (ta) { ta.focus(); ta.select(); try { document.execCommand('copy'); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ } }
  };

  const micCheck = checks.find((c) => c.key === 'mic');
  const micVerdict =
    micCheck?.status === 'ok' ? { t: '麦克风 OK ✅', s: 'getUserMedia 拿到音频流 → A/B 方案可走', cls: 'bg-green-500/10 text-green-700 dark:text-green-400' }
    : micCheck?.status === 'fail' ? { t: '麦克风被拦 ❌', s: micCheck.detail || '', cls: 'bg-red-500/10 text-red-700 dark:text-red-400' }
    : micCheck?.status === 'running' ? { t: '麦克风请求中… ⏳', s: micCheck.detail || '', cls: 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400' }
    : { t: '未测试', s: '点上面按钮运行探测', cls: 'bg-muted text-muted-foreground' };

  // Whenever checks change while not running, snapshot the raw JSON for the
  // "copy back" panel. (Done as an effect, not inside a setState updater.)
  useEffect(() => {
    if (!running) {
      setRaw(JSON.stringify(checks, null, 2));
    }
  }, [checks, running]);

  const okCount = checks.filter((c) => c.status === 'ok').length;
  const verdict =
    checks.find((c) => c.key === 'mic')?.status === 'ok' &&
    checks.find((c) => c.key === 'scriptProc')?.status === 'ok' &&
    checks.find((c) => c.key === 'wasm')?.status === 'ok'
      ? (checks.find((c) => c.key === 'simd')?.status === 'ok'
          ? '✅ A 方案前置条件满足：麦克风 + ScriptProcessor + WASM SIMD 都 OK，可上 sherpa-onnx WASM。'
          : '⚠️ 麦克风+WASM OK 但无 SIMD：sherpa-onnx 单线程能跑但慢 2-4x，可能不够实时。')
      : '❌ A 方案前置条件不满足（见下方 ✗ 项）—— 考虑走 B 方案（穿透 bridge→dashscope）。';

  return (
    <div className="h-full overflow-y-auto p-4 text-sm">
      <div className="mx-auto max-w-2xl">
        <h1 className="mb-1 text-base font-semibold">Voice Input Capability Probe</h1>
        <p className="mb-3 text-xs text-muted-foreground">
          在飞书手机端打开此页，点下面的按钮跑探测。麦克风需用户手势触发，所以必须点按钮。
          结果在屏幕上，可截图回传。{running ? '(运行中…)' : ''}
        </p>

        <button
          onClick={runProbe}
          disabled={running}
          className={cn(
            'mb-4 rounded-md px-4 py-2 font-medium text-white',
            running ? 'bg-muted-foreground/40' : 'bg-primary hover:bg-primary/90',
          )}
        >
          {running ? '运行中… (对着麦克风说几句话)' : '▶ 运行探测 (按一次)'}
        </button>

        {/* Live mic meter */}
        {(running || volume > 0) && (
          <div className="mb-4 rounded-md border border-border p-2">
            <div className="mb-1 text-xs text-muted-foreground">
              实时音量 / PCM 块数 {sampleRate ? `@ ${sampleRate}Hz` : ''}
            </div>
            <div className="h-3 w-full overflow-hidden rounded bg-muted">
              <div
                className="h-full bg-green-500 transition-all"
                style={{ width: `${Math.min(100, volume * 400)}%` }}
              />
            </div>
            <div className="mt-1 text-[11px] tabular-nums text-muted-foreground">
              chunks: {chunkCount} · rms: {volume.toFixed(4)}
            </div>
          </div>
        )}

        <div className={cn('mb-4 rounded-md p-2 text-xs font-medium', verdict.startsWith('✅') ? 'bg-green-500/10 text-green-700 dark:text-green-400' : verdict.startsWith('⚠') ? 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400' : 'bg-red-500/10 text-red-700 dark:text-red-400')}>
          {verdict}
        </div>

        {/* Big mic verdict — the one bit that decides everything */}
        <div className={cn('mb-4 rounded-lg p-3', micVerdict.cls)}>
          <div className="text-lg font-bold">{micVerdict.t}</div>
          <div className="mt-1 break-all text-xs">{micVerdict.s}</div>
        </div>

        {probeErr && (
          <div className="mb-4 break-all rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-700 dark:text-red-400">
            ⚠️ {probeErr}
          </div>
        )}

        <ul className="space-y-1">
          {checks.map((c) => (
            <li key={c.key} className="flex flex-col rounded-md border border-border px-2 py-1">
              <div className="flex items-center gap-2">
                <span className="w-5 text-center">{icon(c.status)}</span>
                <span className="font-medium">{c.label}</span>
                <span className="ml-auto text-[10px] uppercase text-muted-foreground">{c.status}</span>
              </div>
              {c.detail && <div className="ml-7 break-all text-[11px] text-muted-foreground">{c.detail}</div>}
            </li>
          ))}
        </ul>

        {captureLog.length > 0 && (
          <details className="mt-4">
            <summary className="cursor-pointer text-xs text-muted-foreground">capture log ({captureLog.length})</summary>
            <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-[10px]">{captureLog.join('\n')}</pre>
          </details>
        )}

        {raw && (
          <div className="mt-2 mb-8">
            <div className="mb-1 flex items-center gap-2">
              <span className="text-xs text-muted-foreground">raw JSON</span>
              <button
                onClick={copyAll}
                className="rounded border border-border px-2 py-0.5 text-xs hover:bg-accent"
              >
                {copied ? '✓ 已复制' : '📋 复制'}
              </button>
            </div>
            <textarea
              ref={taRef}
              readOnly
              value={raw}
              onFocus={(e) => { e.currentTarget.select(); }}
              className="h-48 w-full resize-y overflow-auto rounded bg-muted p-2 font-mono text-[10px]"
            />
          </div>
        )}

        <p className="mb-8 text-[11px] text-muted-foreground">
          判定：mic + scriptProc + wasm 都 OK → A 方案可行；缺 SIMD → A 慢但可试；mic 被拒 → 走 B 方案。
          ({okCount}/{checks.length} checks ok)
        </p>
      </div>
    </div>
  );
};

function icon(s: Status): string {
  switch (s) {
    case 'ok': return '✅';
    case 'fail': return '❌';
    case 'running': return '⏳';
    default: return '·';
  }
}

export default VoiceProbe;
