import ast
import hashlib
import os
import re
import shutil
from pathlib import Path


FLOW_PATH = Path(__file__).resolve().parents[1] / "shadowbot" / "test2" / "vertical_slice_read_price.py"


def _load_evidence_helpers():
    source = FLOW_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "_safe_path_part",
        "_sha256",
        "_storage_uri_for_path",
        "_copy_evidence_to_share",
        "_summarize_evidence_status",
    }
    helpers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    module = ast.Module(body=helpers, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "hashlib": hashlib,
        "os": os,
        "re": re,
        "shutil": shutil,
    }
    exec(compile(module, str(FLOW_PATH), "exec"), namespace)
    return namespace


def test_copy_evidence_to_share_copies_file_and_verifies_hash(tmp_path):
    helpers = _load_evidence_helpers()
    local = tmp_path / "local.png"
    local.write_bytes(b"shadowbot evidence")
    share = tmp_path / "share"

    result = helpers["_copy_evidence_to_share"](
        str(local),
        str(share),
        r"\\pra-share\evidence",
        "ATTEMPT:001",
    )

    copied = share / "ATTEMPT_001" / "local.png"
    assert copied.read_bytes() == b"shadowbot evidence"
    assert result["upload_status"] == "SUCCESS"
    assert result["storage_path"] == str(copied)
    assert result["storage_uri"] == r"\\pra-share\evidence/ATTEMPT_001/local.png"
    assert result["storage_sha256"] == helpers["_sha256"](str(local))
    assert result["hash_verified"] is True
    assert result["upload_error"] == ""
    assert result["error_code"] == ""


def test_copy_evidence_to_share_reports_hash_mismatch_with_distinct_code(tmp_path):
    helpers = _load_evidence_helpers()
    local = tmp_path / "local.png"
    local.write_bytes(b"shadowbot evidence")

    result = helpers["_copy_evidence_to_share"](
        str(local),
        str(tmp_path / "share"),
        "",
        "ATTEMPT-001",
        "EVIDENCE_HASH_MISMATCH",
    )

    assert result["upload_status"] == "FAILED"
    assert result["error_code"] == "EVIDENCE_HASH_MISMATCH"
    assert result["hash_verified"] is False
    assert result["storage_sha256"] != helpers["_sha256"](str(local))


def test_copy_evidence_to_share_marks_skipped_when_share_dir_missing(tmp_path):
    helpers = _load_evidence_helpers()
    local = tmp_path / "local.png"
    local.write_bytes(b"shadowbot evidence")

    result = helpers["_copy_evidence_to_share"](str(local), "", "", "ATTEMPT-001")

    assert result["upload_status"] == "SKIPPED"
    assert result["storage_uri"] == ""
    assert result["storage_path"] == ""
    assert result["hash_verified"] is False
    assert "evidence_share_dir" in result["upload_error"]


def test_summarize_evidence_status():
    helpers = _load_evidence_helpers()
    summarize = helpers["_summarize_evidence_status"]

    assert summarize([]) == "NONE"
    assert summarize([{"upload_status": "SUCCESS"}]) == "COMPLETE"
    assert summarize([{"upload_status": "SKIPPED"}]) == "LOCAL_ONLY"
    assert summarize([{"upload_status": "SUCCESS"}, {"upload_status": "FAILED"}]) == "FAILED"
    assert summarize([{"upload_status": "SUCCESS"}, {"upload_status": "SKIPPED"}]) == "PARTIAL"


def test_read_preview_reconcile_capture_calls_include_shared_evidence_arguments():
    source = FLOW_PATH.read_text(encoding="utf-8")

    assert source.count("_capture_window(") >= 5
    assert source.count("evidence_share_dir,") >= 5
    assert source.count("evidence_storage_uri_prefix,") >= 5
    assert 'if execution_mode == "READ_ONLY":' in source
    assert 'elif execution_mode == "RECONCILE":' in source
    assert 'if execution_mode == "FILL_PREVIEW":' in source
    assert 'if execution_mode not in ("READ_ONLY", "FILL_PREVIEW", "COMMIT", "RECONCILE")' in source
    assert "def _fill_target_price(" in source
    assert 'getattr(element, "clipboard_input", None)' in source
    assert "PREVIEW_COMPLETED" in source
