import { Injectable, Inject, Logger } from '@nestjs/common';
import {
  DRIZZLE_DATABASE,
  type PostgresJsDatabase,
} from '@lark-apaas/fullstack-nestjs-core';
import { renames, sessions } from '@server/database/schema';
import { and, desc, eq } from 'drizzle-orm';

@Injectable()
export class SessionsService {
  private readonly logger = new Logger(SessionsService.name);

  constructor(
    @Inject(DRIZZLE_DATABASE) private readonly db: PostgresJsDatabase,
  ) {}

  /** All agent sessions, most recently active first. Optional agent filter.
   *  Archived (hidden) sessions are excluded unless includeHidden=true. */
  async list(agent?: string, includeHidden = false) {
    try {
      const conds = [];
      if (agent) conds.push(eq(sessions.agent, agent));
      if (!includeHidden) conds.push(eq(sessions.hidden, false));
      return await this.db.select().from(sessions).where(and(...conds)).orderBy(desc(sessions.updatedAt));
    } catch (err) {
      this.logger.error('query sessions failed', String(err));
      throw err;
    }
  }

  /** One session by id (for the conversation header). */
  async get(id: string) {
    try {
      const rows = await this.db.select().from(sessions).where(eq(sessions.id, id)).limit(1);
      return rows[0] ?? null;
    } catch (err) {
      this.logger.error('query session failed', String(err));
      throw err;
    }
  }

  /** Rename a session: update display_name + summary for instant display,
   *  AND enqueue a `renames` row for the bridge to write the name back to the
   *  CLI's native storage (Claude custom-title / Copilot session-store summary),
   *  so page ↔ CLI names correspond. Empty/null clears the display name. */
  async rename(id: string, displayName: string | null) {
    try {
      const value = displayName && displayName.trim() ? displayName.trim() : null;
      // Instant display update (summary mirrors the native name; display_name
      // is the page-side override that survives indexer upserts).
      await this.db.update(sessions)
        .set({ displayName: value, summary: value })
        .where(eq(sessions.id, id));

      if (value) {
        // Resolve agent so the bridge dispatches to the right adapter.
        const existing = await this.get(id);
        const agent = existing?.agent || 'copilot';
        const rid = Date.now() * 1000 + Math.floor(Math.random() * 1000);
        await this.db.insert(renames).values({
          id: rid,
          sessionId: id,
          agent,
          name: value,
          consumed: false,
          createdAt: new Date().toISOString(),
        });
      }
      return this.get(id);
    } catch (err) {
      this.logger.error('rename session failed', String(err));
      throw err;
    }
  }

  /** Archive (hide) or unarchive a session. The bridge indexer never touches
   *  `hidden`, so an archive survives reindex. CLI files are NOT deleted. */
  async archive(id: string, hidden: boolean) {
    try {
      await this.db.update(sessions).set({ hidden }).where(eq(sessions.id, id));
      return this.get(id);
    } catch (err) {
      this.logger.error('archive session failed', String(err));
      throw err;
    }
  }
}
