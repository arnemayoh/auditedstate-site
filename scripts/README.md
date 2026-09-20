# Sitemap generator

Run locally from the repo:

```
python3 scripts/build_sitemaps.py
```

That writes `sitemap.xml` and the child `sitemap-*.xml` files at the repo root. Use `-v` for the rename report and lastmod spot-checks. Generation is **not** part of deploy: a shallow clone on the host must never compute dates. Commit the XML alongside content changes.

`structural-alignment/` is grouped in `sitemap-structural-alignment.xml` (this replaced `sitemap-eu-ai-act.xml`). The script removes a child sitemap that is no longer in its mapping table.

Each URL's `<lastmod>` is the author date of the last **substantive** commit that touched that page (`git log --follow -M --name-status`), skipping hashes in `audit/sweep-commits.txt`, any commit whose subject starts with `[sweep]`, and any commit where git records that file as an **exact rename** (`R100`). A rename that also changes content (`R099` and below) still counts. If every remaining commit is skipped, the date used is the earliest commit in the follow chain.

Mechanical commits (nav, term renames, canonical/title/template fixes, link repoints, shared CSS) must begin with `[sweep]` from now on so they never move a page's date.

New real pages must be added to `sitemap-approved.txt` or `sitemap-exclude.txt`. They are never picked up automatically. URLs already public live in `sitemap-baseline.txt`.
