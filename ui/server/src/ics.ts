// A minimal RFC 5545 writer for the module delivery feed: a handful of all-day VEVENTs,
// no recurrence, no attendees or alarms, floating dates (no timezone math needed). Hand
// rolled rather than a dependency — this format is simple and stable enough that a ~40
// line formatter is easier to audit than a library's own tree, matching the server's
// otherwise small dependency list.
export interface IcsEvent { uid: string; date: string /* YYYY-MM-DD */; summary: string; description?: string }

function escapeText(s: string): string {
  return s.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
}

/** RFC 5545 §3.1: a logical line must be folded before 75 octets, continuation lines
 *  starting with a single space. Outlook is strict about this for long DESCRIPTIONs. */
function foldLine(line: string): string {
  const bytes = Buffer.from(line, 'utf8');
  if (bytes.length <= 75) return line;
  const out: string[] = [];
  let start = 0;
  let limit = 75;
  while (start < bytes.length) {
    let end = Math.min(start + limit, bytes.length);
    // Never split a multi-byte UTF-8 sequence: back off while the next byte is a continuation byte.
    while (end < bytes.length && (bytes[end] & 0xc0) === 0x80) end--;
    out.push(bytes.subarray(start, end).toString('utf8'));
    start = end;
    limit = 74; // continuation lines lose one octet to the leading space
  }
  return out.join('\r\n ');
}

function ymd(date: string): string { return date.replace(/-/g, ''); }

function nextDay(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

export function buildIcs(calendarName: string, events: IcsEvent[]): string {
  const dtstamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  const lines: string[] = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//NUCS//Module Delivery Planning//EN',
    'CALSCALE:GREGORIAN',
    `X-WR-CALNAME:${escapeText(calendarName)}`,
  ];
  for (const ev of events) {
    lines.push('BEGIN:VEVENT');
    lines.push(`UID:${ev.uid}`);
    lines.push(`DTSTAMP:${dtstamp}`);
    lines.push(`DTSTART;VALUE=DATE:${ymd(ev.date)}`);
    lines.push(`DTEND;VALUE=DATE:${ymd(nextDay(ev.date))}`);
    lines.push(`SUMMARY:${escapeText(ev.summary)}`);
    if (ev.description) lines.push(`DESCRIPTION:${escapeText(ev.description)}`);
    lines.push('END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return lines.map(foldLine).join('\r\n') + '\r\n';
}
