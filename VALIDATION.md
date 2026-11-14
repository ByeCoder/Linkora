# Validation

[English](VALIDATION.md) · [فارسی](VALIDATION_FA.md)

Release **2.2.0**, prepared on **2026-11-14**.

## Automated checks

The token-free pytest suite passed **91 tests** and exercises all eight language catalogs and actual Telegram-shaped message/callback flows. Coverage includes mandatory language selection before first use, persisted choices, authorization, owner-bound buttons, private/group support replies, contact ownership, referrals and reward limits, both channel goal modes, calendar boundaries, daily and period aggregates, campaign counts, CSV escaping, backups, old-database migration, callback expiry and group migration.

The tests use mocks/HTTP mock transports and a minimized public-search fixture. They do not send Telegram messages. The fixture preserves public response structure and metadata while replacing community descriptions with short test text; it is never served as live search data.

```bash
python -m pytest -q
python -m compileall -q bot tools
```

Runtime requirements were also installed in a fresh virtual environment. Program imports, schema creation, Jalali reporting, fixture parsing and a clear startup error for missing configuration were checked.

## Live read-only site verification

On 2026-11-14, `python` filtered to Telegram at offset 10 returned 167 site-reported total results and ten Telegram entries on its second page. Earlier same-day probes returned 283 all-platform results, 24 results with English/1001–10000-member filters, and public option lists containing 92 languages, 57 categories and 206 locations. These are observations, not fixed expected production counts.

## Not verified in a live deployment

No real bot token or owner configuration was supplied. Real Telegram delivery, actual channel membership, operator-group replies, user photos, custom-emoji eligibility, client-side button rendering and live broadcasting therefore still require an operator deployment check. Docker was unavailable in the preparation environment, so an actual container build/run was not performed there.

No live bot has been deployed by uploading this repository. Private Waybien account operations are outside the implemented scope; see the README.
