import { Injectable, Inject, Logger } from '@nestjs/common';
import {
  DRIZZLE_DATABASE,
  type PostgresJsDatabase,
} from '@lark-apaas/fullstack-nestjs-core';
import { commands } from '@server/database/schema';

@Injectable()
export class CommandsService {
  private readonly logger = new Logger(CommandsService.name);

  constructor(
    @Inject(DRIZZLE_DATABASE) private readonly db: PostgresJsDatabase,
  ) {}

  /** Enqueue a command for the bridge to inject into an agent CLI session. */
  async create(input: {
    sessionId: string;
    content: string;
    senderOpenId?: string;
    agent?: string;
  }) {
    // bigint id generated client-side (no auto-increment on the table).
    // mode:"number" => JS number; Date.now()*1000+rand stays within safe integer range.
    const id = Date.now() * 1000 + Math.floor(Math.random() * 1000);
    const createdAt = new Date().toISOString();
    const agent = input.agent || 'copilot';
    try {
      await this.db.insert(commands).values({
        id,
        sessionId: input.sessionId,
        content: input.content,
        senderOpenId: input.senderOpenId,
        createdAt,
        consumed: false,
        agent,
      });
      return { id: String(id) };
    } catch (err) {
      this.logger.error('insert command failed', String(err));
      throw err;
    }
  }
}
