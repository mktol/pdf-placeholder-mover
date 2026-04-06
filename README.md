# PDF Placeholder Viewer (PySide6)

Simple Python UI app to:
- Open and read a PDF (no PDF editing)
- Draw placeholder rectangles on top of pages
- Move and resize placeholders after creation
- Scroll pages with mouse wheel/scrollbars
- Store each placeholder position and size (per page)
- Export placeholders to JSON for use in other apps

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

## Usage

- Click `Open PDF`
- Draw placeholder: drag on empty area
- Select placeholder: left click on it
- Move selected: drag inside placeholder
- Resize selected: drag bottom-right square handle
- Remove placeholder: right click on it
- Scroll page: mouse wheel (hold `Shift` for horizontal)
- Wheel at top/bottom switches to previous/next PDF page
- Navigate pages: `Prev` / `Next` (or keyboard arrows)
- Zoom: `Zoom -` / `Zoom +`
- Export coordinates: `Export JSON`

## JSON format

```json
{
  "schema": "pdf-placeholder-v1",
  "placeholders": [
    {
      "id": 1,
      "page_index": 0,
      "x": 120.5,
      "y": 300.0,
      "width": 220.0,
      "height": 45.0
    }
  ]
}
```

Coordinates are in PDF page space (not screen pixels), so they stay consistent across zoom levels.
