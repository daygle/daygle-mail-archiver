# Translation workflow

Translations are stored under `api/locales/<language>/LC_MESSAGES/`. The English catalog is the source for translatable UI strings.

From the repository root, run:

```bash
./api/scripts/update_translations.sh extract
./api/scripts/update_translations.sh update
# Edit the .po files under api/locales/<language>/LC_MESSAGES/
./api/scripts/update_translations.sh compile
./api/scripts/update_translations.sh stats
```

For the complete workflow, use:

```bash
./api/scripts/update_translations.sh full
```

To add a language, use its locale code, for example:

```bash
./api/scripts/update_translations.sh add pt
```

After compiling, restart the API container so the generated `.mo` catalogs are loaded. Review new or changed UI strings in every supported language where a translation is available, and keep the English fallback meaningful when a translation is missing.

## CI gate

CI runs `tests/test_i18n_catalog.py` (the `Verify translation catalogs are in sync and complete` step), which fails the build if:

- a `gettext` string used in a template or Python source is missing from `messages.pot` (or the POT contains msgids the code no longer uses), or
- any language catalog is below 100% translated (missing, empty, or fuzzy entries).

If that step fails, run `./api/scripts/update_translations.sh full`, translate the new strings, and commit the updated `messages.pot`, `.po`, and `.mo` files together with the code change.
