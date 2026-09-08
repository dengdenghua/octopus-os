#!/bin/bash
# List all available design brands

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESIGNS_DIR="$(dirname "$SCRIPT_DIR")/designs"

echo "Available Design Systems:"
echo "========================"
echo ""

for brand in "$DESIGNS_DIR"/*/; do
    if [ -d "$brand" ]; then
        brand_name=$(basename "$brand")
        echo "  • $brand_name"
    fi
done

echo ""
echo "Usage: ./add-design.sh [brand-name]"
echo "Example: ./add-design.sh vercel"
