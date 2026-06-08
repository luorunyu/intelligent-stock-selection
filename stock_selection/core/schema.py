"""Shared column names used across the project."""

DATE_COL = "date"
SYMBOL_COL = "symbol"
OPEN_COL = "open"
HIGH_COL = "high"
LOW_COL = "low"
CLOSE_COL = "close"
VOLUME_COL = "volume"
AMOUNT_COL = "amount"
NAME_COL = "name"
INDUSTRY_COL = "industry"

REQUIRED_PRICE_COLUMNS = [
    DATE_COL,
    SYMBOL_COL,
    OPEN_COL,
    HIGH_COL,
    LOW_COL,
    CLOSE_COL,
    VOLUME_COL,
]

PRICE_COLUMNS = [OPEN_COL, HIGH_COL, LOW_COL, CLOSE_COL]

