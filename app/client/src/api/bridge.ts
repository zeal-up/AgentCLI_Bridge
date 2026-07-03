import { axiosForBackend } from '@lark-apaas/client-toolkit/utils/getAxiosForBackend';

export interface SessionRow {
  id: string;
  agent?: string;
  cwd?: string;
  summary?: string;
  displayName?: string;
  display_name?: string;
  updatedAt?: string;
  updated_at?: string;
  online?: boolean;
  pid?: number;
  ctxUsed?: number | null;
  ctx_used?: number | null;
  ctxLimit?: number | null;
  ctx_limit?: number | null;
  hidden?: boolean;
}

export type AgentKey = 'copilot' | 'claude' | 'codex';

export const AGENT_LABELS: Record<string, string> = {
  copilot: 'Copilot',
  claude: 'Claude',
  codex: 'Codex',
};

export interface EventRow {
  id: number | string;
  sessionId?: string;
  session_id?: string;
  role: string;
  content?: string;
  ts?: string;
}

export async function listSessions(agent?: string, includeHidden = false): Promise<SessionRow[]> {
  const params: Record<string, string> = {};
  if (agent) params.agent = agent;
  if (includeHidden) params.includeHidden = '1';
  const r = await axiosForBackend({ url: 'api/sessions', method: 'GET', params });
  const d = r.data;
  return Array.isArray(d) ? d : Array.isArray(d?.data) ? d.data : [];
}

export async function getSession(id: string): Promise<SessionRow | null> {
  const r = await axiosForBackend({ url: `api/sessions/${encodeURIComponent(id)}`, method: 'GET' });
  const d = r.data;
  return d && typeof d === 'object' ? d : null;
}

export async function renameSession(id: string, displayName: string | null): Promise<SessionRow | null> {
  const r = await axiosForBackend({
    url: `api/sessions/${encodeURIComponent(id)}`,
    method: 'PATCH',
    data: { displayName },
  });
  const d = r.data;
  return d && typeof d === 'object' ? d : null;
}

export async function archiveSession(id: string, hidden: boolean): Promise<SessionRow | null> {
  const r = await axiosForBackend({
    url: `api/sessions/${encodeURIComponent(id)}/archive`,
    method: 'PATCH',
    data: { hidden },
  });
  const d = r.data;
  return d && typeof d === 'object' ? d : null;
}

/** Format a context-usage pair as "28k / 500k (5%)" or null if unavailable. */
export function formatContext(s?: SessionRow | null): string | null {
  const used = s?.ctxUsed ?? s?.ctx_used;
  const limit = s?.ctxLimit ?? s?.ctx_limit;
  if (used == null && limit == null) return null;
  const fmt = (n: number | null | undefined) => (n == null ? '?' : `${Math.round(n / 1000)}k`);
  const pct = used != null && limit ? Math.round((used / limit) * 100) : null;
  return `ctx: ${fmt(used)} / ${fmt(limit)}${pct != null ? ` (${pct}%)` : ''}`;
}

/** List events for a session.
 *  `since` (ISO ts, exclusive) = incremental newer events.
 *  `before` (ISO ts, exclusive) = paginate older events (before this ts).
 */
export async function listEvents(
  sessionId: string,
  since?: string,
  before?: string,
  limit?: number,
): Promise<EventRow[]> {
  const params: Record<string, string> = { session_id: sessionId };
  if (since) params.since = since;
  if (before) params.before = before;
  if (limit !== undefined) params.limit = String(limit);
  const r = await axiosForBackend({ url: 'api/events', method: 'GET', params });
  const d = r.data;
  return Array.isArray(d) ? d : Array.isArray(d?.data) ? d.data : [];
}

export async function sendCommand(
  sessionId: string,
  content: string,
  agent?: string,
): Promise<{ id: string }> {
  const r = await axiosForBackend({
    url: 'api/commands',
    method: 'POST',
    data: { sessionId, content, agent },
  });
  return r.data;
}

/** Resolve a human title for a session: user rename > local summary > cwd basename. */
export function sessionTitle(s?: SessionRow | null, fallbackId?: string): string {
  if (!s) return fallbackId ? fallbackId.slice(0, 8) : 'Session';
  const dn = (s.displayName || s.display_name || '').trim();
  if (dn) return dn;
  const sum = (s.summary || '').trim();
  if (sum) return sum;
  if (s.cwd) {
    const parts = s.cwd.split('/').filter(Boolean);
    if (parts.length) return parts[parts.length - 1];
  }
  return (fallbackId || s.id).slice(0, 8);
}
