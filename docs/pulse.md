# Pulse: what it does and why it matters

Pulse is a multi-tenant notification service. Your product sends it one **event** ("order shipped", "new comment"),
and Pulse decides **who** to tell, **how** (email, SMS, push, in-app), **when**, and **what the message says**. It
then delivers the message, tracks whether it arrived, and tries something else if it did not.

Without a service like this, every team in a company ends up writing its own half-reliable email sender, its own
retry loop and its own "don't text people at 3 a.m." rule. Pulse does it once, in one place.

This document lists every feature that exists today. For each: **what it is**, **how it helps the business**, and
**when and where it is useful**. Items still on the roadmap are listed at the end so nobody mistakes them for shipped.
Deeper how-to guides live beside this file ([templates](templates.md), [sequences](sequences.md),
[digests](digests.md), [inbox](inbox.md), [observability](observability.md), [Sentinel contract](sentinel-contract.md)).

---

## Contents

1. [Sending: events and channels](#1-sending-events-and-channels)
2. [Deciding what to send: workflows](#2-deciding-what-to-send-workflows)
3. [Writing the message: templates](#3-writing-the-message-templates)
4. [Timing: schedules, sequences, digests](#4-timing-schedules-sequences-digests)
5. [Respecting people: preferences and quiet hours](#5-respecting-people-preferences-and-quiet-hours)
6. [In-app inbox and live stream](#6-in-app-inbox-and-live-stream)
7. [Reliability: never lose or double-send](#7-reliability-never-lose-or-double-send)
8. [Providers: bring your own email/SMS/push vendor](#8-providers-bring-your-own-vendor)
9. [Delivery tracking](#9-delivery-tracking)
10. [Multi-tenancy, access and security](#10-multi-tenancy-access-and-security)
11. [Operating Pulse: console, metrics, dead letters](#11-operating-pulse)
12. [Not built yet](#12-not-built-yet)

---

## 1. Sending: events and channels

### Event ingestion (`POST /api/v1/events`)
Your backend sends one JSON event with a type, a payload and, optionally, who it concerns. Pulse answers `202
Accepted` immediately and does the rest in the background.
- **Business value:** the product team makes one cheap call and never waits on an email vendor. Notification logic
  leaves the application code.
- **Useful when:** anything happens that a person should hear about: signup, payment failed, order shipped, comment
  received, password reset.
- **Idempotency keys:** re-sending the same event (a producer retry after a timeout) does not notify people twice.
  Useful for any producer that retries, which is every producer.

### Four channels
| Channel | Typical use |
| :--- | :--- |
| **Email** | Receipts, reports, anything long or formal; the default for account events. |
| **SMS** | Time-sensitive or high-value: one-time codes, delivery alerts, payment failures. |
| **Push** (mobile) | Re-engaging people who are not in the app; "your ride is here". |
| **In-app** | Messages shown inside your product, live and stored. |

- **Business value:** reach each person where they actually pay attention, from one integration.
- **Device registration (`POST /api/v1/devices`):** your mobile app registers its push tokens, so Pulse can reach
  every device a person owns. Dead tokens are removed automatically so they are not retried forever.

### Recipient resolution
An event can carry its recipients inline, name a user, or leave Pulse to ask an external user/tenant service
(**Sentinel**). Pulse fans the event out to every recipient found.
- **Business value:** the producer only needs to know *what happened*, not everyone's contact details.
- **Useful when:** one event concerns many people (everyone on a project, every admin of an account).
- **Resilience:** Sentinel answers are cached, retried, circuit-broken and served stale during an outage. If
  Sentinel is down, the event is **not** dropped and is not treated as "nobody to notify": the producer gets a `503`
  and retries. See the [Sentinel contract](sentinel-contract.md).

---

## 2. Deciding what to send: workflows

A **workflow** is the rule that says "when an event of type X arrives, send these messages over these channels".
Workflows are created through the API or the console's structured editor (or as raw JSON).

| Capability | What it does | When it is useful |
| :--- | :--- | :--- |
| **Multi-channel workflow** | One event sends on several channels at once. | Important events where one missed email is costly: send email and push together. |
| **Fallback channels** | If the first channel fails or is unconfirmed past a timeout, escalate to the next (e.g. `in_app → push`). | "Tell them in-app; text them only if they did not see it." Cuts SMS cost and gets urgent messages through. |
| **Provider chains** | Per channel, an ordered list of vendors to try. | Vendor outage protection (see section 8). |
| **Per-workflow isolation** | A workflow only uses providers it names. | Different products or brands in one tenant never send through each other's accounts or sender identities. |
| **Draft/edit/delete with safety checks** | Writes are validated; things in use cannot be deleted. | Prevents breaking live notifications by editing a neighbour's config. |

**Business value:** product and growth teams change *who gets what, where* by editing configuration, without an
engineering release.

---

## 3. Writing the message: templates

### Inline messages
A subject and body written straight into the workflow, using the event payload (`{{ order.id }}`). Rendering is
sandboxed Jinja; the format can be plain text or Markdown (rendered to HTML for email).
- **Useful when:** one-off or simple messages.

### Saved templates with versions, rollback and preview
A named, reusable message per channel and language. See [templates.md](templates.md).
- **Draft vs. published:** edit freely, and nothing live changes until you **publish**. Each publish is an immutable
  numbered version (v1, v2, …).
- **Rollback:** restore an older version in one call. History only moves forward, so nothing is lost.
- **Preview:** render a template with sample data (or digest data) before it reaches anyone. Unsaved text can also
  be previewed from the editor.
- **Protected while in use:** a template used by a workflow cannot be deleted, renamed or deactivated.
- **Business value:** copy changes by marketing/support without code deploys; a typo that goes out can be reversed in
  seconds; legal or brand wording is reviewed before it goes live.
- **Useful when:** the same message is used in several workflows; teams that want a review step; any message that
  must not be wrong (billing, compliance).

### Localisation
One template per language; each recipient gets theirs by `locale` (`fr-CA` → `fr` → `en`).
- **Business value:** a French customer reads French without a separate workflow per country.
- **Useful when:** you sell internationally.

---

## 4. Timing: schedules, sequences, digests

### Scheduled sends
A notification can be set for a future time. Preferences are re-checked at the moment it is actually sent.
- **Useful when:** reminders, appointment notices, "send Monday 9 a.m.". Someone who opted out after scheduling is
  still respected.

### Multi-step sequences
A series of messages with delays and conditions: *in-app now → wait 10 minutes → email only if still unread → wait an
hour → SMS only if the order total is over 100.* See [sequences.md](sequences.md).
- Conditions test whether an earlier step was delivered, read, failed or skipped, and can check payload fields
  (`gte`, `in`, `contains`, …).
- A chain stops as soon as someone has read the message.
- **Business value:** escalate only as far as needed. Fewer annoying duplicates, lower SMS/email spend, and higher
  completion of things that matter.
- **Useful when:** abandoned-cart nudges, payment-failure dunning, onboarding drip steps, approvals that need a
  response, incident escalation.

### Digests (batching)
Collapse a burst of events into one message per recipient: "5 new comments" instead of five emails. Configure a time
window, a max item count and an optional grouping key (e.g. one digest per post). See [digests.md](digests.md).
- **Business value:** protects the inbox, and your sender reputation and notification opt-in rate, from noisy
  activity. People who are not drowned in alerts keep notifications turned on.
- **Useful when:** comments, mentions, likes, activity feeds, monitoring alerts that fire in bursts.

### Quiet hours
A recipient's quiet window (their time zone, overnight windows supported) holds messages until it ends.
- **Useful when:** non-urgent notifications to consumers; avoiding 3 a.m. buzzing, which drives uninstalls and
  complaints.

---

## 5. Respecting people: preferences and quiet hours

`PUT/GET/DELETE /api/v1/preferences` store, per recipient and channel, whether they are opted in and their quiet hours.
- Opt-outs are honoured at send time, including for scheduled and sequence steps.
- **Business value:** compliance with consent rules (marketing opt-out, SMS consent) and fewer spam complaints that
  damage deliverability. Users stay in control, so they keep notifications on.
- **Useful when:** your app has a "notification settings" screen; you need to prove opt-outs are respected.

---

## 6. In-app inbox and live stream

### Persistent inbox
In-app messages are **stored** per recipient, so people who were offline find them when they return. See
[inbox.md](inbox.md).
- API: list (filter by unread/read/archived, cursor paging), unread count (the bell badge), mark read/unread,
  archive/unarchive, mark all read. Old messages are pruned automatically (90 days; archived after 7).
- **Business value:** a notification centre inside your product without building one. In-app is free to send, so it
  lowers the cost of reaching users compared with SMS.
- **Useful when:** web apps and dashboards with a bell icon; messages people may want to find again.

### Live updates (SSE stream)
Connected clients receive messages instantly over a server-sent-events stream; on reconnect they replay what they
missed and can resume from the last event they saw.
- **Useful when:** collaborative or real-time products: chat mentions, job-finished banners, live order status.

### Read = delivered, with escalation
In-app delivery is confirmed when the person **reads** it. If they do not within a set time (120 s by default), Pulse
escalates to the fallback channel. "Push only if they did not see it" requires no extra configuration.

---

## 7. Reliability: never lose or double-send

| Feature | Plain-language meaning | Why a business cares |
| :--- | :--- | :--- |
| **Transactional outbox** | The notification and its "to send" record are saved together. A crash cannot lose one without the other. | No silently lost receipts or password resets. |
| **At-least-once with de-duplication** | Delivery is retried safely and de-duplicated downstream. | No duplicate charges notices or duplicate texts. |
| **Automatic retries with backoff** | Failed sends are retried with growing delays. | Brief vendor hiccups heal themselves. |
| **Provider failover** | If the first vendor times out or errors, the next in the chain is tried within the same send. | A single vendor's outage does not stop your notifications. |
| **Circuit breaker** | After repeated failures a provider is rested for a minute, then probed with one message. | Avoids hammering a dead vendor and delaying everything behind it. |
| **Reconciliation sweep** | Finds messages stuck unconfirmed and escalates them to the fallback channel or dead-letters them. Each channel is tried at most once per notification. | Nothing sits in limbo forever, and nobody is spammed repeatedly. |
| **Worker crash recovery** | A worker that dies mid-send leaves a claim that expires; the message is then recovered. | Deployments and crashes do not drop messages. |
| **Dead letters** | Messages that finally fail are kept, listed and re-driveable with one click or API call. | Operations can see, fix and retry failures instead of guessing. |
| **Per-tenant rate limiting and daily quotas** | A token-bucket limit and optional daily cap on events per tenant; excess gets a `429`. | One noisy customer or runaway loop cannot starve everyone else or run up a vendor bill. |
| **Tested for it** | Real-stack smoke and chaos tests (kill a worker mid-send), CI on Postgres/Redis. | The guarantees above are verified, not just claimed. |

---

## 8. Providers: bring your own vendor

| Channel | Supported providers |
| :--- | :--- |
| Email | SMTP, Amazon SES, Resend, SendGrid, console (development) |
| SMS | Twilio, console (development) |
| Push | Firebase Cloud Messaging, console (development) |
| In-app | Built-in (SSE) |

- **Provider catalog:** a picker with logos and the exact fields each vendor needs.
- **Connection test:** verify credentials live *before* saving.
- **Encrypted secrets:** API keys are encrypted at rest and write-only through the API (never shown again). Keys can
  be rotated.
- **Multiple providers per type:** e.g. a transactional and a marketing SES account, with labels and usage tracking.
- **Health view:** success rate and latency per provider, so you can see a vendor degrading.
- **Business value:** no vendor lock-in; negotiate or switch providers by changing configuration. Different brands
  or regions can use different sender accounts.
- **Useful when:** you want redundancy, need to move vendors for cost or deliverability, or operate several brands.

---

## 9. Delivery tracking

- **Notification history:** list and inspect every notification, each recipient, each attempt, provider used, the
  failover trail and (for sequences) every step's state and skip reason.
- **Signed delivery webhooks:** vendors call back with delivered/bounced/failed. Pulse verifies each vendor's
  signature (Twilio, Resend, SendGrid, SES via SNS) so callbacks cannot be forged.
- **Acknowledgements:** client apps can ack a message, e.g. when a push was shown.
- **Suppression list (`/api/v1/suppressions`):** a permanent bounce, a spam complaint (SES, Resend, SendGrid), an
  unsubscribe or a Twilio STOP reply (error 21610) adds the email address or phone number to the tenant's suppression
  list. Later sends to that address on that channel are skipped, whichever recipient or workflow they come from; other
  channels still go out. Admins can also add or remove entries by hand. Addresses are matched case-insensitively
  (email) and without spaces (phone). The `pulse.suppressed_sends` metric counts skips.
  - **Unsubscribe links:** with `PULSE_SECURITY__UNSUBSCRIBE_KEY` and `PULSE_API__PUBLIC_URL` set, every template can use
    `{{ unsubscribe_url }}`: a signed link (no login) to a confirm page whose button adds the address to the list. The
    same URL accepts an RFC 8058 one-click `POST`, so it can go in a `List-Unsubscribe` header too.
  - **Business value:** protects sender reputation (repeated sends to dead addresses get a domain blocked) and honours
    opt-outs the vendor has already recorded.
- **Business value:** answers "did the customer get it?" without opening a vendor dashboard. Bounces are known quickly
  and drive escalation: an email that bounced triggers the fallback, not a silent miss.
- **Useful when:** support investigates a complaint; audits; any message where proof of delivery matters.

---

## 10. Multi-tenancy, access and security

- **Tenant isolation:** every record belongs to a tenant; one tenant cannot see or change another's providers,
  workflows, templates, preferences, notifications, dead letters, keys or members. This is covered by a dedicated
  attack-style test suite.
- **API keys with scopes:** `ingest` (send events), `read` (view), `client` (end-user apps: stream, inbox, devices,
  preferences, ack) and `admin` (change configuration). Keys can be created, rotated and revoked.
- **Console logins and members:** people sign in to the console; admins manage members and keys.
- **Platform admin:** create and update tenants and users (for the operator running Pulse for many customers).
- **Usage view:** per-tenant usage numbers.
- **Business value:** safe to run as a shared service for many customers or internal teams, and a mobile app can be
  given a key that cannot change configuration.
- **Useful when:** you are a platform serving multiple customers, or an organisation with several product teams that
  must not see each other's data.

---

## 11. Operating Pulse

### Admin console (web UI)
A built-in dashboard with: recent notifications and events with status badges and detail rows, a dead-letter queue
with one-click retry, a structured workflow editor (channels, templates, fallbacks, drag-to-reorder provider chains,
digest controls), template editor with versions and preview, provider catalog and health, and key/member management.
- **Useful when:** support and operations staff need to see and fix things without engineers.

### Metrics and alerts (OpenTelemetry)
Pulse exports metrics for events ingested, deliveries by outcome, provider latency, API latency, rate-limit
rejections, outbox lag, queue depth, stuck attempts and dead letters. OpenObserve is the reference backend; any OTLP
backend works. Suggested alerts are in [observability.md](observability.md).
- **Business value:** you find out a vendor is failing or the pipeline is backing up before customers complain.
- **Useful when:** production on-call; SLA reporting.

### Separate processes that scale independently
API, email/SMS/push workers and a scheduler run as their own processes, so a slow SMS vendor cannot slow email.

---

## 12. Not built yet

Planned, but **not** available today (see [todo.md](todo.md) and [backlog.md](backlog.md)):

| ID | Item |
| :--- | :--- |
| H4 | Unregister a device token (e.g. on logout) |
| H5 | Rich push options (sounds, badges, priority, TTL) |
| H6 | Apple Push Notification service (APNs) driver |
| H8 | Short-lived tokens for the SSE stream |
| D1 | Distributed tracing across API, relay, worker and webhook |
| E5 | Self-service preference centre and unsubscribe portal |
| E6 | Chat channels (Slack, Teams, Discord) |

Also note: there is no chat/Slack channel and no iOS-native push yet, and only Twilio is supported for SMS.

---

## Quick guide: which feature for which problem?

| You want to… | Use |
| :--- | :--- |
| Send a receipt or alert from your backend | Events + a workflow |
| Not lose a critical message if a vendor is down | Provider chains + failover |
| Reach people who ignored the first message | Fallbacks or a sequence |
| Stop flooding users with activity alerts | Digests |
| Avoid waking customers at night | Quiet hours |
| Let marketing fix copy safely | Saved templates, preview, rollback |
| Serve customers in several languages | Per-locale templates |
| Add a notification bell to your app | Inbox + SSE stream |
| Prove a message was delivered | Delivery webhooks + notification history |
| Run it for multiple customers | Tenants, scoped API keys, rate limits |
| Know when something is wrong | Metrics, alerts, dead-letter queue |
