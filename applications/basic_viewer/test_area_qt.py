"""Exercise area requests through the actual Qt worker and WebChannel."""
import argparse
import math
from multiprocessing import active_children
from pathlib import Path
import sys
import time


def main(base, comparison, output, app_directory=None):
    if app_directory:
        sys.path.insert(0, str(app_directory))
    from test_qt import execute, poll
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from main import create_view

    app = QApplication([])
    view = create_view(base, comparison)
    view.resize(1920, 1080)
    view.show()
    def capture(name):
        frame = QEventLoop()
        QTimer.singleShot(250, frame.quit)
        frame.exec()
        assert view.grab().save(str(output / name))

    try:
        poll(view, "window.viewerReady && state.image && state.result && !state.applying && JSON.stringify(true)")
        execute(view, "$('#areaDifference').click()")
        image = poll(view, "state.areaData && JSON.stringify({"
                     "count:state.areaData.patterns.length,issues:state.areaData.issues.length,"
                     "aligned:JSON.stringify(state.areaData.shift)===JSON.stringify(state.image.shift),"
                     "paths:state.areaData.patterns.every(p=>areaKinds.every(k=>p[k].path instanceof Path2D)),"
                     "error:$('#error').textContent})")
        assert image == dict(count=20, issues=0, aligned=True, paths=True, error=""), image
        output.mkdir(parents=True, exist_ok=True)
        capture("area-image-qt.png")
        started = time.perf_counter()
        execute(view, "setContourView(true);$('[data-contour-measure=area]').click();")
        poll(view, "state.contourData?.measure==='area' && state.contourData.scope==='image' && "
             "!state.contourLoading && JSON.stringify(true)")
        image_seconds = time.perf_counter() - started
        capture("area-this-image-qt.png")
        started = time.perf_counter()
        execute(view, "$('[data-contour-scope=pid]').click()")
        poll(view, "state.contourData?.measure==='area' && state.contourData.scope==='pid' && "
             "!state.contourLoading && JSON.stringify(true)")
        pid_seconds = time.perf_counter() - started
        capture("area-same-pid-qt.png")
        started = time.perf_counter()
        execute(view, "$('[data-contour-scope=selection]').click()")
        area = poll(view, "state.contourData?.measure==='area' && !state.contourLoading && JSON.stringify({"
                    "pairs:state.contourData.pairs,selected:state.contourData.selected_pairs,"
                    "compared:state.contourData.compared,excluded:state.contourData.excluded,"
                    "totals:areaKinds.map(k=>state.contourData[k].reduce((a,b)=>a+b,0)),"
                    "means:areaKinds.map(k=>state.contourData.mean_area_nm2[k]),error:$('#error').textContent})",
                    timeout=600)
        selection_seconds = time.perf_counter() - started
        assert area["pairs"] == area["selected"] == 1288, area
        assert area["compared"] == 25760 and not area["excluded"] and not area["error"], area
        for total, mean in zip(area["totals"], area["means"]):
            assert math.isclose(total, mean, rel_tol=1e-8), area
        execute(view, "$('#contourDesign').click()")
        poll(view, "state.contourProjection && JSON.stringify(true)")
        capture("area-selection-qt.png")
        execute(view, "$('[data-area-channel=inward]').click()")
        assert poll(view, "JSON.stringify(getComputedStyle($('[data-area-channel=inward]')).backgroundColor)") == "rgb(255, 158, 174)"
        capture("area-inward-selected-qt.png")
        assert not active_children(), "Completed area requests must release their worker processes"
        execute(view, "state.contourCache.clear();state.contourKey=null;refreshContourView();")
        deadline = time.monotonic() + 30
        while not active_children() and time.monotonic() < deadline:
            wait = QEventLoop()
            QTimer.singleShot(50, wait.quit)
            wait.exec()
        assert active_children(), "The full selection must use spawned area workers"
        assert len(active_children()) <= 2
        view.close()
        view.bridge.pool.waitForDone()
        assert not active_children(), "Closing during calculation must not leave area workers"
        print("PASS: actual Qt worker/WebChannel, aligned Image fills, 1,288 pairs / 25,760 patterns,"
              " both decoded 1024² grids conserve area, selected Inward pink, worker cleanup after completion/close.")
        print(f"Qt request timings: This image {image_seconds:.2f}s; Same PID {pid_seconds:.2f}s; "
              f"All selected {selection_seconds:.2f}s")
    finally:
        view.close()
        view.bridge.pool.waitForDone()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("base", type=Path)
    cli.add_argument("comparison", type=Path)
    cli.add_argument("output", type=Path)
    cli.add_argument("--app-directory", type=Path)
    args = cli.parse_args()
    main(args.base, args.comparison, args.output, args.app_directory)
