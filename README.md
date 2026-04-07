# PDF Placeholder Viewer (PySide6)

Simple Python UI app to:
- Open and read a PDF (no PDF editing)
- Draw placeholder rectangles on top of pages
- Move and resize placeholders after creation
- Scroll pages with mouse wheel/scrollbars
- Store each placeholder position and size (per page)
- Import placeholders from DocuSign-style tabs JSON
- Export placeholders to DocuSign-style tabs JSON
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
- Choose tab collection type in `Tab type` dropdown (for new placeholders)
- Import DocuSign tabs: `Import Tabs JSON`
- Draw placeholder: drag on empty area
- Select placeholder: left click on it
- Move selected: drag inside placeholder
- Resize selected: drag bottom-right square handle
- Remove placeholder: right click on it
- Scroll page: mouse wheel (hold `Shift` for horizontal)
- Wheel at top/bottom switches to previous/next PDF page
- Navigate pages: `Prev` / `Next` (or keyboard arrows)
- Zoom: `Zoom -` / `Zoom +`
- Export DocuSign tabs: `Export DocuSign JSON`
- Export app raw data: `Export Raw JSON`

## DocuSign-style JSON format

Export produces this structure:

```json
{
  "recipients": {
    "signers": [
      {
        "tabs": {
          "fullNameTabs": [
            {
              "documentId": "1",
              "pageNumber": "1",
              "xPosition": "70",
              "yPosition": "655",
              "tabId": "uuid",
              "tabLabel": "some label",
              "width": "160",
              "height": "24"
            }
          ]
        }
      }
    ]
  }
}
```

Import supports finding tab objects anywhere in JSON if they contain:
- `pageNumber`
- `xPosition`
- `yPosition`

Supported collections include `signHereTabs`, `initialHereTabs`, `dateSignedTabs`, `fullNameTabs`,
`firstNameTabs`, `lastNameTabs`, `emailTabs`, `emailAddressTabs`, `companyTabs`, `titleTabs`, `textTabs`,
`numberTabs`, `numericalTabs`, `dateTabs`, `phoneNumberTabs`, `ssnTabs`, `zipTabs`, `noteTabs`,
`checkboxTabs`, `radioGroupTabs`, `listTabs`, `approveTabs`, `declineTabs`, `formulaTabs`, `drawTabs`,
`viewTabs`, `signerAttachmentTabs`, `notarizeTabs`, `notarySealTabs`, `envelopeIdTabs` and others in the
DocuSign tabs model.

Import also accepts numeric strings (for example `"pageNumber": "2"`).

Width/height fallback defaults are used when imported values are `0`.
`documentId` is preserved on import and exported back per placeholder.

## Raw app JSON format

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
      "height": 45.0,
      "tab_id": "uuid",
      "document_id": "1",
      "tab_label": "some label",
      "tab_type": "signHereTabs"
    }
  ]
}
```

Coordinates are in PDF page space (not screen pixels), so they stay consistent across zoom levels.
