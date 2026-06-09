"""Excel export — one workbook with a tab per statement/table."""
from __future__ import annotations

import io
import pandas as pd


def to_excel_bytes(results) -> bytes:
    """Render the workbook to an in-memory bytes buffer (for downloads)."""
    buf = io.BytesIO()
    to_excel(results, buf)
    buf.seek(0)
    return buf.getvalue()


def to_excel(results, path):
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        # config
        cfg = pd.DataFrame(
            sorted(results.config.to_dict().items()), columns=["parameter", "value"])
        cfg.to_excel(xl, sheet_name="Assumptions", index=False)

        results.income_statement.to_excel(xl, sheet_name="Income Statement")
        results.cash_flow.to_excel(xl, sheet_name="Cash Flow")
        results.balance_sheet.to_excel(xl, sheet_name="Balance Sheet")
        results.portfolio.to_excel(xl, sheet_name="Portfolio")
        results.metrics.to_excel(xl, sheet_name="Metrics")
        results.stage_detail.to_excel(xl, sheet_name="IFRS9 Staging", index=False)
        if len(results.vintage):
            results.vintage.to_excel(xl, sheet_name="Vintage", index=False)
        results.invariant_report.to_excel(xl, sheet_name="Invariants", index=False)

        # light formatting: widen first column, number formats
        for ws in xl.book.worksheets:
            ws.column_dimensions["A"].width = 22
    return path
