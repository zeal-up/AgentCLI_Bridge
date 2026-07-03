import React, { useEffect, useMemo, useState } from 'react';
import { AGENT_LABELS, archiveSession, listSessions, sessionTitle, type SessionRow } from '../api/bridge';
import { cn } from '../lib/utils';

interface Props {
  selected: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

type Agent = 'copilot' | 'claude' | 'codex';
const AGENTS: Agent[] = ['copilot', 'claude', 'codex'];

const DOT: Record<Agent, string> = {
  copilot: 'bg-sky-500',
  claude: 'bg-orange-500',
  codex: 'bg-emerald-500',
};
const BADGE: Record<Agent, string> = {
  copilot: 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
  claude: 'bg-orange-500/15 text-orange-700 dark:text-orange-400',
  codex: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
};

const SessionsDrawer: React.FC<Props> = ({ selected, onSelect, onClose }) => {
  const [allRows, setAllRows] = useState<SessionRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // No "All" tab — the list is always scoped to one agent. Default to the
  // agent the user is most likely actively using (claude here).
  const [filter, setFilter] = useState<Agent>('claude');
  const [showArchived, setShowArchived] = useState(false);

  // Always fetch the full set (all agents) so per-agent counts stay accurate
  // when switching tabs without a refetch; the list is filtered client-side.
  const refresh = () => {
    listSessions(undefined, showArchived)
      .then((r) => setAllRows(r))
      .catch((e) => setErr(e?.response?.status ? `HTTP ${e.response.status}` : String(e?.message ?? e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, [showArchived]);

  const counts = useMemo(() => {
    const c: Record<Agent, number> = { copilot: 0, claude: 0, codex: 0 };
    for (const r of allRows) {
      const a = (r.agent || 'copilot') as Agent;
      if (a in c) c[a]++;
    }
    return c;
  }, [allRows]);

  const rows = useMemo(() => {
    return allRows
      .filter((r) => (r.agent || 'copilot') === filter)
      .sort((a, b) => {
        // Online first, then most-recently-updated.
        const ao = a.online ? 1 : 0;
        const bo = b.online ? 1 : 0;
        if (ao !== bo) return bo - ao;
        const at = a.updatedAt ?? a.updated_at ?? '';
        const bt = b.updatedAt ?? b.updated_at ?? '';
        return bt.localeCompare(at);
      });
  }, [allRows, filter]);

  return (
    <div className="flex h-full flex-col">
      <div className="sticky top-0 z-10 flex items-center justify-between bg-background px-3 py-2 text-sm font-semibold shadow-sm">
        <span>Sessions</span>
        <button onClick={onClose} className="rounded px-2 py-0.5 text-xs hover:bg-accent">✕</button>
      </div>

      {/* Agent picker — vertical, larger touch targets. No "All" tab. */}
      <div className="flex flex-col gap-1 border-b border-border px-2 py-2">
        {AGENTS.map((f) => {
          const active = filter === f;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                'relative flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
                active
                  ? 'bg-accent font-semibold text-accent-foreground'
                  : 'font-medium text-foreground/80 hover:bg-accent/60',
              )}
            >
              <span
                className={cn(
                  'absolute left-0 top-2 bottom-2 w-1 rounded-full',
                  active ? DOT[f] : 'bg-transparent',
                )}
              />
              <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', DOT[f])} />
              <span>{AGENT_LABELS[f]}</span>
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {loading ? '…' : counts[f]}
              </span>
            </button>
          );
        })}
        <label className="mt-1 flex items-center gap-1.5 px-3 py-1 text-[11px] text-muted-foreground">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            className="h-3 w-3"
          />
          show archived
        </label>
      </div>

      {err && <div className="px-3 py-2 text-xs text-destructive">Error: {err}</div>}
      <div className="flex-1 overflow-y-auto">
        <ul className="py-1">
          {rows.map((r) => {
            const active = r.id === selected;
            const label = sessionTitle(r, r.id);
            const ts = r.updatedAt ?? r.updated_at ?? '';
            const agent = (r.agent || 'copilot') as Agent;
            const isHidden = !!r.hidden;
            return (
              <li key={`${agent}:${r.id}`}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelect(r.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(r.id); } }}
                  className={cn(
                    'flex flex-col gap-0.5 px-3 py-2 text-sm hover:bg-accent cursor-pointer',
                    active && 'bg-accent',
                    isHidden && 'opacity-50',
                  )}
                >
                  <div className="flex items-center gap-2 text-left">
                    <span className={cn('h-2 w-2 shrink-0 rounded-full', r.online ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                    <span className="truncate font-medium">{label}</span>
                    {isHidden && <span className="shrink-0 text-[9px] text-muted-foreground">archived</span>}
                    <span className={cn(
                      'ml-auto shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase',
                      BADGE[agent],
                    )}>
                      {AGENT_LABELS[agent] || agent}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 pl-4 text-xs text-muted-foreground">
                    <span className="shrink-0 font-mono">{r.id.slice(0, 8)}</span>
                    <span className="break-all">{r.cwd || r.id}</span>
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        await archiveSession(r.id, !isHidden);
                        refresh();
                      }}
                      className="ml-auto shrink-0 text-[11px] hover:text-destructive"
                      title={isHidden ? 'Unarchive' : 'Archive (hide)'}
                    >
                      {isHidden ? '↩' : '🗑'}
                    </button>
                  </div>
                  {ts && <span className="pl-4 text-[10px] text-muted-foreground/70">{ts}</span>}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
};

export default SessionsDrawer;
