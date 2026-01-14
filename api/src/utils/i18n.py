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
            return trans.gettext
        except Exception as e:
            logging.debug(f"i18n: failed to load translations from {chosen} for {lang}: {e}")
    else:
        logging.debug(f"i18n: no locales directory found among candidates: {candidates}")
        # Fallback to small in-code dictionaries while .mo loading is fixed
        if lang == 'es':
            translations = {
                "Display & Formatting Settings": "Configuración de visualización y formato",
                "Configure how content is displayed throughout the application": "Configura cómo se muestra el contenido en toda la aplicación",
                "Items Per Page": "Elementos por página",
                "Number of emails to display per page in lists": "Número de correos electrónicos para mostrar por página en listas",
                "Date Format": "Formato de fecha",
                "Language": "Idioma",
                "Interface language": "Idioma de la interfaz"
            }
            return lambda x: translations.get(x, x)
        elif lang == 'fr':
            translations = {
                "Display & Formatting Settings": "Paramètres d'affichage et de formatage",
                "Configure how content is displayed throughout the application": "Configurer l'affichage du contenu dans toute l'application",
                "Items Per Page": "Éléments par page",
                "Number of emails to display per page in lists": "Nombre d'e-mails à afficher par page dans les listes",
                "Date Format": "Format de date",
                "Language": "Langue",
                "Interface language": "Langue de l'interface"
            }
            return lambda x: translations.get(x, x)
        elif lang == 'de':
            translations = {
                "Display & Formatting Settings": "Anzeige- und Formatierungseinstellungen",
                "Configure how content is displayed throughout the application": "Konfigurieren Sie, wie Inhalte in der gesamten Anwendung angezeigt werden",
                "Items Per Page": "Elemente pro Seite",
                "Number of emails to display per page in lists": "Anzahl der E-Mails, die pro Seite in Listen angezeigt werden",
                "Date Format": "Datumsformat",
                "Language": "Sprache",
                "Interface language": "Schnittstellensprache"
            }
            return lambda x: translations.get(x, x)
        elif lang == 'zh':
            translations = {
                "Display & Formatting Settings": "显示和格式设置",
                "Configure how content is displayed throughout the application": "配置整个应用程序中内容的显示方式",
                "Items Per Page": "每页项目数",
                "Number of emails to display per page in lists": "列表中每页显示的电子邮件数量",
                "Date Format": "日期格式",
                "Language": "语言",
                "Interface language": "界面语言"
            }
            return lambda x: translations.get(x, x)
        else:
            return lambda x: x