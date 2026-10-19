const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const codes = 'FGHJKMNQUVXZ';

export function candleSymbol(label) {
  const match = /^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{2})$/.exec(label || '');
  return match ? `CAPITALCOM:TRNLTTFM${codes[months.indexOf(match[1])]}20${match[2]}` : null;
}

export function chartGeometry(input, width = 900, height = 240) {
  const points = input.filter(p => Number.isFinite(p.value) && p.value > 0 && Number.isFinite(Date.parse(p.observed_at)))
    .map(p => ({...p, time: Date.parse(p.observed_at)})).sort((a, b) => a.time - b.time);
  if (!points.length) return null;
  const minimum = Math.min(...points.map(p => p.value));
  const maximum = Math.max(...points.map(p => p.value));
  const padding = Math.max((maximum - minimum) * .16, .08);
  const low = minimum - padding, high = maximum + padding;
  const first = points[0].time, span = Math.max(1, points.at(-1).time - first);
  const mapped = points.map(p => ({...p, x: 12 + (p.time - first) / span * (width - 24), y: 12 + (high - p.value) / (high - low) * (height - 24)}));
  const line = mapped.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
  return {points: mapped, line, area: `${line} L${mapped.at(-1).x},${height} L${mapped[0].x},${height} Z`, low, high, minimum, maximum};
}

export function quoteChange(quote, reference) {
  if (!quote || !reference?.available || quote.contract !== reference.contract || !Number.isFinite(reference.previous_price) || reference.previous_price <= 0) return null;
  const delta = quote.value - reference.previous_price;
  return {delta, percent: delta / reference.previous_price * 100};
}

export function candleGeometry(input, width = 900, height = 250) {
  const items = input.filter(p => [p.open, p.high, p.low, p.close].every(v => Number.isFinite(v) && v > 0)
    && p.high >= Math.max(p.open, p.close, p.low) && p.low <= Math.min(p.open, p.close, p.high));
  if (!items.length) return null;
  const minimum = Math.min(...items.map(p => p.low)), maximum = Math.max(...items.map(p => p.high));
  const padding = Math.max((maximum - minimum) * .12, .1);
  const low = minimum - padding, high = maximum + padding;
  const y = v => 12 + (high - v) / (high - low) * (height - 24);
  const span = (width - 65) / items.length;
  return {items: items.map((p, i) => ({...p, x: 12 + (i + .5) * span, openY: y(p.open), closeY: y(p.close), highY: y(p.high), lowY: y(p.low)})),
    bodyWidth: Math.max(2, Math.min(16, span * .54)), minimum, maximum,
    ticks: [0, .25, .5, .75, 1].map(f => ({value: low + (high - low) * f, y: y(low + (high - low) * f)}))};
}
