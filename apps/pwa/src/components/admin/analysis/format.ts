// Shared formatters for the analysis tab. Estimates are always shown as
// low-high ranges, never a point figure.

// Compact USD, e.g. 1_420_000 -> "$1.4M", 980 -> "$980", 3_100_000_000 -> "$3.1B".
export function formatUsd(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "$0";
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`;
  return `$${Math.round(value)}`;
}

// "$1.4M-$3.1M". A plain hyphen, not an en/em dash.
export function formatUsdRange(low: number | null, high: number | null): string {
  if (low == null || high == null) return "pending";
  return `${formatUsd(low)}-${formatUsd(high)}`;
}

// "180-360" with thousands separators. Collapses to a single figure when the
// range is degenerate (e.g. 0-0 reads as just "0").
export function formatCountRange(low: number, high: number): string {
  if (low === high) return low.toLocaleString();
  return `${low.toLocaleString()}-${high.toLocaleString()}`;
}
