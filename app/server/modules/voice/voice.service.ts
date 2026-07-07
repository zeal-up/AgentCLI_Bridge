import { Injectable, Logger } from '@nestjs/common';
import { createHmac } from 'node:crypto';

export interface VoiceConfigResponse {
  enabled: boolean;
  backend?: string;
  wssUrl?: string;
  token?: string;
  sampleRate?: number;
  lang?: string;
}

/**
 * Voice relay connection config. The page calls GET /api/voice/config to learn
 * whether streaming voice is enabled and, if so, where to connect (the
 * cloudflared tunnel URL) plus a short-lived HMAC token.
 *
 * Token format MUST match bridge/voice/token.py exactly:
 *   base64url(JSON.stringify({uid,exp})) "." base64url(hmac_sha256(payload, secret))
 * The bridge verifies the signature, checks exp, and rejects unless the
 * embedded userId is in config.ALLOWED_OPEN_IDS. No allowlist lives here —
 * consistent with the commands endpoint, the bridge is the gatekeeper.
 */
@Injectable()
export class VoiceService {
  private readonly logger = new Logger(VoiceService.name);

  configFor(userId: string | undefined): VoiceConfigResponse {
    const backend = process.env.VOICE_ASR_BACKEND || 'none';
    const secret = process.env.VOICE_RELAY_SECRET || '';
    const wssUrl = process.env.VOICE_RELAY_PUBLIC_URL || '';
    const enabled = backend !== 'none' && !!secret && !!wssUrl;

    if (!enabled || !userId) {
      return { enabled: false };
    }

    // 5-minute token TTL — plenty for a press-and-hold session, short enough
    // that a leaked token is useless quickly.
    const exp = Date.now() + 5 * 60 * 1000;
    const token = this.signToken(userId, exp, secret);
    return {
      enabled: true,
      backend,
      wssUrl,
      token,
      sampleRate: 16000,
      lang: 'zh-CN',
    };
  }

  private signToken(userId: string, expMs: number, secret: string): string {
    const payload = JSON.stringify({ uid: userId, exp: expMs });
    const payloadB64 = this.base64Url(Buffer.from(payload, 'utf-8'));
    const sig = createHmac('sha256', secret).update(payload, 'utf-8').digest();
    const sigB64 = this.base64Url(sig);
    return `${payloadB64}.${sigB64}`;
  }

  private base64Url(buf: Buffer): string {
    return buf.toString('base64url');
  }
}
