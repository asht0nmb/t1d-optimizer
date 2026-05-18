import { fromZonedTime, toZonedTime } from "date-fns-tz";
import { addDays, format, parseISO } from "date-fns";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function isValidDateParam(dateStr: string): boolean {
  if (!DATE_RE.test(dateStr)) return false;
  const d = parseISO(dateStr);
  return !Number.isNaN(d.getTime()) && format(d, "yyyy-MM-dd") === dateStr;
}

/** Calendar-day window [start, end) in the given IANA timezone. */
export function dayWindowUtc(
  dateStr: string,
  timezone: string,
): { since: Date; until: Date } {
  const since = fromZonedTime(`${dateStr}T00:00:00`, timezone);
  const until = fromZonedTime(
    `${format(addDays(parseISO(dateStr), 1), "yyyy-MM-dd")}T00:00:00`,
    timezone,
  );
  return { since, until };
}

export function todayInTimezone(timezone: string): string {
  return format(toZonedTime(new Date(), timezone), "yyyy-MM-dd");
}

export function defaultCompareDate(dateStr: string): string {
  return format(addDays(parseISO(dateStr), -7), "yyyy-MM-dd");
}
