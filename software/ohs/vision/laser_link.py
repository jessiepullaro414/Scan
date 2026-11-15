"""The row-by-row loop of :func:`ohs.vision.laser._link_tracks`, compiled with numba (optional).

:func:`link_rows` does exactly what :func:`ohs.vision.laser._link_rows` does (the same decisions
from the same floating-point operations, so the same labels), written with arrays instead of
lists so that numba can compile it. Compiled, it takes a fraction of a millisecond where the
Python loop takes several, and it runs without Python's lock, so the two laser threads and the
tracker are not held up by it. ``tests/test_vision_laser_link.py`` compares the two on made-up
candidates and on rendered frames.

Importing this module needs numba; :mod:`ohs.vision.laser` imports it at the first image with
several lines per family, and uses its own Python loop when numba is not installed
(``pip install numba``; the ``fast`` extra of the package) or when the environment variable
``OHS_NUMBA`` is ``0``. The first call compiles (a few seconds, once: the result is cached on
disk beside this file, or in numba's cache folder when that is not writable).

Kept in step with the Python loop by hand: a change to one is a change to both.
"""
from __future__ import annotations

import numpy as np
from numba import njit, types
from numba.typed import Dict


@njit(cache=True, nogil=True)
def link_rows(t, c, sep, gap, step, hist, rad):  # pragma: no cover (compiled)
    """Track label per candidate (-1: dropped or merged away); see ``_link_tracks``.

    ``t`` int64 rows (sorted), ``c`` float64 centres; ``sep``, ``step`` floats; ``gap``, ``hist``,
    ``rad`` ints."""
    n = len(t)
    labs = np.full(n, -1, np.int64)
    if n == 0:
        return labs
    # one ridge: a row's candidates closer than the NMS radius
    drop = np.zeros(n, np.bool_)
    for k in range(1, n):
        if t[k] == t[k - 1] and abs(c[k] - c[k - 1]) < rad and not drop[k - 1]:
            drop[k] = True
    # a track is its label (tracks are numbered as they start); per track: last t, last c, slope,
    # real points, and its last ``hist`` points in a ring
    tr_t = np.empty(n, np.int64)
    tr_c = np.empty(n, np.float64)
    tr_s = np.empty(n, np.float64)
    tr_n = np.empty(n, np.int64)
    rec_t = np.empty((n, hist), np.int64)
    rec_c = np.empty((n, hist), np.float64)
    rec_len = np.empty(n, np.int64)
    rec_first = np.empty(n, np.int64)
    live = np.empty(n, np.int64)  # the tracks still followed, oldest first
    n_live = 0
    n_tracks = 0
    pred = np.empty(n, np.float64)  # by position in ``live``
    busy_t = np.zeros(n, np.bool_)  # by position in ``live``
    busy_c = np.zeros(n, np.bool_)  # by candidate
    here = np.empty(n, np.int64)
    pe = np.empty(64, np.float64)  # candidate-track pairs within reach: error, position in live, candidate
    pq = np.empty(64, np.int64)
    pk = np.empty(64, np.int64)
    # pair of labels (a * n + b, a < b) -> last row with both, rows with both
    tg_last = Dict.empty(types.int64, types.int64)
    tg_cnt = Dict.empty(types.int64, types.int64)
    merging = Dict.empty(types.int64, types.int64)  # the pairs carried on through a merge (a set)
    still = Dict.empty(types.int64, types.int64)
    i = 0
    while i < n:
        row = t[i]
        k1 = i + 1
        while k1 < n and t[k1] == row:
            k1 += 1
        m = 0
        for a in range(n_live):
            q = live[a]
            if row - tr_t[q] <= gap:
                live[m] = q
                m += 1
        n_live = m
        for a in range(n_live):
            q = live[a]
            pred[a] = tr_c[q] + tr_s[q] * (row - tr_t[q])
            busy_t[a] = False
        # two lines closer than the separation: drop what is near them, carry both on
        if len(still) > 0:
            still.clear()
        for a in range(n_live):
            qa = live[a]
            for b in range(a + 1, n_live):
                if abs(pred[a] - pred[b]) >= sep:
                    continue
                qb = live[b]
                key = qa * n + qb if qa < qb else qb * n + qa
                if key not in merging:
                    if tr_n[qa] < hist or tr_n[qb] < hist:
                        continue
                    if key not in tg_last:
                        continue
                    if row - tg_last[key] > gap or tg_cnt[key] < 3:
                        continue
                still[key] = 1
                for side in range(2):
                    pos = a if side == 0 else b
                    busy_t[pos] = True
                    for k in range(i, k1):
                        if not drop[k] and abs(c[k] - pred[pos]) < sep:
                            busy_c[k] = True
        for a in range(n_live):
            if busy_t[a]:  # coast
                q = live[a]
                tr_c[q] = pred[a]
                tr_t[q] = row
        merging, still = still, merging
        # the rest: best prediction first
        n_pairs = 0
        for k in range(i, k1):
            if drop[k] or busy_c[k]:
                continue
            ck = c[k]
            for a in range(n_live):
                if not busy_t[a]:
                    e = abs(ck - pred[a])
                    if e <= step * (row - tr_t[live[a]]) + 0.3:
                        if n_pairs == len(pe):
                            pe = np.concatenate((pe, pe))
                            pq = np.concatenate((pq, pq))
                            pk = np.concatenate((pk, pk))
                        pe[n_pairs] = e
                        pq[n_pairs] = a
                        pk[n_pairs] = k
                        n_pairs += 1
        for j in range(1, n_pairs):  # sort by (error, track, candidate)
            e, a, k = pe[j], pq[j], pk[j]
            w = j - 1
            while w >= 0 and (pe[w] > e or (pe[w] == e and (pq[w] > a or (pq[w] == a and pk[w] > k)))):
                pe[w + 1], pq[w + 1], pk[w + 1] = pe[w], pq[w], pk[w]
                w -= 1
            pe[w + 1], pq[w + 1], pk[w + 1] = e, a, k
        for j in range(n_pairs):
            a, k = pq[j], pk[j]
            if busy_t[a] or busy_c[k]:
                continue
            busy_t[a] = True
            busy_c[k] = True
            q = live[a]
            ck = c[k]
            if rec_len[q] < hist:
                w = (rec_first[q] + rec_len[q]) % hist
                rec_len[q] += 1
            else:
                w = rec_first[q]
                rec_first[q] = (w + 1) % hist
            rec_t[q, w] = row
            rec_c[q, w] = ck
            t0 = rec_t[q, rec_first[q]]
            c0 = rec_c[q, rec_first[q]]
            if row - t0 >= 3:
                tr_s[q] = (ck - c0) / (row - t0)
            tr_t[q] = row
            tr_c[q] = ck
            tr_n[q] += 1
            labs[k] = q
        n_here = 0
        for k in range(i, k1):
            if drop[k]:
                continue
            if not busy_c[k]:
                q = n_tracks
                n_tracks += 1
                tr_t[q] = row
                tr_c[q] = c[k]
                tr_s[q] = 0.0
                tr_n[q] = 1
                rec_t[q, 0] = row
                rec_c[q, 0] = c[k]
                rec_len[q] = 1
                rec_first[q] = 0
                live[n_live] = q
                n_live += 1
                labs[k] = q
            if labs[k] >= 0:
                here[n_here] = labs[k]
                n_here += 1
        if n_here > 1:
            for j in range(1, n_here):
                v = here[j]
                w = j - 1
                while w >= 0 and here[w] > v:
                    here[w + 1] = here[w]
                    w -= 1
                here[w + 1] = v
            for a in range(n_here):
                for b in range(a + 1, n_here):
                    key = here[a] * n + here[b]
                    tg_last[key] = row
                    tg_cnt[key] = tg_cnt.get(key, 0) + 1
        i = k1
    return labs


@njit(cache=True)
def fused_multiply_add_probe(a, b, c):  # pragma: no cover (compiled)
    """``a * b + c`` as the compiler does it: the test checks that it is not fused into one
    rounding, which would make the compiled loop differ from Python in the last bit."""
    return a * b + c
