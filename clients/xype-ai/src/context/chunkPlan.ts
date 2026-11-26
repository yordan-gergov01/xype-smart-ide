// Turning a file's definitions ("units") into chunks (ADR-0017, slice 10). Pure: the
// syntax trees are read elsewhere (`extract.ts`), so the rules here test without
// grammars. A chunk is a range of whole lines of the scrubbed file, its content exactly
// those lines with their terminators, so it is a function of (text, range) and the
// server's later fetch by line range returns the same bytes. Each line belongs to at
// most one chunk. These rules are part of the shared index: every client of a
// repository must cut the same chunks (golden fixtures in `chunker.test.ts`).

import type { ChunkInput } from './merkle';

/** At most this many lines in one chunk before a unit is split. */
export const MAX_UNIT_LINES = 150;
/** Lines per window when a range has no units to split by. */
export const WINDOW_LINES = 60;
/** UTF-8 bytes per chunk, well below the indexer's 64 KiB cap; longer lines are left out. */
export const MAX_CHUNK_BYTES = 16 * 1024;
const MAX_SYMBOL = 200;
const MAX_SYMBOL_PATH = 1000;

/** A definition found in the syntax tree; rows are 0-based and inclusive. */
export interface Unit {
  readonly kind: string;
  /** The simple name, or empty for an unnamed unit (a SQL statement, a stage). */
  readonly name: string;
  readonly start: number;
  readonly end: number;
  /** Units inside it (methods, subsections), used only when it is too large. */
  readonly children: readonly Unit[];
}

/** A call or construction in the file: what it names, and its row. */
export interface CallSite {
  readonly row: number;
  readonly name: string;
}

export interface Chunk extends ChunkInput {
  /** The last segment of the symbol: what calls to this chunk name. */
  readonly name: string;
  /** Simple names this chunk calls, sorted and unique. */
  readonly calls: readonly string[];
}

interface Piece {
  readonly kind: string;
  readonly symbol: string;
  readonly start: number;
  readonly end: number;
}

/** Collapses whitespace and caps the length, so odd names (YAML keys, headings) stay stable. */
export function symbolName(raw: string, max = MAX_SYMBOL): string {
  // By code points, so a surrogate pair is never cut in half.
  return Array.from(raw.replace(/\s+/g, ' ').trim()).slice(0, max).join('');
}

/** Lines with their terminators; no empty last line for a final newline. */
export function splitLines(text: string): string[] {
  const lines = text.split(/(?<=\n)/);
  return lines.length === 1 && lines[0] === '' ? [] : lines;
}

/**
 * Sorted, with each row owned once: a unit starting on a row the one before it already
 * owns starts on the next row, and a unit wholly inside it is dropped.
 */
function owned(units: readonly Unit[], from: number, to: number): Unit[] {
  const sorted = units
    .filter((u) => u.start >= from && u.end <= to)
    .sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Unit[] = [];
  let last = from - 1;
  for (const unit of sorted) {
    if (unit.end <= last) continue;
    const start = Math.max(unit.start, last + 1);
    out.push({ ...unit, start, children: unit.children.filter((c) => c.start >= start) });
    last = unit.end;
  }
  return out;
}

class Planner {
  readonly pieces: Piece[] = [];
  private readonly lines: readonly string[];
  private readonly bytes: number[];

  constructor(lines: readonly string[]) {
    this.lines = lines;
    this.bytes = lines.map((line) => Buffer.byteLength(line, 'utf8'));
  }

  range(units: readonly Unit[], from: number, to: number, scope: Piece | null): void {
    let cursor = from;
    for (const unit of owned(units, from, to)) {
      this.gap(cursor, unit.start - 1, scope);
      this.unit(unit, scope);
      cursor = unit.end + 1;
    }
    this.gap(cursor, to, scope);
  }

  private unit(unit: Unit, scope: Piece | null): void {
    const name = symbolName(unit.name);
    // The whole path is capped too, so deep nesting cannot grow symbols without bound.
    const symbol = symbolName(
      scope !== null && scope.symbol !== '' && name !== '' ? `${scope.symbol}.${name}` : name,
      MAX_SYMBOL_PATH,
    );
    const piece = { kind: unit.kind, symbol, start: unit.start, end: unit.end };
    if (this.fits(unit.start, unit.end)) this.pieces.push(piece);
    else if (unit.children.length > 0) this.range(unit.children, unit.start, unit.end, piece);
    else this.windows(piece);
  }

  /** Lines between units: inside a split unit they keep its kind and symbol. */
  private gap(from: number, to: number, scope: Piece | null): void {
    if (from > to) return;
    const kind = scope?.kind ?? 'block';
    this.windows({ kind, symbol: scope?.symbol ?? '', start: from, end: to });
  }

  private fits(from: number, to: number): boolean {
    if (to - from + 1 > MAX_UNIT_LINES) return false;
    let total = 0;
    for (let row = from; row <= to; row++) total += this.bytes[row] ?? 0;
    return total <= MAX_CHUNK_BYTES;
  }

  /** Splits by lines and bytes, trims blank lines at the edges, leaves out long lines. */
  private windows(piece: Piece): void {
    let start = -1;
    let total = 0;
    const flush = (end: number) => {
      if (start >= 0) this.push({ ...piece, start, end });
      start = -1;
      total = 0;
    };
    for (let row = piece.start; row <= piece.end; row++) {
      const size = this.bytes[row] ?? 0;
      if (size > MAX_CHUNK_BYTES) {
        flush(row - 1);
        continue;
      }
      if (start >= 0 && (row - start >= WINDOW_LINES || total + size > MAX_CHUNK_BYTES)) {
        flush(row - 1);
      }
      if (start < 0) start = row;
      total += size;
    }
    flush(piece.end);
  }

  private push(piece: Piece): void {
    const blank = (row: number) => (this.lines[row] ?? '').trim() === '';
    let { start, end } = piece;
    while (start <= end && blank(start)) start++;
    while (end >= start && blank(end)) end--;
    if (start <= end) this.pieces.push({ ...piece, start, end });
  }
}

/**
 * The chunks of one file in line order. `units` and `calls` come from its syntax tree;
 * `prefix` is the language part of a symbol (`py:Class.method`, ADR-0016).
 */
export function planChunks(
  prefix: string,
  text: string,
  units: readonly Unit[],
  calls: readonly CallSite[],
): Chunk[] {
  const lines = splitLines(text);
  const planner = new Planner(lines);
  planner.range(units, 0, lines.length - 1, null);
  const pieces = planner.pieces.sort((a, b) => a.start - b.start);
  const owner = new Map<number, number>();
  pieces.forEach((piece, index) => {
    for (let row = piece.start; row <= piece.end; row++) owner.set(row, index);
  });
  const called = pieces.map(() => new Set<string>());
  for (const call of calls) {
    const index = owner.get(call.row);
    if (index !== undefined && call.name !== '') called[index]?.add(call.name);
  }
  const seen = new Map<string, number>();
  return pieces.map((piece, index) => {
    const key = `${piece.kind}\u0000${piece.symbol}`;
    const occurrence = seen.get(key) ?? 0;
    seen.set(key, occurrence + 1);
    return {
      kind: piece.kind,
      symbol: piece.symbol === '' ? '' : `${prefix}:${piece.symbol}`,
      occurrence,
      content: lines.slice(piece.start, piece.end + 1).join(''),
      lines: [piece.start + 1, piece.end + 1],
      // What calls name; a dotted YAML key or heading gives only its tail, which is
      // harmless: calls are resolved for Python and scripts only.
      name: piece.symbol.slice(piece.symbol.lastIndexOf('.') + 1),
      calls: [...(called[index] ?? [])].sort(),
    };
  });
}
