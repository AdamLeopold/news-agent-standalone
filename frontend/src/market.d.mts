export type Point = {observed_at: string; value: number; available_at?: string};
export function candleSymbol(label?: string): string | null;
export function chartGeometry(input: Point[], width?: number, height?: number): null | {
  points: (Point & {time: number; x: number; y: number})[]; line: string; area: string; low: number; high: number; minimum: number; maximum: number;
};
export function quoteChange(quote: {contract: string; value: number} | null | undefined, reference: {available: boolean; contract?: string; previous_price?: number} | undefined): {delta: number; percent: number} | null;
export type Candle = {day: string; open: number; high: number; low: number; close: number; forming?: boolean};
export function candleGeometry(input: Candle[], width?: number, height?: number): null | {
  items: (Candle & {x: number; openY: number; closeY: number; highY: number; lowY: number})[];
  bodyWidth: number; minimum: number; maximum: number; ticks: {value: number; y: number}[];
};
