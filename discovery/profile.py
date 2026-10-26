"""Field profiling, PII classification and key discovery over records from any source.

Sources hand over plain records (dicts). Nested documents are flattened into paths ("address.city", "rentals[].film_id"),
so a MongoDB collection, a JSON-lines feed and a Postgres table are profiled by the same code.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

PII_RULES = [  # (class, field-name pattern, value pattern); either can match, the value must not contradict
    ("email", r"e[-_]?mail", r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$"),
    ("phone", r"phone|mobile|tel", r"^\+?[\d\s().-]{7,}$"),
    ("person name", r"(first|last|full)[-_]?name|^name$|customer[-_]?name|employee[-_]?name", None),
    ("street address", r"^address\d?$|street|address[-_]?line", None),
    ("postal code", r"post(al)?[-_]?code|zip", None),
    ("payment", r"card|iban|account[-_]?number", r"^\d{12,19}$"),
    ("credential", r"pass(word)?|secret|token", None),
]


def kind(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, (float, Decimal)):
        return "number"
    if isinstance(v, (datetime, date)):
        return "timestamp"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, dict):
        return "object"
    s = str(v)
    if re.fullmatch(r"-?\d+", s):
        return "integer-as-text"
    if re.fullmatch(r"-?\d+\.\d+", s):
        return "number-as-text"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?", s):
        return "timestamp-as-text"
    return "text"


def shape(v) -> str:
    """A value's pattern: letters -> A/a, digits -> 9, runs collapsed (e.g. 'Aa+ Aa+', '9+-9+')."""
    s = re.sub(r"[A-Z]", "A", re.sub(r"[a-z]", "a", re.sub(r"\d", "9", str(v))))
    return re.sub(r"(.)\1+", r"\1+", s)[:24]


def flatten(record: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in record.items():
        if k == "_id":
            v = str(v)
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, path + "."))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            for item in v:  # arrays of documents: profile each element's fields under path[]
                for kk, vv in flatten(item, path + "[].").items():
                    out.setdefault(kk, []).append(vv)
        else:
            out[path] = v
    return out


@dataclass
class FieldProfile:
    dataset: str
    path: str
    present: int = 0
    nulls: int = 0
    kinds: Counter = field(default_factory=Counter)
    values: Counter = field(default_factory=Counter)
    shapes: Counter = field(default_factory=Counter)
    min_value: object = None
    max_value: object = None
    in_array: bool = False

    def add(self, v) -> None:
        values = v if (self.in_array and isinstance(v, list)) else [v]
        for x in values:
            self.present += 1
            k = kind(x)
            self.kinds[k] += 1
            if x is None:
                self.nulls += 1
                continue
            key = str(x)
            self.values[key] += 1
            self.shapes[shape(x)] += 1
            if k in ("integer", "number", "timestamp"):
                self.min_value = x if self.min_value is None else min(self.min_value, x)
                self.max_value = x if self.max_value is None else max(self.max_value, x)


def profile_records(dataset: str, records) -> tuple[int, dict[str, FieldProfile]]:
    fields: dict[str, FieldProfile] = {}
    n = 0
    for r in records:
        n += 1
        for path, v in flatten(r).items():
            fp = fields.setdefault(path, FieldProfile(dataset, path, in_array="[]" in path))
            fp.add(v)
    return n, fields


def classify_pii(path: str, fp: FieldProfile) -> str | None:
    name = path.split(".")[-1].replace("[]", "").lower()
    sample = [v for v, _ in fp.values.most_common(50)]
    for cls, name_rx, value_rx in PII_RULES:
        by_name = re.search(name_rx, name)
        by_value = value_rx and sample and sum(bool(re.match(value_rx, s, re.I)) for s in sample) >= 0.8 * len(sample)
        if by_value or (by_name and not (value_rx and sample and not by_value and cls in ("email", "payment"))):
            return cls
    return None


def summarise(n_records: int, fp: FieldProfile) -> dict:
    non_null = fp.present - fp.nulls
    distinct = len(fp.values)
    main_kind = max(((k, c) for k, c in fp.kinds.items() if k != "null"), key=lambda kc: kc[1], default=("null", 0))[0]
    return {
        "dataset": fp.dataset,
        "field": fp.path,
        "type": main_kind,
        "types_seen": sorted(k for k in fp.kinds if k != "null"),
        "completeness": round(non_null / n_records, 4) if n_records and not fp.in_array else (round(non_null / fp.present, 4) if fp.present else 0),
        "missing_from_records": 0 if fp.in_array else n_records - fp.present,
        "distinct": distinct,
        "unique": (distinct == non_null and non_null > 0 and not fp.in_array),
        "top_shape": fp.shapes.most_common(1)[0][0] if fp.shapes else None,
        "min": None if fp.min_value is None else str(fp.min_value),
        "max": None if fp.max_value is None else str(fp.max_value),
        "pii": classify_pii(fp.path, fp),
    }


def normalise(name: str) -> str:
    """Name used to compare fields across sources: 'customerRef' / 'customer_id' -> 'customer'."""
    n = re.sub(r"([a-z])([A-Z])", r"\1_\2", name.split(".")[-1].replace("[]", "")).lower()
    n = re.sub(r"(_id|_ref|_key|_no|id|ref)$", "", n).strip("_")
    return n or name.lower()


def key_candidates(summaries: list[dict], fields: dict[tuple[str, str], FieldProfile]) -> list[dict]:
    """Unique, complete fields of a key-like type: candidate primary keys."""
    out = []
    for s in summaries:
        if s["unique"] and s["completeness"] == 1.0 and s["type"] in ("integer", "integer-as-text", "text"):
            out.append({"dataset": s["dataset"], "field": s["field"]})
    return out


def inclusion_dependencies(summaries: list[dict], fields: dict[tuple[str, str], FieldProfile],
                           min_containment: float = 0.99, min_distinct: int = 3) -> list[dict]:
    """Candidate foreign keys: a field whose distinct values sit (almost) entirely inside a candidate key of another
    dataset, of a compatible type, with a related name. Containment alone over-matches small integer ranges, so the
    name must agree (same normalised name, or the child name equals the parent dataset's entity).

    A parent must be the dataset's own key: its name is the dataset's entity ('customer.customer_id'), or the entity
    starts with it ('film_catalog.film_id'). A unique 'customer.address_id' is not customer's key, so nothing points
    at it. Where one key name has several homes, the dataset named exactly for it wins: film_id points at 'film', not
    at its 1:1 extensions 'film_embedding' or 'film_catalog'. An exact own-key match may have as few as 2 values
    (two stores), because the name already carries the evidence that a small range otherwise cannot."""
    keys = key_candidates(summaries, fields)
    by_dataset_kind = {(s["dataset"], s["field"]): s for s in summaries}
    entity = lambda ds: re.sub(r"(?<!s)s$", "", ds.split(".")[-1])  # customers -> customer, address stays
    homes = defaultdict(list)  # normalised key name -> (parent, exact)
    for k in keys:
        pname, ent = normalise(k["field"]), entity(k["dataset"])
        if pname == ent or ent.startswith(pname + "_"):
            homes[pname].append(((k["dataset"], k["field"]), pname == ent))
    for name, hs in homes.items():
        if any(exact for _, exact in hs):
            homes[name] = [h for h in hs if h[1]]
    out = []
    for child, fp in fields.items():
        cs = by_dataset_kind[child]
        if cs["type"] not in ("integer", "integer-as-text", "text") or not fp.values:
            continue
        child_vals = set(fp.values)
        cname = normalise(child[1])
        for name, hs in homes.items():
            if not (cname == name or cname.endswith("_" + name)):
                continue
            for parent, exact in hs:
                if parent[0] == child[0] or len(child_vals) < (2 if exact and cname == name else min_distinct):
                    continue
                parent_vals = set(fields[parent].values)
                contained = len(child_vals & parent_vals) / len(child_vals)
                if contained >= min_containment:
                    out.append({"child_dataset": child[0], "child_field": child[1], "parent_dataset": parent[0],
                                "parent_field": parent[1], "containment": round(contained, 4),
                                "cross_source": child[0].split(".")[0] != parent[0].split(".")[0]})
    return out


def near_duplicate_fields(summaries: list[dict]) -> list[dict]:
    """Schema drift inside one dataset: two field names that mean the same thing (emailAddress / email)."""
    by_ds = defaultdict(list)
    for s in summaries:
        by_ds[s["dataset"]].append(s)
    out = []
    for ds, rows in by_ds.items():
        names = [(normalise(r["field"]), r) for r in rows]
        for i, (a, ra) in enumerate(names):
            for b, rb in names[i + 1:]:
                same_parent = ra["field"].rsplit(".", 1)[0] == rb["field"].rsplit(".", 1)[0] if "." in ra["field"] else "." not in rb["field"]
                base_a, base_b = re.sub(r"_?address$", "", a), re.sub(r"_?address$", "", b)
                if same_parent and a != b and (base_a == base_b or a.startswith(b) or b.startswith(a)) \
                        and ra["completeness"] + rb["completeness"] <= 1.02 and min(len(a), len(b)) >= 4:
                    out.append({"dataset": ds, "field_a": ra["field"], "field_b": rb["field"],
                                "completeness_a": ra["completeness"], "completeness_b": rb["completeness"]})
    return out


def confidence(summaries: list[dict], has_key: bool, freshness_days: float | None) -> dict:
    """A 0-100 data-confidence score per dataset, from its parts, so the score can be explained line by line."""
    complete = sum(s["completeness"] for s in summaries) / max(len(summaries), 1)
    consistent = sum(len(s["types_seen"]) <= 1 for s in summaries) / max(len(summaries), 1)
    fresh = 1.0 if freshness_days is None else max(0.0, 1 - freshness_days / 365)
    parts = {"completeness": round(complete, 3), "type_consistency": round(consistent, 3),
             "has_primary_key": 1.0 if has_key else 0.0, "freshness": round(fresh, 3)}
    score = 100 * (0.35 * parts["completeness"] + 0.25 * parts["type_consistency"]
                   + 0.25 * parts["has_primary_key"] + 0.15 * parts["freshness"])
    return {**parts, "score": round(score, 1)}
