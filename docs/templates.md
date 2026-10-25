# Saved templates: drafts, versions, rollback and preview

A saved template is a named message (subject + body + format) for one channel and locale that workflows can **reference**
instead of repeating the text. Templates have a **draft** and **published versions**:

- The template's own `subject`, `body` and `format` are the **draft**, the working copy. Editing it changes nothing live.
- **Publish** freezes the draft as the next numbered, immutable version (v1, v2, …) and makes it live.
- **Roll back** publishes a copy of an older version as a new version (v3 = a copy of v1) and loads it into the draft.
  History only moves forward, so nothing is ever lost.
- Workflows send the **published** version, looked up when each message is built, so a publish or rollback takes effect
  for the next message with no workflow change.

## Using a template in a workflow

```json
{"channels": [
  {"channel": "email", "template": "order-shipped"},
  {"channel": "sms", "body": "Your order shipped", "format": "text"}
]}
```

`template` is the template's **name**; the channel comes from the entry. It works in `channels`, `fallback_templates`
and `steps`, and mixes freely with inline messages. An entry has either `template` or its own `body`/`subject`, not both.

- **Languages:** the recipient's `locale` picks the translation (`fr-CA` → `fr` → `en`). Create one template row per
  locale with the same name and channel. If nothing matches, English or the first one is used.
- **Checked on save:** a workflow can only reference a template that exists for that channel, is active, and has a
  published version.
- **Protected while in use:** a template used by a workflow cannot be deleted, renamed, moved to another channel or
  deactivated (409, naming the workflows). Editing the draft is always allowed.
- If a referenced template somehow becomes unusable at send time, `POST /events` returns 422 naming it.
  Digests and sequence steps that hit it are dropped or skipped with the reason logged.

## API

| Method | Path | Scope | |
| :--- | :--- | :--- | :--- |
| `POST` | `/templates` | admin | Creates a draft. `"publish": true` also publishes v1. |
| `PUT` | `/templates/{id}` | admin | Edits the draft. |
| `GET` | `/templates`, `/templates/{id}` | read | Includes `published_version` and `has_unpublished_changes`. |
| `POST` | `/templates/{id}/publish` | admin | `{"note": "…"}` optional. 409 if the draft equals the published version. |
| `POST` | `/templates/{id}/rollback` | admin | `{"version": 1, "note": "…"}`. |
| `GET` | `/templates/{id}/versions` | read | Newest first, each with `is_published`. |
| `GET` | `/templates/{id}/versions/{n}` | read | |
| `POST` | `/templates/{id}/preview` | read | Render with sample data (below). |
| `POST` | `/templates/preview` | admin | Render text that is not saved yet (an editor's current contents). |

### Preview

```json
POST /api/v1/templates/{id}/preview
{"data": {"item": "kettle"},
 "recipient": {"external_ref": "ada", "locale": "fr"},
 "version": "draft",
 "digest": [{"text": "a"}, {"text": "b"}]}
```

- `data` is the sample event payload; `recipient` defaults to a sample user.
- `version` is `draft` (default), `published`, or a version number.
- `digest` simulates a digest of those events so `digest.count` and `digest.items` render.
- It uses the same rendering as real messages (sandboxed Jinja, then Markdown → HTML). A template that does not
  render returns 422 with the error.

## Upgrading

Templates that existed before versioning become **version 1, published**, so nothing changes for anyone. Before this
feature nothing sent saved templates (workflows used inline text only), so existing deployments are unaffected.

## Comparing versions and seeing what was sent

- **Diff:** `GET /api/v1/templates/{id}/diff?from=1&to=2` returns line diffs of the subject and body (`to` defaults to the
  current draft; `from`/`to` accept a version number or `draft`). In the console, **History → Changes** shows what a
  version changed from the one before it.
- **Usage:** every message built from a saved template records the template and version it used
  (`template_id`, `template_version` on each delivery attempt, shown in `GET /notifications/{id}`).
  `GET /api/v1/templates/{id}/usage` counts messages per version, and the History table shows the same count, so
  you can see whether anything still goes out on an old version after a rollback. Messages sent before this feature, and
  inline (non-saved) templates, are not counted.
