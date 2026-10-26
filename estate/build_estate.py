"""Build a three-system data estate from Pagila, the way real estates grow, with known problems planted for discovery.

  rental_core (PostgreSQL)    Pagila as published: the system of record, with declared primary and foreign keys
  customer_app (MongoDB)      what an app team would build from it: customer profiles with nested contact details and
                              embedded recent rentals, and a denormalised film catalogue
  partner_feeds (JSON files)  a payment provider's monthly drop: its own field names, amounts as text

Planted, and written to estate/planted.json as the answer key (fixed seed):
  - 5% of customer profiles spell the email field "emailAddress" (schema drift)
  - 3% of customer profiles have no phone
  - partner payments rename every key (transactionId, customerRef, rentalRef) and send amounts as text
The cross-system links a discovery tool should find are listed there too; nothing in the profiler reads this file.

Usage: python estate/build_estate.py --pg "$PG_DSN" --mongo mongodb://localhost:27017 --feeds data/estate
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

SEED = 56


def main() -> None:
    import psycopg
    import pymongo

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pg", required=True)
    ap.add_argument("--mongo", default="mongodb://localhost:27017")
    ap.add_argument("--feeds", type=Path, default=Path("data/estate"))
    args = ap.parse_args()
    rng = random.Random(SEED)
    pg = psycopg.connect(args.pg).cursor()

    pg.execute("""select c.customer_id, c.store_id, c.first_name, c.last_name, c.email, c.activebool, c.create_date,
                         a.address, a.district, a.postal_code, a.phone, ci.city, co.country
                  from customer c join address a using (address_id) join city ci using (city_id)
                  join country co using (country_id) order by c.customer_id""")
    cols = [d.name for d in pg.description]
    customers = [dict(zip(cols, r)) for r in pg.fetchall()]
    pg.execute("""select customer_id, rental_id, inventory_id, rental_date,
                         (select film_id from inventory i where i.inventory_id = r.inventory_id) as film_id
                  from rental r order by customer_id, rental_date desc""")
    rentals = defaultdict(list)
    for cid, rid, inv, at, film in pg.fetchall():
        if len(rentals[cid]) < 5:
            rentals[cid].append({"rental_id": rid, "film_id": film, "rented_at": at.isoformat()})

    drift = set(rng.sample([c["customer_id"] for c in customers], k=round(0.05 * len(customers))))
    no_phone = set(rng.sample([c["customer_id"] for c in customers], k=round(0.03 * len(customers))))
    docs = []
    for c in customers:
        doc = {"customer_id": c["customer_id"], "store_id": c["store_id"],
               "name": {"first": c["first_name"].title(), "last": c["last_name"].title()},
               "contact": {"address": {"line1": c["address"], "district": c["district"], "city": c["city"],
                                       "country": c["country"], "postalCode": c["postal_code"]}},
               "active": bool(c["activebool"]), "created_at": c["create_date"].isoformat(),
               "recent_rentals": rentals[c["customer_id"]]}
        doc["emailAddress" if c["customer_id"] in drift else "email"] = c["email"].lower()
        if c["customer_id"] not in no_phone:
            doc["contact"]["phone"] = c["phone"]
        docs.append(doc)

    pg.execute("""select f.film_id, f.title, f.rating::text, f.length, f.rental_rate,
                         coalesce(array_agg(distinct cat.name) filter (where cat.name is not null), '{}') as categories
                  from film f left join film_category fc using (film_id) left join category cat using (category_id)
                  group by all order by f.film_id""")
    films = {r[0]: {"film_id": r[0], "title": r[1], "rating": r[2], "length_min": r[3],
                    "rental_rate": float(r[4]), "categories": list(r[5]), "cast": []} for r in pg.fetchall()}
    pg.execute("""select fa.film_id, a.actor_id, a.first_name || ' ' || a.last_name
                  from film_actor fa join actor a using (actor_id) order by 1, 2""")
    for film_id, actor_id, name in pg.fetchall():
        films[film_id]["cast"].append({"actor_id": actor_id, "name": name.title()})

    db = pymongo.MongoClient(args.mongo)["customer_app"]
    for name, rows in (("customer_profiles", docs), ("film_catalog", list(films.values()))):
        db[name].drop()
        db[name].insert_many(rows)

    pg.execute("select payment_id, customer_id, rental_id, amount, payment_date from payment order by payment_date")
    by_month = defaultdict(list)
    for pid, cid, rid, amount, at in pg.fetchall():
        by_month[at.strftime("%Y-%m")].append({"transactionId": f"TX{pid:07d}", "customerRef": cid, "rentalRef": rid,
                                               "amount": f"{amount:.2f}", "currency": "USD", "paidAt": at.isoformat()})
    feed = args.feeds / "payments"
    feed.mkdir(parents=True, exist_ok=True)
    for month, rows in by_month.items():
        (feed / f"{month}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    planted = {
        "drift": [{"dataset": "customer_app.customer_profiles", "fields": ["email", "emailAddress"], "share": 0.05}],
        "missing": [{"dataset": "customer_app.customer_profiles", "field": "contact.phone", "share": 0.03}],
        "amount_as_text": [{"dataset": "partner_feeds.payments", "field": "amount"}],
        "cross_system_links": [
            {"child": "customer_app.customer_profiles.customer_id", "parent": "rental_core.customer.customer_id"},
            {"child": "customer_app.customer_profiles.recent_rentals[].rental_id", "parent": "rental_core.rental.rental_id"},
            {"child": "customer_app.customer_profiles.recent_rentals[].film_id", "parent": "rental_core.film.film_id"},
            {"child": "customer_app.customer_profiles.store_id", "parent": "rental_core.store.store_id"},
            {"child": "customer_app.film_catalog.film_id", "parent": "rental_core.film.film_id"},
            {"child": "customer_app.film_catalog.cast[].actor_id", "parent": "rental_core.actor.actor_id"},
            {"child": "partner_feeds.payments.customerRef", "parent": "rental_core.customer.customer_id"},
            {"child": "partner_feeds.payments.rentalRef", "parent": "rental_core.rental.rental_id"},
        ],
    }
    Path("estate/planted.json").write_text(json.dumps(planted, indent=2))
    print(f"MongoDB: {len(docs)} customer profiles, {len(films)} films; partner feed: "
          f"{sum(map(len, by_month.values()))} payments in {len(by_month)} monthly files")


if __name__ == "__main__":
    main()
