import React, { useEffect, useState } from 'react';
import { AGENT_LABELS, archiveSession, listSessions, sessionTitle, type SessionRow } from '../api/bridge';
import { cn } from '../lib/utils';

interface Props {
  selected: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}

type Filter = 'all' | 'copilot' | 'claude' | 'codex';

const FILTERS: Filter[] = ['all', 'copilot', 'claude', 'codex'];

const SessionsDrawer: React.FC<Props> = ({ selected, onSelect, onClose }) => {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('all');
  const [showArchived, setShowArchived] = useState(false);

  const refresh = () => {
    listSessions(filter === 'all' ? undefined : filter, showArchived)
      .then((r) => {
        // Online first, then most-recently-updated.
        r.sort((a, b) => {
          const ao = a.online ? 1 : 0;
          const bo = b.online ? 1 : 0;
          if (ao !== bo) return bo - ao;
          const at = a.updatedAt ?? a.updated_at ?? '';
          const bt = b.updatedAt ?? b.updated_at ?? '';
          return bt.localeCompare(at);
        });
        setRows(r);
      })
      .catch((e) => setErr(e?.response?.status ? `HTTP ${e.response.status}` : String(e?.message ?? e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, [filter, showArchived]);

  return (
    <div className="flex h-full flex-col">
      <div className="sticky top-0 z-10 flex items-center justify-between bg-background px-3 py-2 text-sm font-semibold shadow-sm">
        <span>Sessions</span>
        <button onClick={onClose} className="rounded px-2 py-0.5 text-xs hover:bg-accent">✕</button>
      </div>
      <div className="sticky top-[2.6rem] z-10 flex items-center gap-1 border-b border-border bg-background px-2 py-1">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={cn(
              'rounded-full px-2.5 py-0.5 text-[11px] font-medium',
              filter === f ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-accent',
            )}
          >
            {f === 'all' ? 'All' : AGENT_LABELS[f]}
          </button>
        ))}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {loading ? '…' : `${rows.length}`}
        </span>
      </div>
      <div className="sticky top-[5rem] z-10 flex items-center border-b border-border bg-background px-2 py-0.5">
        <label className="flex items-center gap-1 text-[10px] text-muted-foreground">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} className="h-3 w-3" />
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
            const agent = r.agent || 'copilot';
            const isHidden = !!r.hidden;
            return (
              <li key={`${agent}:${r.id}`}>
                <div
                  className={cn(
                    'flex flex-col gap-0.5 px-3 py-2 text-sm hover:bg-accent',
                    active && 'bg-accent',
                    isHidden && 'opacity-50',
                  )}
                >
                  <button onClick={() => onSelect(r.id)} className="flex items-center gap-2 text-left">
                    <span className={cn('h-2 w-2 shrink-0 rounded-full', r.online ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                    <span className="truncate font-medium">{label}</span>
                    {isHidden && <span className="shrink-0 text-[9px] text-muted-foreground">archived</span>}
                    <span className={cn(
                      'ml-auto shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase',
                      agent === 'claude' ? 'bg-orange-500/15 text-orange-700 dark:text-orange-400'
                        : agent === 'codex' ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                        : 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
                    )}>
                      {AGENT_LABELS[agent] || agent}
                    </span>
                  </button>
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
