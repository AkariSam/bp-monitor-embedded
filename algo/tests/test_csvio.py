"""bp/csvio.py 测试：契约读写往返 + validate 正/负例。"""

import numpy as np
import pytest

from bp.csvio import DT_MS, Recording, read_csv, validate, write_csv


def _good_rec(n: int = 100, dt: float = DT_MS) -> Recording:
    return Recording(
        t_ms=np.arange(n) * dt,
        p_raw=np.full(n, 120.0),
        phase=np.array(["DEFLATE"] * n),
    )


def test_roundtrip(tmp_path):
    rec = _good_rec()
    path = tmp_path / "rec.csv"
    write_csv(path, rec)
    back = read_csv(path)
    assert np.allclose(back.t_ms, rec.t_ms)
    assert np.allclose(back.p_raw, rec.p_raw)
    assert list(back.phase) == list(rec.phase)
    assert validate(back) == []


def test_validate_clean():
    assert validate(_good_rec()) == []


def test_validate_empty():
    rec = Recording(np.array([]), np.array([]), np.array([], dtype="<U7"))
    assert validate(rec) != []


def test_validate_non_monotonic():
    rec = _good_rec()
    rec.t_ms[5] = rec.t_ms[4]
    assert any("递增" in s for s in validate(rec))


def test_validate_wrong_dt():
    rec = _good_rec(dt=25.0)  # 模拟 40 Hz 输出
    issues = validate(rec)
    assert any("采样间隔" in s for s in issues)
    assert validate(rec, dt_ms=25.0) == []


def test_validate_bad_phase():
    rec = _good_rec()
    rec.phase[3] = "PAUSE"
    assert any("phase" in s for s in validate(rec))


def test_validate_nan():
    rec = _good_rec()
    rec.p_raw[7] = np.nan
    assert any("NaN" in s for s in validate(rec))


def test_read_rejects_bad_header(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("time,pressure,phase\n0.0,100.0,DEFLATE\n",
                    encoding="utf-8")
    with pytest.raises(ValueError):
        read_csv(path)


def test_read_rejects_bad_row(tmp_path):
    path = tmp_path / "bad_row.csv"
    path.write_text("t_ms,p_raw,phase\n0.0,100.0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_csv(path)
