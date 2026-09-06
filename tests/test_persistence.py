import json
from unittest.mock import patch

import pytest

from utils.persistence import atomic_write_json


def test_atomic_write_json_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"hello": "world"})
    assert json.loads(path.read_text()) == {"hello": "world"}


def test_atomic_write_failure_preserves_existing_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"old": true}\n')
    with patch("utils.persistence.os.replace", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            atomic_write_json(path, {"new": True})
    assert path.read_text() == '{"old": true}\n'
    assert list(tmp_path.glob("*.tmp")) == []
