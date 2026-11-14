# Linkora 💚

**Find your people.** A multilingual Telegram bot for discovering communities through Waybien's public search, with a management panel, two-way support, membership goals, and tools to grow an engaged audience.

**[English](README.md) · [فارسی — راهنمای کامل](README_FA.md)**

Documentation is English-first. **The bot does not choose English for users:** every new user sees a language picker before the home menu. Their explicit choice is saved and can be changed with `/language`.

## What you get

| Area | Included |
| --- | --- |
| Discovery | Live public Waybien search, 10-result pagination, platform/category/language/country/member-count/age filters, result details and links |
| Personal tools | Up to 200 favorites, the last 20 searches, page CSV export, five saved searches with optional new-result notifications |
| Growth | Unique referral links, one-time verified referral rewards, daily points and streaks, optional leaderboard, curated picks, campaign links |
| Support | Numbered tickets, text and media messages, replies from the operator panel or by replying in the support group, user ticket closure |
| Membership | Required public/private channels, per-channel goals based on verified bot users or the channel's actual total membership |
| Analytics | Daily, weekly, monthly and all-time statistics, Jalali/Gregorian month selection, seven-day view, 30-day CSV |
| Administration | `/panel`, additional admins, user lookup/ban/unban, language-targeted broadcasts, maintenance mode, SQLite backups |
| Presentation | Friendly messages, per-language button labels, ordering, visibility, one/two-column menus, supported colors and custom emoji |

This repository contains the source and installation instructions. You supply a Telegram bot token, owner ID, and a server. Publishing the source does not start a live bot.

## Languages and first-run behavior

The user interface supports **English, Persian, Simplified Chinese, Hindi, Arabic, Hebrew, Spanish, and French**. The first screen offers all eight languages; it does not skip selection based on Telegram's language. The chosen language is stored for subsequent visits.

The operator panel currently uses Persian. Community titles/descriptions and filter values returned by Waybien retain the source language. Free-text broadcasts and custom link titles are written by the operator; they are not automatically translated.

## Requirements

- Python **3.12** on Linux, or Docker with the Compose plugin.
- A bot token from [BotFather](https://t.me/BotFather).
- Your numeric Telegram user ID; the setup assistant can help you identify it from your own private message.
- Outbound access to Telegram and Waybien. No domain, webhook, or inbound port is required.

## Quick start

```bash
git clone https://github.com/ByeCoder/Linkora.git
cd Linkora
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python tools/setup.py
python -m bot
```

The setup assistant does not echo your token. It verifies the bot and creates `.env` with private file permissions. If you do not know your owner ID, leave that prompt empty, send the requested message to your bot, and select **your own** account from the returned candidates. It will not overwrite an existing `.env`.

For Persian setup prompts:

```bash
python tools/setup.py --language fa
```

Alternatively, copy `.env.example` to `.env` and set `BOT_TOKEN` and `OWNER_IDS` manually. Multiple owner IDs are comma-separated. Use numeric IDs, not usernames. Then send `/start` in Telegram, choose a language, and open `/panel` as the owner.

If an existing webhook is configured, startup stops without deleting it. Stop the old integration and deliberately remove that webhook using Telegram's `deleteWebhook` method before switching to polling. Pending updates are not silently discarded.

## Docker deployment

After creating `.env`:

```bash
docker compose up -d --build
docker compose logs -f --tail=100
```

The `bot_data` named volume persists the SQLite database. `docker compose down` preserves it; **`docker compose down -v` deletes the volume**. Run one process per bot token/database. A process lock prevents concurrent access to the same database.

The container has a heartbeat health check and restarts after process exit. Docker does not automatically restart a container solely because its health status becomes unhealthy. Use Docker on Windows; direct execution uses Linux's `fcntl` lock.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `BOT_TOKEN` | Required | Telegram BotFather token |
| `OWNER_IDS` | Required | Comma-separated positive numeric owner IDs |
| `DATABASE_PATH` | `data/bot.sqlite3` | Persistent SQLite database location |
| `TIMEZONE` | `Asia/Tehran` | Calendar boundaries, daily rewards and quotas |
| `WAYBIEN_BASE_URL` | `https://waybien.com` | Public search website |
| `WAYBIEN_API_URL` | `https://dashboard.waybien.com` | Public filter-list endpoints |
| `SEARCH_CACHE_SECONDS` | `120` | In-memory search cache duration |
| `SEARCH_INTERVAL_SECONDS` | `3` | Minimum interval between accepted search requests |
| `SEARCH_DAILY_LIMIT` | `200` | Initial daily quota; an operator can override it |
| `LOG_LEVEL` | `INFO` | Application log verbosity |

Bot tokens belong in `.env` or your deployment secret manager. `.env`, database files, runtime data and backups are excluded from Git. The standard HTTP proxy environment variables are supported by httpx; no proxy is embedded in the source.

## Operator setup

1. Open `/panel` from an owner account. Only owners can add/remove administrators or bind log/support groups. Administrators can access user records, broadcasts and backups.
2. Create private log and support groups and add the bot as an administrator. From the owner account, send `/bindlog` in the log group and `/bindsupport` in the support group. Commands with `@bot_username` also work. Send them as your own account rather than anonymously.
3. Alternatively, configure numeric group IDs in the panel's integrations section. Enter `0` to disable a destination. Without a support group, incoming tickets are delivered to the owners.
4. Add required channels and configure the goal mode and value. The bot must be an administrator in each channel.
5. Configure referral rewards, daily points, optional invitation requirements, curated picks, welcome messages and button presentation.
6. Test with an ordinary account. Owners/admins are exempt from membership and maintenance gates.

Home, language choice, profile, invitation links, privacy information and support remain available so an ordinary user can resolve access problems. Search, result actions, pagination and exports recheck access. A failed membership check does not grant access. Pending join requests are not treated as membership.

## Required channels and membership targets

Add a public channel as `@channel_username`. For a private channel, send its numeric ID and invitation URL on one line:

```text
-1001234567890 https://t.me/+INVITE
```

The operator must verify that the invitation URL belongs to the intended private channel. This flow cannot independently prove that an arbitrary invitation URL matches the numeric channel ID. Up to ten channels can be configured.

| Goal mode | What is measured | When the goal is reached |
| --- | --- | --- |
| `verified` | Distinct bot user IDs whose membership the bot has confirmed | That channel's requirement stops applying to subsequent requests |
| `members` | The actual total returned by Telegram's `getChatMemberCount` | The channel's requirement is disabled; only an operator re-enables it |
| Goal `0` | No target threshold | Membership remains required while enabled |

Verified-user counts are cumulative, include pre-existing members, and do not decrease when a member leaves. Neither mode proves that the bot acquired those members. In `members` mode, a later drop below the target does not automatically re-enable the requirement.

The optional **minimum verified referrals to unlock search** is separate from a channel target. Referrals qualify after a new user passes the membership stage, before invitation-based search gating, to avoid a circular unlock requirement.

## Support and user records

A user chooses the support button or `/support`, then sends text, a photo, document, voice note, audio, video, animation, video note or sticker. The message receives a ticket number. Further messages remain in support mode until `/cancel` or navigation away from it. Limits are three seconds between messages and 100 support messages per user per day.

An authorized operator can respond by:

- Replying to the ticket header or copied user message in the configured support group.
- Opening the ticket in `/panel` and choosing its reply action.
- Sending `/reply 123` privately and then sending the response for ticket 123.
- Replying to a copied ticket message received in the operator's private bot chat.

An ordinary group member cannot answer as the bot: the sender must also be a registered bot administrator or owner. Users can view their own recent tickets and close them. After closure, a new support message creates a new ticket.

The operator's user record includes names, ID, username, Telegram/bot language, Premium flag, first/last interaction, points, referrals, activity counts, ban state and last known reachability. When available through Telegram, the bot can also retrieve the user's bio, birthday, active usernames, personal channel and accessible profile photo. Unavailable fields are not invented.

Phone numbers are saved only when users voluntarily share **their own** contact through the request-contact button. Users can remove the stored number. The bot cannot retrieve a user's IP, email, contacts or unrelated private chats.

The log group receives supported profile information for registration, profile changes, contact changes, qualified referrals, tickets/replies, search errors, channel-goal completion and management events. Search history can be inspected in the operator's user view; each search is not sent as a separate group log message.

Ticket history keeps Telegram message references and can copy the last five available ticket messages to an operator. It is not an archive of every original media file. Deleted or protected originals may no longer be copyable. Removing a stored phone does not retroactively remove prior log-group messages or backups.

## Discovery, saved searches and growth

Search pages contain up to ten results, next/previous controls, result details, direct links and a CSV of **that page**. Favorites are snapshots from the time of saving. The similar-result button searches the result's category; curated collections are operator-selected search topics.

Each user may save five searches. New-result alerts are **off by default** and checked at most once every six hours. The first successful check establishes a baseline. Later checks compare first-page result IDs; a ranking change can count as a new first-page result. This is not exhaustive monitoring of the entire website. Disabling alerts also skips an alert still waiting in the outbox. Opening a result from an alert rechecks access.

Referral links use Telegram deep linking. Only a new bot user can be attributed, and rewards are issued once after membership qualification. Self-referrals, existing users and repeated qualification do not earn rewards. This is duplicate prevention, not proof of real-world identity or protection against all account farming.

Daily rewards create a streak. Fifty points can be exchanged for 20 extra searches **that day**, up to five exchanges daily. Points have no cash value. Leaderboard participation is opt-in. Popular topics use this bot's last seven days of successful searches and require at least three distinct users before an expression is displayed.

Campaign links count newly attributed users rather than repeated `/start` visits. Referral and campaign links are separate attribution modes. Broadcasts can target all eligible users or one bot-interface language; the operator supplies the text for that audience. Broadcasts require preview confirmation, persist their recipient queue, and can be stopped. An in-flight message may still be delivered after stopping.

## Button and message customization

Open `/panel` → button design. The `default` entry sets the default presentation. Supported settings include:

- A label for each language, for example `en Let's discover 🔎` or `fa بیا بگردیم 🔎`. Use `en reset` to clear an override.
- `primary` blue, `success` green, `danger` red, or the Telegram client's default style. Ordinary Bot API buttons do not expose arbitrary RGB/CSS styling.
- A custom emoji ID, extracted from a custom-emoji message or entered as a numeric `custom_emoji_id`; enter `0` to remove it. Telegram's bot-owner Premium/eligible Fragment-username requirements apply. The end user's Premium subscription alone does not grant the bot this capability.
- A home-menu ordering value from 0–1000, visibility, and a one/two-column layout.
- Up to ten custom HTTPS link buttons, with per-language label overrides.

When Telegram explicitly rejects a custom emoji or style, the keyboard is retried without those decorations while keeping its actions. Older clients may display an unstyled button. Visibility/order settings affect home-menu items and custom links; dynamically generated workflow rows follow their data. Overriding a dynamic result-button label can replace its specific title, so styling those rows without relabeling is usually clearer.

Welcome messages are editable per language: send `en Your welcome text` or `fa متن خوشامد`. Use `en reset` to restore that language's default. Free text is escaped rather than treated as arbitrary HTML.

## Daily, weekly, monthly and all-time analytics

Statistics come from this bot's SQLite records. With the default `Asia/Tehran` timezone:

- Daily begins at local midnight.
- Weekly begins on Saturday.
- Monthly begins on the first day of the current **Jalali** month by default; the operator can switch to Gregorian months.
- All-time starts at the beginning of the retained records.

Every period ends at report time and includes new users, distinct active users, search requests/successes/errors, link requests, denied membership checks, qualified referrals, campaign-attributed new users, tickets and successful operator replies. New users are first recorded private interactions, even if membership is not yet satisfied. Active users are distinct IDs that interacted in the interval.

Successful searches include valid empty results, cached responses and page navigation. A process interruption between request recording and outcome recording can leave request counts higher than success + error counts. A link request is **not** proof of clicking a URL or joining a discovered community.

The seven-day view shows each day separately. The 30-day CSV includes Gregorian and Jalali dates and all counters. An event exactly at midnight belongs only to the new day. Distinct users over a period are not the sum of daily distinct-user counts.

## Backups, upgrades and delivery behavior

Download a consistent SQLite backup from `/panel`. Stop the bot before replacing a database. Keep `.env` separately. Only after a complete stop should old database/WAL/SHM files be moved away from the replacement path. Runtime data and backups contain personal data.

To upgrade, stop the old bot, take a backup, replace the source/install requirements or rebuild the container, and retain the same `.env` and database volume. Missing columns/tables are added automatically. Existing users, channels, favorites and search history are preserved. Existing onboarded users retain their language; users without completed onboarding must choose one.

If Telegram reports that a configured log/support group became a supergroup, its destination ID and unsent outbox entries are updated. Historical message IDs are not assumed to match; tickets can be reopened from the operator panel.

Broadcast, ticket and log queues survive restart. A transmission interrupted between network delivery and local confirmation has an unknown outcome and is not automatically repeated. Exactly-once delivery is not guaranteed. Ordinary update processing can also repeat its last response after a sudden crash. A callback-notification timeout does not discard a still-valid, owner-bound button action; other API errors are not silently ignored.

## Waybien integration boundary

The adapter reads the public Next.js Flight data in `/en/search` HTML and the public filter-list endpoints on `dashboard.waybien.com`. Search `page` is an offset: `0`, `10`, `20`, … . This is not a guaranteed official search API contract. Website markup/access changes can require adapter updates. Invalid or blocked responses produce an error rather than fake zero-result data. The displayed total is the site's reported total, not a promise of access to its private database.

Waybien account login, account-level group submission/editing/deletion, voting, remote reports, advertising, payments and earnings are **not implemented inside this bot**. The relevant website links remain available. Local favorites, points and analytics are not synchronized with a Waybien account. Implementing private account operations requires an authorized API/authentication arrangement from the service owner. Access restrictions are not bypassed.

## Development and checks

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q bot tools
python tools/check_site.py python
python tools/check_site.py python --platform telegram --offset 10
```

The offline suite covers authorization, all eight user interfaces, explicit language onboarding, referrals, membership targets, support routing, privacy controls, calendar boundaries, statistics, migration, CSV, backups and queue behavior. It needs no bot token and sends no real Telegram messages. The site probe makes real read-only website requests and may fail if the website is unavailable or its public format changes.

See [Validation](VALIDATION.md) / [نتیجهٔ آزمون‌ها](VALIDATION_FA.md) and [Research](RESEARCH.md) / [بررسی نمونه‌ها](RESEARCH_FA.md). Tests do not substitute for a real deployment check with your token, channels and groups. The implementation is designed for one process; larger, multi-instance deployments need a shared service database/queue architecture.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Telegram `401` | Invalid or revoked bot token |
| Telegram `409` | Another poller or an existing webhook |
| Membership never passes | Channel ID, the bot's admin status, user membership and configured goal mode |
| Support messages do not arrive | Configured destination, bot permissions, outbox state and whether the original is copyable; inspect the ticket in `/panel` |
| Site `403`/`429` or parser error | Site availability, access limitations and adapter compatibility; use the site probe |
| Bot stops on Windows | Use the provided Docker setup; direct locking is Linux-specific |
| Docker reports unhealthy | Inspect heartbeat/logs; health status alone does not trigger a Docker restart |

Use a private support/log group with trusted operators. The introductory privacy message explains the data handling, and `/privacy` remains accessible. Clearing a search history list does not erase retained statistical events, previous group logs or backups.
