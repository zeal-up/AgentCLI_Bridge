import { Controller, Get, Req } from '@nestjs/common';
import type { Request } from 'express';
import { VoiceService } from './voice.service';

@Controller('api/voice')
export class VoiceController {
  constructor(private readonly voiceService: VoiceService) {}

  /** GET /api/voice/config -> relay connection info + a short-lived HMAC
   *  token the page presents to the bridge WSS. The bridge verifies the
   *  token and checks the embedded userId against the allowlist. Read-only;
   *  safe to call even when voice is off (returns {enabled:false}). */
  @Get('config')
  async config(@Req() req: Request) {
    const userId = req.userContext?.userId;
    return this.voiceService.configFor(userId);
  }
}
