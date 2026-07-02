import React, { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { AGENT_LABELS, formatContext, getSession, listEvents, renameSession, sendCommand, sessionTitle, type EventRow, type SessionRow } from '../api/bridge';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';
import { Input } from '../components/ui/input';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../components/ui/collapsible';
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
  const lastTsRef = useRef<string>('');
  const oldestTsRef = useRef<string>('');
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!sessionId) return;
    setSession(null);
    getSession(sessionId).then(setSession).catch(() => {});
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
      if (minTs && (!oldestTsRef.current || minTs < oldestTsRef.current)) oldestTsRef.current = minTs;
      // Drop optimistic pending messages once their real event lands.
      const userContents = new Set(fresh.filter((r) => r.role === 'user').map((r) => r.content));
      if (userContents.size) {
        setPending((p) => p.filter((m) => !userContents.has(m.content)));
      }
      return fresh.length;
    };
    const poll = async () => {
      try {
        const since = lastTsRef.current || undefined;
        const rows = await listEvents(sessionId, since, undefined, PAGE_SIZE);
        if (cancelled) return;
        const n = applyRows(rows);
        if (!since && n === PAGE_SIZE) setHasMore(true); // initial page full → maybe more older
        setErr(null);
      } catch (e: any) {
        setErr(e?.response?.status ? `HTTP ${e.response.status}` : String(e?.message ?? e));
      } finally {
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
      const rows = await listEvents(sessionId, undefined, oldestTsRef.current, PAGE_SIZE);
      // prepend (oldest-first from API), dedup by id
      setEvents((prev) => {
        const seenIds = new Set(prev.map((e) => e.id));
        const fresh = rows.filter((r) => !seenIds.has(r.id));
        if (fresh.length) {
          const minTs = fresh.reduce((m, r) => (r.ts && (!m || r.ts < m) ? r.ts : m), '');
          if (minTs && (!oldestTsRef.current || minTs < oldestTsRef.current)) oldestTsRef.current = minTs;
        }
        return [...fresh, ...prev];
      });
      setHasMore(rows.length === PAGE_SIZE);
      // keep scroll position stable after prepend
      requestAnimationFrame(() => {
        if (prevScroll) prevScroll.scrollTop = prevScroll.scrollHeight - prevHeight + prevScroll.scrollTop;
      });
    } catch (e: any) {
      setErr(e?.response?.status ? `HTTP ${e.response.status}` : String(e?.message ?? e));
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
    setPending((p) => [...p, { content: text, ts: new Date().toISOString() }]);
    try {
      await sendCommand(sessionId, text, session?.agent);
      setInput('');
      setErr(null);
    } catch (e: any) {
      // Roll back the optimistic bubble on failure.
      setPending((p) => p.filter((m) => m.content !== text));
      setErr(e?.response?.status ? `HTTP ${e.response.status}` : String(e?.message ?? e));
    } finally {
      setSending(false);
    }
  };

  if (!sessionId) return <div className="p-4 text-sm">No session.</div>;

  const title = sessionTitle(session, sessionId);
  const agent = session?.agent || 'copilot';
  const ctxLabel = formatContext(session);
  const turns = groupTurns(events);

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
      setErr(e?.response?.status ? `HTTP ${e.response.status}` : String(e?.message ?? e));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 flex-col gap-0.5 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          {session?.online && <span className="h-2 w-2 shrink-0 rounded-full bg-green-500" />}
          <span className={cn(
            'shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase',
            agent === 'claude' ? 'bg-orange-500/15 text-orange-700 dark:text-orange-400'
              : agent === 'codex' ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
              : 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
          )}>
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
          {ctxLabel && <span className="ml-auto shrink-0 font-mono">{ctxLabel}</span>}
        </div>
      </header>

      {err && <div className="shrink-0 bg-destructive/10 px-3 py-1 text-xs text-destructive">Error: {err}</div>}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-3 px-3 py-4">
          {loading && events.length === 0 && (
            <div className="text-sm text-muted-foreground">Loading…</div>
          )}
          {!loading && events.length === 0 && !err && (
            <div className="text-sm text-muted-foreground">
              No events on disk for this session. If it is online, send a command below — the bridge
              will run it via <code>copilot --resume</code> and the answer will appear here.
            </div>
          )}
          {hasMore && (
            <div className="flex justify-center py-1">
              <Button variant="ghost" size="sm" onClick={loadOlder} disabled={loadingOlder}>
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
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            placeholder="Send a command to this session… (Enter to send)"
            className="max-h-32 min-h-[40px] resize-none"
            rows={2}
          />
          <Button onClick={onSend} disabled={sending || !input.trim()}>
            Send
          </Button>
        </div>
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
    const restLines = (turn.user?.content || '').split('\n').slice(1).join('\n');
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
            <span>process · {turn.process.length} step{turn.process.length > 1 ? 's' : ''}</span>
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
    <div className={cn('font-mono text-[11px]', role === 'system' ? 'italic text-muted-foreground' : 'text-muted-foreground')}>
      <span className="break-words">{content}</span>
    </div>
  );
};

export default Conversation;
