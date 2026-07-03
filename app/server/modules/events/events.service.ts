import { BadRequestException, Injectable, Inject, Logger } from '@nestjs/common';
import {
  DRIZZLE_DATABASE,
  type PostgresJsDatabase,
} from '@lark-apaas/fullstack-nestjs-core';
import { events } from '@server/database/schema';
import { and, asc, desc, eq, gt, lt } from 'drizzle-orm';

@Injectable()
export class EventsService {
  private readonly logger = new Logger(EventsService.name);

  constructor(
    @Inject(DRIZZLE_DATABASE) private readonly db: PostgresJsDatabase,
  ) {}

  /** List conversation events ordered by time (ts).
   *  - `since` (ISO ts): incremental newer events (ts > since), oldest-first.
   *  - `before` (ISO ts): paginate older events (ts < before), newest-first then reversed.
   *  - neither: the most recent `limit` events, chronological order.
   *  `id` is a stable hash (not time-ordered), so we always order by `ts`.
   */
  async list(opts: { sessionId?: string; since?: string; before?: string; limit?: number } = {}) {
    const sessionId = (opts.sessionId || '').trim();
    if (!sessionId) throw new BadRequestException('session_id is required');
    if (opts.since && opts.before) {
      throw new BadRequestException('since and before cannot be used together');
    }
    const rawLimit = opts.limit ?? 500;
    const limit = Number.isFinite(rawLimit) && rawLimit > 0
      ? Math.min(Math.floor(rawLimit), 1000)
      : 500;
    const conds = [];
    conds.push(eq(events.sessionId, sessionId));
    if (opts.since) conds.push(gt(events.ts, opts.since));
    if (opts.before) conds.push(lt(events.ts, opts.before));
    try {
      if (opts.since) {
        return await this.db.select().from(events).where(and(...conds)).orderBy(asc(events.ts)).limit(limit);
      }
      // initial (no since) or before (older page): newest-first then reverse to chronological
      const rows = await this.db
        .select()
        .from(events)
        .where(and(...conds))
        .orderBy(desc(events.ts))
        .limit(limit);
      return rows.reverse();
    } catch (err) {
      this.logger.error('query events failed', String(err));
      throw err;
    }
  }
}
