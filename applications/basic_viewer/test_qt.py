import argparse
import json
import os
from pathlib import Path
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --no-sandbox"

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from main import create_view
from engine import Store


def poll(view, expression, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loop = QEventLoop()
        result = []

        def accept(value):
            result.append(value)
            loop.quit()

        view.page().runJavaScript(expression, accept)
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
        if result and result[0]:
            decoded = json.loads(result[0])
            if decoded:
                return decoded
        wait = QEventLoop()
        QTimer.singleShot(50, wait.quit)
        wait.exec()
    raise AssertionError(f"Timed out: {expression}")


def execute(view, source):
    return poll(view, f"(()=>{{{source};return JSON.stringify(true);}})()")


def main(database, screenshot, comparison=None):
    app = QApplication([])
    test_override = Store(database, comparison).index()["metadata"].get("test_chip_override")
    view = create_view(database, comparison)
    view.show()
    index = poll(view, "window.viewerReady && JSON.stringify({"
                 "images:state.index.images.length,visible:state.visible.length,"
                 "vectors:state.image?.vectors.comparison.length||0,error:document.querySelector('#error').textContent})")
    assert index == dict(images=2580, visible=1288 if test_override else 0, vectors=16 if test_override else 0, error=""), index
    poll(view, "state.result && !document.querySelector('#update').disabled && JSON.stringify(true)")
    execute(view, "updateStatistics()")
    initial = poll(view, "state.result && !document.querySelector('#update').disabled && JSON.stringify({"
                   "images:state.result.images,n:state.result.table[0].n,paired:state.result.paired_images})")
    assert initial == dict(images=1288 if test_override else 0, n=22214 if test_override else 0,
                           paired=1288 if test_override else 0), initial
    assert poll(view, "JSON.stringify(document.documentElement.lang)") == "en"
    assert poll(view, "JSON.stringify(document.querySelectorAll('#stats tbody tr').length)") == 6
    if test_override:
        warning = poll(view, "JSON.stringify(document.querySelector('#testWarning').textContent)")
        assert "synthetic chip coordinates" in warning and "Not a validated same-site comparison" in warning, warning
        assert "TEST ONLY" not in warning, warning
    else:
        assert poll(view, "JSON.stringify(state.result.counts.base.unpaired)") == 1290
        view.close()
        view.bridge.pool.waitForDone()
        print("PASS: real Qt empty matched cohort, explicit 1290 unpaired Base images, no false pairing.")
        return
    execute(view, "document.querySelector('#contourViewButton').click()")
    poll(view, "state.contourData?.scope==='image' && !state.contourLoading && JSON.stringify(true)")
    execute(view, "document.querySelector('[data-contour-scope=pid]').click()")
    pid = poll(view, "state.contourData?.scope==='pid' && !state.contourLoading && "
               "JSON.stringify({counts:state.contourData.counts,error:document.querySelector('#error').textContent})")
    assert pid == dict(counts=dict(base=1288, comparison=1288), error=""), pid
    execute(view, "document.querySelector('[data-contour-scope=selection]').click()")
    density = poll(view, "state.contourData?.scope==='selection' && !state.contourLoading && "
                   "JSON.stringify({pairs:state.contourData.pairs,mapped:state.contourData.mapped,bins:state.contourData.bins})")
    assert density == dict(pairs=1288, mapped=dict(base=25760, comparison=25760), bins=1024), density
    execute(view, "document.querySelector('#contourViewButton').click()")
    execute(view, "setFilters({chips:[[8,7]]})")
    narrowed = poll(view, "state.visible.length===256 && JSON.stringify({"
                    "stale:document.querySelector('#statsStatus').textContent,"
                    "disabled:document.querySelector('#exportStats').disabled})")
    assert "pending" in narrowed["stale"] and narrowed["disabled"], narrowed
    execute(view, "setFilters({roi:[0,0,256,512]});updateStatistics()")
    roi = poll(view, "state.resultRevision===state.revision && !document.querySelector('#update').disabled && "
               "JSON.stringify({images:state.result.images,n:state.result.table[0].n,roi:state.result.filters.roi})")
    assert roi["images"] == 256 and 0 < roi["n"] < initial["n"], roi
    execute(view, "document.querySelector('#rawToggle').click();document.querySelector('[data-raw-role=comparison]').click()")
    empty = poll(view, "JSON.stringify({rows:document.querySelectorAll('#raw tbody tr').length,"
                 "message:document.querySelector('#rawStatus').textContent})")
    if test_override:
        assert empty["rows"] == 20 and "source rows" in empty["message"], empty
    else:
        assert empty["rows"] == 0 and "same-chip" in empty["message"], empty
    execute(view, "document.querySelector('[data-raw-role=base]').click();"
            "document.querySelector('[data-map-role=comparison]').click();"
            "document.querySelector('[data-graph=box]').click();"
            "document.querySelector('#metric').value='magnitude';document.querySelector('#metric').onchange()")
    execute(view, "document.querySelector('#clearRoi').click();document.querySelector('#undo').click()")
    assert poll(view, "JSON.stringify(state.filters.roi)") == [0, 0, 256, 512]
    execute(view, "setFilters({chips:[[0,0]]})")
    poll(view, "state.visible.length===0 && state.image===null && JSON.stringify(true)")
    execute(view, "document.querySelector('#clearAll').click()")
    poll(view, "state.image && JSON.stringify(true)")
    execute(view, "document.querySelector('#update').click()")
    poll(view, "state.resultRevision===state.revision && !document.querySelector('#update').disabled && JSON.stringify(true)")
    errors = poll(view, "JSON.stringify({error:document.querySelector('#error').textContent,"
                  "raw:document.querySelectorAll('#raw tbody tr').length,"
                  "overflow:Math.max(...['#rail','#center',':root'].map(s=>{const e=document.querySelector(s);"
                  "return e.scrollHeight-e.clientHeight;}))})")
    assert errors["error"] == "" and errors["raw"] == 20, errors
    assert errors["overflow"] <= 2, errors
    if test_override:
        execute(view, f"choose(state.visible.find(e=>e.id==={test_override['initial_image_id']}))")
        poll(view, "state.image && state.image.matched===20 && JSON.stringify(true)")
    if screenshot:
        frame = QEventLoop()
        QTimer.singleShot(250, frame.quit)
        frame.exec()
        assert view.grab().save(str(screenshot))
    view.close()
    view.bridge.pool.waitForDone()
    print("PASS: actual Qt WebChannel/worker/SQLite integration, all image metadata, known valid count,")
    print("chip filter, shared ROI, statistics refresh/stale guard, raw tabs, graph, undo, empty selection.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("database", type=Path)
    cli.add_argument("--screenshot", type=Path)
    cli.add_argument("--comparison", type=Path)
    args = cli.parse_args()
    main(args.database, args.screenshot, args.comparison)
