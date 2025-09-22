# app/services/exports.py
import logging
from pathlib import Path
import csv
from datetime import datetime

log = logging.getLogger("exports")

def export_table(rows, headers, fname_prefix: str, out_dir="data/exports"):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    path = Path(out_dir) / f"{fname_prefix}_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h,"") for h in headers})
    log.info("Exported %s (%d rows)", path, len(rows))
    return path
