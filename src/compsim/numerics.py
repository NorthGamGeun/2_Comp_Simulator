"""수치 해석 루틴 (scipy 대체).

plan.md §1.3 은 scipy 를 의존성으로 두었으나, 개발 샌드박스에서 PyPI 접근이
차단되어 검증 루프를 돌릴 수 없었다. 검증 우선 원칙(§5)을 지키기 위해
필요한 최소 루틴만 자체 구현한다. 알고리즘은 표준 Brent 법으로,
scipy.optimize.brentq 와 동일한 수렴 특성을 갖는다.

의존성을 numpy 하나로 줄이는 부수 효과도 있어 배포가 단순해진다.
"""

from __future__ import annotations

import math
from collections.abc import Callable


class ConvergenceError(RuntimeError):
    """반복 해법이 허용 횟수 내에 수렴하지 못함."""


class BracketError(ValueError):
    """구간 양 끝에서 함수 부호가 같아 근을 가둘 수 없음."""


def brentq(
    f: Callable[[float], float],
    a: float,
    b: float,
    *,
    xtol: float = 2.0e-12,
    rtol: float = 8.881784197001252e-16,
    maxiter: int = 200,
) -> float:
    """[a, b] 구간에서 f(x)=0 의 근을 Brent 법으로 찾는다.

    f(a)*f(b) <= 0 이어야 한다. 아니면 BracketError.
    """
    xpre, xcur = float(a), float(b)
    fpre, fcur = f(xpre), f(xcur)

    if fpre == 0.0:
        return xpre
    if fcur == 0.0:
        return xcur
    if fpre * fcur > 0.0:
        raise BracketError(f"구간 부호 동일: f({a})={fpre:.6g}, f({b})={fcur:.6g}")

    xblk = 0.0
    fblk = 0.0
    spre = 0.0
    scur = 0.0

    for _ in range(maxiter):
        if fpre * fcur < 0.0:
            xblk, fblk = xpre, fpre
            spre = scur = xcur - xpre
        if abs(fblk) < abs(fcur):
            xpre, xcur, xblk = xcur, xblk, xcur
            fpre, fcur, fblk = fcur, fblk, fcur

        delta = (xtol + rtol * abs(xcur)) / 2.0
        sbis = (xblk - xcur) / 2.0

        if fcur == 0.0 or abs(sbis) < delta:
            return xcur

        if abs(spre) > delta and abs(fcur) < abs(fpre):
            if xpre == xblk:  # 할선법
                stry = -fcur * (xcur - xpre) / (fcur - fpre)
            else:  # 역 2차 보간
                dpre = (fpre - fcur) / (xpre - xcur)
                dblk = (fblk - fcur) / (xblk - xcur)
                stry = -fcur * (fblk * dblk - fpre * dpre) / (dblk * dpre * (fblk - fpre))
            if 2.0 * abs(stry) < min(abs(spre), 3.0 * abs(sbis) - delta):
                spre, scur = scur, stry
            else:
                spre = scur = sbis
        else:
            spre = scur = sbis

        xpre, fpre = xcur, fcur
        if abs(scur) > delta:
            xcur += scur
        else:
            xcur += delta if sbis > 0.0 else -delta
        fcur = f(xcur)

    raise ConvergenceError(f"brentq 미수렴 (maxiter={maxiter})")


def find_bracket(
    f: Callable[[float], float],
    lo: float,
    hi: float,
    *,
    n: int = 200,
    log_spacing: bool = False,
) -> tuple[float, float] | None:
    """[lo, hi] 를 스캔해 f 의 부호가 바뀌는 첫 구간을 반환. 없으면 None.

    brentq 호출 전 반드시 이 함수로 구간을 확보한다 (plan.md §3.3 견고성 요구).
    """
    if log_spacing:
        if lo <= 0.0:
            raise ValueError("log_spacing=True 이면 lo > 0 이어야 함")
        xs = [lo * (hi / lo) ** (i / n) for i in range(n + 1)]
    else:
        xs = [lo + (hi - lo) * i / n for i in range(n + 1)]

    f_prev = f(xs[0])
    if f_prev == 0.0:
        return xs[0], xs[0]
    for i in range(1, len(xs)):
        f_cur = f(xs[i])
        if f_cur == 0.0:
            return xs[i], xs[i]
        if math.copysign(1.0, f_prev) != math.copysign(1.0, f_cur):
            return xs[i - 1], xs[i]
        f_prev = f_cur
    return None


def solve_scalar(
    f: Callable[[float], float],
    lo: float,
    hi: float,
    *,
    n_scan: int = 200,
    log_spacing: bool = False,
) -> float | None:
    """구간 탐색 + brentq 를 묶은 안전 래퍼. 실패 시 예외 대신 None."""
    br = find_bracket(f, lo, hi, n=n_scan, log_spacing=log_spacing)
    if br is None:
        return None
    a, b = br
    if a == b:
        return a
    try:
        return brentq(f, a, b)
    except (BracketError, ConvergenceError):
        return None


def golden_min(
    f: Callable[[float], float],
    a: float,
    b: float,
    *,
    tol: float = 1.0e-10,
    maxiter: int = 300,
) -> tuple[float, float]:
    """[a, b] 에서 단봉(unimodal) 함수의 최소점을 황금분할로 탐색.

    반환: (x_min, f(x_min))
    """
    inv_phi = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - inv_phi * (b - a)
    d = a + inv_phi * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(maxiter):
        if abs(b - a) < tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - inv_phi * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + inv_phi * (b - a)
            fd = f(d)
    x = (a + b) / 2.0
    return x, f(x)


def rel_error(actual: float, expected: float) -> float:
    """상대오차. expected 가 0 이면 절대오차를 반환한다."""
    if expected == 0.0:
        return abs(actual)
    return abs(actual - expected) / abs(expected)


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


__all__ = [
    "BracketError",
    "ConvergenceError",
    "brentq",
    "clamp",
    "find_bracket",
    "golden_min",
    "rel_error",
    "solve_scalar",
]
