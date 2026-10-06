import { describe, expect, it } from 'vitest';
import { buildIcs } from '../src/ics.js';

describe('buildIcs', () => {
  it('writes one matched BEGIN/END:VEVENT pair per event, with the right UID', () => {
    const ics = buildIcs('KV7015', [
      { uid: 'KV7015-U01-T01-brief@beacon-ui', date: '2026-11-01', summary: 'Brief due: KV7015-U01-T01' },
      { uid: 'KV7015-U01-T01-recording@beacon-ui', date: '2026-11-05', summary: 'Record by: KV7015-U01-T01' },
    ]);
    expect(ics.match(/BEGIN:VEVENT/g)).toHaveLength(2);
    expect(ics.match(/END:VEVENT/g)).toHaveLength(2);
    expect(ics).toContain('UID:KV7015-U01-T01-brief@beacon-ui');
    expect(ics).toContain('UID:KV7015-U01-T01-recording@beacon-ui');
  });

  it('formats DTSTART/DTEND as 8-digit YYYYMMDD all-day values, DTEND one day after DTSTART', () => {
    const ics = buildIcs('KV7015', [{ uid: 'a@beacon-ui', date: '2026-11-01', summary: 'x' }]);
    expect(ics).toMatch(/DTSTART;VALUE=DATE:20261101\r\n/);
    expect(ics).toMatch(/DTEND;VALUE=DATE:20261102\r\n/);
  });

  it('sets X-WR-CALNAME to the given calendar name', () => {
    const ics = buildIcs('KV7015', []);
    expect(ics).toContain('X-WR-CALNAME:KV7015');
  });

  it('folds a long DESCRIPTION line at 75 octets with a single leading space on continuations', () => {
    const longDescription = 'x'.repeat(200);
    const ics = buildIcs('KV7015', [{ uid: 'a@beacon-ui', date: '2026-11-01', summary: 's', description: longDescription }]);
    const lines = ics.split('\r\n');
    const descStart = lines.findIndex((l) => l.startsWith('DESCRIPTION:'));
    expect(descStart).toBeGreaterThanOrEqual(0);
    // The first physical line (and each folded continuation up to the last) must be
    // exactly at the fold width; continuations start with exactly one space.
    expect(Buffer.byteLength(lines[descStart], 'utf8')).toBeLessThanOrEqual(75);
    expect(lines[descStart + 1].startsWith(' ')).toBe(true);
    // Unfolding (strip "\r\n " continuations) must recover the original content exactly.
    let i = descStart;
    let unfolded = lines[i];
    while (lines[i + 1] && lines[i + 1].startsWith(' ')) { i++; unfolded += lines[i].slice(1); }
    expect(unfolded).toBe(`DESCRIPTION:${longDescription}`);
  });

  it('escapes commas, semicolons and backslashes in text fields', () => {
    const ics = buildIcs('KV7015', [{ uid: 'a@beacon-ui', date: '2026-11-01', summary: 'a; b, c\\d' }]);
    expect(ics).toContain('SUMMARY:a\\; b\\, c\\\\d');
  });

  it('produces a parseable VCALENDAR wrapper regardless of event count', () => {
    const ics = buildIcs('KV7015', []);
    expect(ics.startsWith('BEGIN:VCALENDAR\r\n')).toBe(true);
    expect(ics.trim().endsWith('END:VCALENDAR')).toBe(true);
  });
});
