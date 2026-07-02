/* eslint-disable */
/** auto generated, do not edit */
import { bigint, boolean, integer, pgTable, text, varchar, customType } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm";

export const customTimestamptz = customType<{
  data: Date;
  driverData: string;
  config: { precision?: number };
}>({
  dataType(config) {
    const precision = typeof config?.precision !== 'undefined'
      ? ` (${config.precision})`
      : '';
    return `timestamptz${precision}`;
  },
  toDriver(value: Date | string | number) {
    if (value == null) return value as any;
    if (typeof value === 'number') return new Date(value).toISOString();
    if (typeof value === 'string') return value;
    if (value instanceof Date) return value.toISOString();
    throw new Error('Invalid timestamp value');
  },
  fromDriver(value: string | Date): Date {
    if (value instanceof Date) return value;
    return new Date(value);
  },
});

export const userProfile = customType<{
  data: string;
  driverData: string;
}>({
  dataType() {
    return 'user_profile';
  },
  toDriver(value: string) {
    return sql`ROW(${value})::user_profile`;
  },
  fromDriver(value: string) {
    const [userId] = value.slice(1, -1).split(',');
    return userId.trim();
  },
});

export type FileAttachment = {
  bucket_id: string;
  file_path: string;
};

export const fileAttachment = customType<{
  data: FileAttachment;
  driverData: string;
}>({
  dataType() {
    return 'file_attachment';
  },
  toDriver(value: FileAttachment) {
    return sql`ROW(${value.bucket_id},${value.file_path})::file_attachment`;
  },
  fromDriver(value: string): FileAttachment {
    const [bucketId, filePath] = value.slice(1, -1).split(',');
    return { bucket_id: bucketId.trim(), file_path: filePath.trim() };
  },
});

export function escapeLiteral(str: string): string {
  return "'" + str.replace(/'/g, "''") + "'";
}

export const userProfileArray = customType<{
  data: string[];
  driverData: string;
}>({
  dataType() {
    return 'user_profile[]';
  },
  toDriver(value: string[]) {
    if (!value || value.length === 0) {
      return sql`'{}'::user_profile[]`;
    }
    const elements = value.map(id => `ROW(${escapeLiteral(id)})::user_profile`).join(',');
    return sql.raw(`ARRAY[${elements}]::user_profile[]`);
  },
  fromDriver(value: string): string[] {
    if (!value || value === '{}') return [];
    const inner = value.slice(1, -1);
    const matches = inner.match(/\([^)]*\)/g) || [];
    return matches.map(m => m.slice(1, -1).split(',')[0].trim());
  },
});

export const fileAttachmentArray = customType<{
  data: FileAttachment[];
  driverData: string;
}>({
  dataType() {
    return 'file_attachment[]';
  },
  toDriver(value: FileAttachment[]) {
    if (!value || value.length === 0) {
      return sql`'{}'::file_attachment[]`;
    }
    const elements = value.map(f =>
      `ROW(${escapeLiteral(f.bucket_id)},${escapeLiteral(f.file_path)})::file_attachment`
    ).join(',');
    return sql.raw(`ARRAY[${elements}]::file_attachment[]`);
  },
  fromDriver(value: string): FileAttachment[] {
    if (!value || value === '{}') return [];
    const inner = value.slice(1, -1);
    const matches = inner.match(/\([^)]*\)/g) || [];
    return matches.map(m => {
      const [bucketId, filePath] = m.slice(1, -1).split(',');
      return { bucket_id: bucketId.trim(), file_path: filePath.trim() };
    });
  },
});

export const renames = pgTable("renames", {
  id: bigint("id", { mode: 'number' }).primaryKey(),
  sessionId: text("session_id").notNull(),
  agent: text("agent").notNull().default('copilot'),
  name: text("name").notNull(),
  consumed: boolean("consumed").default(false),
  createdAt: text("created_at"),
  consumedAt: text("consumed_at"),
  result: text("result"),
});

export const commands = pgTable("commands", {
  id: bigint("id", { mode: 'number' }).primaryKey(),
  sessionId: text("session_id").notNull(),
  content: text("content").notNull(),
  senderOpenId: text("sender_open_id"),
  createdAt: text("created_at"),
  consumed: boolean("consumed").default(false),
  consumedAt: text("consumed_at"),
  result: text("result"),
  agent: text("agent").notNull().default('copilot'),
});

export const sessions = pgTable("sessions", {
  id: text("id").primaryKey(),
  cwd: text("cwd"),
  summary: text("summary"),
  updatedAt: text("updated_at"),
  online: boolean("online").default(false),
  pid: integer("pid"),
  indexedAt: text("indexed_at"),
  displayName: text("display_name"),
  agent: text("agent").notNull().default('copilot'),
  ctxUsed: integer("ctx_used"),
  ctxLimit: integer("ctx_limit"),
  hidden: boolean("hidden").notNull().default(false),
});

export const events = pgTable("events", {
  id: bigint("id", { mode: 'number' }).primaryKey(),
  sessionId: varchar("session_id", { length: 64 }),
  role: varchar("role", { length: 16 }),
  content: text("content"),
  ts: varchar("ts", { length: 32 }),
});

// table aliases
export const commandsTable = commands;
export const eventsTable = events;
export const renamesTable = renames;
export const sessionsTable = sessions;
