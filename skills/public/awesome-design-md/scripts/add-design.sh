#!/bin/bash
# Add DESIGN.md to project
# Usage: ./add-design.sh [brand] [output-path]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESIGNS_DIR="$(dirname "$SCRIPT_DIR")/designs"

BRAND="${1:-vercel}"
OUTPUT_PATH="${2:-./DESIGN.md}"

# Check if brand exists
if [ ! -d "$DESIGNS_DIR/$BRAND" ]; then
    echo "Error: Brand '$BRAND' not found"
    echo "Available brands:"
    ls -1 "$DESIGNS_DIR"
    exit 1
fi

# Copy DESIGN.md
cp "$DESIGNS_DIR/$BRAND/DESIGN.md" "$OUTPUT_PATH"

echo "✓ Installed $BRAND design system to $OUTPUT_PATH"
echo ""
echo "Next steps:"
echo "1. Tell your AI assistant to follow the DESIGN.md guidelines"
echo "2. Reference specific sections when requesting UI changes"
echo "3. Customize colors and typography to match your brand"
