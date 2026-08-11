# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.nl2sql.validator import SQLValidator

p = Path(__file__).resolve().parent / "nl2sql_qa_examples_锅炉四管_一期人工样例.jsonl"
rows = [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]
v = SQLValidator()
assert len(rows) == 100
assert all(v.validate(r["sql"]) for r in rows)
assert sum(1 for r in rows if "btp.device_id = t.device_id" in r["sql"]) >= 20
assert any("GROUP BY boiler_id" in r["sql"] and "最近一次检修" in r["question"] for r in rows)
assert not any("current_a >= 20" in r["sql"] for r in rows)
assert any("mark_type = '2' AND r.defect_type = '2'" in r["sql"] for r in rows)
for r in rows:
    if "最近一次检修发现" in r["question"] or "磨损类" in r["question"] or "吹灰工作电流" in r["question"]:
        print("====", r["id"], r["question"])
        print(r["sql"][:360])
        print()
print("OK", len(rows))
