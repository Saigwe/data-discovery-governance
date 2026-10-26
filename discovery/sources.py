"""Source connectors. Each yields (dataset name, records) and, where the source declares them, its own keys.

  PostgresSource  every ordinary or partitioned table (partitions are read through their parent), plus the primary
                  and foreign keys declared in the catalogue, which become the answer key for key discovery
  MongoSource     every collection; documents are profiled as they are, nested
  JsonLinesSource every folder of *.jsonl files is one dataset (a feed dropped by month, say)
"""
from __future__ import annotations

import json
from pathlib import Path


class PostgresSource:
    system = "postgres"

    def __init__(self, dsn: str, schema: str = "public", name: str = "rental_core"):
        import psycopg

        self.conn = psycopg.connect(dsn)
        self.schema, self.name = schema, name

    def datasets(self):
        cur = self.conn.cursor()
        cur.execute("""select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
                       where n.nspname = %s and c.relkind in ('r', 'p') and not c.relispartition order by 1""",
                    (self.schema,))
        for (table,) in cur.fetchall():
            cur.execute(f'select * from "{self.schema}"."{table}"')
            cols = [d.name for d in cur.description]
            yield f"{self.name}.{table}", (dict(zip(cols, row)) for row in cur.fetchall())

    def declared_keys(self) -> tuple[list[dict], list[dict]]:
        cur = self.conn.cursor()
        cur.execute("""
            select tc.constraint_type, tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on kcu.constraint_name = tc.constraint_name and kcu.table_schema = tc.table_schema
            left join information_schema.constraint_column_usage ccu
              on ccu.constraint_name = tc.constraint_name and ccu.table_schema = tc.table_schema
            where tc.table_schema = %s and tc.constraint_type in ('PRIMARY KEY', 'FOREIGN KEY')""", (self.schema,))
        pks, fks = [], []
        for ctype, table, col, ptable, pcol in cur.fetchall():
            if "_p20" in table:  # partitions repeat the parent's constraints
                continue
            if ctype == "PRIMARY KEY":
                pks.append({"dataset": f"{self.name}.{table}", "field": col})
            else:
                fks.append({"child_dataset": f"{self.name}.{table}", "child_field": col,
                            "parent_dataset": f"{self.name}.{ptable}", "parent_field": pcol})
        dedup = lambda rows: [dict(t) for t in {tuple(sorted(r.items())) for r in rows}]
        return dedup(pks), dedup(fks)


class MongoSource:
    system = "mongodb"

    def __init__(self, uri: str, database: str = "customer_app", client=None):
        if client is None:
            import pymongo

            client = pymongo.MongoClient(uri)
        self.db, self.name = client[database], database

    def datasets(self):
        for coll in sorted(self.db.list_collection_names()):
            yield f"{self.name}.{coll}", self.db[coll].find({})

    def declared_keys(self):
        return [], []  # MongoDB declares no foreign keys; that is the point of discovering them


class JsonLinesSource:
    system = "json files"

    def __init__(self, root: Path, name: str = "partner_feeds"):
        self.root, self.name = Path(root), name

    def datasets(self):
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            def records(folder=folder):
                for f in sorted(folder.glob("*.jsonl")):
                    for line in f.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            yield json.loads(line)
            yield f"{self.name}.{folder.name}", records()

    def declared_keys(self):
        return [], []
