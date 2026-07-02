import { Controller, Get, Query } from '@nestjs/common';
import { EventsService } from './events.service';

@Controller('api/events')
export class EventsController {
  constructor(private readonly eventsService: EventsService) {}

  /** GET /api/events?session_id=X&since=<ts>&before=<ts>&limit=L */
  @Get()
  async list(
    @Query('session_id') sessionId?: string,
    @Query('since') since?: string,
    @Query('before') before?: string,
    @Query('limit') limit?: string,
  ) {
    return this.eventsService.list({
      sessionId,
      since,
      before,
      limit: limit ? Number(limit) : undefined,
    });
  }
}
