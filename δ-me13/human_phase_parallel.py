# human_phase_parallel.py — 人类阶段 Numba 批量并行加速
# 将逐实体的 Python 循环替换为 (N,12) 数组 + @njit(parallel=True) + prange

import numpy as np
from numba import njit, prange

# ── 常量（与 constants.py 一致）──────────────────────

# Titan→Path 映射矩阵 (12 titans × 3 paths)
# 由 simulation.py 的 titan_to_path_matrix 在运行时传入
# 12 titans: Kephale, Aquila, Oronyx, Janus, Thanatos, Georios,
#            Talanton, Phagousa, Cerces, Mnestia, Zagreus, Nikador
# 3 paths: Destruction, Erudition, Harmony (简化为 3 维)


# ── 批量 recalculate_concepts ────────────────────────

@njit(parallel=True)
def _batch_recalculate_scores_wrapper(
    all_affinities,        # (N, 12) float64 titan_affinities
    titan_to_path_matrix,  # (12, num_paths) float64 映射矩阵
    path_distribution,     # (num_paths,) float64 命途分布
    zeitgeist_multiplier,  # float64 思潮乘数（全局）
    out_scores,            # (N,) float64 输出评分
    out_dom_idx,           # (N,) int64 输出主导命途索引
):
    """简化版批量评分：activity + stability + purity + saturation + zeitgeist。
    匹配 entities.py recalculate_concepts 的实际公式。"""
    N, T = all_affinities.shape
    num_paths = titan_to_path_matrix.shape[1]
    for i in prange(N):
        aff = all_affinities[i]
        # path_affinities = aff @ titan_to_path_matrix
        pa = np.zeros(num_paths)
        for p in range(num_paths):
            s = 0.0
            for t in range(T):
                s += aff[t] * titan_to_path_matrix[t, p]
            pa[p] = s

        # activity = norm(pa[:6]), stability = norm(pa[6:])
        act = 0.0
        for v in range(min(6, num_paths)):
            act += pa[v] * pa[v]
        act = np.sqrt(act) * 10.0

        stab = 0.0
        for v in range(6, num_paths):
            stab += pa[v] * pa[v]
        stab = np.sqrt(stab) * 10.0

        # purity = max(pa) / norm(pa)
        pa_norm = 0.0
        max_pa = pa[0]
        for v in range(num_paths):
            pv = pa[v]
            pa_norm += pv * pv
            if pv > max_pa:
                max_pa = pv
        pa_norm = np.sqrt(pa_norm)
        if pa_norm < 1e-12:
            pa_norm = 1.0
        purity = max_pa / pa_norm

        base_potential = (act + stab) * (1.0 + purity)

        # saturation_modifier = 1 / (1 + 2 * path_distribution[dom_idx])
        dom_idx = 0
        best = pa[0]
        for v in range(1, num_paths):
            if pa[v] > best:
                best = pa[v]
                dom_idx = v
        dom_penalty = path_distribution[dom_idx]
        sat_mod = 1.0 / (1.0 + 2.0 * dom_penalty)

        score = base_potential * sat_mod * zeitgeist_multiplier
        if not np.isfinite(score):
            score = 0.0

        out_scores[i] = score
        out_dom_idx[i] = dom_idx


# ── 批量 recalculate_and_normalize ───────────────────

@njit(parallel=True)
def _batch_recalculate_and_normalize(
    all_affinities,       # (N, 12) float64 titan_affinities
    purity_factor,        # float64 纯化因子
    cosmic_tide_vector,   # (12,) float64 宇宙潮汐向量
    target_norm,          # float64 归一化目标
):
    """批量执行 internal_purification + tide + clip + normalize。原地修改 all_affinities。"""
    N, T = all_affinities.shape
    for i in prange(N):
        aff = all_affinities[i]

        # internal_purification: 削弱非主导维度
        dom_idx = 0
        dom_val = aff[0]
        for j in range(1, T):
            if aff[j] > dom_val:
                dom_val = aff[j]
                dom_idx = j

        total_other = 0.0
        for j in range(T):
            if j != dom_idx:
                total_other += aff[j]

        if total_other > 0:
            reduce = total_other * purity_factor
            for j in range(T):
                if j != dom_idx:
                    aff[j] -= reduce * (aff[j] / total_other)

        # cosmic_tide_vector
        for j in range(T):
            aff[j] += cosmic_tide_vector[j]
            if aff[j] < 0.0:
                aff[j] = 0.0

        # normalize
        n = 0.0
        for j in range(T):
            n += aff[j] * aff[j]
        n = np.sqrt(n)
        if n < 1e-12:
            n = 1.0
        scale = target_norm / n
        for j in range(T):
            aff[j] *= scale


# ── 批量 parliament 投票 ──────────────────────────────

@njit(parallel=True)
def _batch_parliament_vote(
    all_scores,        # (N,) float64 评分
    all_dom_idx,       # (N,) int64 主导命途索引
    vote_matrix,       # (num_paths, num_paths) float64 投票矩阵
):
    """聚合所有实体的加权投票结果。返回 (num_paths,) 票数分布。"""
    N = len(all_scores)
    num_paths = vote_matrix.shape[0]
    totals = np.zeros(num_paths)
    for i in prange(N):
        if all_scores[i] <= 0 or not np.isfinite(all_scores[i]):
            continue
        dom = all_dom_idx[i]
        weight = all_scores[i]
        # softmax 计算投票权重
        vote_row = vote_matrix[dom]
        v_max = vote_row[0]
        for v in range(1, num_paths):
            if vote_row[v] > v_max:
                v_max = vote_row[v]
        v_exp_sum = 0.0
        for v in range(num_paths):
            v_exp_sum += np.exp(vote_row[v] - v_max)
        if v_exp_sum < 1e-12:
            v_exp_sum = 1.0
        for v in range(num_paths):
            totals[v] += (np.exp(vote_row[v] - v_max) / v_exp_sum) * weight
    return totals


# ── 批量多样性干预 ───────────────────────────────────

@njit(parallel=True)
def _batch_subsidize_minorities(
    all_affinities,     # (N, 12) float64
    all_scores,         # (N,) float64
    all_dom_idx,        # (N,) int64
    path_dist,          # (num_paths,) int64 每命途实体数
    low_threshold,      # float64
    boost_factor,       # float64
):
    """对少数命途实体进行评分增强（原地修改 out_scores）。"""
    N = len(all_scores)
    num_paths = len(path_dist)
    max_per_path = path_dist[0]
    for v in range(1, num_paths):
        if path_dist[v] > max_per_path:
            max_per_path = path_dist[v]

    for i in prange(N):
        dom = all_dom_idx[i]
        if path_dist[dom] < low_threshold * max_per_path:
            all_scores[i] *= boost_factor


@njit(parallel=True)
def _batch_conformity_plague(
    all_scores,         # (N,) float64
    affected_mask,      # (N,) bool
    plague_strength,    # float64
):
    """对受影响实体施加瘟疫惩罚。"""
    N = len(all_scores)
    for i in prange(N):
        if affected_mask[i]:
            all_scores[i] *= plague_strength


# ── 批量实体评分提取 ─────────────────────────────────

def extract_entity_arrays(population):
    """从 Python 实体列表提取 numpy 数组。返回 (affinities, scores, dom_idx)。"""
    N = len(population)
    all_affinities = np.empty((N, 12), dtype=np.float64)
    all_scores = np.empty(N, dtype=np.float64)
    all_dom_idx = np.empty(N, dtype=np.int64)

    for i, entity in enumerate(population):
        all_affinities[i] = entity.titan_affinities
        all_scores[i] = entity.score if np.isfinite(entity.score) else 0.0
        all_dom_idx[i] = entity.dominant_path_idx

    return all_affinities, all_scores, all_dom_idx


def write_back_scores(population, scores, dom_idx=None):
    """将计算结果写回实体对象。"""
    for i, entity in enumerate(population):
        entity.score = float(scores[i])
        if dom_idx is not None:
            entity._cached_dominant_path_idx = int(dom_idx[i])


def write_back_affinities(population, all_affinities):
    """将 affinities 数组写回实体对象。"""
    for i, entity in enumerate(population):
        entity.titan_affinities = all_affinities[i]
