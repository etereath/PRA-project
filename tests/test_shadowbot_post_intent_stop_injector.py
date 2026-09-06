import importlib.util
import json
import threading
import time
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "inject_shadowbot_stop_after_submit_intent.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("shadowbot_stop_injector", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_injector_waits_until_submit_intent(tmp_path):
    module = _load_module()
    queue_dir = tmp_path / "queue"
    phase_path = queue_dir / "working" / "ATTEMPT-1.phase.json"
    log_path = tmp_path / "injection.json"
    phase_path.parent.mkdir(parents=True)
    phase_path.write_text(
        json.dumps({"phase": "TARGET_FILLED", "side_effect_state": "NOT_STARTED"}),
        encoding="utf-8",
    )

    def advance_phase():
        time.sleep(0.05)
        phase_path.write_text(
            json.dumps(
                {
                    "phase": "SUBMIT_INTENT_RECORDED",
                    "side_effect_state": "SUBMIT_INTENT_RECORDED",
                    "updated_at": "2026-07-06T08:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

    thread = threading.Thread(target=advance_phase)
    thread.start()
    result = module.inject_after_submit_intent(
        queue_dir=queue_dir,
        execution_attempt_id="ATTEMPT-1",
        log_path=log_path,
        timeout_seconds=1,
        poll_seconds=0.01,
    )
    thread.join()

    assert result["status"] == "INJECTED"
    assert result["observed_phase"] == "SUBMIT_INTENT_RECORDED"
    assert (queue_dir / "control" / "stop.signal").read_text(encoding="ascii") == "stop\n"
    assert json.loads(log_path.read_text(encoding="utf-8"))["status"] == "INJECTED"


def test_injector_does_not_write_stop_after_result_already_exists(tmp_path):
    module = _load_module()
    queue_dir = tmp_path / "queue"
    result_path = queue_dir / "results" / "ATTEMPT-2.result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text("{}", encoding="utf-8")
    log_path = tmp_path / "injection.json"

    result = module.inject_after_submit_intent(
        queue_dir=queue_dir,
        execution_attempt_id="ATTEMPT-2",
        log_path=log_path,
        timeout_seconds=1,
        poll_seconds=0.01,
    )

    assert result["status"] == "MISSED_RESULT_ALREADY_WRITTEN"
    assert not (queue_dir / "control" / "stop.signal").exists()
