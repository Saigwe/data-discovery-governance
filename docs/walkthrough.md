# 3-minute walkthrough: Data Discovery & Governance

A timed script for a Loom recording of about 3 minutes (screen plus camera bubble, 1080p). Say it in your own words; the lines are a guide, not a teleprompter.

**Open these tabs before you press record:** README, the estate diagram, `estate/build_estate.py` (the planted problems), `discovery/profile.py` (`inclusion_dependencies`), the `catalog` branch: `catalog/data_dictionary.md`, `catalog/source_map.md`, `results/dbt_sources.yml`, `results/discovery_eval.json`.

| Time | On screen | What to say |
|---|---|---|
| 0:00–0:20 | Portfolio card, then the README title | A new data team inherits a relational system of record, an app team's document store and a partner's file drops. They use different names for the same thing and declare no keys between them. Before anyone reports on it, someone has to find what's there, how it joins and what's personal data. |
| 0:20–0:40 | README intro | For VoCoVo I profiled 56 tables across PostgreSQL, MongoDB and JSON, prioritised 36 for governed reporting and built the data dictionary. This rebuilds that method on public data and adds what client work had no time for: a measurement of how accurate the discovery is. |
| 0:40–1:00 | Estate diagram, then `build_estate.py` | Pagila in PostgreSQL 18 declares its keys, so it's the answer key. From it I build an app team's MongoDB with nested documents and a partner's JSON feed with its own field names. Drift, missing phones, amounts sent as text and 8 cross-system links are planted. The profiler never reads that file. |
| 1:00–1:30 | `inclusion_dependencies` in `profile.py` | A foreign key is a field whose values sit inside another dataset's key, with a related name, so `customerRef` matches `customer_id`. The parent must be that dataset's own key, and the dataset named exactly for it wins, so `film_id` points at `film`, not its one-to-one extensions. |
| 1:30–2:00 | `discovery_eval.json` | Graded: primary keys 13 of 13. Foreign keys at precision and recall of 0.955, 21 of 22. All 8 cross-system links found. The miss is a column with no data. The "extra" is a real manager-to-staff link Pagila never declared. |
| 2:00–2:20 | README "How it got there" | The first run scored 0.50 precision. Four fixes, each with a unit test, got it there. One was that my singulariser turned "address" into "addres". PII had three false positives, a date taken for a phone number among them; now it's 21 fields and none false. |
| 2:20–2:45 | `data_dictionary.md`, `source_map.md`, then `dbt_sources.yml` | Every push publishes the data dictionary, a source map of the links, a PII register, a confidence score per dataset, and dbt sources with unique, not-null and relationships tests generated from what it found. That's the hand-off to governance. |
| 2:45–3:00 | Prioritisation table, then the README top | 17 of 19 datasets are ready for governed reporting, and the score explains why each isn't. For a data team, that's weeks of manual profiling turned into a repeatable run with a measured error rate. Links are under the video. |

## Recording checklist
- Close email, chat and notifications. Zoom the browser to 125% so code is readable.
- Running it needs PostgreSQL 18 and MongoDB; record from the `catalog` branch and the CI run instead of running it live.
- Keep to one take per section; Loom lets you trim. Aim for 2:45 to 3:10.
- Show real numbers on screen as you say them; never estimate on camera.
- Say that the MongoDB and JSON estate is generated from Pagila with planted problems.

## After recording
1. Copy the Loom share link.
2. In `README.md`, replace the roadmap line with `- [x] [3-minute Loom walkthrough](<your Loom link>)` and add the link under the title.
3. Add the link to this project's card on saigwe.github.io.
