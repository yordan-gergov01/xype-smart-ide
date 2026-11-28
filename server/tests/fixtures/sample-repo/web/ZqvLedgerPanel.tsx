// Zqvorchard panel showing the zqvyield total.
import { zqvAverageCrates, type ZqvTotals } from './zqvLedgerClient';

export function ZqvLedgerPanel(props: { zqvTotals: ZqvTotals }) {
  const zqvAverage = zqvAverageCrates(props.zqvTotals);
  return <section className="zqv-panel">zqvyield average: {zqvAverage}</section>;
}
