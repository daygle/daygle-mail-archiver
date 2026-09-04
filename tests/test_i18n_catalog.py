"""CI guardrails that keep the translation catalogs from drifting.

Two invariants are enforced:

1. ``api/locales/messages.pot`` must be exactly regenerable from the current
   source: every ``gettext`` call in a template or Python source under
   ``api/`` must have a matching msgid in the POT, and the POT must not
   contain msgids the code no longer uses. This is the same extraction the
   documented workflow performs (see ``docs/translations.md`` and
   ``api/scripts/update_translations.sh``).

2. Every language catalog must translate 100% of the POT's msgids with no
   fuzzy placeholders, so no string silently falls back to English.

When either check fails, regenerate the catalogs and fill in the new
strings:

    ./api/scripts/update_translations.sh full
"""

from __future__ import annotations

from pathlib import Path

from babel.messages.frontend import CommandLineInterface
from babel.messages.pofile import read_po

API_DIR = Path(__file__).resolve().parent.parent / "api"
POT_PATH = API_DIR / "locales" / "messages.pot"

# Languages with a catalog. If you add a language, also update
# api/src/utils/i18n.py and the language pickers (login.html,
# user-settings.html) and add the locale to api/scripts/update_translations.sh.
LANGS = ("de", "en", "es", "fr", "zh")


def _msgid_keys(catalog) -> set[tuple[str, str]]:
    """Return {(msgctxt, msgid)} for every real message (skipping the header)."""
    return {(msg.context or "", msg.id) for msg in catalog if msg.id}


def _extract_pot(tmp_path: Path):
    """Regenerate messages.pot from source, exactly like update_translations.sh."""
    out = tmp_path / "extracted.pot"
    CommandLineInterface().run(
        ["pybabel", "extract", "-F", str(API_DIR / "babel.cfg"), "-o", str(out), "."]
    )
    return read_po(out.open("rb"))


def test_pot_in_sync_with_source(tmp_path, monkeypatch):
    """Every gettext string used in code is in messages.pot, and none are obsolete."""
    monkeypatch.chdir(API_DIR)
    extracted = _extract_pot(tmp_path)
    committed = read_po(POT_PATH.open("rb"))

    used_ids = _msgid_keys(extracted)
    committed_ids = _msgid_keys(committed)

    missing_from_pot = sorted(used_ids - committed_ids)
    obsolete_in_pot = sorted(committed_ids - used_ids)

    problems = []
    if missing_from_pot:
        problems.append(
            "gettext strings used in code are missing from messages.pot:\n"
            + "".join(f"    - {msgid!r}\n" for _, msgid in missing_from_pot)
            + "  Regenerate the POT, then translate the new strings in every "
            "catalog:\n"
            "    ./api/scripts/update_translations.sh extract"
        )
    if obsolete_in_pot:
        problems.append(
            "messages.pot contains msgids the code no longer uses:\n"
            + "".join(f"    - {msgid!r}\n" for _, msgid in obsolete_in_pot)
            + "  Regenerate the POT to drop them:\n"
            "    ./api/scripts/update_translations.sh extract"
        )

    assert not problems, "\n\n".join(problems)


def test_catalogs_fully_translated():
    """Every POT msgid is translated (non-empty, non-fuzzy) in every language."""
    pot_ids = _msgid_keys(read_po(POT_PATH.open("rb")))

    failures: dict[str, list[str]] = {}
    for lang in LANGS:
        po_path = API_DIR / "locales" / lang / "LC_MESSAGES" / "messages.po"
        assert po_path.exists(), f"Missing catalog for language '{lang}': {po_path}"

        catalog = read_po(po_path.open("rb"))
        by_key = {(msg.context or "", msg.id): msg for msg in catalog if msg.id}

        lang_failures: list[str] = []
        for key in sorted(pot_ids, key=lambda k: k[1]):
            msg = by_key.get(key)
            if msg is None:
                lang_failures.append(f"missing msgid {key[1]!r} -- add a translation")
            elif not msg.string:
                lang_failures.append(f"untranslated msgid {key[1]!r}")
            elif msg.fuzzy:
                lang_failures.append(f"fuzzy msgid {key[1]!r} -- needs a real translation")
        if lang_failures:
            failures[lang] = lang_failures

    assert not failures, (
        "Translation catalogs must be 100% translated (no missing, empty, or "
        "fuzzy entries). Run ./api/scripts/update_translations.sh full and fill "
        "in the new strings.\n\n"
        + "\n".join(
            f"{lang}:\n" + "\n".join(f"  - {f}" for f in errs)
            for lang, errs in failures.items()
        )
    )