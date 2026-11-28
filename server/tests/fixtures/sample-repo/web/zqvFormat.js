// Zqvorchard formatting helpers.
export function zqvFormatCrates(zqvCount) {
  return `${zqvCount} zqvcrates`;
}

export function zqvPadRow(zqvRow) {
  return String(zqvRow).padStart(3, '0');
}
