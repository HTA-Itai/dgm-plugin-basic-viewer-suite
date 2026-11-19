from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from engine import Store


ROOT = Path(__file__).resolve().parent


class Result(QObject):
    ready = Signal(str, str)


class Work(QRunnable):
    def __init__(self, identity, action, result):
        super().__init__()
        self.identity = identity
        self.action = action
        self.result = result

    def run(self):
        try:
            response = {"ok": True, "data": self.action()}
        except Exception as error:
            logging.exception("BasicViewer operation failed")
            response = {"ok": False, "error": str(error)}
        self.result.ready.emit(self.identity, json.dumps(response, ensure_ascii=False, allow_nan=False))


class Bridge(QObject):
    reply = Signal(str, str)
    datasetChanged = Signal(str)
    ruleApplied = Signal(str)
    ruleTextReady = Signal(str)

    def __init__(self, store, view):
        super().__init__(view)
        self.store = store
        self.view = view
        self.results = Result(self)
        self.results.ready.connect(self.reply.emit)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

    @Slot(str, str, str)
    def request(self, identity, command, argument):
        def action():
            params = json.loads(argument or "{}")
            if command == "index":
                return self.store.index() if self.store is not None else None
            if self.store is None:
                raise ValueError("Open a SQLite dataset first")
            if command == "image":
                return self.store.image(int(params["id"]))
            if command == "statistics":
                return self.store.statistics(params["metric"])
            if command == "apply_rule":
                return self.store.apply_rule(params["rule"])
            raise ValueError(f"Unknown command: {command}")
        self.pool.start(Work(identity, action, self.results))

    def _replace_store(self, path):
        try:
            next_store = Store(path)
            self.store = next_store
            self.view.reload()
            self.datasetChanged.emit(str(path))
        except Exception as error:
            logging.exception("Dataset open failed")
            QMessageBox.critical(self.view, "Cannot open dataset", str(error))

    @Slot()
    def openDataset(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self.view, "Open BasicViewer SQLite dataset", "", "SQLite (*.sqlite *.db);;All files (*)"
        )
        if chosen:
            self._replace_store(Path(chosen))

    @Slot()
    def applyGroupingRule(self):
        if self.store is None:
            QMessageBox.information(self.view, "Apply grouping rule", "Open a SQLite dataset first.")
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self.view, "Apply grouping rule", "", "Grouping rule (*.yml *.yaml *.json);;All files (*)"
        )
        if not chosen:
            return
        try:
            result = self.store.apply_rule_file(Path(chosen))
            self.ruleApplied.emit(json.dumps(result, ensure_ascii=False))
            self.view.reload()
        except Exception as error:
            logging.exception("Grouping rule apply failed")
            QMessageBox.critical(self.view, "Cannot apply grouping rule", str(error))

    @Slot()
    def openGroupingEditor(self):
        if self.store is None:
            QMessageBox.information(self.view, "Edit Groups", "Open a SQLite dataset first.")
            return
        self.view.open_grouping_editor()

    @Slot()
    def openGroupingRule(self):
        chosen, _ = QFileDialog.getOpenFileName(
            self.view, "Open grouping rule", "", "Grouping rule (*.json *.yml *.yaml);;All files (*)"
        )
        if not chosen:
            return
        try:
            text = Path(chosen).read_text(encoding="utf-8")
            try:
                rule = json.loads(text)
            except json.JSONDecodeError:
                try:
                    import yaml
                except ImportError as error:
                    raise ValueError("YAML grouping rules require PyYAML") from error
                rule = yaml.safe_load(text)
            if not isinstance(rule, dict):
                raise ValueError("Grouping rule must be an object")
            self.ruleTextReady.emit(json.dumps(rule, ensure_ascii=False))
        except Exception as error:
            logging.exception("Grouping rule open failed")
            QMessageBox.critical(self.view, "Cannot open grouping rule", str(error))

    @Slot(str, str)
    def saveGroupingRule(self, text, suggested):
        chosen, _ = QFileDialog.getSaveFileName(
            self.view, "Save grouping rule", suggested or "grouping.json",
            "Grouping rule (*.json *.yml *.yaml);;All files (*)"
        )
        if not chosen:
            return
        try:
            rule = json.loads(text)
            path = Path(chosen)
            if path.suffix.lower() in {".yml", ".yaml"}:
                try:
                    import yaml
                except ImportError as error:
                    raise ValueError("YAML grouping rule export requires PyYAML") from error
                path.write_text(yaml.safe_dump(rule, sort_keys=False, allow_unicode=True), encoding="utf-8")
            else:
                path.write_text(json.dumps(rule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            QMessageBox.information(self.view, "Grouping rule saved", str(path))
        except Exception as error:
            logging.exception("Grouping rule save failed")
            QMessageBox.critical(self.view, "Cannot save grouping rule", str(error))


_profile = None


def viewer_profile():
    global _profile
    if _profile is None:
        _profile = QWebEngineProfile("BasicViewer", QApplication.instance())
        _profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    return _profile


class ViewerView(QWebEngineView):
    def __init__(self):
        super().__init__()
        self.setPage(QWebEnginePage(viewer_profile(), self))
        self.popups = []
        self.bridge = None
        self.channel = None

    def open_grouping_editor(self):
        popup = QWebEngineView()
        popup.setPage(QWebEnginePage(self.page().profile(), popup))
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popup.setWindowTitle("BasicViewer - Grouping Editor")
        popup.resize(1100, 760)
        channel = QWebChannel(popup.page())
        channel.registerObject("backend", self.bridge)
        popup.page().setWebChannel(channel)
        popup.popups = getattr(popup, "popups", [])
        popup.page().titleChanged.connect(popup.setWindowTitle)
        popup.page().windowCloseRequested.connect(popup.close)
        popup.destroyed.connect(lambda *_: self.popups.remove(popup) if popup in self.popups else None)
        self.popups.append(popup)
        popup.load(QUrl.fromLocalFile(str(ROOT / "grouping_editor.html")))
        popup.show()

    def closeEvent(self, event):
        for popup in list(self.popups):
            popup.close()
        super().closeEvent(event)


def create_view(database=None):
    view = ViewerView()
    view.setWindowTitle("BasicViewer - Candidate C")
    view.resize(1800, 1100)
    store = Store(database) if database is not None else None
    channel = QWebChannel(view.page())
    bridge = Bridge(store, view)
    channel.registerObject("backend", bridge)
    view.page().setWebChannel(channel)
    view.bridge = bridge
    view.channel = channel
    view.load(QUrl.fromLocalFile(str(ROOT / "web.html")))
    return view


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Open a BasicViewer SQLite dataset")
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("BasicViewer")
    try:
        window = create_view(args.database)
    except Exception as error:
        QMessageBox.critical(None, "Startup failed", str(error))
        return 1
    window.showMaximized()
    code = app.exec()
    window.bridge.pool.waitForDone()
    del window
    return code


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
