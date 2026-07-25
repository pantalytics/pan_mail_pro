## What changed

<!-- One or two sentences. Why, not just what. -->

## Odoo 19 checklist

CI enforces the first four automatically; the rest need a human.

- [ ] No `attrs` in views — `invisible` / `readonly` / `required` directly
- [ ] No `numbercall` on cron jobs
- [ ] `version` bumped in `__manifest__.py` (`19.0.X.Y.Z`)
- [ ] New data/asset files added to `__manifest__.py`
- [ ] Stored computed fields have `@api.depends`
- [ ] Field access controlled with `groups` where it holds credentials
- [ ] XML ids follow `pan_outlook_pro.record_name`
- [ ] Custom fields use the `x_` prefix (Odoo.sh requirement)

## Testing

- [ ] `tests/` covers the change, or the change is not testable (say why)
- [ ] Verified against a database that has mailboxes configured, not only a fresh one

## Migration

- [ ] No schema change, **or** a script exists in `migrations/<version>/`
- [ ] Migration is idempotent and rehearsed against a restored backup
