import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useParams } from 'react-router-dom';
import {
  AGENT_LABELS,
  formatContext,
  getSession,
  listEvents,
  renameSession,
  sendCommand,
  sessionTitle,
  type EventRow,
  type SessionRow,
} from '../api/bridge';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import { Input } from '../components/ui/input';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '../components/ui/collapsible';
import { useVoiceRelay, VoiceDisabledError } from '../hooks/use-voice-relay';
import { cn } from '../lib/utils';

const PAGE_SIZE = 500;

// A turn = user message → (tool calls / intermediate assistant) → final assistant answer.
interface Turn {
  user?: EventRow;
  process: EventRow[];
  answer?: EventRow;
}

function isToolCallContent(s: string): boolean {
  return s.startsWith('🔧');
}

/** Agent-triggered scheduled prompts come in as role='user' but with a marker;
 *  they are not real user input, so we fold + dim them. */
function isScheduledUser(s: string | undefined): boolean {
  return !!s && s.startsWith('[Scheduled prompt');
}

function groupTurns(events: EventRow[]): Turn[] {
  const turns: Turn[] = [];
  let cur: Turn | null = null;
  for (const e of events) {
    // '✓ turn complete' is a lock signal, not conversation content; hide it.
    if (e.role === 'system' && e.content === '✓ turn complete') continue;
    if (e.role === 'user') {
      if (cur) turns.push(cur);
      cur = { user: e, process: [], answer: undefined };
      continue;
    }
    if (!cur) {
      cur = { user: undefined, process: [e], answer: undefined };
      continue;
    }
    if (e.role === 'assistant' && e.content && !isToolCallContent(e.content)) {
      if (cur.answer) cur.process.push(cur.answer);
      cur.answer = e;
    } else {
      cur.process.push(e);
    }
  }
  if (cur) turns.push(cur);
  return turns;
}

const Conversation: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [events, setEvents] = useState<EventRow[]>([]);
  const [session, setSession] = useState<SessionRow | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [pending, setPending] = useState<{ content: string; ts: string }[]>([]);
  const [nowTick, setNowTick] = useState(0);
  const lastTsRef = useRef<string>('');
  const oldestTsRef = useRef<string>('');
  const scrollRef = useRef<HTMLDivElement>(null);

  // ---- Voice input (press-and-hold) ----
  // Two paths, picked at press time:
  //  (A) Bridge WSS relay (useVoiceRelay): page captures PCM, relays to a
  //      server-side ASR backend (cloud DashScope / local FunASR), streams
  //      partial/final text back LIVE. This is the path that works on the
  //      Feishu Android WebView, where Web Speech interim results never fire.
  //  (B) Browser Web Speech API: zero-config fallback when voice is OFF or
  //      the relay is unreachable. On Feishu Android it only emits text on
  //      release, but it's better than nothing when no relay is configured.
  // Both paths share the same finalTextRef/holdBaseLenRef/setInput
  // accumulation, so the composer UI below is path-agnostic.
  const SR: any =
    typeof window !== 'undefined'
      ? (window as any).SpeechRecognition ||
        (window as any).webkitSpeechRecognition
      : null;
  // Track which path is active on the current press-hold so stopListen routes
  // to the right teardown (relay.stop keeps the WSS warm; recRef.stop is Web Speech).
  const usingRelayRef = useRef(false);
  // Button shows if Web Speech is present (the relay, if configured, is used
  // when prepare() succeeds on voice-mode enter; otherwise Web Speech fallback).
  const voiceSupported = !!SR;

  const [listening, setListening] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const recRef = useRef<any>(null);
  const holdRef = useRef(false);
  const finalTextRef = useRef('');
  const holdBaseLenRef = useRef(0);

  // Relay results -> same accumulation shape as the Web Speech onresult below:
  // finalTextRef holds all committed segments; partial is the trailing interim.
  const onPartialRelay = useCallback((text: string) => {
    setInput((finalTextRef.current + text).slice(0, 8000));
  }, []);
  const onFinalRelay = useCallback((text: string) => {
    finalTextRef.current += text;
    setInput(finalTextRef.current.slice(0, 8000));
  }, []);
  const onErrorRelay = useCallback((msg: string) => {
    setErr(`voice relay: ${msg}`);
    holdRef.current = false;
  }, []);
  const {
    state: relayState,
    prepare: relayPrepare,
    start: relayStart,
    stop: relayStop,
    teardown: relayTeardown,
  } = useVoiceRelay({
    onPartial: onPartialRelay,
    onFinal: onFinalRelay,
    onError: onErrorRelay,
  });

  const beginRecognition = useCallback(() => {
    if (!SR) return;
    const rec = new SR();
    rec.lang = 'zh-CN';
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e: any) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const seg = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalTextRef.current += seg;
        else interim += seg;
      }
      setInput((finalTextRef.current + interim).slice(0, 8000));
    };
    rec.onend = () => {
      // Browser ended (silence/timeout). If still held, restart so recognition
      // stays continuous across pauses instead of dropping after one sentence.
      if (holdRef.current) {
        try {
          rec.start();
          return;
        } catch {
          /* fall through */
        }
      }
      setListening(false);
    };
    rec.onerror = (e: any) => {
      // no-speech/aborted/interrupted are benign (a pause with no words); don't
      // surface them or they'd abort the whole hold on every brief silence.
      if (
        e.error === 'no-speech' ||
        e.error === 'aborted' ||
        e.error === 'interrupted'
      )
        return;
      setErr(`voice: ${e.error || 'error'}`);
    };
    recRef.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch {
      setListening(false);
    }
  }, [SR]);

  const startListen = useCallback(async () => {
    holdRef.current = true;
    finalTextRef.current = input;
    holdBaseLenRef.current = input.length;
    // Relay path if prepare() armed it (state 'ready'); else Web Speech.
    if (relayState === 'ready') {
      try {
        await relayStart();
        usingRelayRef.current = true;
        setListening(true);
        return;
      } catch (e: any) {
        // Relay start failed (e.g. ASR connect error): reset the relay and
        // fall back to Web Speech for this hold.
        relayTeardown();
        usingRelayRef.current = false;
        if (e instanceof VoiceDisabledError) {
          /* fall through to Web Speech */
        } else {
          setErr(`voice relay: ${e?.message || e}; using Web Speech`);
        }
      }
    } else {
      usingRelayRef.current = false;
    }
    beginRecognition();
  }, [input, relayState, relayStart, relayTeardown, beginRecognition]);

  const stopListen = useCallback(() => {
    holdRef.current = false;
    if (usingRelayRef.current) {
      // Relay flushes the final segment before resolving {ended}; keeps the
      // WSS warm (state back to 'ready') so the next press is instant.
      relayStop().catch(() => {
        /* ignore */
      });
    } else {
      try {
        recRef.current?.stop();
      } catch {
        /* ignore */
      }
    }
    setListening(false);
    // Stay in voice mode (armed) after release; tap 🎤 again to exit. The
    // recognized text is already in the input box, editable.
  }, [relayStop]);

  const toggleVoiceMode = useCallback(async () => {
    // Listening? Tapping 🎤 releases (stopListen).
    if (listening) {
      stopListen();
      return;
    }
    // Already in voice mode (armed, not listening)? Tap exits to keyboard.
    if (voiceMode) {
      relayTeardown();
      usingRelayRef.current = false;
      setVoiceMode(false);
      return;
    }
    // Entering voice mode: preconnect the relay (WSS + mic + audio graph) so
    // press-hold only pays the ASR-connect cost. Best-effort — if it fails
    // (voice off / transport error), press-hold falls back to Web Speech.
    try {
      await relayPrepare();
    } catch {
      /* best effort; startListen falls back to Web Speech */
    }
    setVoiceMode(true);
  }, [listening, voiceMode, stopListen, relayPrepare, relayTeardown]);

  useEffect(
    () => () => {
      holdRef.current = false;
      try {
        recRef.current?.stop();
      } catch {
        /* ignore */
      }
      relayTeardown();
    },
    [relayTeardown],
  );

  // Tick every 2s so time-based busy clearing re-evaluates even with no new events.
  useEffect(() => {
    const t = setInterval(() => setNowTick((x) => x + 1), 2000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    const refreshSession = () => {
      getSession(sessionId)
        .then((row) => {
          if (!cancelled) setSession(row);
        })
        .catch(() => {});
    };
    setSession(null);
    refreshSession();
    const t = setInterval(refreshSession, 10000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    setEvents([]);
    setErr(null);
    setLoading(true);
    setHasMore(false);
    setPending([]);
    lastTsRef.current = '';
    oldestTsRef.current = '';

    let cancelled = false;
    let inFlight = false;
    const seen = new Set<string | number>();
    const applyRows = (rows: EventRow[]) => {
      let maxTs = lastTsRef.current;
      let minTs = oldestTsRef.current || '';
      const fresh: EventRow[] = [];
      for (const r of rows) {
        if (seen.has(r.id)) continue;
        seen.add(r.id);
        fresh.push(r);
        const t = r.ts ?? '';
        if (t > maxTs) maxTs = t;
        if (t && (!minTs || t < minTs)) minTs = t;
      }
      if (fresh.length) setEvents((prev) => [...prev, ...fresh]);
      if (maxTs > lastTsRef.current) lastTsRef.current = maxTs;
      if (minTs && (!oldestTsRef.current || minTs < oldestTsRef.current))
        oldestTsRef.current = minTs;
      // Drop optimistic pending messages once their real event lands.
      const userContents = new Set(
        fresh.filter((r) => r.role === 'user').map((r) => r.content),
      );
      if (userContents.size) {
        setPending((p) => p.filter((m) => !userContents.has(m.content)));
      }
      return fresh.length;
    };
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const since = lastTsRef.current || undefined;
        const rows = await listEvents(sessionId, since, undefined, PAGE_SIZE);
        if (cancelled) return;
        const n = applyRows(rows);
        if (!since && n === PAGE_SIZE) setHasMore(true); // initial page full → maybe more older
        setErr(null);
      } catch (e: any) {
        setErr(
          e?.response?.status
            ? `HTTP ${e.response.status}`
            : String(e?.message ?? e),
        );
      } finally {
        inFlight = false;
        if (!cancelled) setLoading(false);
      }
    };
    poll();
    const t = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [sessionId]);

  const loadOlder = async () => {
    if (!sessionId || loadingOlder || !oldestTsRef.current) return;
    setLoadingOlder(true);
    const prevScroll = scrollRef.current;
    const prevHeight = prevScroll?.scrollHeight ?? 0;
    try {
      const rows = await listEvents(
        sessionId,
        undefined,
        oldestTsRef.current,
        PAGE_SIZE,
      );
      // prepend (oldest-first from API), dedup by id
      setEvents((prev) => {
        const seenIds = new Set(prev.map((e) => e.id));
        const fresh = rows.filter((r) => !seenIds.has(r.id));
        if (fresh.length) {
          const minTs = fresh.reduce(
            (m, r) => (r.ts && (!m || r.ts < m) ? r.ts : m),
            '',
          );
          if (minTs && (!oldestTsRef.current || minTs < oldestTsRef.current))
            oldestTsRef.current = minTs;
        }
        return [...fresh, ...prev];
      });
      setHasMore(rows.length === PAGE_SIZE);
      // keep scroll position stable after prepend
      requestAnimationFrame(() => {
        if (prevScroll)
          prevScroll.scrollTop =
            prevScroll.scrollHeight - prevHeight + prevScroll.scrollTop;
      });
    } catch (e: any) {
      setErr(
        e?.response?.status
          ? `HTTP ${e.response.status}`
          : String(e?.message ?? e),
      );
    } finally {
      setLoadingOlder(false);
    }
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  const onSend = async () => {
    const text = input.trim();
    if (!text || !sessionId || sending) return;
    setSending(true);
    // Optimistic: show the user's message instantly, before the round-trip.
    // BUT when responding to a surfaced terminal prompt, the text goes via
    // tmux send-keys and is NOT echoed back as a user.message event, so skip
    // the optimistic bubble (it would never clear and keep Send frozen).
    if (!pendingPrompt) {
      setPending((p) => [
        ...p,
        { content: text, ts: new Date().toISOString() },
      ]);
    }
    try {
      await sendCommand(sessionId, text, session?.agent);
      setInput('');
      setErr(null);
    } catch (e: any) {
      // Roll back the optimistic bubble on failure.
      setPending((p) => p.filter((m) => m.content !== text));
      setErr(
        e?.response?.status
          ? `HTTP ${e.response.status}`
          : String(e?.message ?? e),
      );
    } finally {
      setSending(false);
    }
  };

  // Agent-busy lock: freeze Send while the backend agent is working on a turn.
  // Busy iff (a) a message was just sent and not yet echoed, or (b) the last
  // user message has no final answer yet AND there was recent activity
  // (otherwise a stalled turn frees up after 90s).
  // A recent "📋 terminal waiting for input" system event means the live agent
  // is blocked on an interactive prompt (permission/picker). Surface it and
  // UNFREEZE Send so the user can type the response (it goes via tmux send-keys).
  // Only active when the prompt is the MOST RECENT event — once the agent
  // resumes and emits any newer output, the prompt is considered resolved.
  const pendingPrompt = useMemo(() => {
    const last = events[events.length - 1];
    if (!last) return null;
    if (
      last.role !== 'system' ||
      !last.content ||
      !last.content.startsWith('📋 terminal')
    ) {
      return null;
    }
    const ageMs = last.ts ? Date.now() - new Date(last.ts).getTime() : Infinity;
    if (ageMs < 60000) return last;
    return null;
  }, [events, nowTick]);

  // Send-lock: is the agent still working on the turn after the last user msg?
  // Release on a REAL completion signal (the tailer writes a '✓ turn complete'
  // system marker when it sees end_turn / turn_end / task_complete), so we
  // don't unlock on intermediate "let me check…" text that's followed by a
  // tool call. Codex has no per-turn end marker, so for it we also release
  // when the last event is an assistant reply and the stream has been silent
  // for a few seconds (settled). 90s of total silence is the crash fallback.
  const busy = useMemo(() => {
    if (pendingPrompt) return false; // agent is waiting on a terminal prompt -> respond
    if (pending.length > 0) return true;
    let lastUser = -1;
    for (let i = events.length - 1; i >= 0; i--) {
      if (events[i].role === 'user') {
        lastUser = i;
        break;
      }
    }
    if (lastUser === -1) return false;
    const after = events.slice(lastUser + 1);
    // Explicit turn-complete marker after the last user msg -> reliably done.
    if (
      after.some((e) => e.role === 'system' && e.content === '✓ turn complete')
    ) {
      return false;
    }
    const last = events[events.length - 1];
    const idleMs = last?.ts
      ? Date.now() - new Date(last.ts).getTime()
      : Infinity;
    // Codex fallback: no completion marker, but the last event is an assistant
    // reply (not a tool call) and the stream has settled -> done with this turn.
    const lastIsReply =
      !!last &&
      last.role === 'assistant' &&
      !!last.content &&
      !last.content.startsWith('🔧');
    if (lastIsReply && idleMs > 4000) return false;
    if (idleMs > 90000) return false; // no activity for 90s -> assume done/stalled
    return true;
  }, [events, pending, pendingPrompt, nowTick]);

  const turns = useMemo(() => groupTurns(events), [events]);

  if (!sessionId) return <div className="p-4 text-sm">No session.</div>;

  const title = sessionTitle(session, sessionId);
  const agent = session?.agent || 'copilot';
  const ctxLabel = formatContext(session);

  const startRename = () => {
    setNameDraft(title);
    setEditingName(true);
  };
  const saveRename = async () => {
    const next = nameDraft.trim();
    setEditingName(false);
    // no change (or cleared and nothing persisted) -> skip
    if (next === (session?.displayName || session?.display_name || '')) return;
    try {
      const updated = await renameSession(sessionId, next || null);
      if (updated) setSession(updated);
    } catch (e: any) {
      setErr(
        e?.response?.status
          ? `HTTP ${e.response.status}`
          : String(e?.message ?? e),
      );
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 flex-col gap-0.5 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          {session?.online && (
            <span className="h-2 w-2 shrink-0 rounded-full bg-green-500" />
          )}
          <span
            className={cn(
              'shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase',
              agent === 'claude'
                ? 'bg-orange-500/15 text-orange-700 dark:text-orange-400'
                : agent === 'codex'
                  ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                  : 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
            )}
          >
            {AGENT_LABELS[agent] || agent}
          </span>
          {editingName ? (
            <Input
              autoFocus
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onBlur={saveRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  saveRename();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  setEditingName(false);
                }
              }}
              className="h-7 flex-1 text-sm"
              placeholder="Session name (empty = use default)"
            />
          ) : (
            <button
              onClick={startRename}
              className="flex min-w-0 flex-1 items-center gap-1 text-left text-sm font-medium hover:opacity-80"
              title="Click to rename"
            >
              <span className="truncate">{title}</span>
              <span className="shrink-0 text-[11px] opacity-50">✎</span>
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="truncate">{session?.cwd || sessionId}</span>
          <span className="font-mono shrink-0">{sessionId.slice(0, 8)}</span>
          {ctxLabel && (
            <span className="ml-auto shrink-0 font-mono">{ctxLabel}</span>
          )}
        </div>
      </header>

      {err && (
        <div className="shrink-0 bg-destructive/10 px-3 py-1 text-xs text-destructive">
          Error: {err}
        </div>
      )}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-3 px-3 py-4">
          {loading && events.length === 0 && (
            <div className="text-sm text-muted-foreground">Loading…</div>
          )}
          {!loading && events.length === 0 && !err && (
            <div className="text-sm text-muted-foreground">
              No events on disk for this session. If it is online, send a
              command below — the bridge will run it via{' '}
              <code>copilot --resume</code> and the answer will appear here.
            </div>
          )}
          {hasMore && (
            <div className="flex justify-center py-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={loadOlder}
                disabled={loadingOlder}
              >
                {loadingOlder ? 'Loading…' : 'Load older messages'}
              </Button>
            </div>
          )}
          {turns.map((t, i) => (
            <TurnView key={i} turn={t} />
          ))}
          {pending.map((m, i) => (
            <div key={`pending-${i}`} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-primary-foreground">
                {m.content}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="shrink-0 border-t border-border p-3">
        {pendingPrompt && (
          <div className="mx-auto mb-2 max-w-3xl rounded-lg border border-amber-400 bg-amber-50 p-2 text-[12px] text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <div className="mb-1 font-semibold">
              📋 Terminal is waiting for your input
            </div>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-amber-100/60 p-1 font-mono text-[11px] dark:bg-amber-900/30">
              {(pendingPrompt.content || '').replace(
                /^📋 terminal waiting for input:\n?/,
                '',
              )}
            </pre>
            <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
              Type your response (e.g. <code>y</code> / <code>1</code>) and
              press Send — it goes straight to the live terminal.
            </div>
          </div>
        )}
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          {voiceSupported && (
            <Button
              variant="outline"
              size="icon"
              onClick={toggleVoiceMode}
              disabled={busy}
              title={voiceMode ? '切回键盘输入' : '切换语音输入模式'}
              className={
                voiceMode
                  ? 'select-none border-primary bg-primary/10 text-primary'
                  : 'select-none'
              }
            >
              🎤
            </Button>
          )}
          {voiceMode && voiceSupported ? (
            <button
              type="button"
              onPointerDown={(e) => {
                e.preventDefault();
                if (!busy) startListen();
              }}
              onPointerUp={stopListen}
              onPointerLeave={stopListen}
              onPointerCancel={stopListen}
              disabled={busy}
              className={
                'min-h-[72px] flex-1 resize-none rounded-md border-2 border-dashed px-4 py-3 text-center text-sm transition-colors select-none touch-none ' +
                (listening
                  ? 'border-red-500 bg-red-50 text-red-900 animate-pulse dark:bg-red-950/30 dark:text-red-200'
                  : 'border-input bg-muted/50 text-muted-foreground hover:bg-muted')
              }
            >
              {listening ? (
                <div className="flex flex-col items-center gap-1.5 py-1">
                  <span className="text-base font-medium leading-snug text-red-900 dark:text-red-200">
                    {input.slice(holdBaseLenRef.current).trim() || '正在聆听…'}
                  </span>
                  <span className="flex items-center gap-2 text-[11px] text-red-500/80">
                    <span className="flex items-end gap-0.5 h-3">
                      {[
                        { h: 'h-1', d: '0ms' },
                        { h: 'h-2.5', d: '120ms' },
                        { h: 'h-3', d: '240ms' },
                        { h: 'h-2', d: '360ms' },
                        { h: 'h-1.5', d: '480ms' },
                      ].map((b, i) => (
                        <span
                          key={i}
                          className={`w-1 ${b.h} rounded-full bg-red-500 animate-pulse`}
                          style={{ animationDelay: b.d }}
                        />
                      ))}
                    </span>
                    松开结束
                  </span>
                </div>
              ) : input.trim() ? (
                `📝 ${input.slice(-60)}  ·  按住继续说话`
              ) : (
                '按住 说话'
              )}
            </button>
          ) : (
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  onSend();
                }
              }}
              placeholder={
                pendingPrompt
                  ? 'Respond to the terminal… (Enter to send)'
                  : busy
                    ? 'Agent is working — wait for it to finish…'
                    : 'Send a command to this session… (Enter to send, or tap 🎤 for voice)'
              }
              className="max-h-32 min-h-[40px] resize-none"
              rows={2}
              disabled={busy}
            />
          )}
          <Button
            onClick={onSend}
            disabled={busy || listening || sending || !input.trim()}
          >
            {busy ? 'Working…' : 'Send'}
          </Button>
        </div>
        {busy && !pendingPrompt && (
          <div className="mx-auto mt-1 max-w-3xl text-[11px] text-muted-foreground">
            <span className="inline-block animate-pulse">⏳</span> Agent is
            working on this session. Send is frozen until it finishes.
          </div>
        )}
        {listening && (
          <div className="mx-auto mt-1 max-w-3xl text-[11px] text-red-500">
            🎙 Listening… (speak; text fills the box). Tap 🎤 again to stop.
          </div>
        )}
      </div>
    </div>
  );
};

const TurnView: React.FC<{ turn: Turn }> = ({ turn }) => {
  const hasProcess = turn.process.length > 0;
  const scheduled = isScheduledUser(turn.user?.content);

  // Scheduled (agent-triggered) prompts are NOT real user input: render as a
  // folded, dimmed amber block on the left, distinct from real user bubbles.
  if (scheduled) {
    const firstLine = (turn.user?.content || '').split('\n')[0];
    const restLines = (turn.user?.content || '')
      .split('\n')
      .slice(1)
      .join('\n');
    return (
      <div className="flex flex-col gap-2">
        <Collapsible>
          <CollapsibleTrigger className="flex items-center gap-1 rounded-md bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-400 hover:bg-amber-500/20">
            <span className="text-[10px]">⏰</span>
            <span className="font-medium">{firstLine}</span>
            <span className="opacity-60">· scheduled</span>
            <span className="text-[10px]">▾</span>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="ml-2 border-l border-amber-500/30 pl-3">
              {restLines && (
                <pre className="whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground">
                  {restLines}
                </pre>
              )}
              {turn.process.map((e) => (
                <ProcessRow key={String(e.id)} ev={e} />
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
        {turn.answer && (
          <div className="flex justify-start">
            <div className="max-w-[90%] whitespace-pre-wrap break-words rounded-2xl rounded-bl-sm bg-muted px-3 py-2">
              {turn.answer.content}
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {turn.user && (
        <div className="flex justify-end">
          <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-primary-foreground">
            {turn.user.content}
          </div>
        </div>
      )}
      {hasProcess && (
        <Collapsible>
          <CollapsibleTrigger className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent">
            <span className="text-[10px]">⚙️</span>
            <span>
              process · {turn.process.length} step
              {turn.process.length > 1 ? 's' : ''}
            </span>
            <span className="text-[10px]">▾</span>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="ml-2 flex flex-col gap-1 border-l border-border pl-3">
              {turn.process.map((e) => (
                <ProcessRow key={String(e.id)} ev={e} />
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
      {turn.answer ? (
        <div className="flex justify-start">
          <div className="max-w-[90%] whitespace-pre-wrap break-words rounded-2xl rounded-bl-sm bg-muted px-3 py-2">
            {turn.answer.content}
          </div>
        </div>
      ) : hasProcess ? (
        <div className="text-xs italic text-muted-foreground">…working</div>
      ) : null}
    </div>
  );
};

const ProcessRow: React.FC<{ ev: EventRow }> = ({ ev }) => {
  const role = ev.role || 'system';
  const content = ev.content || '';
  return (
    <div
      className={cn(
        'font-mono text-[11px]',
        role === 'system'
          ? 'italic text-muted-foreground'
          : 'text-muted-foreground',
      )}
    >
      <span className="break-words">{content}</span>
    </div>
  );
};

export default Conversation;
