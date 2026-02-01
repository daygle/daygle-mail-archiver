# Translation Management Scripts

This directory contains scripts for managing translations in Daygle Mail Archiver.

## update_translations.sh

Translation management script for extracting, updating, and compiling translations.

### Usage

```bash
./update_translations.sh {extract|update|compile|stats|full|add <lang_code>}
```

### Commands

- **extract** - Extract translatable strings from code to messages.pot
- **update** - Update all .po files with strings from messages.pot
- **compile** - Compile .po files to .mo files (required before running app)
- **stats** - Show translation statistics for all languages
- **full** - Run extract, update, compile, and stats in sequence
- **add <lang>** - Add a new language (e.g., `add pt` for Portuguese)

### Examples

```bash
# Complete update cycle (most common)
./api/scripts/update_translations.sh full

# Extract new strings after code changes
./api/scripts/update_translations.sh extract

# Check translation coverage
./api/scripts/update_translations.sh stats

# Add Portuguese support
./api/scripts/update_translations.sh add pt
```

### Workflow

1. **After adding new UI strings**, run `extract` to update the template
2. **Edit .po files** in `api/locales/<lang>/LC_MESSAGES/messages.po`
3. **Run compile** to generate .mo files for the application
4. **Restart the API** container to load new translations

For detailed information, see [TRANSLATION_GUIDE.md](../../TRANSLATION_GUIDE.md) in the project root.
