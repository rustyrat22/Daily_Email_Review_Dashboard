"""
Generates sample_data.csv with columns: date, category, metric, value, status
Run this once to create example data. Replace data/sample_data.csv with your
own CSV (same column names, or update app.py to match your columns) once
you're ready to use real data.
"""
import csv
import random
from datetime import datetime, timedelta

random.seed(42)

CATEGORIES = ["Sales", "Support", "Marketing", "Ops"]
METRICS = ["Revenue", "Tickets", "Signups", "Errors"]
STATUSES = ["Complete", "Pending", "Failed"]

start_date = datetime(2026, 8, 1)
days = 45

rows = []
for d in range(days):
    date = start_date + timedelta(days=d)
    for _ in range(random.randint(3, 8)):
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "category": random.choice(CATEGORIES),
            "metric": random.choice(METRICS),
            "value": round(random.uniform(10, 500), 2),
            "status": random.choices(STATUSES, weights=[70, 20, 10])[0],
        })

with open("/data/sample_data.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["date", "category", "metric", "value", "status"])
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} rows to /data/sample_data.csv")
