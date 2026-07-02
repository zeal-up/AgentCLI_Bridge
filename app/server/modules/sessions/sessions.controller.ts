import { Body, Controller, Get, Param, Patch, Query } from '@nestjs/common';
import { SessionsService } from './sessions.service';

@Controller('api/sessions')
export class SessionsController {
  constructor(private readonly sessionsService: SessionsService) {}

  /** GET /api/sessions?agent=copilot|claude&includeHidden=1 -> agent session index. */
  @Get()
  async list(@Query('agent') agent?: string, @Query('includeHidden') includeHidden?: string) {
    return this.sessionsService.list(agent, includeHidden === '1' || includeHidden === 'true');
  }

  /** GET /api/sessions/:id -> one session (cwd/summary/online). */
  @Get(':id')
  async get(@Param('id') id: string) {
    return this.sessionsService.get(id);
  }

  /** PATCH /api/sessions/:id { displayName } -> rename (user display name). */
  @Patch(':id')
  async rename(@Param('id') id: string, @Body('displayName') displayName: string | null) {
    return this.sessionsService.rename(id, displayName ?? null);
  }

  /** PATCH /api/sessions/:id/archive { hidden } -> archive (hide) / unarchive. */
  @Patch(':id/archive')
  async archive(@Param('id') id: string, @Body('hidden') hidden: boolean) {
    return this.sessionsService.archive(id, !!hidden);
  }
}
