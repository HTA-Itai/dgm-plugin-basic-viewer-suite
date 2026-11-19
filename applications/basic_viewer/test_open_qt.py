"""Actual Qt/WebChannel session replacement; dialogs are mocked, not Windows-native tested."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from test_qt import execute, poll
from PySide6.QtWidgets import QApplication

from main import create_view


def main(base, comparison, synthetic):
    app = QApplication([])
    view = create_view()
    view.show()
    poll(view, "window.viewerReady && JSON.stringify(document.body.classList.contains('no-dataset'))")
    assert poll(view, "JSON.stringify(!document.querySelector('#startup').hidden)")
    bridge = view.bridge
    pick = lambda *paths: patch("main.QFileDialog.getOpenFileName", side_effect=[(str(path), "") for path in paths])
    # Comp cannot be opened before Base.
    with patch("main.QMessageBox.information") as info, pick(comparison) as dialog:
        bridge.openComparison()
        assert bridge.store is None and info.call_count == 1 and dialog.call_count == 0
    with pick(""):
        bridge.openBase()
        assert bridge.store is None
    with pick(base):
        bridge.openBase()
    poll(view, "window.viewerReady && state.result?.single===true && JSON.stringify(true)")
    assert bridge.store.single
    assert poll(view, "JSON.stringify(state.result.images)") == 1290
    assert poll(view, "JSON.stringify(document.querySelector('[data-lower=\"ppe\"]').disabled)")
    single_store = bridge.store
    with tempfile.TemporaryDirectory() as folder:
        invalid = Path(folder) / "invalid-v1.sqlite"
        with sqlite3.connect(invalid) as db:
            db.execute("PRAGMA user_version=1")
        prefix = poll(view, "JSON.stringify(requestPrefix)")
        with pick(invalid), patch("main.QMessageBox.critical") as error:
            bridge.openBase()
            assert bridge.store is single_store and error.call_count == 1
            assert "version 4" in error.call_args.args[2]
        assert poll(view, "JSON.stringify(requestPrefix)") == prefix
        try:
            unexpected = create_view(invalid)
        except ValueError as error:
            assert "version 4" in str(error)
        else:
            unexpected.close()
            raise AssertionError("Startup accepted a non-Viewer v1 database")
    with pick(Path(__file__)), patch("main.QMessageBox.critical") as error:
        bridge.openComparison()
        assert bridge.store is single_store and error.call_count == 1
    with pick(synthetic):
        bridge.openComparison()
    poll(view, "window.viewerReady && state.result?.paired_images===1288 && JSON.stringify(true)")
    assert poll(view, "JSON.stringify(!document.body.classList.contains('no-dataset'))")
    # PPE value export goes through the worker command and the CSV writer.
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "ppe_values.csv"
        with patch("main.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            execute(view, "exportPpeValues()")
            poll(view, "!state.exportingPpe && JSON.stringify(true)", timeout=120)
            for _ in range(600):
                if target.exists():
                    break
                poll(view, "JSON.stringify(true)")
        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        expected = poll(view, "JSON.stringify(state.result.ppe.table[0].n)")
        assert sum(1 for r in rows if r["ppe_x"]) == expected and rows[0]["test_only_chip_override"] == "True"
    previous = bridge.store
    # A bad replacement must not change the active pair.
    with pick(Path(__file__)), patch("main.QMessageBox.critical") as error:
        bridge.openComparison()
        assert bridge.store is previous and error.call_count == 1
    with pick(""):
        bridge.openComparison()
        assert bridge.store is previous
    # A response from the old session may finish after a new page starts.
    execute(view, "window.oldPrefix=requestPrefix")
    old_prefix = poll(view, "JSON.stringify(window.oldPrefix)")
    bridge.request(old_prefix + "late", "statistics", json.dumps(dict(pno=2)))
    with pick(comparison):
        bridge.openComparison()
    poll(view, "window.viewerReady && state.result?.paired_images===0 && state.result.single===false && JSON.stringify(true)")
    assert poll(view, "JSON.stringify(requestPrefix)") != old_prefix
    assert poll(view, "JSON.stringify(state.result.counts.base.unpaired)") == 1290
    bridge.pool.waitForDone()
    assert poll(view, "JSON.stringify(state.result.paired_images===0 && document.querySelector('#error').textContent==='')")
    assert poll(view, "JSON.stringify(state.index.metadata.files.comparison)") == str(comparison.resolve())
    bridge.closeComparison()
    poll(view, "window.viewerReady && state.result?.single===true && JSON.stringify(true)")
    assert bridge.store.single and bridge.store.path.base.resolve() == base.resolve()
    # Synthetic Base must stay marked both before and after closing Comp.
    with pick(synthetic):
        bridge.openBase()
    synthetic_path = json.dumps(str(synthetic.resolve()))
    poll(view, f"window.viewerReady && state.result?.single===true && "
               f"state.index.metadata.files.base==={synthetic_path} && JSON.stringify(true)")
    assert poll(view, "JSON.stringify(!document.querySelector('#testWarning').hidden)")
    with pick(base):
        bridge.openComparison()
    base_path = json.dumps(str(base.resolve()))
    poll(view, f"window.viewerReady && state.result?.single===false && "
               f"state.index.metadata.files.comparison==={base_path} && JSON.stringify(true)")
    assert poll(view, "JSON.stringify(!!state.index.metadata.test_chip_override.datasets.base)")
    bridge.closeComparison()
    poll(view, "window.viewerReady && state.result?.single===true && JSON.stringify(true)")
    assert poll(view, "JSON.stringify(!!state.index.metadata.test_chip_override.datasets.base && "
               "!document.querySelector('#testWarning').hidden)")
    view.close()
    print("PASS: blank startup; Comp needs Base; Base-only session; invalid v1 rejected on open and startup;")
    print("cancel and invalid file retain session; synthetic Base warning survives closing Comp;")
    print("PPE values CSV export through the Qt bridge;")
    print("successful replacement clears prior selection/results; late old-session replies cannot overwrite new results;")
    print("close Comp returns to Base only.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("base", type=Path)
    cli.add_argument("comparison", type=Path)
    cli.add_argument("synthetic", type=Path)
    args = cli.parse_args()
    main(args.base, args.comparison, args.synthetic)
