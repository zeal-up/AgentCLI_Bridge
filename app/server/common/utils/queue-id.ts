let lastMs = 0;
let seq = 0;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Generate a process-local monotonic bigint-compatible number id.
 *
 * Tables use BIGINT with Drizzle mode "number", so keep values below
 * Number.MAX_SAFE_INTEGER. Date.now() * 1000 is safe for current epochs and
 * leaves 1000 ids per millisecond for this server process.
 */
export async function nextQueueId(): Promise<number> {
  while (true) {
    const now = Date.now();
    if (now === lastMs) {
      if (seq < 999) {
        seq += 1;
        return now * 1000 + seq;
      }
      await sleep(1);
      continue;
    }
    lastMs = now;
    seq = 0;
    return now * 1000;
  }
}
