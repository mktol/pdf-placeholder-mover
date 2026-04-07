import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import fitz  # PyMuPDF
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolBar,
    QWidget,
)

CANVAS_PAD = 16
ZOOM_STEP = 1.15
MIN_ZOOM = 0.25
MAX_ZOOM = 4.0
MIN_PLACEHOLDER_SIZE = 6.0
HANDLE_HALF_SIZE = 8.0
DOCUSIGN_TAB_TYPES = [
    "approveTabs",
    "checkboxTabs",
    "commentThreadTabs",
    "commissionCountyTabs",
    "commissionExpirationTabs",
    "commissionNumberTabs",
    "commissionStateTabs",
    "companyTabs",
    "dateSignedTabs",
    "dateTabs",
    "declineTabs",
    "drawTabs",
    "emailAddressTabs",
    "emailTabs",
    "envelopeIdTabs",
    "firstNameTabs",
    "formulaTabs",
    "fullNameTabs",
    "initialHereTabs",
    "lastNameTabs",
    "listTabs",
    "notarizeTabs",
    "notarySealTabs",
    "noteTabs",
    "numberTabs",
    "numericalTabs",
    "phoneNumberTabs",
    "polyLineOverlayTabs",
    "radioGroupTabs",
    "signerAttachmentTabs",
    "signHereTabs",
    "smartSectionTabs",
    "ssnTabs",
    "textTabs",
    "titleTabs",
    "viewTabs",
    "zipTabs",
]
DOCUSIGN_TAB_TYPES_SET = set(DOCUSIGN_TAB_TYPES)
DOCUSIGN_TAB_TYPES_BY_LOWER = {name.lower(): name for name in DOCUSIGN_TAB_TYPES}


@dataclass
class Placeholder:
    id: int
    page_index: int
    x: float
    y: float
    width: float
    height: float
    tab_id: str
    document_id: str = "1"
    tab_label: str = ""
    tab_type: str = "fullNameTabs"


class PdfCanvas(QWidget):
    def __init__(self, owner: "MainWindow") -> None:
        super().__init__()
        self.owner = owner
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(400, 300)

        self._drag_mode: str | None = None  # draw | move | resize
        self._active_placeholder_id: int | None = None
        self._draw_start_page: QPointF | None = None
        self._draw_current_page: QPointF | None = None
        self._drag_anchor_page: QPointF | None = None
        self._drag_origin: tuple[float, float, float, float] | None = None

    def refresh_size(self) -> None:
        if self.owner.page_image is None:
            self.setFixedSize(800, 600)
            return
        w = self.owner.page_image.width() + CANVAS_PAD * 2
        h = self.owner.page_image.height() + CANVAS_PAD * 2
        self.setFixedSize(w, h)

    def _widget_to_page(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - CANVAS_PAD) / self.owner.zoom, (p.y() - CANVAS_PAD) / self.owner.zoom)

    def _inside_page(self, page_pt: QPointF) -> bool:
        return 0.0 <= page_pt.x() <= self.owner.page_width and 0.0 <= page_pt.y() <= self.owner.page_height

    def _page_limits(self) -> tuple[float, float]:
        return self.owner.page_width, self.owner.page_height

    def _placeholder_at(self, page_pt: QPointF) -> Placeholder | None:
        cur = self.owner.placeholders_for_current_page()
        for ph in reversed(cur):
            if ph.x <= page_pt.x() <= ph.x + ph.width and ph.y <= page_pt.y() <= ph.y + ph.height:
                return ph
        return None

    def _is_on_resize_handle(self, ph: Placeholder, page_pt: QPointF) -> bool:
        tol = HANDLE_HALF_SIZE / self.owner.zoom
        right = ph.x + ph.width
        bottom = ph.y + ph.height
        return right - tol <= page_pt.x() <= right + tol and bottom - tol <= page_pt.y() <= bottom + tol

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self.owner.doc is None:
            return

        page_pt = self._widget_to_page(event.position())

        if event.button() == Qt.MouseButton.RightButton:
            clicked = self._placeholder_at(page_pt)
            if clicked is not None:
                self.owner.placeholders = [ph for ph in self.owner.placeholders if ph.id != clicked.id]
                if self.owner.selected_placeholder_id == clicked.id:
                    self.owner.selected_placeholder_id = None
                self.owner.update_status()
                self.update()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        clicked = self._placeholder_at(page_pt)
        if clicked is not None:
            self.owner.selected_placeholder_id = clicked.id
            self._active_placeholder_id = clicked.id
            self._drag_anchor_page = page_pt
            self._drag_origin = (clicked.x, clicked.y, clicked.width, clicked.height)
            self._drag_mode = "resize" if self._is_on_resize_handle(clicked, page_pt) else "move"
            self.update()
            return

        if not self._inside_page(page_pt):
            self.owner.selected_placeholder_id = None
            self.update()
            return

        self.owner.selected_placeholder_id = None
        self._drag_mode = "draw"
        self._draw_start_page = page_pt
        self._draw_current_page = page_pt
        self.update()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.owner.doc is None or self._drag_mode is None:
            return

        page_pt = self._widget_to_page(event.position())
        page_w, page_h = self._page_limits()

        if self._drag_mode == "draw":
            px = max(0.0, min(page_w, page_pt.x()))
            py = max(0.0, min(page_h, page_pt.y()))
            self._draw_current_page = QPointF(px, py)
            self.update()
            return

        ph = self.owner.find_placeholder_by_id(self._active_placeholder_id)
        if ph is None or self._drag_anchor_page is None or self._drag_origin is None:
            return

        dx = page_pt.x() - self._drag_anchor_page.x()
        dy = page_pt.y() - self._drag_anchor_page.y()
        ox, oy, ow, oh = self._drag_origin

        if self._drag_mode == "move":
            ph.x = max(0.0, min(page_w - ow, ox + dx))
            ph.y = max(0.0, min(page_h - oh, oy + dy))
        elif self._drag_mode == "resize":
            ph.width = max(MIN_PLACEHOLDER_SIZE, min(page_w - ox, ow + dx))
            ph.height = max(MIN_PLACEHOLDER_SIZE, min(page_h - oy, oh + dy))

        self.update()

    def mouseReleaseEvent(self, _event) -> None:  # type: ignore[override]
        if self.owner.doc is None or self._drag_mode is None:
            return

        if self._drag_mode == "draw" and self._draw_start_page is not None and self._draw_current_page is not None:
            x0, y0 = self._draw_start_page.x(), self._draw_start_page.y()
            x1, y1 = self._draw_current_page.x(), self._draw_current_page.y()

            w = abs(x1 - x0)
            h = abs(y1 - y0)
            if w >= MIN_PLACEHOLDER_SIZE and h >= MIN_PLACEHOLDER_SIZE:
                ph = Placeholder(
                    id=self.owner.next_placeholder_id,
                    page_index=self.owner.current_page,
                    x=min(x0, x1),
                    y=min(y0, y1),
                    width=w,
                    height=h,
                    tab_id=str(uuid4()),
                    document_id=self.owner.current_document_id,
                    tab_type=self.owner.default_tab_type,
                )
                self.owner.next_placeholder_id += 1
                self.owner.placeholders.append(ph)
                self.owner.selected_placeholder_id = ph.id

        self._drag_mode = None
        self._active_placeholder_id = None
        self._draw_start_page = None
        self._draw_current_page = None
        self._drag_anchor_page = None
        self._drag_origin = None
        self.owner.update_status()
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#2f2f2f"))

        if self.owner.page_image is not None:
            painter.drawImage(CANVAS_PAD, CANVAS_PAD, self.owner.page_image)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for ph in self.owner.placeholders_for_current_page():
            selected = ph.id == self.owner.selected_placeholder_id
            outline = QColor("#4fc3f7") if selected else QColor("#ff5252")
            pen = QPen(outline, 3 if selected else 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            x0 = CANVAS_PAD + ph.x * self.owner.zoom
            y0 = CANVAS_PAD + ph.y * self.owner.zoom
            w = ph.width * self.owner.zoom
            h = ph.height * self.owner.zoom
            painter.drawRect(QRectF(x0, y0, w, h))

            painter.setPen(QColor("#ffd54f"))
            caption = ph.tab_label if ph.tab_label else ph.tab_id[:8]
            painter.drawText(QPointF(x0 + 4, y0 + 14), caption)

            if selected:
                painter.setPen(QPen(QColor("#4fc3f7"), 1))
                painter.setBrush(QColor("#4fc3f7"))
                rx = x0 + w
                ry = y0 + h
                painter.drawRect(QRectF(rx - HANDLE_HALF_SIZE, ry - HANDLE_HALF_SIZE, HANDLE_HALF_SIZE * 2, HANDLE_HALF_SIZE * 2))

        if self._drag_mode == "draw" and self._draw_start_page is not None and self._draw_current_page is not None:
            painter.setPen(QPen(QColor("#00bcd4"), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x0 = CANVAS_PAD + self._draw_start_page.x() * self.owner.zoom
            y0 = CANVAS_PAD + self._draw_start_page.y() * self.owner.zoom
            x1 = CANVAS_PAD + self._draw_current_page.x() * self.owner.zoom
            y1 = CANVAS_PAD + self._draw_current_page.y() * self.owner.zoom
            painter.drawRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))


class PageScrollArea(QScrollArea):
    def __init__(self, owner: "MainWindow") -> None:
        super().__init__()
        self.owner = owner
        self.setWidgetResizable(False)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self.owner.doc is None:
            return super().wheelEvent(event)

        delta_y = event.angleDelta().y()
        delta_x = event.angleDelta().x()
        if delta_y == 0 and delta_x == 0:
            return super().wheelEvent(event)

        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            step = bar.singleStep() * 3
            units = -int(delta_y / 120) if delta_y else -int(delta_x / 120)
            if units:
                bar.setValue(bar.value() + units * step)
                event.accept()
                return
            return super().wheelEvent(event)

        bar = self.verticalScrollBar()
        step = bar.singleStep() * 3
        units = -int(delta_y / 120)
        if units == 0:
            return super().wheelEvent(event)

        before = bar.value()
        bar.setValue(before + units * step)
        moved = bar.value() != before
        if moved:
            event.accept()
            return

        if units > 0 and self.owner.current_page < self.owner.total_pages() - 1:
            self.owner.next_page()
            self.verticalScrollBar().setValue(0)
            event.accept()
            return

        if units < 0 and self.owner.current_page > 0:
            self.owner.prev_page()
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            event.accept()
            return

        super().wheelEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Placeholder Viewer (PySide6)")
        self.resize(1200, 800)

        self.doc: fitz.Document | None = None
        self.current_page = 0
        self.zoom = 1.0
        self.page_image: QImage | None = None
        self.page_width = 0.0
        self.page_height = 0.0

        self.placeholders: list[Placeholder] = []
        self.next_placeholder_id = 1
        self.selected_placeholder_id: int | None = None
        self.default_tab_type = "signHereTabs"
        self.current_document_id = "1"

        self._build_ui()
        self.render_page()

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_btn = QPushButton("Open PDF")
        open_btn.clicked.connect(self.open_pdf)
        toolbar.addWidget(open_btn)

        prev_btn = QPushButton("Prev")
        prev_btn.clicked.connect(self.prev_page)
        toolbar.addWidget(prev_btn)

        next_btn = QPushButton("Next")
        next_btn.clicked.connect(self.next_page)
        toolbar.addWidget(next_btn)

        zoom_out_btn = QPushButton("Zoom -")
        zoom_out_btn.clicked.connect(lambda: self.change_zoom(1 / ZOOM_STEP))
        toolbar.addWidget(zoom_out_btn)

        zoom_in_btn = QPushButton("Zoom +")
        zoom_in_btn.clicked.connect(lambda: self.change_zoom(ZOOM_STEP))
        toolbar.addWidget(zoom_in_btn)

        import_tabs_btn = QPushButton("Import Tabs JSON")
        import_tabs_btn.clicked.connect(self.import_tabs_json)
        toolbar.addWidget(import_tabs_btn)

        export_tabs_btn = QPushButton("Export DocuSign JSON")
        export_tabs_btn.clicked.connect(self.export_tabs_json)
        toolbar.addWidget(export_tabs_btn)

        export_btn = QPushButton("Export Raw JSON")
        export_btn.clicked.connect(self.export_json)
        toolbar.addWidget(export_btn)

        clear_btn = QPushButton("Clear Page Placeholders")
        clear_btn.clicked.connect(self.clear_current_page)
        toolbar.addWidget(clear_btn)

        toolbar.addWidget(QLabel("Document ID:"))
        self.document_id_edit = QLineEdit(self.current_document_id)
        self.document_id_edit.setFixedWidth(64)
        self.document_id_edit.editingFinished.connect(self._on_document_id_changed)
        toolbar.addWidget(self.document_id_edit)

        toolbar.addWidget(QLabel("Tab type:"))
        self.tab_type_combo = QComboBox()
        self.tab_type_combo.addItems(DOCUSIGN_TAB_TYPES)
        self.tab_type_combo.setCurrentText(self.default_tab_type)
        self.tab_type_combo.currentTextChanged.connect(self._on_tab_type_changed)
        toolbar.addWidget(self.tab_type_combo)

        self.status_label = QLabel("Open a PDF to start.")
        self.status_label.setMinimumWidth(300)
        toolbar.addWidget(self.status_label)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = PageScrollArea(self)
        self.canvas = PdfCanvas(self)
        self.scroll_area.setWidget(self.canvas)
        layout.addWidget(self.scroll_area)

        self.setCentralWidget(container)

        help_text = QLabel(
            "Draw: drag empty area | Move: drag inside selected placeholder | "
            "Resize: drag bottom-right square | Wheel: scroll | Remove: right click"
        )
        self.statusBar().addWidget(help_text)

        self._bind_shortcuts()

    def _bind_shortcuts(self) -> None:
        prev_action = QAction(self)
        prev_action.setShortcut(QKeySequence(Qt.Key.Key_Left))
        prev_action.triggered.connect(self.prev_page)
        self.addAction(prev_action)

        next_action = QAction(self)
        next_action.setShortcut(QKeySequence(Qt.Key.Key_Right))
        next_action.triggered.connect(self.next_page)
        self.addAction(next_action)

        zoom_in_action = QAction(self)
        zoom_in_action.setShortcut(QKeySequence("Ctrl+="))
        zoom_in_action.triggered.connect(lambda: self.change_zoom(ZOOM_STEP))
        self.addAction(zoom_in_action)

        zoom_out_action = QAction(self)
        zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out_action.triggered.connect(lambda: self.change_zoom(1 / ZOOM_STEP))
        self.addAction(zoom_out_action)

    def total_pages(self) -> int:
        return len(self.doc) if self.doc is not None else 0

    def _on_tab_type_changed(self, value: str) -> None:
        if value:
            self.default_tab_type = value
            self.update_status()

    def _on_document_id_changed(self) -> None:
        value = self.document_id_edit.text().strip()
        if not value:
            value = "1"
            self.document_id_edit.setText(value)
        self.current_document_id = value
        self.selected_placeholder_id = None
        self.canvas.update()
        self.update_status()

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_tab_collection_name(value: str | None) -> str | None:
        if not value:
            return None
        raw = value.strip()
        if not raw:
            return None
        raw_lower = raw.lower()
        if raw_lower.endswith("tabs"):
            return DOCUSIGN_TAB_TYPES_BY_LOWER.get(raw_lower, raw)
        candidate = f"{raw}Tabs"
        return DOCUSIGN_TAB_TYPES_BY_LOWER.get(candidate.lower())

    def find_placeholder_by_id(self, placeholder_id: int | None) -> Placeholder | None:
        if placeholder_id is None:
            return None
        for ph in self.placeholders:
            if ph.id == placeholder_id:
                return ph
        return None

    def placeholders_for_current_page(self) -> list[Placeholder]:
        return [
            ph
            for ph in self.placeholders
            if ph.page_index == self.current_page and str(ph.document_id) == self.current_document_id
        ]

    def placeholders_for_current_document(self) -> list[Placeholder]:
        return [ph for ph in self.placeholders if str(ph.document_id) == self.current_document_id]

    def open_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF files (*.pdf);;All files (*.*)")
        if not path:
            return

        try:
            if self.doc is not None:
                self.doc.close()
            self.doc = fitz.open(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open PDF failed", str(exc))
            return

        self.current_page = 0
        self.zoom = 1.0
        self.placeholders.clear()
        self.next_placeholder_id = 1
        self.selected_placeholder_id = None
        self.render_page()

    def render_page(self) -> None:
        if self.doc is None:
            self.page_image = None
            self.page_width = 0
            self.page_height = 0
            self.canvas.refresh_size()
            self.canvas.update()
            self.update_status()
            return

        page = self.doc[self.current_page]
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), alpha=False)

        # Copy detaches from PyMuPDF buffer so image remains valid after function exit.
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self.page_image = image
        self.page_width = pix.width / self.zoom
        self.page_height = pix.height / self.zoom

        self.canvas.refresh_size()
        self.canvas.update()
        self.update_status()

    def update_status(self) -> None:
        if self.doc is None:
            self.status_label.setText("Open a PDF to start.")
            return

        self.status_label.setText(
            f"Page {self.current_page + 1}/{len(self.doc)} | Zoom {self.zoom:.2f} | "
            f"Document ID: {self.current_document_id} | "
            f"Tab type: {self.default_tab_type} | "
            f"Page placeholders: {len(self.placeholders_for_current_page())} | "
            f"Doc placeholders: {len(self.placeholders_for_current_document())} | Total: {len(self.placeholders)}"
        )

    def prev_page(self) -> None:
        if self.doc is None:
            return
        if self.current_page > 0:
            self.current_page -= 1
            self.selected_placeholder_id = None
            self.render_page()

    def next_page(self) -> None:
        if self.doc is None:
            return
        if self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.selected_placeholder_id = None
            self.render_page()

    def change_zoom(self, factor: float) -> None:
        if self.doc is None:
            return

        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-6:
            return

        self.zoom = new_zoom
        self.render_page()

    def _extract_tabs_from_payload(self, node, found: list[dict], tab_type: str | None = None) -> None:
        if isinstance(node, dict):
            x_pos = self._to_float(node.get("xPosition"), default=float("nan"))
            y_pos = self._to_float(node.get("yPosition"), default=float("nan"))
            page_num = self._to_int(node.get("pageNumber"), default=-1)

            if page_num >= 1 and x_pos == x_pos and y_pos == y_pos:
                tab = dict(node)
                typed = self._normalize_tab_collection_name(str(node.get("tabType") or ""))
                tab["_tab_type"] = typed or tab_type or self.default_tab_type
                found.append(tab)

            for key, value in node.items():
                normalized_key = self._normalize_tab_collection_name(key)
                child_tab_type = normalized_key if normalized_key else tab_type
                self._extract_tabs_from_payload(value, found, child_tab_type)
            return

        if isinstance(node, list):
            for item in node:
                self._extract_tabs_from_payload(item, found, tab_type)

    def import_tabs_json(self) -> None:
        if self.doc is None:
            QMessageBox.information(self, "Open PDF first", "Open a PDF before importing tabs JSON.")
            return

        path, _ = QFileDialog.getOpenFileName(self, "Import tabs JSON", "", "JSON files (*.json);;All files (*.*)")
        if not path:
            return

        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"Could not read JSON:\n{exc}")
            return

        tabs: list[dict] = []
        self._extract_tabs_from_payload(payload, tabs)
        if not tabs:
            QMessageBox.warning(self, "No tabs found", "No tab entries with x/y/pageNumber were found.")
            return

        new_placeholders: list[Placeholder] = []
        doc_pages = len(self.doc)
        next_id = 1
        for tab in tabs:
            page_num = self._to_int(tab.get("pageNumber"), 1)
            page_index = page_num - 1
            if page_index < 0 or page_index >= doc_pages:
                continue

            width = self._to_float(tab.get("width", 0), 0)
            height = self._to_float(tab.get("height", 0), 0)
            if width <= 0:
                width = 160.0
            if height <= 0:
                height = 24.0

            ph = Placeholder(
                id=next_id,
                page_index=page_index,
                x=self._to_float(tab.get("xPosition", 0), 0),
                y=self._to_float(tab.get("yPosition", 0), 0),
                width=width,
                height=height,
                tab_id=str(tab.get("tabId") or uuid4()),
                document_id=str(tab.get("documentId") or "1"),
                tab_label=str(tab.get("tabLabel") or ""),
                tab_type=str(tab.get("_tab_type") or self.default_tab_type),
            )
            next_id += 1
            new_placeholders.append(ph)

        if not new_placeholders:
            QMessageBox.warning(self, "No usable tabs", "No tabs matched this PDF page count.")
            return

        self.placeholders = new_placeholders
        self.next_placeholder_id = max(ph.id for ph in self.placeholders) + 1
        if self.placeholders:
            self.current_document_id = str(self.placeholders[0].document_id or "1")
            self.document_id_edit.setText(self.current_document_id)
        self.selected_placeholder_id = None
        self.current_page = min(self.current_page, len(self.doc) - 1)
        self.render_page()
        QMessageBox.information(
            self,
            "Import complete",
            f"Imported {len(new_placeholders)} tabs as placeholders.\nCurrent document filter: {self.current_document_id}",
        )

    def export_tabs_json(self) -> None:
        doc_placeholders = [ph for ph in self.placeholders if str(ph.document_id) == self.current_document_id]
        if not doc_placeholders:
            QMessageBox.information(self, "Nothing to export", "No placeholders to export yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save DocuSign-style tabs JSON",
            "docusign_tabs.json",
            "JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return

        grouped: dict[str, list[dict]] = {}
        for ph in doc_placeholders:
            tab_type = self._normalize_tab_collection_name(ph.tab_type) or self.default_tab_type
            tab = {
                "documentId": str(ph.document_id or "1"),
                "pageNumber": str(ph.page_index + 1),
                "xPosition": str(int(round(ph.x))),
                "yPosition": str(int(round(ph.y))),
                "tabId": ph.tab_id,
                "tabLabel": ph.tab_label,
                "width": str(int(round(ph.width))) if ph.width > 0 else "0",
                "height": str(int(round(ph.height))) if ph.height > 0 else "0",
            }
            grouped.setdefault(tab_type, []).append(tab)

        payload = {
            "recipients": {
                "signers": [
                    {
                        "tabs": grouped,
                    }
                ]
            }
        }

        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        QMessageBox.information(
            self,
            "Export complete",
            f"Saved {len(doc_placeholders)} tabs for documentId={self.current_document_id} to:\n{path}",
        )

    def export_json(self) -> None:
        if not self.placeholders:
            QMessageBox.information(self, "Nothing to export", "No placeholders to export yet.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save placeholders JSON",
            "placeholders.json",
            "JSON files (*.json);;All files (*.*)",
        )
        if not path:
            return

        payload = {
            "schema": "pdf-placeholder-v1",
            "placeholders": [asdict(ph) for ph in self.placeholders],
        }

        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return

        QMessageBox.information(self, "Export complete", f"Saved {len(self.placeholders)} placeholders to:\n{path}")

    def clear_current_page(self) -> None:
        if self.doc is None:
            return

        before = len(self.placeholders)
        self.placeholders = [
            ph
            for ph in self.placeholders
            if not (ph.page_index == self.current_page and str(ph.document_id) == self.current_document_id)
        ]
        if before != len(self.placeholders):
            self.selected_placeholder_id = None
            self.canvas.update()
            self.update_status()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.doc is not None:
            self.doc.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
