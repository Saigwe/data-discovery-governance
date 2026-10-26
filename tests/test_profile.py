"""Unit tests for profiling, PII classification and key discovery, on small in-memory records (no database needed)."""
from __future__ import annotations

import sys
from pathlib import Path

import mongomock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "discovery"))
from profile import (classify_pii, confidence, flatten, inclusion_dependencies, kind, near_duplicate_fields,  # noqa: E402
                     profile_records, shape, summarise)
from sources import MongoSource  # noqa: E402


def profiled(name, records):
    n, fps = profile_records(name, records)
    return [summarise(n, fp) for fp in fps.values()], {(name, p): fp for p, fp in fps.items()}


def test_flatten_nested_documents_and_arrays():
    flat = flatten({"a": {"b": 1}, "items": [{"x": 1}, {"x": 2}], "tags": ["p", "q"]})
    assert flat == {"a.b": 1, "items[].x": [1, 2], "tags": ["p", "q"]}


def test_kinds_and_shapes():
    assert kind("4.99") == "number-as-text" and kind("2026-01-02T03:04:05") == "timestamp-as-text" and kind(7) == "integer"
    assert shape("AB-1234") == "A+-9+"


def test_pii_by_name_and_by_value():
    s, f = profiled("t", [{"emailAddress": "a@b.com", "notes": "x@y.org", "first_name": "Ann", "amount": "12"}])
    pii = {r["field"]: r["pii"] for r in s}
    assert pii == {"emailAddress": "email", "notes": "email", "first_name": "person name", "amount": None}


def test_pii_uses_path_context_and_ignores_dates():
    s, _ = profiled("app.people", [{"name": {"first": "Ann", "last": "Lee"}, "cast": [{"name": "Bo Ray"}],
                                    "contact": {"address": {"line1": "1 High St", "postalCode": "12345"}},
                                    "create_date": "2006-02-14", "label": {"name": "Comedy"}}])
    pii = {r["field"]: r["pii"] for r in s}
    assert pii == {"name.first": "person name", "name.last": "person name", "cast[].name": "person name",
                   "contact.address.line1": "street address", "contact.address.postalCode": "postal code",
                   "create_date": None, "label.name": None}


def test_inclusion_dependency_needs_containment_and_a_related_name():
    customers = [{"customer_id": i} for i in range(1, 11)]
    orders = [{"order_id": i, "customerRef": 1 + i % 10, "qty": 1 + i % 10} for i in range(30)]
    s1, f1 = profiled("db.customer", customers)
    s2, f2 = profiled("feed.orders", orders)
    links = inclusion_dependencies(s1 + s2, {**f1, **f2})
    pairs = {(l["child_field"], l["parent_field"]) for l in links}
    assert ("customerRef", "customer_id") in pairs      # same entity, different spelling
    assert ("qty", "customer_id") not in pairs          # contained values, unrelated name: rejected
    assert all(l["cross_source"] for l in links if l["child_field"] == "customerRef")


def test_drift_between_spellings_of_one_field():
    recs = [{"email": "a@b.com"}] * 19 + [{"emailAddress": "c@d.com"}]
    s, _ = profiled("app.users", recs)
    assert [(d["field_a"], d["field_b"]) for d in near_duplicate_fields(s)] == [("email", "emailAddress")]


def test_mongo_source_reads_nested_collections():
    client = mongomock.MongoClient()
    client["customer_app"]["profiles"].insert_many([{"customer_id": 1, "contact": {"phone": "555-0100"}}])
    (name, records), = list(MongoSource("unused", client=client).datasets())
    s, _ = profiled(name, records)
    assert name == "customer_app.profiles" and {r["field"] for r in s} == {"_id", "customer_id", "contact.phone"}


def test_parent_must_be_the_datasets_own_key_and_the_exact_home_wins():
    film = [{"film_id": i, "title": f"T{i}"} for i in range(1, 6)]
    embedding = [{"film_id": i} for i in range(1, 6)]                           # 1:1 extension of film
    customer = [{"customer_id": i, "address_id": i, "store_id": 1 + i % 2} for i in range(1, 6)]
    address = [{"address_id": i} for i in range(1, 9)]
    store = [{"store_id": 1}, {"store_id": 2}]
    inventory = [{"inventory_id": i, "film_id": 1 + i % 5, "store_id": 1 + i % 2} for i in range(10)]
    s, f = [], {}
    for name, recs in (("db.film", film), ("db.film_embedding", embedding), ("db.customer", customer),
                       ("db.store", store), ("db.inventory", inventory), ("db.address", address)):
        a, b = profiled(name, recs)
        s, f = s + a, {**f, **b}
    pairs = {(l["child_dataset"], l["child_field"], l["parent_dataset"]) for l in inclusion_dependencies(s, f)}
    assert ("db.inventory", "film_id", "db.film") in pairs
    assert ("db.film_embedding", "film_id", "db.film") in pairs
    assert not any(p == "db.film_embedding" for _, _, p in pairs)              # extensions are not parents
    assert ("db.inventory", "store_id", "db.store") in pairs                   # two stores are enough by name
    assert ("db.customer", "address_id", "db.address") in pairs                # 'address' is not a plural
    assert not any(p == "db.customer" and c == "address_id" for _, c, p in pairs)  # customer.address_id is no home


def test_confidence_penalises_text_typed_numbers_and_drift():
    s, _ = profiled("feed.pay", [{"id": i, "amount": f"{i}.50", "email": "a@b.com"} for i in range(9)]
                    + [{"id": 9, "amount": "1.50", "emailAddress": "c@d.com"}])
    clean, _ = profiled("db.pay", [{"id": i, "amount": i + 0.5, "email": "a@b.com"} for i in range(10)])
    worse = confidence(s, True, 0, {"email", "emailAddress"})
    better = confidence(clean, True, 0)
    assert worse["conformance"] == 0.25 and better["conformance"] == 1.0 and worse["score"] < better["score"]
