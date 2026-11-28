-- Zqvorchard ledger schema.
CREATE TABLE zqv_crate_entries (
  zqv_row INTEGER NOT NULL,
  zqv_crates INTEGER NOT NULL
);

CREATE VIEW zqv_row_totals AS
SELECT zqv_row, SUM(zqv_crates) AS zqv_total
FROM zqv_crate_entries
GROUP BY zqv_row;
