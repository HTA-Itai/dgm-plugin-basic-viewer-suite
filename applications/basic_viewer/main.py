import csv
import argparse
import io
import json
import logging
from pathlib import Path
import re
import sys

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from engine import Store

ROOT = Path(__file__).resolve().parent
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
# Raw CSV columns keep numbers as text; signed numbers must stay numeric in spreadsheets.
PLAIN_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def spreadsheet_safe(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES) and not PLAIN_NUMBER.fullmatch(value):
        return "'" + value
    return value


def csv_text(records):
    # Column names come from source CSV headers, so they are neutralized like values.
    keys = list(dict.fromkeys(k for row in records for k in row))
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([spreadsheet_safe(key) for key in keys])
    for record in records:
        writer.writerow([spreadsheet_safe(record.get(key, "")) for key in keys])
    return stream.getvalue()


class Result(QObject):
    ready = Signal(str, str)


class Work(QRunnable):
    def __init__(self, identity, action, result):
        super().__init__()
        self.identity, self.action, self.result = identity, action, result

    def run(self):
        try:
            response = {"ok": True, "data": self.action()}
        except Exception as error:
            logging.exception("Viewer operation failed")
            response = {"ok": False, "error": str(error)}
        self.result.ready.emit(self.identity, json.dumps(response, ensure_ascii=False, allow_nan=False))


class Bridge(QObject):
    reply = Signal(str, str)

    def __init__(self, store, view):
        super().__init__(view)
        self.store, self.view = store, view
        self.results = Result(self)
        self.results.ready.connect(self.reply.emit)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

    @Slot(str, str, str)
    def request(self, identity, command, argument):
        store = self.store
        def action():
            params = json.loads(argument)
            if command == "index":
                return store.index() if store is not None else None
            if store is None:
                raise ValueError("Open a Base dataset first")
            if command == "image":
                return store.image(int(params["id"]))
            if command == "comparison_image":
                return store.comparison_image(int(params["id"]))
            if command == "statistics":
                return store.statistics(params)
            if command == "ppe_values":
                return store.ppe_values(params)
            if command == "contour_overlay":
                return store.contour_overlay(params)
            if command == "area_difference":
                return store.area_difference(params)
            raise ValueError(f"Unknown command: {command}")
        self.pool.start(Work(identity, action, self.results))

    def _dataset_paths(self):
        path = getattr(self.store, "path", None)
        return (getattr(path, "base", None), getattr(path, "comparison", None))

    def _replace_store(self, base, comparison, title):
        """Validate the new combination first; on failure the open session stays unchanged."""
        try:
            self.store = Store(Path(base), Path(comparison) if comparison else None) if base else None
            self.view.reload()
        except Exception as error:
            logging.exception("Dataset open failed")
            QMessageBox.critical(self.view, title, str(error))

    def _choose(self, caption, folder):
        filters = "Viewer dataset (*.sqlite *.db);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(self.view, caption, str(folder or ""), filters)
        return path

    @Slot()
    def openBase(self):
        base, comparison = self._dataset_paths()
        chosen = self._choose("Open Base dataset", base.parent if base else "")
        if chosen:
            self._replace_store(chosen, comparison, "Cannot open Base dataset")

    @Slot()
    def openComparison(self):
        base, comparison = self._dataset_paths()
        if base is None:
            QMessageBox.information(self.view, "Open Comp dataset", "Open a Base dataset first.")
            return
        chosen = self._choose("Open Comp dataset", (comparison or base).parent)
        if chosen:
            self._replace_store(base, chosen, "Cannot open Comp dataset")

    @Slot()
    def closeComparison(self):
        base, _ = self._dataset_paths()
        if base is not None:
            self._replace_store(base, None, "Cannot close Comp dataset")

    @Slot(str, str)
    def exportCsv(self, text, suggested):
        try:
            records = json.loads(text)
            if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
                raise ValueError("Invalid CSV rows")
            path, _ = QFileDialog.getSaveFileName(self.view, "Export CSV", Path(suggested).name, "CSV (*.csv)")
            if not path:
                return
            Path(path).write_text(csv_text(records), encoding="utf-8-sig", newline="")
        except Exception as error:
            logging.exception("CSV export failed")
            QMessageBox.critical(self.view, "CSV export failed", str(error))


_profile = None


def viewer_profile():
    """Named profile: the default one is off-the-record and would forget the saved panel layout."""
    global _profile
    if _profile is None:
        _profile = QWebEngineProfile("ComparisonViewer", QApplication.instance())
        _profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    return _profile


class ViewerView(QWebEngineView):
    """Main window. JavaScript window.open() creates the Expand image window, drawn by this page."""

    def __init__(self):
        super().__init__()
        self.setPage(QWebEnginePage(viewer_profile(), self))
        self.popups = []

    def createWindow(self, kind):
        popup = QWebEngineView()
        popup.setPage(QWebEnginePage(self.page().profile(), popup))
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup.setWindowTitle("Comparison Viewer - Image")
        popup.page().titleChanged.connect(popup.setWindowTitle)
        popup.page().windowCloseRequested.connect(popup.close)
        popup.destroyed.connect(lambda *_: self.popups.remove(popup) if popup in self.popups else None)
        self.popups.append(popup)
        popup.resize(1600, 1000)
        popup.showMaximized()
        return popup

    def closeEvent(self, event):
        for popup in list(self.popups):
            popup.close()
        super().closeEvent(event)


def create_view(database=None, comparison=None):
    view = ViewerView()
    view.setWindowTitle("Comparison Viewer - Spatial Analysis")
    view.resize(1800, 1100)
    store = Store(database, comparison) if database is not None else None
    channel = QWebChannel(view.page())
    bridge = Bridge(store, view)
    channel.registerObject("backend", bridge)
    view.page().setWebChannel(channel)
    view.bridge = bridge
    view.channel = channel
    view.loadFinished.connect(lambda ok: None if ok else QMessageBox.critical(
        view, "Startup failed", "Cannot load the interface. Extract the entire ZIP before starting."))
    view.load(QUrl.fromLocalFile(str(ROOT / "web.html")))
    return view


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Open a Base Builder SQLite dataset, optionally with a Comparison dataset")
    parser.add_argument("--base", type=Path)
    parser.add_argument("--comparison", type=Path)
    args = parser.parse_args()
    if args.comparison and not args.base:
        parser.error("--comparison needs --base")
    app = QApplication([sys.argv[0]])
    app.setApplicationName("ComparisonViewer")
    try:
        window = create_view(args.base, args.comparison)
    except Exception as error:
        logging.exception("Startup failed")
        QMessageBox.critical(None, "Startup failed", str(error))
        sys.exit(1)
    window.showMaximized()
    code = app.exec()
    window.bridge.pool.waitForDone()
    # Pages must be released before their profile.
    del window
    sys.exit(code)
