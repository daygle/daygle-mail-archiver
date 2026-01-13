import os
from babel.support import Translations

def get_gettext(lang='en'):
    try:
        locales_dir = os.path.join(os.path.dirname(__file__), '..', 'locales')
        trans = Translations.load(locales_dir, [lang], 'messages')
        return trans.gettext
    except Exception:
        # Fallback to identity function
        return lambda x: x