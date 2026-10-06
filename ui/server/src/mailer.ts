// Shared "send via SendGrid, or log to stderr if no API key is configured" mechanics,
// used by both login (auth.ts) and delivery-planning reminders. Local dev needs no real
// SendGrid key to exercise either path end to end.
import sgMail from '@sendgrid/mail';

export interface MailerOptions {
  sendgridApiKey?: string;
  emailFrom: string;
}

export class Mailer {
  private readonly enabled: boolean;

  constructor(private opts: MailerOptions) {
    this.enabled = Boolean(opts.sendgridApiKey);
    if (opts.sendgridApiKey) sgMail.setApiKey(opts.sendgridApiKey);
  }

  async send(to: string, subject: string, text: string): Promise<void> {
    if (!this.enabled) {
      process.stderr.write(`beacon-ui: email to ${to}: ${subject}\n${text}\n`);
      return;
    }
    await sgMail.send({ from: this.opts.emailFrom, to, subject, text });
  }
}
