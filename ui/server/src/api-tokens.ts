// Personal API tokens: how Claude Desktop (through the NUCS extension, see
// ui/desktop-extension/) reaches the MCP endpoint as a particular person.
//
// A token is created by someone signed in with the magic-link session, shown to them once,
// and stored only as its sha256 (the same scheme as runner tokens in runner-api.ts). It acts
// as that person: everything written through it is attributed to their email address. It
// is accepted only on /mcp and the one upload route the extension needs (server.ts), never
// across the rest of the API, and never to create more tokens.
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import type { DB } from './db.js';
import { now } from './util.js';

export const TOKEN_PREFIX = 'nucs_';
const NAME_MAX = 80;

const hash = (token: string) => createHash('sha256').update(token).digest('hex');

export interface ApiTokenRow {
  id: string; email: string; name: string; token_hash: string;
  created: string; last_used: string | null; revoked: boolean;
}

/** What the app lists: never the hash, never the token. */
export interface ApiTokenInfo { id: string; name: string; created: string; lastUsed: string | null }

export class ApiTokens {
  constructor(private db: DB, private allows: (email: string) => boolean) {}

  async create(email: string, name: string): Promise<{ id: string; token: string; name: string }> {
    const clean = name.trim().slice(0, NAME_MAX) || 'Claude Desktop';
    const token = `${TOKEN_PREFIX}${randomBytes(32).toString('base64url')}`;
    const id = randomUUID();
    await this.db.pool.query('INSERT INTO api_tokens(id, email, name, token_hash, created) VALUES($1, $2, $3, $4, $5)',
      [id, email.toLowerCase(), clean, hash(token), now()]);
    return { id, token, name: clean };
  }

  async list(email: string): Promise<ApiTokenInfo[]> {
    const res = await this.db.pool.query<ApiTokenRow>(
      'SELECT * FROM api_tokens WHERE email=$1 AND NOT revoked ORDER BY created DESC', [email.toLowerCase()]);
    return res.rows.map((r) => ({ id: r.id, name: r.name, created: r.created, lastUsed: r.last_used }));
  }

  /** Revokes one of `email`'s tokens (any token, for an administrator). False if there was none. */
  async revoke(id: string, email: string, admin: boolean): Promise<boolean> {
    const res = admin
      ? await this.db.pool.query('UPDATE api_tokens SET revoked=true WHERE id=$1 AND NOT revoked', [id])
      : await this.db.pool.query('UPDATE api_tokens SET revoked=true WHERE id=$1 AND email=$2 AND NOT revoked', [id, email.toLowerCase()]);
    return (res.rowCount ?? 0) > 0;
  }

  /** The email a bearer token acts as, or null. The owner must still be on the sign-in
   *  allowlist: someone who could no longer sign in loses their tokens with it. */
  async verify(header: string | undefined, skipAllowlist = false): Promise<string | null> {
    const m = /^Bearer\s+(\S+)$/i.exec(header || '');
    if (!m || !m[1].startsWith(TOKEN_PREFIX)) return null;
    const res = await this.db.pool.query<ApiTokenRow>(
      'UPDATE api_tokens SET last_used=$2 WHERE token_hash=$1 AND NOT revoked RETURNING *', [hash(m[1]), now()]);
    const row = res.rows[0];
    if (!row || (!skipAllowlist && !this.allows(row.email))) return null;
    return row.email;
  }
}
