/**
 * Relative and absolute time formatting utilities.
 * Pure functions with no external dependencies.
 */

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

/**
 * Format a date string as a human-readable relative time.
 *
 * @example
 * formatRelativeTime("2026-03-11T12:00:00Z") // "2 hours ago"
 * formatRelativeTime("2026-03-11T14:29:00Z") // "just now"
 */
export function formatRelativeTime(dateString: string): string {
  const date = new Date(dateString);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  // Future dates
  if (diff < -MINUTE) {
    const absDiff = Math.abs(diff);
    if (absDiff < HOUR) return `in ${Math.floor(absDiff / MINUTE)} minutes`;
    if (absDiff < DAY) return `in ${Math.floor(absDiff / HOUR)} hours`;
    if (absDiff < WEEK) return `in ${Math.floor(absDiff / DAY)} days`;
    return formatAbsoluteTime(dateString);
  }

  // Just now (within 1 minute)
  if (diff < MINUTE) return "just now";

  // Minutes ago
  if (diff < HOUR) {
    const mins = Math.floor(diff / MINUTE);
    return mins === 1 ? "1 minute ago" : `${mins} minutes ago`;
  }

  // Hours ago
  if (diff < DAY) {
    const hours = Math.floor(diff / HOUR);
    return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  }

  // Days ago
  if (diff < WEEK) {
    const days = Math.floor(diff / DAY);
    return days === 1 ? "1 day ago" : `${days} days ago`;
  }

  // Weeks ago
  if (diff < MONTH) {
    const weeks = Math.floor(diff / WEEK);
    return weeks === 1 ? "1 week ago" : `${weeks} weeks ago`;
  }

  // Months ago
  if (diff < YEAR) {
    const months = Math.floor(diff / MONTH);
    return months === 1 ? "1 month ago" : `${months} months ago`;
  }

  // Years ago
  const years = Math.floor(diff / YEAR);
  return years === 1 ? "1 year ago" : `${years} years ago`;
}

/**
 * Format a date string as an absolute, human-readable timestamp.
 *
 * @example
 * formatAbsoluteTime("2026-03-11T14:30:00Z") // "Mar 11, 2026 14:30"
 */
export function formatAbsoluteTime(dateString: string): string {
  const date = new Date(dateString);
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];

  const month = months[date.getMonth()];
  const day = date.getDate();
  const year = date.getFullYear();
  const hours = date.getHours().toString().padStart(2, "0");
  const minutes = date.getMinutes().toString().padStart(2, "0");

  return `${month} ${day}, ${year} ${hours}:${minutes}`;
}
