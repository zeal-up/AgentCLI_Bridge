import React, { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { listSessions, type SessionRow } from '../api/bridge';
import { cn } from '../lib/utils';
import { Badge } from './ui/badge';
import { ScrollArea } from './ui/scroll-area';
import { Separator } from './ui/separator';

const SessionsSidebar: React.FC = () => {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const location = useLocation();

  const refresh = () => {
    listSessions()
      .then(setRows)
      .catch((e) => setErr(e?.response?.status ? `HTTP ${e.response.status}` : String(e?.message ?? e)));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000); // refresh list every 10s
    return () => clearInterval(t);
  }, []);

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border bg-muted/30">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-sm font-semibold">Copilot sessions</span>
        <Badge variant="secondary">{rows.length}</Badge>
      </div>
      <Separator />
      {err && <div className="px-3 py-2 text-xs text-destructive">Error: {err}</div>}
      <ScrollArea className="flex-1">
        <ul className="py-1">
          {rows.map((r) => {
            const active = location.pathname.startsWith(`/s/${r.id}`);
            const label = r.summary || (r.cwd ? r.cwd.split('/').filter(Boolean).pop() : r.id.slice(0, 8));
            return (
              <li key={r.id}>
                <Link
                  to={`/s/${r.id}`}
                  className={cn(
                    'flex flex-col gap-0.5 px-3 py-2 text-sm hover:bg-accent',
                    active && 'bg-accent',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className={cn('h-2 w-2 shrink-0 rounded-full', r.online ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                    <span className="truncate font-medium">{label}</span>
                  </div>
                  <span className="truncate pl-4 text-xs text-muted-foreground">{r.cwd || r.id}</span>
                  <span className="pl-4 text-[10px] text-muted-foreground/70">
                    {r.updatedAt ?? r.updated_at ?? ''}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
    </aside>
  );
};

export default SessionsSidebar;
