# Internationalization (i18n) Guide

This document explains how to manage translations in Daygle Mail Archiver.

## Overview

Daygle Mail Archiver uses the industry-standard **Babel** framework with **gettext** for internationalization. The system supports multiple languages and allows users to switch languages through their User Settings.

### Currently Supported Languages

- 🇬🇧 **English (en)** - Default language
- 🇪🇸 **Spanish (es)** - Español  
- 🇫🇷 **French (fr)** - Français
- 🇩🇪 **German (de)** - Deutsch
- 🇨🇳 **Chinese (zh)** - 中文 (Simplified)

## Architecture

### How It Works

1. **Templates**: All UI strings in Jinja2 templates use `{{ gettext('String to translate') }}`
2. **Extraction**: Babel extracts these strings into a `.pot` template file
3. **Translation**: `.po` files contain translations for each language
4. **Compilation**: `.po` files are compiled to binary `.mo` files for runtime
5. **Runtime**: The app loads the appropriate `.mo` file based on user's language preference

### File Structure

```
api/
├── babel.cfg                      # Babel configuration
├── locales/                       # Translation files
│   ├── messages.pot              # Template (extracted strings)
│   ├── en/LC_MESSAGES/
│   │   ├── messages.po           # English translations
│   │   └── messages.mo           # Compiled English
│   ├── es/LC_MESSAGES/
│   │   ├── messages.po           # Spanish translations
│   │   └── messages.mo           # Compiled Spanish
│   └── [other languages...]
├── scripts/
│   └── update_translations.sh    # Translation management script
├── src/
│   ├── utils/
│   │   └── i18n.py              # Translation loader
│   └── templates/                # HTML templates with gettext()
```

## For Developers

### Making Strings Translatable

#### In Jinja2 Templates

Wrap user-facing strings with `{{ gettext('...') }}`:

```html
<!-- Button text -->
<button>{{ gettext('Save Changes') }}</button>

<!-- Form labels -->
<label>{{ gettext('Username') }}</label>

<!-- Placeholder text -->
<input placeholder="{{ gettext('Enter your email') }}">

<!-- Page titles -->
{% block page_title %}{{ gettext('Dashboard') }}{% endblock %}
```

#### In Python Code (Future)

If you need to translate Python strings (e.g., flash messages):

```python
from utils.i18n import get_gettext

def my_route(request):
    gettext = get_gettext(request.session.get('language', 'en'))
    message = gettext('Operation completed successfully')
```

### Translation Workflow

#### Quick Reference

```bash
# Full update (most common)
./api/scripts/update_translations.sh full

# Individual steps
./api/scripts/update_translations.sh extract   # Extract strings
./api/scripts/update_translations.sh update    # Update .po files  
./api/scripts/update_translations.sh compile   # Compile to .mo
./api/scripts/update_translations.sh stats     # Show coverage

# Add a new language
./api/scripts/update_translations.sh add pt    # Add Portuguese
```

#### Detailed Steps

1. **After adding/changing translatable strings:**

   ```bash
   cd /opt/daygle-mail-archiver
   ./api/scripts/update_translations.sh extract
   ```

   This creates/updates `locales/messages.pot` with all extractable strings.

2. **Update language files:**

   ```bash
   ./api/scripts/update_translations.sh update
   ```

   This merges new strings into all `.po` files.

3. **Translate the strings:**

   Edit the `.po` files manually or use a translation tool:

   ```
   api/locales/es/LC_MESSAGES/messages.po
   api/locales/fr/LC_MESSAGES/messages.po
   etc.
   ```

   Each entry looks like:
   ```
   #: templates/login.html:53
   msgid "Sign in to access your archived emails"
   msgstr "Inicia sesión para acceder a tus correos archivados"
   ```

4. **Compile translations:**

   ```bash
   ./api/scripts/update_translations.sh compile
   ```

   This creates `.mo` files that the application uses at runtime.

5. **Test your changes:**

   ```bash
   docker compose restart api
   ```

   Then log in and change your language in User Settings.

## For Translators

### Translation Tools

#### Option 1: Manual Editing

Edit `.po` files directly in any text editor:

```
msgid "Welcome"
msgstr "Bienvenido"
```

#### Option 2: GUI Tools

- **Poedit** (Windows/Mac/Linux) - https://poedit.net/
- **Lokalize** (Linux KDE)
- **Gtranslator** (Linux GNOME)

#### Option 3: Online Platforms

You can import `.po` files into:
- Crowdin
- Transifex  
- Weblate
- Lokalise

### Translation Guidelines

1. **Maintain placeholders**: If you see `{variable}` or `%(name)s`, keep them unchanged
2. **Keep HTML tags**: Don't translate HTML tags like `<b>`, `<i>`, etc.
3. **Context matters**: Check the source location comment (`#: templates/file.html:line`)
4. **Test in UI**: Always verify translations look correct in the actual interface
5. **String length**: Translated text may be longer; ensure it fits in the UI

### Common Strings Reference

For consistency, here are key terms:

| English | Spanish | French | German | Chinese |
|---------|---------|--------|--------|---------|
| Dashboard | Panel de control | Tableau de bord | Dashboard | 仪表板 |
| Emails | Correos electrónicos | E-mails | E-Mails | 电子邮件 |
| Settings | Configuración | Paramètres | Einstellungen | 设置 |
| Login | Iniciar sesión | Connexion | Anmelden | 登录 |
| Logout | Cerrar sesión | Déconnexion | Abmelden | 登出 |

## Adding a New Language

### 1. Initialize Language Files

```bash
./api/scripts/update_translations.sh add <language_code>

# Examples:
./api/scripts/update_translations.sh add pt    # Portuguese
./api/scripts/update_translations.sh add ja    # Japanese
./api/scripts/update_translations.sh add ar    # Arabic
```

Common language codes: `pt` (Portuguese), `it` (Italian), `ja` (Japanese), `ko` (Korean), `ar` (Arabic), `ru` (Russian), `nl` (Dutch), `pl` (Polish)

### 2. Translate Strings

Edit the newly created `.po` file:
```
api/locales/<lang_code>/LC_MESSAGES/messages.po
```

### 3. Update Application Code

Add the language to these files:

**a) `api/templates/login.html`** - Add to language picker:
```html
<button data-lang="pt"><span class="flag">🇵🇹</span>&nbsp;{{ gettext('Português') }}</button>
```

**b) `api/templates/user-settings.html`** - Add to dropdown:
```html
<option value="pt" {% if language == 'pt' %}selected{% endif %}>Português</option>
```

**c) Update scripts** - Modify `api/scripts/update_translations.sh` to include the new language in the loop:
```bash
for lang in en es fr de zh pt; do
```

### 4. Compile and Test

```bash
./api/scripts/update_translations.sh compile
docker compose restart api
```

## Translation Status

To check translation coverage:

```bash
./api/scripts/update_translations.sh stats
```

Example output:
```
Translation Statistics:
----------------------------------------
en: 223/223 translated (100%)
es: 14/223 translated (6%)
fr: 14/223 translated (6%)
de: 14/223 translated (6%)
zh: 14/223 translated (6%)
----------------------------------------
```

## User Language Selection

### Login Page

Users can select their preferred language:
1. Language picker (flag icon) in top-right corner
2. Language dropdown in the login form

The selection is stored in the session immediately.

### After Login

Users can change language in **User Settings**:
1. Navigate to **Settings** → **User Settings**
2. Select language from the **Language** dropdown
3. Click **Save Changes**

The language preference is:
- Stored in the database per user
- Applied immediately upon saving
- Persists across sessions

## Troubleshooting

### Translations Not Appearing

1. **Check `.mo` files exist:**
   ```bash
   ls -la api/locales/*/LC_MESSAGES/*.mo
   ```

2. **Recompile translations:**
   ```bash
   ./api/scripts/update_translations.sh compile
   ```

3. **Restart the application:**
   ```bash
   docker compose restart api
   ```

4. **Check logs:**
   ```bash
   docker compose logs api | grep -i i18n
   ```

### New Strings Not Extracted

1. **Check babel.cfg includes your file type:**
   ```
   [python: **.py]
   [jinja2: **.html]
   ```

2. **Ensure you're using gettext() in templates:**
   ```html
   {{ gettext('Your string') }}
   ```

3. **Re-run extraction:**
   ```bash
   ./api/scripts/update_translations.sh extract
   ```

### Character Encoding Issues

- All `.po` files use **UTF-8** encoding
- If you see garbled characters, ensure your editor is set to UTF-8
- The line `charset=utf-8` in `.po` file headers must be present

## Best Practices

1. **Extract strings regularly** - Run extraction after UI changes
2. **Update before releases** - Ensure translations are current
3. **Test all languages** - At least basic smoke testing
4. **Keep strings concise** - Shorter strings are easier to translate
5. **Avoid string concatenation** - Use complete sentences with placeholders
6. **Document context** - Add comments for ambiguous strings
7. **Use translation memory** - Reuse translations for common phrases

## Resources

- **Babel Documentation**: https://babel.pocoo.org/
- **gettext Manual**: https://www.gnu.org/software/gettext/manual/
- **Poedit Editor**: https://poedit.net/
- **Language Codes (ISO 639-1)**: https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes

## Contributing Translations

We welcome translation contributions! To contribute:

1. Fork the repository
2. Create/update translations in your language
3. Test the translations in the application
4. Submit a pull request with:
   - Updated `.po` files
   - Compiled `.mo` files
   - Updates to language selectors (if new language)

Thank you for helping make Daygle Mail Archiver accessible to users worldwide! 🌍
