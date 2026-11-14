"""
"Add dose reminders to my calendar" (80% check, 12 Nov: reminders without an SMS service).

Builds an iCalendar (.ics) file that Google Calendar, Apple Calendar and Outlook can import:
one daily repeating event per dose time of each CURRENT medication, with an alert at the dose
time. Written by hand (the format is small; no extra package):
  - times are local (TZID Africa/Nairobi, UTC+3, no daylight saving);
  - RRULE:FREQ=DAILY, with UNTIL = the medication's end date if it has one;
  - a stable UID per dose schedule, so importing an updated file again updates the events instead
    of duplicating them (in calendar apps that support it);
  - text escaped and long lines folded as the standard requires (RFC 5545), CRLF line endings.
"""
from datetime import datetime, timedelta

from app.dashboard_data import local_today

TZID = "Africa/Nairobi"
EVENT_MINUTES = 15


def _escape(text):
    """Escape text values (RFC 5545 3.3.11)."""
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    """Lines longer than 75 octets continue on the next line, starting with a space."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    parts, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += b
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def dose_calendar(medications, offset_hours=3, now=None):
    """The .ics text for these medications (pass only the current ones)."""
    now = now or datetime.utcnow()
    today = local_today()
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//PD Compass//Dose reminders//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:PD Compass doses",
        # Kenya time: UTC+3 all year
        "BEGIN:VTIMEZONE", f"TZID:{TZID}", "BEGIN:STANDARD", "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0300", "TZOFFSETTO:+0300", "TZNAME:EAT", "END:STANDARD", "END:VTIMEZONE",
    ]
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    for pm in medications:
        first_day = max(pm.started_date, today)
        title = f"Take {pm.dose_label()} ({pm.quantity_label()})"
        details = (pm.instructions + "\n" if pm.instructions else "") + \
            "Then log it in PD Compass (Taken or Missed)."
        for s in pm.schedules:
            start = datetime.combine(first_day, s.dose_time)
            event = [
                "BEGIN:VEVENT",
                f"UID:pdcompass-schedule-{s.schedule_id}@pdcompass.local",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID={TZID}:{start:%Y%m%dT%H%M%S}",
                f"DURATION:PT{EVENT_MINUTES}M",
            ]
            rule = "RRULE:FREQ=DAILY"
            if pm.end_date:
                # UNTIL must be UTC when DTSTART has a TZID: the end date's last local minute
                until = datetime.combine(pm.end_date, datetime.max.time().replace(microsecond=0)) \
                    - timedelta(hours=offset_hours)
                rule += f";UNTIL={until:%Y%m%dT%H%M%SZ}"
            event += [
                rule,
                f"SUMMARY:{_escape(title)}",
                f"DESCRIPTION:{_escape(details)}",
                "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(title)}",
                "TRIGGER:PT0M", "END:VALARM",                  # alert at the dose time
                "END:VEVENT",
            ]
            lines += event
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
