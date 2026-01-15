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

    # Built-in fallback mappings (used when .mo loading fails or for
    # msgids that are missing from compiled translation files)
    def _builtin_fallbacks():
        return {
            'es': {
                "Display & Formatting Settings": "Configuración de visualización y formato",
                "Configure how content is displayed throughout the application": "Configura cómo se muestra el contenido en toda la aplicación",
                "Items Per Page": "Elementos por página",
                "Number of emails to display per page in lists": "Número de correos electrónicos para mostrar por página en listas",
                "Date Format": "Formato de fecha",
                "Language": "Idioma",
                "Interface language": "Idioma de la interfaz",
                "Sign in to access your archived emails": "Inicia sesión para acceder a tus correos archivados",
                "Username": "Nombre de usuario",
                "Enter your username": "Ingresa tu nombre de usuario",
                "Password": "Contraseña",
                "Enter your password": "Ingresa tu contraseña",
                "Sign In": "Iniciar sesión",
                "Forgot Password?": "¿Olvidaste tu contraseña?"
            },
            'fr': {
                "Display & Formatting Settings": "Paramètres d'affichage et de formatage",
                "Configure how content is displayed throughout the application": "Configurer l'affichage du contenu dans toute l'application",
                "Items Per Page": "Éléments par page",
                "Number of emails to display per page in lists": "Nombre d'e-mails à afficher par page dans les listes",
                "Date Format": "Format de date",
                "Language": "Langue",
                "Interface language": "Langue de l'interface",
                "Sign in to access your archived emails": "Connectez-vous pour accéder à vos e-mails archivés",
                "Username": "Nom d'utilisateur",
                "Enter your username": "Entrez votre nom d'utilisateur",
                "Password": "Mot de passe",
                "Enter your password": "Entrez votre mot de passe",
                "Sign In": "Se connecter",
                "Forgot Password?": "Mot de passe oublié ?"
            },
            'de': {
                "Display & Formatting Settings": "Anzeige- und Formatierungseinstellungen",
                "Configure how content is displayed throughout the application": "Konfigurieren Sie, wie Inhalte in der gesamten Anwendung angezeigt werden",
                "Items Per Page": "Elemente pro Seite",
                "Number of emails to display per page in lists": "Anzahl der E-Mails, die pro Seite in Listen angezeigt werden",
                "Date Format": "Datumsformat",
                "Language": "Sprache",
                "Interface language": "Schnittstellensprache",
                "Sign in to access your archived emails": "Melden Sie sich an, um auf Ihre archivierten E-Mails zuzugreifen",
                "Username": "Benutzername",
                "Enter your username": "Geben Sie Ihren Benutzernamen ein",
                "Password": "Passwort",
                "Enter your password": "Geben Sie Ihr Passwort ein",
                "Sign In": "Anmelden",
                "Forgot Password?": "Passwort vergessen?"
            },
            'zh': {
                "Display & Formatting Settings": "显示和格式设置",
                "Configure how content is displayed throughout the application": "配置整个应用程序中内容的显示方式",
                "Items Per Page": "每页项目数",
                "Number of emails to display per page in lists": "列表中每页显示的电子邮件数量",
                "Date Format": "日期格式",
                "Language": "语言",
                "Interface language": "界面语言",
                "Sign in to access your archived emails": "登录以访问您的存档电子邮件",
                "Username": "用户名",
                "Enter your username": "输入您的用户名",
                "Password": "密码",
                "Enter your password": "输入您的密码",
                "Sign In": "登录",
                "Forgot Password?": "忘记密码？"
            }
        }

    if chosen:
        try:
            trans = Translations.load(chosen, [lang], domain='messages')
            try:
                mo_path = Path(chosen) / lang / 'LC_MESSAGES' / 'messages.mo'
                print(f"i18n: chosen={chosen} lang={lang} mo_exists={mo_path.exists()} mo_path={mo_path}")
            except Exception:
                pass
            # Wrap the loaded translations so that if a particular msgid is
            # not present in the .mo (returns the original msgid), we fall
            # back to the small in-code mapping below for that msgid.
            def _gettext_with_fallback(msgid):
                res = trans.gettext(msgid)
                if res != msgid:
                    return res
                # fall back to in-code mappings defined below
                fb = _builtin_fallbacks().get(lang, {})
                return fb.get(msgid, msgid)

            return _gettext_with_fallback
        except Exception as e:
            logging.debug(f"i18n: failed to load translations from {chosen} for {lang}: {e}")
            # Fall through to builtin mapping fallbacks below
    else:
        logging.debug(f"i18n: no locales directory found among candidates: {candidates}")

    # If we reach here (either no chosen path or .mo loading failed),
    # fall back to builtin mapping
    fb_map = _builtin_fallbacks().get(lang, {})
    return lambda x: fb_map.get(x, x)