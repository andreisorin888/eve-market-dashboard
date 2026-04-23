"""
Route analysis: compare shortest vs. secure paths, score risk, give recommendation.
Now fully synchronous — no async/await.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from constants import HUB_SYSTEMS, KNOWN_SECURITY
from eve_api import get_route, resolve_names


@dataclass
class SystemNode:
    sys_id:   int
    name:     str
    security: float

    @property
    def sec_class(self) -> str:
        if self.security >= 0.5:  return "highsec"
        if self.security >= 0.1:  return "lowsec"
        return "nullsec"

    @property
    def sec_colour(self) -> str:
        if self.security >= 0.5:  return "#39FF14"
        if self.security >= 0.1:  return "#FFB347"
        return "#FF4444"


@dataclass
class RouteResult:
    flag:        str
    path:        List[SystemNode]
    jumps:       int
    highsec:     int
    lowsec:      int
    nullsec:     int
    risk_score:  float
    est_minutes: float
    risk_label:  str

    @property
    def is_safe(self) -> bool:
        return self.lowsec == 0 and self.nullsec == 0


def _build_nodes(ids: List[int]) -> Dict[int, SystemNode]:
    nodes: Dict[int, SystemNode] = {}
    for sid in ids:
        if sid in KNOWN_SECURITY:
            name, sec = KNOWN_SECURITY[sid]
            nodes[sid] = SystemNode(sid, name, sec)

    unknown = [sid for sid in ids if sid not in nodes]
    if unknown:
        name_map = resolve_names(unknown)
        for sid in unknown:
            nodes[sid] = SystemNode(sid, name_map.get(sid, str(sid)), 0.5)

    return nodes


def _score(path: List[int], nodes: Dict[int, SystemNode], flag: str) -> RouteResult:
    sys_list = [nodes.get(sid, SystemNode(sid, str(sid), 0.5)) for sid in path]
    highsec  = sum(1 for s in sys_list if s.sec_class == "highsec")
    lowsec   = sum(1 for s in sys_list if s.sec_class == "lowsec")
    nullsec  = sum(1 for s in sys_list if s.sec_class == "nullsec")

    raw        = (lowsec * 8) + (nullsec * 15)
    risk_score = min(float(raw), 100.0)

    if   risk_score == 0:    label = "🟢 LOW"
    elif risk_score <= 25:   label = "🟡 MEDIUM"
    elif risk_score <= 60:   label = "🔴 HIGH"
    else:                    label = "💀 EXTREME"

    return RouteResult(
        flag        = flag,
        path        = sys_list,
        jumps       = len(path) - 1,
        highsec     = highsec,
        lowsec      = lowsec,
        nullsec     = nullsec,
        risk_score  = round(risk_score, 1),
        est_minutes = round(len(path) * 0.5, 1),
        risk_label  = label,
    )


def compare_routes(origin: str, destination: str) -> Dict[str, Optional[RouteResult]]:
    """Compare shortest vs secure route between two trade hub names."""
    o_id = HUB_SYSTEMS.get(origin)
    d_id = HUB_SYSTEMS.get(destination)
    if not o_id or not d_id:
        return {"shortest": None, "secure": None}

    short_path  = get_route(o_id, d_id, flag="shortest")
    secure_path = get_route(o_id, d_id, flag="secure")

    all_ids: list[int] = []
    for p in [short_path, secure_path]:
        if p:
            all_ids.extend(p)
    nodes = _build_nodes(list(set(all_ids)))

    return {
        "shortest": _score(short_path,  nodes, "shortest") if short_path  else None,
        "secure":   _score(secure_path, nodes, "secure")   if secure_path else None,
    }


def recommend(routes: Dict[str, Optional[RouteResult]]) -> Optional[RouteResult]:
    """Pick the better route: prefer secure if detour is ≤ 35 % extra jumps."""
    s   = routes.get("shortest")
    sec = routes.get("secure")

    if not s:   return sec
    if not sec: return s
    if s.is_safe: return s

    overhead = (sec.jumps - s.jumps) / max(s.jumps, 1)
    return sec if overhead <= 0.35 else s
