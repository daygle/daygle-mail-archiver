import os
import logging
from pathlib import Path
from babel.support import Translations


def get_gettext(lang='en'):
    """Return a gettext function for `lang`.

    Loads compiled `.mo` files from the project's `api/locales` directory.
    Falls back to a small hardcoded dictionary if loading fails.
    """
    # Try several likely locations for the `locales` directory so this works
    # both on the developer machine and inside the Docker container.
    candidates = []
    p = Path(__file__).resolve()
    # neighbors: /app/utils -> /app/locales
    candidates.append(p.parents[1] / 'locales')
    # repo-style: api/src/utils -> api/locales
    candidates.append(p.parents[2] / 'locales')
    # project root cwd/locales
    candidates.append(Path.cwd() / 'locales')
    # fallback absolute /app/locales (Dockerfile copies to /app/locales)
    candidates.append(Path('/app/locales'))

    chosen = None
    for c in candidates:
        try:
            if c.exists() and any(c.iterdir()):
                chosen = str(c)
                break
        except Exception:
            continue

    if chosen:
        try:
            trans = Translations.load(chosen, [lang], domain='messages')
            try:
                mo_path = Path(chosen) / lang / 'LC_MESSAGES' / 'messages.mo'
                logging.debug(f"i18n: loaded translations from {chosen} for {lang} (mo_path={mo_path})")
            except Exception:
                pass
            # Return the gettext function from loaded translations
            return trans.gettext
        except Exception as e:
            logging.warning(f"i18n: failed to load translations from {chosen} for {lang}: {e}")
    else:
        logging.warning(f"i18n: no locales directory found among candidates: {candidates}")

    # If we reach here (either no chosen path or .mo loading failed),
    # fall back to returning the msgid as-is (identity function)
    logging.info(f"i18n: using identity function (no translation) for language: {lang}")
    return lambda x: x