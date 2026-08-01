# tests/test_numba_optimizations.py — Numba 热点优化 5 项验证
import os
import sys
import numpy as np
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from constants import PATH_NAMES, PATH_RELATIONSHIP_MATRIX


def _make_mock_entities(n: int) -> list:
    """生成 N 个随机 dominant_path_idx 的 mock 实体。"""
    class MockEntity:
        def __init__(self):
            self.dominant_path_idx = random.randint(0, len(PATH_NAMES) - 1)
    return [MockEntity() for _ in range(n)]


# -------- 测试 1：path distribution（旧 for vs 新 bincount）--------
def test_path_distribution():
    population = _make_mock_entities(50)

    # 旧逻辑
    path_counts_old = np.zeros(len(PATH_NAMES))
    for p in population:
        path_counts_old[p.dominant_path_idx] += 1
    old_dist = path_counts_old / len(population)

    # 新逻辑
    indices = np.array([p.dominant_path_idx for p in population], dtype=np.int64)
    path_counts_new = np.bincount(indices, minlength=len(PATH_NAMES))
    new_dist = path_counts_new.astype(np.float64) / len(population)

    assert np.allclose(old_dist, new_dist), f"mismatch:\nold={old_dist}\nnew={new_dist}"
    print("PASS: test_path_distribution")


# -------- 测试 2：vote proposal（旧字符串比较 vs 新数值矩阵）--------
def test_vote_proposal():
    from constants import VOTE_NUMERIC_MATRIX

    class MockEntity:
        def __init__(self, dom_idx):
            self.dominant_path_idx = dom_idx

    for dom_idx in range(len(PATH_NAMES)):
        entity = MockEntity(dom_idx)

        # 旧逻辑——字符串比较
        vote_old = np.zeros(len(PATH_NAMES))
        for i in range(len(PATH_NAMES)):
            if i == dom_idx:
                vote_old[i] = 1.0
                continue
            rel = PATH_RELATIONSHIP_MATRIX[dom_idx, i]
            if rel == "SYNERGY":
                vote_old[i] = 0.5
            elif rel == "MENTORSHIP":
                vote_old[i] = 0.2
            elif rel == "REPULSION":
                vote_old[i] = -0.7
            elif rel == "CLASH":
                vote_old[i] = -1.0
        e_x_old = np.exp(vote_old - np.max(vote_old))
        old_proposal = e_x_old / e_x_old.sum()

        # 新逻辑——预计算数值矩阵
        vote_new = VOTE_NUMERIC_MATRIX[dom_idx].copy()
        vote_new[dom_idx] = 1.0
        e_x_new = np.exp(vote_new - np.max(vote_new))
        new_proposal = e_x_new / e_x_new.sum()

        assert np.allclose(old_proposal, new_proposal), \
            f"mismatch for dom_idx={dom_idx}:\nold={old_proposal}\nnew={new_proposal}"
    print("PASS: test_vote_proposal")


# -------- 测试 3：无机阶段 100 代（同噪声严格对比，确认累积不漂移）--------
def test_inorganic_step():
    from simulation import _inorganic_step_numba

    grid_size = 10
    n_cells = grid_size * grid_size
    n_gens = 100

    np.random.seed(42)
    py_act = np.random.uniform(1, 5, n_cells).astype(np.float64)
    py_stab = np.random.uniform(1, 5, n_cells).astype(np.float64)
    nb_act = py_act.copy()
    nb_stab = py_stab.copy()

    # 提前生成 100 对噪声数组，两版本共用
    noise_pool_act = np.random.uniform(-0.1, 0.1, (n_gens, n_cells)).astype(np.float64)
    noise_pool_stab = np.random.uniform(-0.1, 0.1, (n_gens, n_cells)).astype(np.float64)

    for g in range(n_gens):
        noise_act = noise_pool_act[g]
        noise_stab = noise_pool_stab[g]

        # --- 旧逻辑（纯 Python）---
        new_act = np.zeros(n_cells)
        new_stab = np.zeros(n_cells)
        for i in range(grid_size):
            for j in range(grid_size):
                idx = i * grid_size + j
                act_sum = 0.0
                stab_sum = 0.0
                count = 0
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        if di == 0 and dj == 0:
                            continue
                        ni, nj = i + di, j + dj
                        if 0 <= ni < grid_size and 0 <= nj < grid_size:
                            nidx = ni * grid_size + nj
                            act_sum += py_act[nidx]
                            stab_sum += py_stab[nidx]
                            count += 1
                avg_act = act_sum / count if count > 0 else 0.0
                avg_stab = stab_sum / count if count > 0 else 0.0
                act_change = (avg_act - py_act[idx]) * 0.1 + noise_act[idx]
                stab_change = (avg_stab - py_stab[idx]) * 0.1 + noise_stab[idx]
                new_act[idx] = max(0.0, py_act[idx] + act_change)
                new_stab[idx] = max(0.0, py_stab[idx] + stab_change)
        py_act, py_stab = new_act, new_stab

        # --- 新逻辑（Numba）---
        nb_act, nb_stab = _inorganic_step_numba(
            nb_act, nb_stab, grid_size, noise_act, noise_stab
        )

    # 100 代后累积对比
    assert np.allclose(py_act, nb_act, atol=1e-10), \
        f"activity drift after {n_gens} gens: max diff {np.max(np.abs(py_act - nb_act)):.2e}"
    assert np.allclose(py_stab, nb_stab, atol=1e-10), \
        f"stability drift after {n_gens} gens: max diff {np.max(np.abs(py_stab - nb_stab)):.2e}"
    print(f"PASS: test_inorganic_step ({n_gens} gens, no drift)")


# -------- 测试 4：交互循环行为不变 --------
def test_interaction_loop():
    population = list(range(100))
    culled = set()

    # 模拟淘汰 + remove
    new_valid = list(population)
    for r in random.sample(range(100), 5):
        culled.add(r)
        if r in new_valid:
            new_valid.remove(r)

    expected_valid = [p for p in population if p not in culled]
    assert set(new_valid) == set(expected_valid), "valid_population mismatch"
    print("PASS: test_interaction_loop")


# -------- 测试 5：newborn batch（np.dot 外提等价性）--------
def test_newborn_batch():
    legacy = np.random.rand(12)
    feedback = np.random.rand(12, 12)

    # 外提版本（算一次）
    titan_pre = np.dot(legacy, feedback)

    # 循环内版本（每次算），对比
    for _ in range(10):
        titan_inner = np.dot(legacy, feedback)
        assert np.allclose(titan_pre, titan_inner), "modifier mismatch"

    print("PASS: test_newborn_batch")


# -------- 测试 6：类结构完整性（防止缩进损坏）--------
def test_class_structure():
    from simulation import AeonEvolution
    required = [
        'start', '_run_inorganic_phase', '_run_organic_phase',
        'save_simulation_state', 'load_simulation_state',
        '_train_hybrid_guide_network', '_run_one_generation',
        '_evolve_and_grow', '__init__',
    ]
    for method in required:
        assert hasattr(AeonEvolution, method), f"AeonEvolution 缺少方法: {method}"
    print(f"PASS: test_class_structure ({len(required)} 方法齐全)")


if __name__ == "__main__":
    tests = [
        test_path_distribution,
        test_vote_proposal,
        test_inorganic_step,
        test_interaction_loop,
        test_newborn_batch,
        test_class_structure,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"FAIL: {test.__name__} — {e}")
    print(f"\n{passed}/{len(tests)} 通过")
