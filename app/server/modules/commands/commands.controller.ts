import { Body, Controller, Post, Req } from '@nestjs/common';
import type { Request } from 'express';
import { CommandsService } from './commands.service';

class CreateCommandDto {
  sessionId: string;
  content: string;
  agent?: string;
}

@Controller('api/commands')
export class CommandsController {
  constructor(private readonly commandsService: CommandsService) {}

  /** POST /api/commands {sessionId, content, agent?} -> enqueue; sender from Feishu identity. */
  @Post()
  async create(@Body() body: CreateCommandDto, @Req() req: Request) {
    const senderOpenId = req.userContext?.userId;
    return this.commandsService.create({
      sessionId: body.sessionId,
      content: body.content,
      senderOpenId,
      agent: body.agent,
    });
  }
}
