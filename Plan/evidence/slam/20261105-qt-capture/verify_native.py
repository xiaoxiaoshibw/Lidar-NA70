"""Native Windows Qt visual QA using an existing, read-only real session."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
from na70_qt_capture import (QApplication, QFont, QSettings, QTimer,
                             CaptureWindow, CaptureWorker, load_config)


def main():
    app = QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 10))
    evidence = Path(__file__).resolve().parent
    settings = QSettings(str(ROOT / "radar_capture_data/na70_associations/qt-native-qa.ini"), QSettings.IniFormat)
    window = CaptureWindow(load_config(), settings=settings)
    window.show()
    outcomes = []

    def start():
        window.grab().save(str(evidence / "qt-ready-native.png"))
        worker = CaptureWorker(load_config(), existing=ROOT / "radar_capture_data/na70_sessions/20261104-170341", parent=window)
        worker.outcome.connect(outcomes.append)
        window.begin_worker(worker)

    def check():
        if outcomes and window.worker is None:
            window.grab().save(str(evidence / "qt-associated-native.png"))
            (evidence / "native-workflow.json").write_text(
                json.dumps(outcomes[0], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(window.state.text())
            timer.stop()
            window.close()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(200)
    QTimer.singleShot(500, start)
    app.exec()
    return 0 if outcomes and outcomes[0].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
