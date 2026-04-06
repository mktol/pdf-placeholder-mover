import json
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

import fitz  # PyMuPDF
from PIL import Image, ImageTk

CANVAS_PAD = 16
ZOOM_STEP = 1.15
MIN_ZOOM = 0.25
MAX_ZOOM = 4.0
MIN_PLACEHOLDER_SIZE = 6.0
RESIZE_HANDLE_SIZE = 8


@dataclass
class Placeholder:
    id: int
    page_index: int
    x: float
    y: float
    width: float
    height: float


class PdfPlaceholderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PDF Placeholder Viewer")
        self.root.geometry("1200x800")

        self.doc: fitz.Document | None = None
        self.current_page = 0
        self.zoom = 1.0
        self.tk_image: ImageTk.PhotoImage | None = None
        self.page_size_display = (0, 0)

        self.placeholders: list[Placeholder] = []
        self._next_placeholder_id = 1

        self._selected_placeholder_id: int | None = None
        self._draw_start: tuple[float, float] | None = None
        self._active_draft_rect: int | None = None
        self._drag_mode: str | None = None  # draw | move | resize
        self._active_placeholder_id: int | None = None
        self._drag_anchor_page: tuple[float, float] | None = None
        self._drag_origin: tuple[float, float, float, float] | None = None

        self._build_ui()
        self._bind_events()

    def _build_ui(self) -> None:
        toolbar = tk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=8, pady=8)

        tk.Button(toolbar, text="Open PDF", command=self.open_pdf).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Prev", command=self.prev_page).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(toolbar, text="Next", command=self.next_page).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Zoom -", command=lambda: self.change_zoom(1 / ZOOM_STEP)).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(toolbar, text="Zoom +", command=lambda: self.change_zoom(ZOOM_STEP)).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Export JSON", command=self.export_json).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(toolbar, text="Clear Page Placeholders", command=self.clear_current_page).pack(side=tk.LEFT, padx=(8, 0))

        self.status_var = tk.StringVar(value="Open a PDF to start.")
        tk.Label(toolbar, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, padx=(16, 0), fill=tk.X, expand=True)

        self.canvas = tk.Canvas(self.root, bg="#2f2f2f", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        help_text = (
            "Draw: drag empty area | Move: drag inside selected placeholder | "
            "Resize: drag bottom-right square | Remove: right click"
        )
        tk.Label(self.root, text=help_text, anchor="w").pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 8))

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
        self.canvas.bind("<Button-3>", self._remove_on_right_click)

        self.root.bind("<Left>", lambda _e: self.prev_page())
        self.root.bind("<Right>", lambda _e: self.next_page())
        self.root.bind("<Control-minus>", lambda _e: self.change_zoom(1 / ZOOM_STEP))
        self.root.bind("<Control-equal>", lambda _e: self.change_zoom(ZOOM_STEP))

    def open_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="Open PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            if self.doc is not None:
                self.doc.close()
            self.doc = fitz.open(path)
        except Exception as exc:
            messagebox.showerror("Open PDF failed", str(exc))
            return

        self.current_page = 0
        self.zoom = 1.0
        self.placeholders.clear()
        self._next_placeholder_id = 1
        self._selected_placeholder_id = None
        self.render_page()

    def render_page(self) -> None:
        self.canvas.delete("all")

        if self.doc is None:
            self.status_var.set("Open a PDF to start.")
            return

        page = self.doc[self.current_page]
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        self.tk_image = ImageTk.PhotoImage(img)
        self.page_size_display = (pix.width, pix.height)

        self.canvas.create_image(CANVAS_PAD, CANVAS_PAD, anchor=tk.NW, image=self.tk_image, tags=("pdf",))
        self._draw_overlays()
        self._update_status()
        self.canvas.config(scrollregion=(0, 0, pix.width + 2 * CANVAS_PAD, pix.height + 2 * CANVAS_PAD))

    def _draw_overlays(self) -> None:
        self.canvas.delete("overlay")

        for ph in self._placeholders_for_current_page():
            selected = ph.id == self._selected_placeholder_id
            outline = "#4fc3f7" if selected else "#ff5252"
            width = 3 if selected else 2

            x0 = CANVAS_PAD + ph.x * self.zoom
            y0 = CANVAS_PAD + ph.y * self.zoom
            x1 = CANVAS_PAD + (ph.x + ph.width) * self.zoom
            y1 = CANVAS_PAD + (ph.y + ph.height) * self.zoom

            self.canvas.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                outline=outline,
                width=width,
                dash=(4, 3),
                fill="",
                tags=("overlay", "placeholder", f"ph_{ph.id}"),
            )

            self.canvas.create_text(
                x0 + 4,
                y0 + 4,
                text=f"#{ph.id}",
                fill="#ffd54f",
                anchor=tk.NW,
                font=("Segoe UI", 10, "bold"),
                tags=("overlay", "placeholder_label", f"ph_{ph.id}"),
            )

            if selected:
                hs = RESIZE_HANDLE_SIZE
                self.canvas.create_rectangle(
                    x1 - hs,
                    y1 - hs,
                    x1 + hs,
                    y1 + hs,
                    outline="#4fc3f7",
                    fill="#4fc3f7",
                    width=1,
                    tags=("overlay", "resize_handle", f"ph_{ph.id}"),
                )

    def _update_status(self) -> None:
        if self.doc is None:
            self.status_var.set("Open a PDF to start.")
            return

        total_pages = len(self.doc)
        self.status_var.set(
            f"Page {self.current_page + 1}/{total_pages} | Zoom {self.zoom:.2f} | "
            f"Page placeholders: {len(self._placeholders_for_current_page())} | Total: {len(self.placeholders)}"
        )

    def _canvas_to_page(self, x: float, y: float) -> tuple[float, float]:
        return (x - CANVAS_PAD) / self.zoom, (y - CANVAS_PAD) / self.zoom

    def _inside_page(self, page_x: float, page_y: float) -> bool:
        width, height = self.page_size_display
        return 0 <= page_x <= width / self.zoom and 0 <= page_y <= height / self.zoom

    def _page_limits(self) -> tuple[float, float]:
        return self.page_size_display[0] / self.zoom, self.page_size_display[1] / self.zoom

    def _find_placeholder_by_id(self, placeholder_id: int | None) -> Placeholder | None:
        if placeholder_id is None:
            return None
        for ph in self.placeholders:
            if ph.id == placeholder_id:
                return ph
        return None

    def _placeholder_at(self, page_x: float, page_y: float) -> Placeholder | None:
        current = self._placeholders_for_current_page()
        for ph in reversed(current):
            if ph.x <= page_x <= ph.x + ph.width and ph.y <= page_y <= ph.y + ph.height:
                return ph
        return None

    def _is_on_resize_handle(self, ph: Placeholder, page_x: float, page_y: float) -> bool:
        tol = RESIZE_HANDLE_SIZE / self.zoom
        right = ph.x + ph.width
        bottom = ph.y + ph.height
        return right - tol <= page_x <= right + tol and bottom - tol <= page_y <= bottom + tol

    def _on_left_press(self, event: tk.Event) -> None:
        if self.doc is None:
            return

        page_x, page_y = self._canvas_to_page(event.x, event.y)
        clicked = self._placeholder_at(page_x, page_y)

        if clicked is not None:
            self._selected_placeholder_id = clicked.id
            self._active_placeholder_id = clicked.id
            self._drag_anchor_page = (page_x, page_y)
            self._drag_origin = (clicked.x, clicked.y, clicked.width, clicked.height)
            self._drag_mode = "resize" if self._is_on_resize_handle(clicked, page_x, page_y) else "move"
            self._draw_overlays()
            return

        if not self._inside_page(page_x, page_y):
            return

        self._selected_placeholder_id = None
        self._drag_mode = "draw"
        self._draw_start = (event.x, event.y)
        self._active_draft_rect = self.canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#00bcd4",
            width=2,
            dash=(2, 2),
            fill="",
            tags=("overlay", "draft"),
        )
        self._draw_overlays()

    def _on_left_drag(self, event: tk.Event) -> None:
        if self.doc is None or self._drag_mode is None:
            return

        if self._drag_mode == "draw":
            if self._draw_start is None or self._active_draft_rect is None:
                return
            x0, y0 = self._draw_start
            self.canvas.coords(self._active_draft_rect, x0, y0, event.x, event.y)
            return

        ph = self._find_placeholder_by_id(self._active_placeholder_id)
        if ph is None or self._drag_anchor_page is None or self._drag_origin is None:
            return

        cur_x, cur_y = self._canvas_to_page(event.x, event.y)
        dx = cur_x - self._drag_anchor_page[0]
        dy = cur_y - self._drag_anchor_page[1]
        page_w, page_h = self._page_limits()
        ox, oy, ow, oh = self._drag_origin

        if self._drag_mode == "move":
            ph.x = max(0.0, min(page_w - ow, ox + dx))
            ph.y = max(0.0, min(page_h - oh, oy + dy))
        elif self._drag_mode == "resize":
            ph.width = max(MIN_PLACEHOLDER_SIZE, min(page_w - ox, ow + dx))
            ph.height = max(MIN_PLACEHOLDER_SIZE, min(page_h - oy, oh + dy))

        self._draw_overlays()

    def _on_left_release(self, event: tk.Event) -> None:
        if self.doc is None or self._drag_mode is None:
            return

        if self._drag_mode == "draw" and self._draw_start is not None and self._active_draft_rect is not None:
            x0, y0 = self._draw_start
            x1, y1 = event.x, event.y
            self.canvas.delete(self._active_draft_rect)
            self._active_draft_rect = None
            self._draw_start = None

            cx0, cx1 = sorted((x0, x1))
            cy0, cy1 = sorted((y0, y1))

            if abs(cx1 - cx0) >= 4 and abs(cy1 - cy0) >= 4:
                p0x, p0y = self._canvas_to_page(cx0, cy0)
                p1x, p1y = self._canvas_to_page(cx1, cy1)
                page_w, page_h = self._page_limits()

                p0x = max(0, min(page_w, p0x))
                p1x = max(0, min(page_w, p1x))
                p0y = max(0, min(page_h, p0y))
                p1y = max(0, min(page_h, p1y))

                if abs(p1x - p0x) >= MIN_PLACEHOLDER_SIZE and abs(p1y - p0y) >= MIN_PLACEHOLDER_SIZE:
                    placeholder = Placeholder(
                        id=self._next_placeholder_id,
                        page_index=self.current_page,
                        x=min(p0x, p1x),
                        y=min(p0y, p1y),
                        width=abs(p1x - p0x),
                        height=abs(p1y - p0y),
                    )
                    self._next_placeholder_id += 1
                    self.placeholders.append(placeholder)
                    self._selected_placeholder_id = placeholder.id

        self._drag_mode = None
        self._active_placeholder_id = None
        self._drag_anchor_page = None
        self._drag_origin = None
        self._draw_overlays()
        self._update_status()

    def _remove_on_right_click(self, event: tk.Event) -> None:
        items = self.canvas.find_overlapping(event.x - 2, event.y - 2, event.x + 2, event.y + 2)
        placeholder_id = None

        for item in reversed(items):
            tags = self.canvas.gettags(item)
            for tag in tags:
                if tag.startswith("ph_"):
                    placeholder_id = int(tag.split("_", 1)[1])
                    break
            if placeholder_id is not None:
                break

        if placeholder_id is None:
            return

        self.placeholders = [ph for ph in self.placeholders if ph.id != placeholder_id]
        if self._selected_placeholder_id == placeholder_id:
            self._selected_placeholder_id = None
        self._draw_overlays()
        self._update_status()

    def _placeholders_for_current_page(self) -> list[Placeholder]:
        return [ph for ph in self.placeholders if ph.page_index == self.current_page]

    def prev_page(self) -> None:
        if self.doc is None:
            return
        if self.current_page > 0:
            self.current_page -= 1
            self._selected_placeholder_id = None
            self.render_page()

    def next_page(self) -> None:
        if self.doc is None:
            return
        if self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self._selected_placeholder_id = None
            self.render_page()

    def change_zoom(self, factor: float) -> None:
        if self.doc is None:
            return

        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-6:
            return

        self.zoom = new_zoom
        self.render_page()

    def export_json(self) -> None:
        if not self.placeholders:
            messagebox.showinfo("Nothing to export", "No placeholders to export yet.")
            return

        path = filedialog.asksaveasfilename(
            title="Save placeholders JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="placeholders.json",
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
            messagebox.showerror("Export failed", str(exc))
            return

        messagebox.showinfo("Export complete", f"Saved {len(self.placeholders)} placeholders to:\n{path}")

    def clear_current_page(self) -> None:
        if self.doc is None:
            return

        before = len(self.placeholders)
        self.placeholders = [ph for ph in self.placeholders if ph.page_index != self.current_page]
        removed = before - len(self.placeholders)
        if removed:
            self._selected_placeholder_id = None
            self.render_page()


def main() -> None:
    root = tk.Tk()
    app = PdfPlaceholderApp(root)
    app.render_page()
    root.mainloop()


if __name__ == "__main__":
    main()
