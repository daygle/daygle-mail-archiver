import os
from babel.support import Translations

def get_gettext(lang='en'):
    try:
        locales_dir = os.path.join(os.path.dirname(__file__), '..', 'locales')
        trans = Translations.load(locales_dir, [lang])
        return trans.gettext
    except Exception as e:
        # Fallback to hardcoded
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