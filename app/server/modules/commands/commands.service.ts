import { BadRequestException, ForbiddenException, Injectable, Inject, Logger, NotFoundException } from '@nestjs/common';
import {
  DRIZZLE_DATABASE,
  type PostgresJsDatabase,
} from '@lark-apaas/fullstack-nestjs-core';
import { commands, sessions } from '@server/database/schema';
import { nextQueueId } from '@server/common/utils/queue-id';
import { eq } from 'drizzle-orm';

const AGENTS = new Set(['copilot', 'claude', 'codex']);
const MAX_COMMAND_CHARS = 8000;

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
    const sessionId = (input.sessionId || '').trim();
    const content = (input.content || '').trim();
    const senderOpenId = (input.senderOpenId || '').trim();

    if (!sessionId) throw new BadRequestException('sessionId is required');
    if (!content) throw new BadRequestException('content is required');
    if (content.length > MAX_COMMAND_CHARS) {
      throw new BadRequestException(`content is too long; max ${MAX_COMMAND_CHARS} characters`);
    }
    if (!senderOpenId) throw new ForbiddenException('missing Feishu user identity');

    const createdAt = new Date().toISOString();
    try {
      const existing = await this.db.select().from(sessions).where(eq(sessions.id, sessionId)).limit(1);
      const session = existing[0];
      if (!session) throw new NotFoundException('session not found');

      const requestedAgent = input.agent?.trim();
      const agent = requestedAgent && AGENTS.has(requestedAgent)
        ? requestedAgent
        : session.agent || 'copilot';
      if (requestedAgent && requestedAgent !== agent) {
        throw new BadRequestException('agent does not match the target session');
      }

      const id = await nextQueueId();
      await this.db.insert(commands).values({
        id,
        sessionId,
        content,
        senderOpenId,
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
