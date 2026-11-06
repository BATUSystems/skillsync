---
name: docx-to-markdown
description: 'Convert DOCX documents to clean Markdown with images. Use for converting Word documentation, WCAG reports, specifications to MD format with proper image paths.'
argument-hint: 'Path to DOCX file (e.g., docs/DEMO-176/document.docx)'
---

# DOCX to Markdown Converter

## When to Use

- Converting Word (.docx) documents to Markdown format
- Processing documentation with embedded images
- Cleaning up WCAG reports, specifications, requirement documents
- Preparing documentation for version control

## What It Does

Converts DOCX files to clean Markdown using pandoc with:
1. **Image extraction** - extracts all embedded images to `media/` folder
2. **Path correction** - fixes absolute paths to relative
3. **Cleanup** - removes pandoc size attributes `{width="..." height="..."}`

## Prerequisites

- `pandoc` installed (version 2.0+)
- DOCX file in the workspace

## Procedure

### 1. Check Pandoc Installation

```bash
which pandoc && pandoc --version
```

If not installed:
```bash
# Arch Linux
sudo pacman -S pandoc

# Ubuntu/Debian
sudo apt install pandoc

# macOS
brew install pandoc
```

### 2. Convert Document

Replace `<input-file.docx>` with your DOCX path:

```bash
# Extract directory and filename
INPUT="path/to/document.docx"
DIR=$(dirname "$INPUT")
BASENAME=$(basename "$INPUT" .docx)

# Convert with image extraction
pandoc "$INPUT" \
  -o "$DIR/$BASENAME.md" \
  --extract-media="$DIR/media" \
  --wrap=none
```

### 3. Clean Up Markdown

```bash
OUTPUT="$DIR/$BASENAME.md"

# Fix image paths (absolute → relative)
sed -i "s|$DIR/media/media/|media/media/|g" "$OUTPUT"

# Remove size attributes
sed -i 's/{width="[^"]*" height="[^"]*"}//g' "$OUTPUT"
```

### 4. Verify Results

```bash
# Check file size
ls -lh "$OUTPUT"

# Preview first lines
head -n 30 "$OUTPUT"

# Count images
ls -1 "$DIR/media/media/" | wc -l
```

## Example

Converting `docs/DEMO-176/WCAG raport.docx`:

```bash
pandoc "docs/DEMO-176/WCAG raport.docx" \
  -o "docs/DEMO-176/WCAG raport.md" \
  --extract-media=docs/DEMO-176/media \
  --wrap=none

sed -i 's|docs/DEMO-176/media/media/|media/media/|g' "docs/DEMO-176/WCAG raport.md"
sed -i 's/{width="[^"]*" height="[^"]*"}//g' "docs/DEMO-176/WCAG raport.md"
```

**Result**:
- Markdown file: `docs/DEMO-176/WCAG raport.md`
- Images: `docs/DEMO-176/media/media/image*.png`
- Clean syntax: `![](media/media/image1.png)` (no size attributes)

## Common Issues

### Images not visible in preview
- Check that paths are relative: `media/media/image1.png` not `docs/.../media/media/image1.png`
- Verify images exist: `ls -l docs/*/media/media/`

### Pandoc not found
```bash
# Install pandoc first
which pandoc || sudo pacman -S pandoc
```

### Size attributes still present
```bash
# Run cleanup again
sed -i 's/{width="[^"]*" height="[^"]*"}//g' "path/to/file.md"
```

## Notes

- Original DOCX file is not modified
- Images are extracted in PNG format
- Pandoc may create nested `media/media/` structure - this is normal
- Size attributes are stripped because GitHub/VS Code Markdown doesn't need them
- For batch conversion, wrap commands in a loop
