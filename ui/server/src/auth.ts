// Email magic-link login: enter an address, get a one-time sign-in link, click it to get
// a session. No password, no third-party identity provider — this exists because
// registering an Entra ID app registration for "Sign in with Microsoft" needs tenant-admin
// approval that is outside this project's control, whereas this needs no admin approval
// from anyone. Access is restricted to an allowlisted set of email domains (and, in
// principle, exact addresses); it proves who someone is, not what they may do — that is a
// separate authorization concern once there is more than one programme to guard (see the
// deployment spec's multi-programme extension).
import { randomBytes, randomUUID } from 'node:crypto';
import type { DB } from './db.js';
import { Mailer } from './mailer.js';
import { now } from './util.js';

const LINK_TTL_MS = 15 * 60 * 1000;      // a login link is valid for 15 minutes
const SESSION_TTL_MS = 30 * 24 * 3600_000; // a session lasts 30 days
export const SESSION_COOKIE = 'beacon_session';

export interface AuthOptions {
  allowedDomains: string[];   // e.g. ['northumbria.ac.uk']; empty means nobody can sign in
  baseUrl: string;            // e.g. https://beacon.example.org, used in the emailed link
  emailFrom: string;
  sendgridApiKey?: string;    // unset: log the link instead of emailing it (local dev)
}

export interface Session { email: string }

function isAllowed(email: string, allowedDomains: string[]): boolean {
  const at = email.lastIndexOf('@');
  if (at < 0) return false;
  const domain = email.slice(at + 1).toLowerCase();
  return allowedDomains.some((d) => domain === d.toLowerCase());
}

export class Auth {
  /** Shared with other senders (e.g. planning reminders) so there's one SendGrid-or-stderr
   *  implementation in the process, not one per feature. */
  readonly mailer: Mailer;
  /** e.g. https://beacon.example.org — reused to build other absolute links (e.g. the ICS feed URL). */
  readonly baseUrl: string;
  /** The session cookie is marked Secure unless the deployment's own base URL is plain
   *  http (local dev without TLS) — never send it over an unencrypted connection otherwise. */
  readonly cookieSecure: boolean;

  constructor(private db: DB, private opts: AuthOptions) {
    this.mailer = new Mailer({ sendgridApiKey: opts.sendgridApiKey, emailFrom: opts.emailFrom });
    this.baseUrl = opts.baseUrl.replace(/\/$/, '');
    this.cookieSecure = opts.baseUrl.startsWith('https://');
  }

  static async init(db: DB): Promise<void> {
    await db.pool.query(`
      CREATE TABLE IF NOT EXISTS login_tokens (
        token TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        created TEXT NOT NULL,
        expires TEXT NOT NULL,
        used BOOLEAN NOT NULL DEFAULT false
      );
      CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        created TEXT NOT NULL,
        expires TEXT NOT NULL
      );
    `);
  }

  /** Always succeeds from the caller's point of view, whether or not the address is
   *  allowed or the email actually sends — so this endpoint cannot be used to discover
   *  which addresses are registered or valid. */
  async requestLink(email: string): Promise<void> {
    const normalised = email.trim().toLowerCase();
    if (!normalised || !isAllowed(normalised, this.opts.allowedDomains)) return;
    const token = randomBytes(32).toString('base64url');
    const created = now();
    const expires = now(new Date(Date.now() + LINK_TTL_MS));
    await this.db.pool.query('INSERT INTO login_tokens(token, email, created, expires) VALUES($1, $2, $3, $4)',
      [token, normalised, created, expires]);
    const link = `${this.baseUrl}/api/auth/verify?token=${token}`;
    await this.send(normalised, link);
  }

  /** Redeems a login token for a session token, or null if it is missing, expired, or the
   *  address has since fallen off the allowlist. A token can be used more than once within
   *  its lifetime: email link scanners fetch the link before the user clicks it, and a
   *  one-use token would already be spent by then. */
  async verify(token: string): Promise<string | null> {
    const res = await this.db.pool.query<{ email: string; expires: string }>(
      'SELECT email, expires FROM login_tokens WHERE token=$1', [token]);
    const row = res.rows[0];
    if (!row || new Date(row.expires).getTime() < Date.now() || !isAllowed(row.email, this.opts.allowedDomains)) return null;
    const sessionToken = randomUUID();
    await this.db.pool.query('INSERT INTO sessions(token, email, created, expires) VALUES($1, $2, $3, $4)',
      [sessionToken, row.email, now(), now(new Date(Date.now() + SESSION_TTL_MS))]);
    return sessionToken;
  }

  async session(sessionToken: string | undefined): Promise<Session | null> {
    if (!sessionToken) return null;
    const res = await this.db.pool.query<{ email: string; expires: string }>(
      'SELECT email, expires FROM sessions WHERE token=$1', [sessionToken]);
    const row = res.rows[0];
    if (!row || new Date(row.expires).getTime() < Date.now()) return null;
    return { email: row.email };
  }

  async logout(sessionToken: string | undefined): Promise<void> {
    if (sessionToken) await this.db.pool.query('DELETE FROM sessions WHERE token=$1', [sessionToken]);
  }

  private async send(email: string, link: string): Promise<void> {
    await this.mailer.send(email, 'Sign in to NUCS',
      `Sign in to NUCS: ${link}\n\nThis link expires in 15 minutes.`);
  }
}

export function authOptionsFromEnv(): AuthOptions {
  return {
    allowedDomains: (process.env.ALLOWED_EMAIL_DOMAINS || '').split(',').map((d) => d.trim()).filter(Boolean),
    baseUrl: process.env.MAGIC_LINK_BASE_URL || 'http://localhost',
    // Must be a sender address verified in the SendGrid account (single-sender or a
    // verified/authenticated domain) — SendGrid rejects sends from an unverified "from".
    emailFrom: process.env.EMAIL_FROM || 'beacon@localhost',
    sendgridApiKey: process.env.SENDGRID_API_KEY || undefined,
  };
}
