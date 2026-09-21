## What changed

<!-- One or two sentences. Why, not just what. -->

## Odoo 19 checklist

CI enforces the first four automatically; the rest need a human.

- [ ] No `attrs` in views — `invisible` / `readonly` / `required` directly
- [ ] No `numbercall` on cron jobs
- [ ] `version` in `__manifest__.py` left alone — `19.0` raises it after the merge
      (label `bump:patch` / `bump:major` to change the step; see [docs/release.md](../docs/release.md))
- [ ] New data/asset files added to `__manifest__.py`
- [ ] Stored computed fields have `@api.depends`
- [ ] Field access controlled with `groups` where it holds credentials
- [ ] XML ids follow `pan_mail_pro.record_name`
- [ ] Custom fields use the `x_` prefix (Odoo.sh requirement)

## Testing

- [ ] `tests/` covers the change, or the change is not testable (say why)
- [ ] Verified against a database that has mailboxes configured, not only a fresh one

## Migration

- [ ] No schema change, **or** a script exists in `migrations/<version>/` **and**
      `__manifest__.py` says that exact version (the one case a PR sets it)
- [ ] Migration is idempotent and rehearsed against a restored backup
