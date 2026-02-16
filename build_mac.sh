#!/bin/bash
# Build LeadGen desktop app for macOS (.app bundle)
# Run from the project root: ./build_mac.sh

set -e

echo "=== LeadGen macOS Build ==="
echo ""

# Activate virtual environment
source venv/bin/activate

# Clean previous builds
rm -rf build dist

# Build with PyInstaller
echo "Building with PyInstaller..."
pyinstaller leadgen.spec --noconfirm

echo ""
echo "=== Build Complete ==="
echo "  App:    dist/LeadGen.app"
echo "  Folder: dist/LeadGen/"
echo ""
echo "To run:  open dist/LeadGen.app"
echo "Output CSV files will be saved to: ~/LeadGen_Output/"
echo ""
echo "NOTE: Chrome must be installed on the user's machine for scrapers to work."
