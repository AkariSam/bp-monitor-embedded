"""算法移植证明 demo（四幕）。

用法（包根目录）::

    python firmware/demo/run_algo_demo.py

四幕：
1. PC 正本 vs 固件分析器对拍表；
2. 三场景包络重合图；
3. PulsatileFakeDriver 端到端 FSM；
4. 内存/耗时预算 + float32 敏感性 + 误差总览图。

每幕都是可 import 的函数，函数内自带断言并返回结构化结果；
``main()`` 逐幕打印 PASS/FAIL，任一失败返回非零退出码。
"""

import os
import sys
import traceback
from pathlib import Path

FIRMWARE_DIR = Path(__file__).resolve().parents[1]
ALGO_DIR = FIRMWARE_DIR.parent / "algo"
DEMO_DIR = Path(__file__).resolve().parent
TESTS_DIR = FIRMWARE_DIR / "tests"
SCRIPTS_DIR = FIRMWARE_DIR / "scripts"
for _path in (FIRMWARE_DIR, ALGO_DIR, DEMO_DIR, TESTS_DIR, SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config
from algo.params import AlgoParams
from analyzer import PipelineAnalyzer
from coverify_util import (
    firmware_signal,
    load_scene,
    make_buffer,
    pc_on_buffer,
    pc_signal,
    run_firmware,
)
from f32 import Float32Hooks
from run_demo import PulsatileFakeDriver, ScenarioRun

import budget

SCENARIOS = ("normal", "hypertension", "hypotension")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "out"
HEAP_BUDGET_KIB = 200.0
TARGET_KIB = 160.0


def _scene_signals(name):
    """加载场景并同时得到 PC 正本 / 固件分析器结果。"""
    rec, truth, csv_path, truth_path = load_scene(name)
    buffer = make_buffer(rec)
    pc_run = pc_on_buffer(buffer)
    pc = pc_signal(pc_run)
    result, analyzer, steps = run_firmware(buffer)
    fw = firmware_signal(result)
    return {
        "name": name,
        "truth": truth,
        "csv_path": str(csv_path),
        "buffer": buffer,
        "pc_run": pc_run,
        "pc": pc,
        "result": result,
        "fw": fw,
        "analyzer": analyzer,
        "steps": steps,
    }


def _triple(signal):
    if not signal["accepted"]:
        return "REJECT", "REJECT", "REJECT"
    return (
        "%.3f" % signal["sbp"],
        "%.3f" % signal["dbp"],
        "%.1f" % signal["hr"],
    )


# ---------------------------------------------------------------------------
# Act 1
# ---------------------------------------------------------------------------
def act1_coverify_table():
    """现场跑 3 场景 PC vs 固件，打印对拍表并断言 Δ≤1 mmHg / QC 一致。"""
    print("\n" + "=" * 108)
    print("Act 1: PC 正本 vs 固件分析器（同一 DeflateBuffer）")
    print("-" * 108)
    header = (
        "%-13s %-23s %-23s %-19s %-13s %-11s %-5s"
        % (
            "Scenario",
            "PC SBP/DBP/HR",
            "FW SBP/DBP/HR",
            "dSBP/dDBP (FW-PC)",
            "Truth",
            "PC/FW QC",
            "PASS",
        )
    )
    print(header)
    print("-" * len(header))

    rows = []
    for name in SCENARIOS:
        scene = _scene_signals(name)
        pc = scene["pc"]
        fw = scene["fw"]
        truth = scene["truth"]

        assert fw["accepted"] == pc["accepted"], (
            "%s accept mismatch: PC=%r FW=%r"
            % (name, pc["accepted"], fw["accepted"])
        )
        assert fw["qc_reasons"] == pc["qc_reasons"], (
            "%s QC reasons mismatch: PC=%r FW=%r"
            % (name, pc["qc_reasons"], fw["qc_reasons"])
        )
        assert fw["n_candidates"] == pc["n_candidates"]
        assert fw["n_accepted"] == pc["n_accepted"]
        assert fw["n_points"] == pc["n_points"]

        if pc["accepted"]:
            d_sbp = fw["sbp"] - pc["sbp"]
            d_dbp = fw["dbp"] - pc["dbp"]
            d_hr = fw["hr"] - pc["hr"]
            assert abs(d_sbp) <= 1.0, "%s dSBP %.6f > 1" % (name, d_sbp)
            assert abs(d_dbp) <= 1.0, "%s dDBP %.6f > 1" % (name, d_dbp)
            assert abs(d_hr) <= 1.0, "%s dHR %.6f > 1" % (name, d_hr)
        else:
            d_sbp = d_dbp = d_hr = None
            assert fw["sbp"] is None

        qc = "%s/%s" % (
            "OK" if pc["qc_ok"] else "REJECT",
            "OK" if fw["qc_ok"] else "REJECT",
        )
        row = {
            "name": name,
            "pc": pc,
            "fw": fw,
            "truth": truth,
            "d_sbp": d_sbp,
            "d_dbp": d_dbp,
            "d_hr": d_hr,
            "passed": True,
        }
        rows.append(row)

        p_sbp, p_dbp, p_hr = _triple(pc)
        f_sbp, f_dbp, f_hr = _triple(fw)
        delta = (
            "%+.2e/%+.2e" % (d_sbp, d_dbp)
            if d_sbp is not None
            else "-/-"
        )
        print(
            "%-13s %-23s %-23s %-19s %-13s %-11s %-5s"
            % (
                name,
                "%s/%s/%s" % (p_sbp, p_dbp, p_hr),
                "%s/%s/%s" % (f_sbp, f_dbp, f_hr),
                delta,
                "%.0f/%.0f/%.0f"
                % (truth["sbp"], truth["dbp"], truth["hr"]),
                qc,
                "PASS",
            )
        )
    print("-" * len(header))
    return {"rows": rows, "passed": True}


# ---------------------------------------------------------------------------
# Act 2
# ---------------------------------------------------------------------------
def _trimmed_pressure(buffer, margin):
    times = []
    pressures = []
    for i, (_seq, t_ms, p_mmHg) in enumerate(buffer):
        if margin <= i < len(buffer) - margin:
            times.append(t_ms * 0.001)
            pressures.append(p_mmHg)
    return times, pressures


def act2_envelope_plots(outdir=None):
    """3 场景各画一张包络重合图，断言 PC/FW 包络数值一致。"""
    outdir = Path(outdir) if outdir is not None else DEFAULT_OUTPUT_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    params = AlgoParams()
    plots = []
    numerics = []

    print("\n" + "=" * 108)
    print("Act 2: 包络重合图（PC vs 固件）")
    print("-" * 108)

    for name in SCENARIOS:
        scene = _scene_signals(name)
        pc_run = scene["pc_run"]
        result = scene["result"]
        analyzer = scene["analyzer"]
        assert pc_run.accepted, "%s PC rejected" % name
        assert result["result"] == "OK", "%s firmware rejected" % name

        pc_env = pc_run.envelope
        fw_env = analyzer.envelope
        assert len(pc_env.pc) == len(fw_env.pc), (
            "%s envelope point count mismatch: PC=%d FW=%d"
            % (name, len(pc_env.pc), len(fw_env.pc))
        )
        max_pc_diff = max(
            abs(a - b) for a, b in zip(pc_env.pc, fw_env.pc)
        )
        max_amp_diff = max(
            abs(a - b) for a, b in zip(pc_env.amp_s, fw_env.amp_s)
        )
        assert max_pc_diff <= 1e-3, (
            "%s envelope pressure diff %.3e > 1e-3"
            % (name, max_pc_diff)
        )
        assert max_amp_diff <= 0.05, (
            "%s envelope amp diff %.3e > 0.05"
            % (name, max_amp_diff)
        )

        times, pressures = _trimmed_pressure(scene["buffer"], analyzer.margin)
        peaks = analyzer.accepted
        data = result["data"]
        pc_data = pc_run.decision

        fig, (ax_top, ax_bot) = plt.subplots(
            2,
            1,
            figsize=(11, 9),
            gridspec_kw={"height_ratios": (1.0, 1.5)},
        )
        ax_top.plot(
            times,
            pressures,
            color="#1f77b4",
            lw=1.2,
            label="Cuff pressure",
        )
        ax_top.scatter(
            [times[c.idx] for c in peaks],
            [pressures[c.idx] for c in peaks],
            s=20,
            color="#d62728",
            zorder=3,
            label="Accepted beats",
        )
        ax_top.set_xlabel("Time [s]")
        ax_top.set_ylabel("Cuff pressure [mmHg]")
        ax_top.set_title(
            "Scenario: %s — cuff pressure and detected beats" % name
        )
        ax_top.grid(alpha=0.25)
        ax_top.legend(loc="upper right")

        ax_bot.plot(
            pc_env.pc,
            pc_env.amp_s,
            "o-",
            ms=3,
            lw=1.2,
            color="#1f77b4",
            label="PC envelope",
        )
        ax_bot.plot(
            fw_env.pc,
            fw_env.amp_s,
            "s--",
            ms=3,
            lw=1.2,
            color="#d62728",
            label="Firmware envelope",
        )
        peak_amp = max(fw_env.amp_s)
        ax_bot.axhline(
            params.ratio_sbp * peak_amp,
            color="#ff7f0e",
            ls=":",
            lw=1.2,
            label="0.50 x peak",
        )
        ax_bot.axhline(
            params.ratio_dbp * peak_amp,
            color="#9467bd",
            ls=":",
            lw=1.2,
            label="0.70 x peak",
        )

        for label, x_value, y_value, color in (
            ("SBP", data["sbp"], params.ratio_sbp * peak_amp, "#d62728"),
            ("MAP", data["map"], peak_amp, "#2ca02c"),
            ("DBP", data["dbp"], params.ratio_dbp * peak_amp, "#9467bd"),
        ):
            ax_bot.plot(
                [x_value],
                [y_value],
                "o",
                ms=9,
                mfc="none",
                mec=color,
                mew=2,
            )
            ax_bot.annotate(
                "%s %.1f" % (label, x_value),
                (x_value, y_value),
                textcoords="offset points",
                xytext=(7, 6),
                fontsize=9,
                color=color,
            )
        for label, x_value, y_value in (
            ("PC SBP", pc_data.sbp, params.ratio_sbp * peak_amp),
            ("PC MAP", pc_data.map, peak_amp),
            ("PC DBP", pc_data.dbp, params.ratio_dbp * peak_amp),
        ):
            ax_bot.plot(
                [x_value],
                [y_value],
                "s",
                ms=6,
                mfc="none",
                mec="#1f77b4",
                mew=1,
                alpha=0.7,
            )

        text = (
            "PC  SBP %.2f  DBP %.2f  HR %.1f\n"
            "FW  SBP %.2f  DBP %.2f  HR %.1f\n"
            "|d| SBP %.2e  DBP %.2e mmHg\n"
            "envelope: max|dP|=%.1e  max|dA|=%.1e"
            % (
                pc_data.sbp,
                pc_data.dbp,
                pc_data.hr,
                data["sbp"],
                data["dbp"],
                data["hr"],
                abs(data["sbp"] - pc_data.sbp),
                abs(data["dbp"] - pc_data.dbp),
                max_pc_diff,
                max_amp_diff,
            )
        )
        ax_bot.text(
            0.02,
            0.98,
            text,
            transform=ax_bot.transAxes,
            va="top",
            fontsize=9,
            family="monospace",
            bbox=dict(
                boxstyle="round",
                facecolor="white",
                edgecolor="#999999",
                alpha=0.92,
            ),
        )
        ax_bot.set_xlabel("Cuff pressure [mmHg]")
        ax_bot.set_ylabel("Envelope amplitude [mmHg]")
        ax_bot.set_title(
            "Oscillometric envelope: PC vs firmware (open markers = PC)"
        )
        ax_bot.grid(alpha=0.25)
        ax_bot.legend(loc="upper right", fontsize=8)

        path = outdir / ("algo_envelope_%s.png" % name)
        fig.tight_layout()
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        assert path.exists() and path.stat().st_size > 0, (
            "missing plot %s" % path
        )
        plots.append(str(path))
        numerics.append(
            {
                "name": name,
                "max_pc_diff": max_pc_diff,
                "max_amp_diff": max_amp_diff,
            }
        )
        print(
            "%-13s envelope points=%d  max|dP|=%.2e  max|dA|=%.2e  -> %s"
            % (name, len(pc_env.pc), max_pc_diff, max_amp_diff, path)
        )
    return {"plots": plots, "numerics": numerics, "passed": True}


# ---------------------------------------------------------------------------
# Act 3
# ---------------------------------------------------------------------------
def act3_fsm_end_to_end():
    """PulsatileFakeDriver 从 START 跑到 DONE，真分析器 vs PC 同缓冲。"""
    print("\n" + "=" * 108)
    print("Act 3: 端到端 FSM（PulsatileFakeDriver + PipelineAnalyzer）")
    print("-" * 108)

    analyzer = PipelineAnalyzer()
    run = ScenarioRun(
        0,
        "algo_demo_e2e",
        "Algorithm end-to-end FSM",
        analyzer,
        make_plot=False,
        step_ms=12,
        driver_factory=PulsatileFakeDriver,
    )
    run.record_injection("START")
    run.fsm.start()
    done = run.run_until_done(max_steps=8000)
    assert done, "FSM did not reach DONE"
    assert run.fsm.state == config.STATE_DONE
    assert run.fsm.result == config.RESULT_OK, (
        "FSM result %r reason %r" % (run.fsm.result, run.fsm.reason)
    )
    assert analyzer.result is not None
    fw = firmware_signal(analyzer.result)
    assert fw["accepted"], "firmware analyzer rejected: %r" % (
        fw["qc_reasons"],
    )

    pc = pc_signal(pc_on_buffer(run.fsm.buffer))
    assert pc["accepted"], "PC rejected the same buffer: %r" % (
        pc["qc_reasons"],
    )
    assert fw["qc_reasons"] == pc["qc_reasons"]
    assert fw["n_points"] == pc["n_points"]

    d_sbp = fw["sbp"] - pc["sbp"]
    d_dbp = fw["dbp"] - pc["dbp"]
    d_hr = fw["hr"] - pc["hr"]
    assert abs(d_sbp) <= 0.5, "FSM dSBP %.6f > 0.5" % d_sbp
    assert abs(d_dbp) <= 0.5, "FSM dDBP %.6f > 0.5" % d_dbp
    assert abs(d_hr) <= 0.5, "FSM dHR %.6f > 0.5" % d_hr

    print(
        "FSM: state=%s result=%s steps=%d buffer=%d samples"
        % (run.fsm.state, run.fsm.result, run.steps, len(run.fsm.buffer))
    )
    print(
        "FW : SBP %.3f  DBP %.3f  HR %.1f  QC=%s"
        % (fw["sbp"], fw["dbp"], fw["hr"], fw["qc_reasons"] or "OK")
    )
    print(
        "PC : SBP %.3f  DBP %.3f  HR %.1f  QC=%s"
        % (pc["sbp"], pc["dbp"], pc["hr"], pc["qc_reasons"] or "OK")
    )
    print(
        "|d|: SBP %.3e  DBP %.3e  HR %.3e"
        % (abs(d_sbp), abs(d_dbp), abs(d_hr))
    )
    return {
        "firmware": fw,
        "pc": pc,
        "steps": run.steps,
        "buffer_samples": len(run.fsm.buffer),
        "passed": True,
    }


# ---------------------------------------------------------------------------
# Act 4
# ---------------------------------------------------------------------------
def act4_budget_and_error_overview(outdir=None, strict_timing=None):
    """现场测内存/耗时、打印 f32 对比，并画 3 场景误差总览。

    共享 CI runner 的墙钟时间噪声很大，250 ms ×100 预算只在本地/目标机器上
    作为硬门槛；CI 默认只做 500 ms 的粗筛，避免把调度抖动当作算法回归。
    需要强制严格检查时设置 ``BUDGET_STRICT=1``。
    """
    outdir = Path(outdir) if outdir is not None else DEFAULT_OUTPUT_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    if strict_timing is None:
        if "BUDGET_STRICT" in os.environ:
            strict_timing = os.environ["BUDGET_STRICT"] == "1"
        else:
            strict_timing = os.environ.get("CI") != "true"

    print("\n" + "=" * 108)
    print("Act 4: 预算与精度实测")
    print("-" * 108)

    rec = budget.ensure_slow_recording()
    full = budget.build_buffer(rec, budget.FULL_SAMPLES)
    stages = budget.measure_memory(full)
    peak = max(stages, key=lambda item: item["mp"])
    buffer_kib = budget.kib(peak["rows"][0][5])
    total_kib = budget.kib(peak["mp"])
    analyzer_kib = total_kib - buffer_kib

    print("Memory (9600-point buffer, MicroPython estimate):")
    print(
        "  buffer   %6.1f KiB\n"
        "  analyzer %6.1f KiB\n"
        "  total    %6.1f KiB / %.0f KiB (%.0f%%), target <= %.0f KiB"
        % (
            buffer_kib,
            analyzer_kib,
            total_kib,
            HEAP_BUDGET_KIB,
            100.0 * total_kib / HEAP_BUDGET_KIB,
            TARGET_KIB,
        )
    )
    assert total_kib <= HEAP_BUDGET_KIB, (
        "memory %.1f KiB > %.0f KiB" % (total_kib, HEAP_BUDGET_KIB)
    )
    assert total_kib <= TARGET_KIB, (
        "memory %.1f KiB > target %.0f KiB" % (total_kib, TARGET_KIB)
    )

    timings = budget.measure_timing(full)
    timing_passed = True
    print("\nTiming (4800 points, PC median, x100 estimate):")
    print("  %-12s %10s %12s %8s" % ("stage", "PC [ms]", "x100 [ms]", "<=250"))
    for stage, value in zip(budget.STAGES, timings):
        scaled = value * 100.0
        ok = scaled <= 250.0
        if not ok:
            timing_passed = False
        label = "PASS" if ok else ("WARN" if not strict_timing else "FAIL")
        print(
            "  %-12s %10.3f %12.1f %8s"
            % (stage, value, scaled, label)
        )
        if strict_timing and not ok:
            raise AssertionError("%s x100 %.1f ms > 250 ms" % (stage, scaled))
        if not strict_timing and scaled > 500.0:
            raise AssertionError(
                "%s x100 %.1f ms > 500 ms CI sanity bound" % (stage, scaled)
            )
    if not strict_timing:
        print("  CI timing is advisory; use local/target hardware for the 250 ms gate.")
    print(
        "  %-12s %10.3f %12.1f"
        % ("TOTAL", sum(timings), sum(timings) * 100.0)
    )

    print("\nfloat32 simulation (same buffers, baseline vs Float32Hooks):")
    print(
        "  baseline = array('f') storage + float64 arithmetic; "
        "f32 = all quantizer hooks"
    )
    print(
        "  %-13s %12s %12s %12s %8s"
        % ("scenario", "dSBP [mmHg]", "dDBP [mmHg]", "dHR [bpm]", "PASS")
    )
    f32_rows = []
    for name in SCENARIOS:
        scene = _scene_signals(name)
        base = scene["fw"]
        with Float32Hooks():
            f32_result, _analyzer, _steps = run_firmware(scene["buffer"])
        simulated = firmware_signal(f32_result)
        assert simulated["accepted"] == base["accepted"]
        assert simulated["qc_reasons"] == base["qc_reasons"]
        d_sbp = abs(simulated["sbp"] - base["sbp"])
        d_dbp = abs(simulated["dbp"] - base["dbp"])
        d_hr = abs(simulated["hr"] - base["hr"])
        assert d_sbp <= 1.0
        assert d_dbp <= 1.0
        assert d_hr <= 1.0
        f32_rows.append(
            {
                "name": name,
                "d_sbp": d_sbp,
                "d_dbp": d_dbp,
                "d_hr": d_hr,
            }
        )
        print(
            "  %-13s %12.3e %12.3e %12.3e %8s"
            % (name, d_sbp, d_dbp, d_hr, "PASS")
        )

    overview_rows = []
    for name in SCENARIOS:
        scene = _scene_signals(name)
        pc = scene["pc"]
        fw = scene["fw"]
        overview_rows.append(
            {
                "name": name,
                "d_sbp": abs(fw["sbp"] - pc["sbp"]),
                "d_dbp": abs(fw["dbp"] - pc["dbp"]),
            }
        )

    labels = [row["name"] for row in overview_rows]
    positions = list(range(len(labels)))
    width = 0.36
    fig, ax = plt.subplots(figsize=(9, 5.5))
    sbp_values = [max(row["d_sbp"], 1e-9) for row in overview_rows]
    dbp_values = [max(row["d_dbp"], 1e-9) for row in overview_rows]
    sbp_bars = ax.bar(
        [p - width / 2 for p in positions],
        sbp_values,
        width,
        color="#1f77b4",
        label="dSBP",
    )
    dbp_bars = ax.bar(
        [p + width / 2 for p in positions],
        dbp_values,
        width,
        color="#d62728",
        label="dDBP",
    )
    ax.set_yscale("log")
    ax.set_ylim(1e-6, 10.0)
    ax.axhline(
        1.0,
        color="#111111",
        ls="--",
        lw=1.2,
        label="1 mmHg threshold",
    )
    for bars in (sbp_bars, dbp_bars):
        for bar in bars:
            value = bar.get_height()
            ax.annotate(
                "%.1e" % value,
                (bar.get_x() + bar.get_width() / 2, value),
                textcoords="offset points",
                xytext=(0, 4),
                ha="center",
                fontsize=8,
            )
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Absolute error [mmHg] (log scale)")
    ax.set_title("PC vs firmware absolute error (3 scenarios)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="upper left")
    plot_path = outdir / "algo_error_overview.png"
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    assert plot_path.exists() and plot_path.stat().st_size > 0

    print("\nError overview: %s" % plot_path)
    return {
        "memory": {
            "buffer_kib": buffer_kib,
            "analyzer_kib": analyzer_kib,
            "total_kib": total_kib,
            "budget_kib": HEAP_BUDGET_KIB,
            "target_kib": TARGET_KIB,
        },
        "timing": {
            "stages": list(budget.STAGES),
            "pc_ms": list(timings),
            "x100_ms": [value * 100.0 for value in timings],
            "strict": strict_timing,
            "passed": timing_passed,
            "budget_ms": 250.0,
        },
        "f32": f32_rows,
        "error_rows": overview_rows,
        "plot": str(plot_path),
        "passed": True,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    outdir = DEFAULT_OUTPUT_DIR
    acts = (
        ("Act 1 coverify table", act1_coverify_table, ()),
        ("Act 2 envelope plots", act2_envelope_plots, (outdir,)),
        ("Act 3 FSM end-to-end", act3_fsm_end_to_end, ()),
        (
            "Act 4 budget + error overview",
            act4_budget_and_error_overview,
            (outdir,),
        ),
    )
    passed = 0
    print("=" * 108)
    print("Algorithm port proof demo")
    print("=" * 108)
    for title, function, args in acts:
        try:
            result = function(*args)
            assert result.get("passed") is True
            passed += 1
            print("\nPASS %s" % title)
        except Exception as exc:
            print("\nFAIL %s: %r" % (title, exc))
            traceback.print_exc()
    print("\n" + "=" * 108)
    print("%d/%d checks passed" % (passed, len(acts)))
    return 0 if passed == len(acts) else 1


if __name__ == "__main__":
    sys.exit(main())
