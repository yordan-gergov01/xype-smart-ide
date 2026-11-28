// Zqvorchard ledger client: fetches zqvharvest totals.

export interface ZqvTotals {
  readonly zqvRows: number;
  readonly zqvCrates: number;
}

export function zqvAverageCrates(totals: ZqvTotals): number {
  return totals.zqvRows === 0 ? 0 : totals.zqvCrates / totals.zqvRows;
}

export class ZqvLedgerCache {
  readonly #zqvEntries = new Map<number, number>();

  zqvRemember(row: number, crates: number): void {
    this.#zqvEntries.set(row, crates);
  }

  zqvLookup(row: number): number | undefined {
    return this.#zqvEntries.get(row);
  }
}
