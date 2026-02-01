#!/bin/bash
# Translation management script for Daygle Mail Archiver
# This script helps extract, update, and compile translations

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
API_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
LOCALES_DIR="$API_DIR/locales"

cd "$API_DIR"

echo "========================================="
echo "Translation Management Script"
echo "========================================="
echo ""

# Function to extract translatable strings
extract_strings() {
    echo "1. Extracting translatable strings from templates and Python files..."
    pybabel extract -F babel.cfg -o "$LOCALES_DIR/messages.pot" .
    
    # Count the number of strings
    string_count=$(grep -c "^msgid" "$LOCALES_DIR/messages.pot" || echo "0")
    echo "   ✓ Extracted $string_count translatable strings to messages.pot"
    echo ""
}

# Function to update .po files
update_po_files() {
    echo "2. Updating .po files for all languages..."
    for lang in en es fr de zh; do
        echo "   - Updating $lang..."
        pybabel update -i "$LOCALES_DIR/messages.pot" -d "$LOCALES_DIR" -l "$lang"
    done
    echo "   ✓ All .po files updated"
    echo ""
}

# Function to compile .po files to .mo files
compile_translations() {
    echo "3. Compiling .po files to .mo files..."
    pybabel compile -d "$LOCALES_DIR"
    echo "   ✓ All translations compiled"
    echo ""
}

# Function to show translation statistics
show_stats() {
    echo "4. Translation Statistics:"
    echo "   ----------------------------------------"
    for lang in en es fr de zh; do
        po_file="$LOCALES_DIR/$lang/LC_MESSAGES/messages.po"
        if [ -f "$po_file" ]; then
            total=$(grep -c "^msgid \"" "$po_file" || echo "0")
            translated=$(grep -c "^msgstr \"[^\"]\+" "$po_file" || echo "0")
            # Subtract 1 for the header msgid
            total=$((total - 1))
            percentage=0
            if [ "$total" -gt 0 ]; then
                percentage=$((translated * 100 / total))
            fi
            echo "   $lang: $translated/$total translated ($percentage%)"
        fi
    done
    echo "   ----------------------------------------"
    echo ""
}

# Function to add a new language
add_language() {
    lang_code=$1
    echo "Adding new language: $lang_code"
    pybabel init -i "$LOCALES_DIR/messages.pot" -d "$LOCALES_DIR" -l "$lang_code"
    echo "✓ Language $lang_code initialized"
    echo "  Remember to update the code to include this language in:"
    echo "  - api/src/utils/i18n.py (supported languages)"
    echo "  - api/templates/login.html (language picker)"
    echo "  - api/templates/user-settings.html (language dropdown)"
    echo ""
}

# Main menu
case "${1:-}" in
    extract)
        extract_strings
        ;;
    update)
        update_po_files
        ;;
    compile)
        compile_translations
        ;;
    stats)
        show_stats
        ;;
    full)
        extract_strings
        update_po_files
        compile_translations
        show_stats
        ;;
    add)
        if [ -z "$2" ]; then
            echo "Error: Please specify language code (e.g., ./update_translations.sh add pt)"
            exit 1
        fi
        add_language "$2"
        ;;
    *)
        echo "Usage: $0 {extract|update|compile|stats|full|add <lang_code>}"
        echo ""
        echo "Commands:"
        echo "  extract  - Extract translatable strings from code to messages.pot"
        echo "  update   - Update all .po files with strings from messages.pot"
        echo "  compile  - Compile .po files to .mo files"
        echo "  stats    - Show translation statistics for all languages"
        echo "  full     - Run extract, update, compile, and stats"
        echo "  add      - Add a new language (e.g., add pt for Portuguese)"
        echo ""
        echo "Examples:"
        echo "  $0 full                    # Complete update cycle"
        echo "  $0 extract                 # Extract new strings"
        echo "  $0 stats                   # Show translation coverage"
        echo "  $0 add pt                  # Add Portuguese"
        exit 1
        ;;
esac

echo "Done!"
