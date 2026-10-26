"""Discover, profile and catalogue a data estate across PostgreSQL, MongoDB and JSON files; then score the discovery
against what is known to be true.

Outputs (results/ and catalog/):
  catalog.json                  every dataset and field: type, completeness, uniqueness, pattern, PII class
  data_dictionary.md            one section per dataset, for people
  pii_register.csv              every field classified as personal data, by system
  relationships.csv             discovered keys and foreign keys, within and across systems
  source_map.md                 a Mermaid map of systems, datasets and discovered links
  dbt_sources.yml               dbt sources with tests generated from the discovered keys (the hand-off to governance)
  prioritisation.csv            data-confidence score per dataset, and whether it is ready for governed reporting
  discovery_eval.json           precision and recall of key discovery against Postgres's declared keys, and recall of
                                the planted cross-system links, drift and type problems (estate/planted.json)

Usage: python discovery/run.py --pg "$PG_DSN" --mongo mongodb://localhost:27017 --feeds data/estate
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime
from pathlib import Path

import yaml

from profile import confidence, inclusion_dependencies, key_candidates, near_duplicate_fields, profile_records, summarise
from sources import JsonLinesSource, MongoSource, PostgresSource


def freshness_days(fields, as_of: datetime) -> float | None:
    latest = None
    for fp in fields.values():
        v = fp.max_value
        if isinstance(v, (datetime, date)):
            v = v if isinstance(v, datetime) else datetime(v.year, v.month, v.day)
            latest = v.replace(tzinfo=None) if latest is None else max(latest, v.replace(tzinfo=None))
    return None if latest is None else (as_of - latest).days


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pg", required=True)
    ap.add_argument("--mongo", default="mongodb://localhost:27017")
    ap.add_argument("--feeds", type=Path, default=Path("data/estate"))
    ap.add_argument("--planted", type=Path, default=Path("estate/planted.json"))
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--catalog", type=Path, default=Path("catalog"))
    ap.add_argument("--as-of", default="2026-10-26")
    args = ap.parse_args()
    as_of = datetime.fromisoformat(args.as_of)
    sources = [PostgresSource(args.pg), MongoSource(args.mongo), JsonLinesSource(args.feeds)]

    summaries, fields, datasets = [], {}, []
    for src in sources:
        for name, records in src.datasets():
            n, fps = profile_records(name, records)
            rows = [summarise(n, fp) for fp in fps.values()]
            summaries += rows
            fields.update({(name, path): fp for path, fp in fps.items()})
            datasets.append({"dataset": name, "system": src.system, "records": n, "fields": len(rows),
                             "_fps": fps, "_rows": rows})
    keys = key_candidates(summaries, fields)
    links = inclusion_dependencies(summaries, fields)
    drift = near_duplicate_fields(summaries)

    # data confidence and prioritisation
    key_ds = {k["dataset"] for k in keys}
    prio = []
    for d in datasets:
        c = confidence(d["_rows"], d["dataset"] in key_ds, freshness_days(d["_fps"], as_of))
        prio.append({"dataset": d["dataset"], "system": d["system"], "records": d["records"], "fields": d["fields"],
                     "pii_fields": sum(r["pii"] is not None for r in d["_rows"]), **c,
                     "ready_for_governed_reporting": c["score"] >= 75 and d["dataset"] in key_ds})

    # evaluation against what is known
    declared_pks, declared_fks = sources[0].declared_keys()
    pg_links = {(l["child_dataset"], l["child_field"], l["parent_dataset"], l["parent_field"]) for l in links
                if l["child_dataset"].startswith("rental_core.") and l["parent_dataset"].startswith("rental_core.")}
    true_fks = {(f["child_dataset"], f["child_field"], f["parent_dataset"], f["parent_field"]) for f in declared_fks}
    single_pks = {(p["dataset"], p["field"]) for p in declared_pks
                  if sum(q["dataset"] == p["dataset"] for q in declared_pks) == 1}
    found_pks = {(k["dataset"], k["field"]) for k in keys if k["dataset"].startswith("rental_core.")}
    planted = json.loads(args.planted.read_text())
    cross = {(f"{l['child_dataset']}.{l['child_field']}", f"{l['parent_dataset']}.{l['parent_field']}") for l in links if l["cross_source"]}
    want_cross = {(l["child"], l["parent"]) for l in planted["cross_system_links"]}
    drift_found = {(d["dataset"], frozenset((d["field_a"], d["field_b"]))) for d in drift}
    want_drift = {(d["dataset"], frozenset(d["fields"])) for d in planted["drift"]}
    text_amount = [s for s in summaries if s["dataset"] == "partner_feeds.payments" and s["field"] == "amount"]
    evaluation = {
        "datasets": len(datasets), "fields": len(summaries),
        "systems": {s.system: sum(d["system"] == s.system for d in datasets) for s in sources},
        "primary_keys": {"declared_single_column": len(single_pks), "found": len(found_pks & single_pks),
                         "recall": round(len(found_pks & single_pks) / max(len(single_pks), 1), 3)},
        "foreign_keys_within_postgres": {
            "declared": len(true_fks), "discovered": len(pg_links), "correct": len(pg_links & true_fks),
            "precision": round(len(pg_links & true_fks) / max(len(pg_links), 1), 3),
            "recall": round(len(pg_links & true_fks) / max(len(true_fks), 1), 3),
            "missed": sorted(" -> ".join([f"{a}.{b}", f"{c}.{d}"]) for a, b, c, d in true_fks - pg_links),
            "extra": sorted(" -> ".join([f"{a}.{b}", f"{c}.{d}"]) for a, b, c, d in pg_links - true_fks)},
        "cross_system_links": {"planted": len(want_cross), "found": len(cross & want_cross),
                               "recall": round(len(cross & want_cross) / len(want_cross), 3),
                               "extra": sorted(" -> ".join(x) for x in cross - want_cross),
                               "missed": sorted(" -> ".join(x) for x in want_cross - cross)},
        "schema_drift": {"planted": len(want_drift), "found": len(drift_found & want_drift),
                         "extra": len(drift_found - want_drift)},
        "amount_sent_as_text": bool(text_amount and text_amount[0]["type"] == "number-as-text"),
        "pii_fields": sum(s["pii"] is not None for s in summaries),
        "ready_for_governed_reporting": sum(p["ready_for_governed_reporting"] for p in prio),
    }

    # outputs
    args.out.mkdir(parents=True, exist_ok=True)
    args.catalog.mkdir(parents=True, exist_ok=True)
    (args.out / "discovery_eval.json").write_text(json.dumps(evaluation, indent=2, default=str))
    (args.catalog / "catalog.json").write_text(json.dumps(
        [{k: v for k, v in d.items() if not k.startswith("_")} | {"fields": d["_rows"]} for d in datasets],
        indent=2, default=str))
    for name, rows in (("pii_register", [s for s in summaries if s["pii"]]), ("relationships", links),
                       ("prioritisation", sorted(prio, key=lambda p: -p["score"]))):
        if rows:
            with open(args.out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=[k for k in rows[0] if not k.startswith("_")], extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
    lines = ["# Data dictionary", "", f"Generated by discovery/run.py on {args.as_of}. {len(datasets)} datasets, "
             f"{len(summaries)} fields across PostgreSQL, MongoDB and JSON files.", ""]
    for d in datasets:
        lines += [f"## {d['dataset']} ({d['system']}, {d['records']:,} records)", "",
                  "| Field | Type | Complete | Distinct | Unique | Pattern | PII |", "|---|---|---:|---:|---|---|---|"]
        lines += [f"| `{r['field']}` | {r['type']} | {r['completeness']:.0%} | {r['distinct']:,} | "
                  f"{'yes' if r['unique'] else ''} | `{r['top_shape'] or ''}` | {r['pii'] or ''} |" for r in d["_rows"]]
        lines.append("")
    (args.catalog / "data_dictionary.md").write_text("\n".join(lines), encoding="utf-8")
    mermaid = ["```mermaid", "flowchart LR"]
    for s in sources:
        mermaid.append(f'  subgraph {s.system.replace(" ", "_")}["{s.system}"]')
        mermaid += [f'    {d["dataset"].replace(".", "__")}["{d["dataset"].split(".", 1)[1]}"]' for d in datasets if d["system"] == s.system]
        mermaid.append("  end")
    mermaid += [f'  {l["child_dataset"].replace(".", "__")} -->|{l["child_field"]}| {l["parent_dataset"].replace(".", "__")}'
                for l in links if l["cross_source"]]
    (args.catalog / "source_map.md").write_text("# Source map: cross-system links discovered\n\n" + "\n".join(mermaid + ["```"]), encoding="utf-8")
    dbt = {"version": 2, "sources": []}
    for s in sources:
        tables = []
        for d in (x for x in datasets if x["system"] == s.system and x["system"] == "postgres"):
            table = d["dataset"].split(".", 1)[1]
            cols = []
            for k in (k for k in keys if k["dataset"] == d["dataset"] and (d["dataset"], k["field"]) in single_pks | found_pks):
                cols.append({"name": k["field"], "data_tests": ["unique", "not_null"]})
            for l in (l for l in links if l["child_dataset"] == d["dataset"] and not l["cross_source"]):
                cols.append({"name": l["child_field"], "data_tests": [{"relationships": {"arguments": {
                    "to": f"source('rental_core', '{l['parent_dataset'].split('.', 1)[1]}')", "field": l["parent_field"]}}}]})
            tables.append({"name": table, "columns": cols} if cols else {"name": table})
        if tables:
            dbt["sources"].append({"name": "rental_core", "schema": "public", "tables": tables})
    (args.out / "dbt_sources.yml").write_text(yaml.safe_dump(dbt, sort_keys=False), encoding="utf-8")
    print(json.dumps(evaluation, indent=2, default=str))


if __name__ == "__main__":
    main()
