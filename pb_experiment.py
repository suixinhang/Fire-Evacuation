#!/usr/bin/env python
"""
Plackett-Burman 实验脚本（PB 12-Run, 9 因子 + 2 Dummy）
自动化运行仿真 → 解析输出 → 统计分析 → Pareto Chart

用法: python pb_experiment.py
"""

import subprocess
import sys
import os
import re
import csv
import pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

# ======================================================================
# 基础配置 (用户修改区)
# ======================================================================
PROJECT_ROOT = pathlib.Path(__file__).parent.absolute()
N_RESPONDERS = 2  # 救援人员总数

# 地图字典: map_1 ~ map_8 对应的 pkl 文件名 (修改这里的值为实际地图文件)
MAP_DICT = {
    1: "1780404492_2_smaller",  # TODO: 填入实际 pkl 文件名
    2: "1780404508_2_smaller",
    3: "1780404492_1_larger",
    4: "1780404508_1_larger",
    5: "1780404439_2_smaller",
    6: "1780404461_1_smaller",
    7: "1780404439_1_larger",
    8: "1780404461_2_larger",
}

# 每张地图对应的固定火源坐标（从 pkl 文件中读取的原始 fire_origin）
FIRE_ORIGIN_DICT = {
    "1780404492_2_smaller": (24, 15),
    "1780404508_2_smaller": (24, 16),
    "1780404492_1_larger":  (24, 15),
    "1780404508_1_larger":  (24, 16),
    "1780404439_2_smaller": (24, 15),
    "1780404461_1_smaller": (24, 16),
    "1780404439_1_larger":  (24, 15),
    "1780404461_2_larger":  (24, 16),
}

# ======================================================================
# 因子水平定义
# ======================================================================
# 因子 1-3: 地图选择 (F1=0/1, F2=0/1, F3=0/1 → map_1 ~ map_8)
# 因子 4: responder 开始移动时间 (move-after)
F4_MINUS = 450
F4_PLUS = 510

# 因子 5: 烟雾衰减系数 (decay_factor)
F5_MINUS = 0.9996
F5_PLUS = 0.9998

# 因子 6: 烟雾扩散系数 (diffusion)
F6_MINUS = 6.8e-5
F6_PLUS = 2.1e-4

# 因子 7: 烟雾粘度 (viscosity)
F7_MINUS = 1.85e-5
F7_PLUS = 4.05e-5

# 因子 8: 烟雾生成速率 (inismoke)
F8_MINUS = 0.001
F8_PLUS = 0.1

# 因子 9: 烟雾推力强度 (strength)
F9_MINUS = 0.345
F9_PLUS = 0.525

# ======================================================================
# PB 12-Run 标准设计矩阵 (11 列: F1-F9 + D1-D2)
# + → 1, - → -1 地图1代表+，地图2为-；更分散代表+，更聚集代表-，人更多代表+，人更少代表-
# ======================================================================
PB_MATRIX = np.array([
    # F1  F2  F3  F4  F5  F6  F7  F8  F9 D10 D11
    [ 1,  1, -1,  1,  1,  1, -1, -1, -1,  1, -1],  # Run 1
    [-1,  1,  1, -1,  1,  1,  1, -1, -1, -1,  1],  # Run 2
    [ 1, -1,  1,  1, -1,  1,  1,  1, -1, -1, -1],  # Run 3
    [-1,  1, -1,  1,  1, -1,  1,  1,  1, -1, -1],  # Run 4
    [-1, -1,  1, -1,  1,  1, -1,  1,  1,  1, -1],  # Run 5
    [-1, -1, -1,  1, -1,  1,  1, -1,  1,  1,  1],  # Run 6
    [ 1, -1, -1, -1,  1, -1,  1,  1, -1,  1,  1],  # Run 7
    [ 1,  1, -1, -1, -1,  1, -1,  1,  1, -1,  1],  # Run 8
    [ 1,  1,  1, -1, -1, -1,  1, -1,  1,  1, -1],  # Run 9
    [-1,  1,  1,  1, -1, -1, -1,  1, -1,  1,  1],  # Run 10
    [ 1, -1,  1,  1,  1, -1, -1, -1,  1, -1,  1],  # Run 11
    [-1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1],  # Run 12
])

FACTOR_NAMES = ["F1(Map)", "F2(ANN)", "F3(Occupants)", "F4(Move)", "F5(Decay)",
                "F6(Diff)", "F7(Visc)", "F8(Smoke)", "F9(Strength)", "D10", "D11"]
REAL_FACTORS = [0, 1, 2, 3, 4, 5, 6, 7, 8]  # indices of real factors
DUMMY_FACTORS = [9, 10]  # indices of dummy factors
N_FACTORS = 11
N_RUNS = 12


def get_map_id(row):
    """根据 F1, F2, F3 编码选择地图 (3-bit → 1-8)"""
    f1 = 1 if row[0] == 1 else 0
    f2 = 1 if row[1] == 1 else 0
    f3 = 1 if row[2] == 1 else 0
    map_idx = f1 + 2 * f2 + 4 * f3 + 1
    return MAP_DICT[map_idx]


def get_param_value(row, col_idx, minus_val, plus_val):
    """根据 PB 矩阵的 +/- 返回对应参数值"""
    return plus_val if row[col_idx] == 1 else minus_val


def parse_output(stdout_text, stderr_text):
    """
    解析仿真输出文本，提取 Y1, Y2, Y3。
    返回 (Y1, Y2, Y3) 或 None（解析失败时）
    """
    combined = stdout_text + "\n" + stderr_text

    patterns = {
        "Y1": r"\[STAT\]\s+Occupant Rescue Duration:\s+([\d.]+)s",
        "Y2": r"\[STAT\]\s+Last Responder Exit Duration:\s+([\d.]+)s",
        "Y3": r"\[STAT\]\s+Avg Responder Exit Duration:\s+([\d.]+)s",
    }

    results = {}
    for key, pat in patterns.items():
        m = re.search(pat, combined)
        if m:
            results[key] = float(m.group(1))
        else:
            results[key] = None
    return results


def run_single_experiment(run_idx, row, map_id, move_after,
                          diffusion, viscosity, strength,
                          inismoke, decay_factor):
    """
    在子进程中运行一次仿真。
    通过设置环境变量 + monkey-patch SmokeSimulation.step 传入烟雾参数。
    """
    # 获取当前地图的固定火源坐标
    fire_origin = FIRE_ORIGIN_DICT.get(map_id)
    fire_origin_str = f"{fire_origin[0]},{fire_origin[1]}" if fire_origin else ""

    env_vars = {
        "FIRE_EVACUATION_DEBUG": "0",
        "FIRE_EVACUATION_NO_RECORD": "1",
        "FIRE_EVACUATION_DISABLE_INIT_FIRE": "0",
        "FIRE_EVACUATION_DISABLE_INIT_PROBABILITY": "0",
        "FIRE_EVACUATION_USE_MAP_ID": map_id,
        "FIRE_EVACUATION_RANDOM_OCCUPANT": "0",
        "FIRE_EVACUATION_RESPONDER_CNT": str(N_RESPONDERS),
        "FIRE_EVACUATION_SPEED": "1.0",
        "FIRE_EVACUATION_MOVE_AFTER": str(move_after),
        "FIRE_EVACUATION_USE_OLD_FIRE_SIMULATION": "",
        "FIRE_EVACUATION_SAVE_MAP": "0",
        # 固定火源坐标（跳过随机选取）
        "FIRE_EVACUATION_FIRE_ORIGIN_POS": fire_origin_str,
        # 烟雾参数传入环境变量，供 monkey-patch 读取
        "PB_DIFFUSION": str(diffusion),
        "PB_VISCOSITY": str(viscosity),
        "PB_STRENGTH": str(strength),
        "PB_INISMOKE": str(inismoke),
        "PB_DECAY_FACTOR": str(decay_factor),
        "PB_DX": "0.5",
        # 为每个 run 分配独立 HTTP 端口，方便在浏览器中实时观看
        "FIRE_EVACUATION_HTTP_PORT": str(8001 + run_idx),
    }

    # 构造子进程内运行的 Python 代码
    runner_code = """
import os
import sys

# 注入环境变量（由父进程环境继承，此处仅作兜底）

# Monkey-patch SmokeSimulation.step —— 在 import algorithm_simulation 之前执行
import smoke_simulation

_original_step = smoke_simulation.SmokeSimulation.step

def _patched_step(self, fire_origins, dt=None, **kwargs):
    kwargs["diffusion"] = float(os.environ.get("PB_DIFFUSION", "0.1"))
    kwargs["viscosity"] = float(os.environ.get("PB_VISCOSITY", "0.0001"))
    kwargs["strength"] = float(os.environ.get("PB_STRENGTH", "0.5"))
    kwargs["inismoke"] = float(os.environ.get("PB_INISMOKE", "0.01"))
    kwargs["decay_factor"] = float(os.environ.get("PB_DECAY_FACTOR", "0.9996"))
    kwargs["dx"] = float(os.environ.get("PB_DX", "0.5"))
    return _original_step(self, fire_origins, dt=dt, **kwargs)

smoke_simulation.SmokeSimulation.step = _patched_step

# 导入 algorithm_simulation 即触发完整仿真流程
import algorithm_simulation
"""

    # 构建完整环境变量（继承当前进程且覆盖）
    full_env = {**os.environ, **env_vars}

    # 让子进程在项目根目录下运行
    cwd = str(PROJECT_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", runner_code],
        capture_output=True, text=True, cwd=cwd, env=full_env,
        timeout=1200,  # 10 分钟超时
    )

    print(f"\n{'─'*60}")
    print(f"Run {run_idx + 1}/{N_RUNS} | map={map_id} | move_after={move_after}")
    print(f"  smoke: diff={diffusion:.2e} visc={viscosity:.2e} "
          f"strength={strength} inismoke={inismoke} decay={decay_factor}")
    print(f"  [WEB] http://localhost:{8001 + run_idx}")
    if result.stdout:
        # 只打印关键 [STAT] 行
        for line in result.stdout.splitlines():
            if "[STAT]" in line or "ERROR" in line.upper():
                print(f"  {line.strip()}")
    if result.stderr and "Traceback" in result.stderr:
        print(f"  [ERROR] stderr:\n{result.stderr[:500]}")

    return {
        "run": run_idx + 1,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def run_all_experiments():
    """运行全部 12 次实验"""
    results = []
    for run_idx in range(N_RUNS):
        row = PB_MATRIX[run_idx]

        map_id = get_map_id(row)
        move_after = get_param_value(row, 3, F4_MINUS, F4_PLUS)
        decay_factor = get_param_value(row, 4, F5_MINUS, F5_PLUS)
        diffusion = get_param_value(row, 5, F6_MINUS, F6_PLUS)
        viscosity = get_param_value(row, 6, F7_MINUS, F7_PLUS)
        inismoke = get_param_value(row, 7, F8_MINUS, F8_PLUS)
        strength = get_param_value(row, 8, F9_MINUS, F9_PLUS)

        raw = run_single_experiment(
            run_idx, row, map_id, move_after,
            diffusion, viscosity, strength, inismoke, decay_factor
        )

        parsed = parse_output(raw["stdout"], raw["stderr"])

        result_row = {
            "Run": run_idx + 1,
            **{f"F{j+1}": row[j] for j in range(N_FACTORS)},
            "Map": map_id,
            "MoveAfter": move_after,
            "Diffusion": diffusion,
            "Viscosity": viscosity,
            "Strength": strength,
            "Inismoke": inismoke,
            "DecayFactor": decay_factor,
            "Y1_OccupantRescue": parsed.get("Y1"),
            "Y2_LastResponderExit": parsed.get("Y2"),
            "Y3_AvgResponderExit": parsed.get("Y3"),
        }
        results.append(result_row)

    return results


def compute_effects(pb_matrix, y_values, real_indices, dummy_indices):
    """
    计算每个因子的效应值 (effect = mean(Y|+) - mean(Y|-))
    返回 effects 数组和 dummy 效应列表
    """
    n_factors = pb_matrix.shape[1]
    effects = np.zeros(n_factors)

    for j in range(n_factors):
        col = pb_matrix[:, j]
        y_plus = y_values[col == 1]
        y_minus = y_values[col == -1]
        if len(y_plus) > 0 and len(y_minus) > 0:
            effects[j] = np.mean(y_plus) - np.mean(y_minus)
        else:
            effects[j] = np.nan

    return effects


def pb_analysis(results, pb_matrix):
    """对 Y1, Y2, Y3 分别进行 PB 统计分析，返回分析结果"""
    y_labels = ["Y1_OccupantRescue", "Y2_LastResponderExit", "Y3_AvgResponderExit"]
    y_titles = [
        "Y1: Last Occupant Rescue Time",
        "Y2: Last Responder Exit Time",
        "Y3: Avg Responder Exit Time",
    ]

    analyses = {}

    for yi, (yl, yt) in enumerate(zip(y_labels, y_titles)):
        # 提取有效响应值
        y_raw = np.array([r[yl] for r in results], dtype=float)
        valid_mask = ~np.isnan(y_raw)
        if not np.any(valid_mask):
            print(f"[WARN] {yl}: 所有值为 N/A，跳过分析")
            analyses[yl] = None
            continue

        y_valid = y_raw[valid_mask]
        pb_valid = pb_matrix[valid_mask]

        effects = compute_effects(pb_valid, y_valid, REAL_FACTORS, DUMMY_FACTORS)

        # Dummy 效应用于标准误估计
        dummy_effects_sq = []
        for di in DUMMY_FACTORS:
            if not np.isnan(effects[di]):
                dummy_effects_sq.append(effects[di] ** 2)

        if len(dummy_effects_sq) == 0:
            print(f"[WARN] {yl}: 所有 Dummy 效应为 NaN，无法估计 SE")
            analyses[yl] = None
            continue

        n_dummy = len(dummy_effects_sq)
        se = np.sqrt(np.sum(dummy_effects_sq) / n_dummy)
        df = n_dummy
        t_critical = stats.t.ppf(0.975, df=df)  # 双尾 95%
        threshold = t_critical * se

        # 只对真实因子计算效应、显著性
        real_effects = effects[REAL_FACTORS]
        real_names = [FACTOR_NAMES[i] for i in REAL_FACTORS]
        significant = np.abs(real_effects) > threshold

        print(f"\n{'='*60}")
        print(f"PB Analysis: {yt}")
        print(f"  Dummy effects: {[round(effects[di], 4) for di in DUMMY_FACTORS]}")
        print(f"  SE (from dummies): {se:.4f}")
        print(f"  df (n_dummy): {df}")
        print(f"  t(0.975, {df}): {t_critical:.4f}")
        print(f"  Significance threshold (95%): {threshold:.4f}")
        print(f"  {'Factor':<20} {'Effect':>10} {'|Effect|':>10} {'Signif?'}")
        for i, name in enumerate(real_names):
            sig_mark = "***" if significant[i] else ""
            print(f"  {name:<20} {real_effects[i]:>10.4f} "
                  f"{abs(real_effects[i]):>10.4f} {sig_mark}")

        analyses[yl] = {
            "title": yt,
            "effects": real_effects,
            "names": real_names,
            "se": se,
            "t_critical": t_critical,
            "threshold": threshold,
            "significant": significant,
            "df": df,
        }

    return analyses


def plot_pareto(analysis, output_path):
    """绘制单张 Pareto Chart"""
    if analysis is None:
        print(f"[SKIP] 无数据可绘图 → {output_path}")
        return

    effects = analysis["effects"]
    names = analysis["names"]
    threshold = analysis["threshold"]
    significant = analysis["significant"]
    title = analysis["title"]

    # 按 |effect| 降序排列
    sorted_indices = np.argsort(np.abs(effects))[::-1]
    sorted_effects = effects[sorted_indices]
    sorted_names = [names[i] for i in sorted_indices]
    sorted_sig = significant[sorted_indices]

    fig, ax = plt.subplots(figsize=(12, 7))
    colors = ["#d62728" if s else "#1f77b4" for s in sorted_sig]
    bars = ax.bar(range(len(sorted_effects)), np.abs(sorted_effects), color=colors, edgecolor="white")

    # 标注原始效应值（带符号）
    for i, (eff, name) in enumerate(zip(sorted_effects, sorted_names)):
        y_pos = abs(eff) + max(abs(sorted_effects)) * 0.02
        ax.text(i, y_pos, f"{eff:+.3f}", ha="center", va="bottom",
                fontsize=9, fontweight="bold" if sorted_sig[i] else "normal",
                color=colors[i])

    # 显著性阈值红线
    ax.axhline(y=threshold, color="red", linestyle="--", linewidth=2,
               label=f"Significance threshold (95%): {threshold:.3f}")
    ax.legend(loc="upper right", fontsize=10)

    ax.set_xticks(range(len(sorted_effects)))
    ax.set_xticklabels(sorted_names, rotation=45, ha="right", fontsize=10)
    ax.set_ylabel("Absolute Effect", fontsize=12)
    ax.set_title(f"Pareto Chart of Standardized Effects\n{title}", fontsize=14)
    ax.set_xlim(-0.5, len(sorted_effects) - 0.5)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[OK] Pareto chart saved → {output_path}")


def save_results_csv(results, output_path):
    """保存 12 次实验结果到 CSV"""
    if not results:
        print("[WARN] 无实验数据可保存")
        return

    fieldnames = list(results[0].keys())
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"[OK] Results CSV saved → {output_path}")


def main():
    print("=" * 70)
    print("  Plackett-Burman Experiment (12-Run, 9+2 Dummy)")
    print(f"  Responders: {N_RESPONDERS}")
    print(f"  Maps: {MAP_DICT}")
    print("=" * 70)

    # ---- 第 1 步: 运行 12 次实验 ----
    print("\n[Phase 1] Running 12 experiments...")
    results = run_all_experiments()

    # ---- 第 2 步: 保存 CSV ----
    print("\n[Phase 2] Saving results...")
    out_dir = PROJECT_ROOT / "pb_output" / f"responder_{N_RESPONDERS}"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "pb_results.csv"
    save_results_csv(results, str(csv_path))

    # ---- 第 3 步: 统计分析 ----
    print("\n[Phase 3] Statistical analysis...")
    analyses = pb_analysis(results, PB_MATRIX)

    # ---- 第 4 步: 绘制 Pareto Chart ----
    print("\n[Phase 4] Plotting Pareto Charts...")
    plot_pareto(analyses.get("Y1_OccupantRescue"),
                str(out_dir / "pb_pareto_Y1.png"))
    plot_pareto(analyses.get("Y2_LastResponderExit"),
                str(out_dir / "pb_pareto_Y2.png"))
    plot_pareto(analyses.get("Y3_AvgResponderExit"),
                str(out_dir / "pb_pareto_Y3.png"))

    print("\n" + "=" * 70)
    print("  PB Experiment Complete!")
    print(f"  Results CSV : {csv_path}")
    print(f"  Pareto Y1   : {out_dir / 'pb_pareto_Y1.png'}")
    print(f"  Pareto Y2   : {out_dir / 'pb_pareto_Y2.png'}")
    print(f"  Pareto Y3   : {out_dir / 'pb_pareto_Y3.png'}")
    print("=" * 70)


if __name__ == "__main__":
    main()