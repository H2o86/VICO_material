# -*- coding: utf-8 -*-
"""
Backend FastAPI cho Phần mềm Tra cứu Mác Thép & Gang Nội bộ Công ty Đúc
Dữ liệu nền: Key to Steel v2005.11 (40.163 mác quốc tế) + Mác nội bộ Công ty.
"""

import io
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "kts_database.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(
    title="Hệ thống Tra cứu Mác Thép & Gang - Công ty Đúc",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_code(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


# --- Pydantic Models ---
class ChemElementInput(BaseModel):
    element: str
    c_min: Optional[float] = None
    c_max: Optional[float] = None
    c_approx: Optional[float] = None
    internal_min: Optional[float] = None
    internal_max: Optional[float] = None
    c_text: Optional[str] = ""


class MechPropInput(BaseModel):
    dimen: Optional[str] = ""
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    t_min: Optional[float] = None
    t_max: Optional[float] = None
    e_min: Optional[float] = None
    i_min: Optional[float] = None
    hardness: Optional[str] = ""


class InternalMaterialPayload(BaseModel):
    material: str
    internal_code: Optional[str] = ""
    standard: Optional[str] = "NOI_BO"
    country: Optional[str] = "Việt Nam (Nội bộ Công ty)"
    grp_code: Optional[int] = 6
    subgroup: Optional[str] = ""
    subgroup_desc: Optional[str] = ""
    comment: Optional[str] = ""
    application: Optional[str] = ""
    product_form: Optional[str] = ""
    heat_treatment: Optional[str] = ""
    casting_notes: Optional[str] = ""
    equivalent_vk_key: Optional[int] = None
    crt_num: Optional[int] = None
    chem: List[ChemElementInput] = []
    mech: List[MechPropInput] = []


class SpectrometerRequest(BaseModel):
    elements: Dict[str, float]
    tolerance_pct: float = 15.0
    standard: Optional[str] = None
    grp_code: Optional[int] = None
    only_internal: bool = False
    limit: int = 50


# --- API Endpoints ---

@app.get("/api/meta")
def get_metadata():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT s.standard, s.country, s.is_internal, COUNT(m.vk_key) as mat_count
        FROM standards s
        LEFT JOIN materials m ON m.standard = s.standard
        GROUP BY s.standard
        ORDER BY s.is_internal DESC, mat_count DESC, s.standard ASC
        """
    )
    standards = [dict(r) for r in cur.fetchall()]

    cur.execute(
        """
        SELECT g.grp_code, g.grp_desc, g.grp_desc_vi, COUNT(m.vk_key) as mat_count
        FROM steel_groups g
        LEFT JOIN materials m ON m.grp_code = g.grp_code
        GROUP BY g.grp_code
        ORDER BY g.grp_code ASC
        """
    )
    groups = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) FROM materials")
    total_materials = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM materials WHERE is_internal = 1")
    internal_materials = cur.fetchone()[0]

    conn.close()
    return {
        "total_materials": total_materials,
        "internal_materials": internal_materials,
        "standards": standards,
        "steel_groups": groups,
        "common_elements": [
            "C", "Si", "Mn", "P", "S", "Cr", "Ni", "Mo", "Cu", "V", "W", "Al", "Ti", "Nb", "B", "Mg", "Co", "N"
        ],
    }


@app.get("/api/materials")
def search_materials(
    q: str = Query("", description="Từ khóa tên mác thép, mã nội bộ hoặc tiêu chuẩn"),
    standard: str = Query("", description="Lọc theo bộ tiêu chuẩn"),
    grp_code: Optional[int] = Query(None, description="Lọc theo nhóm vật liệu (1..10)"),
    only_internal: bool = Query(False, description="Chỉ hiện mác nội bộ công ty"),
    y_min: Optional[float] = Query(None, description="Giới hạn chảy tối thiểu Re (MPa)"),
    t_min: Optional[float] = Query(None, description="Độ bền kéo tối thiểu Rm (MPa)"),
    t_max: Optional[float] = Query(None, description="Độ bền kéo tối đa Rm (MPa)"),
    e_min: Optional[float] = Query(None, description="Độ giãn dài tối thiểu A (%)"),
    i_min: Optional[float] = Query(None, description="Độ dai va đập tối thiểu KV (J)"),
    hardness_q: str = Query("", description="Từ khóa độ cứng (VD: HB, HRC, 200)"),
    ht_q: str = Query("", description="Từ khóa nhiệt luyện / ứng dụng / ghi chú đúc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=5, le=200),
):
    conn = get_db()
    cur = conn.cursor()

    where_clauses = []
    params = []

    q_clean = q.strip()
    q_norm = normalize_code(q_clean)

    if q_clean:
        where_clauses.append(
            "(m.material_norm LIKE ? OR m.material LIKE ? OR m.internal_code LIKE ? OR m.subgroup LIKE ?)"
        )
        params.extend([f"%{q_norm}%", f"%{q_clean}%", f"%{q_clean}%", f"%{q_clean}%"])

    if standard:
        where_clauses.append("m.standard = ?")
        params.append(standard)

    if grp_code is not None:
        where_clauses.append("m.grp_code = ?")
        params.append(grp_code)

    if only_internal:
        where_clauses.append("m.is_internal = 1")

    if ht_q.strip():
        hq = f"%{ht_q.strip()}%"
        where_clauses.append(
            "(m.heat_treatment LIKE ? OR m.application LIKE ? OR m.comment LIKE ? OR m.casting_notes LIKE ? OR m.subgroup_desc LIKE ?)"
        )
        params.extend([hq, hq, hq, hq, hq])

    # Lọc theo cơ tính nếu có bất kỳ điều kiện cơ tính nào
    mech_conds = []
    mech_params = []
    if y_min is not None:
        mech_conds.append("(mp.y_min >= ? OR mp.y_max >= ? OR mp.y_approx >= ?)")
        mech_params.extend([y_min, y_min, y_min])
    if t_min is not None:
        mech_conds.append("(mp.t_min >= ? OR mp.t_max >= ? OR mp.t_approx >= ?)")
        mech_params.extend([t_min, t_min, t_min])
    if t_max is not None:
        mech_conds.append("(mp.t_min <= ? OR mp.t_max <= ? OR mp.t_approx <= ?)")
        mech_params.extend([t_max, t_max, t_max])
    if e_min is not None:
        mech_conds.append("(mp.e_min >= ? OR mp.e_max >= ? OR mp.e_approx >= ?)")
        mech_params.extend([e_min, e_min, e_min])
    if i_min is not None:
        mech_conds.append("(mp.i_min >= ? OR mp.i_max >= ? OR mp.i_approx >= ?)")
        mech_params.extend([i_min, i_min, i_min])
    if hardness_q.strip():
        mech_conds.append("mp.hardness LIKE ?")
        mech_params.append(f"%{hardness_q.strip()}%")

    if mech_conds:
        sub_sql = " AND ".join(mech_conds)
        where_clauses.append(f"EXISTS (SELECT 1 FROM mech_props mp WHERE mp.vk_key = m.vk_key AND {sub_sql})")
        params.extend(mech_params)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Đếm tổng số kết quả
    cur.execute(f"SELECT COUNT(*) FROM materials m {where_sql}", params)
    total = cur.fetchone()[0]

    # Sắp xếp thông minh: Ưu tiên khớp chính xác mã chuẩn hóa -> khớp đầu chuỗi -> mác nội bộ
    order_params = []
    if q_norm:
        order_sql = """
        ORDER BY
            CASE
                WHEN m.material_norm = ? THEN 0
                WHEN m.internal_code = ? THEN 1
                WHEN m.is_internal = 1 AND m.material_norm LIKE ? THEN 2
                WHEN m.material_norm LIKE ? THEN 3
                WHEN m.is_internal = 1 THEN 4
                ELSE 5
            END,
            LENGTH(m.material) ASC,
            m.material ASC
        """
        order_params = [q_norm, q_clean.upper(), f"{q_norm}%", f"{q_norm}%"]
    else:
        order_sql = "ORDER BY m.is_internal DESC, m.material ASC"

    offset = (page - 1) * page_size
    sql = f"""
        SELECT
            m.vk_key, m.material, m.standard, m.country, m.grp_code,
            g.grp_desc_vi, m.subgroup, m.subgroup_desc,
            m.chem_preview, m.mech_preview,
            m.is_internal, m.internal_code, m.casting_notes
        FROM materials m
        LEFT JOIN steel_groups g ON g.grp_code = m.grp_code
        {where_sql}
        {order_sql}
        LIMIT ? OFFSET ?
    """
    cur.execute(sql, params + order_params + [page_size, offset])
    items = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@app.get("/api/materials/{vk_key}")
def get_material_detail(vk_key: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT m.*, g.grp_desc, g.grp_desc_vi
        FROM materials m
        LEFT JOIN steel_groups g ON g.grp_code = m.grp_code
        WHERE m.vk_key = ?
        """,
        (vk_key,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy mác vật liệu")

    mat = dict(row)

    # Lấy danh sách tiêu chuẩn nhánh (mat_specs)
    cur.execute(
        "SELECT * FROM mat_specs WHERE vk_key = ? ORDER BY key_num ASC",
        (vk_key,),
    )
    specs = [dict(r) for r in cur.fetchall()]
    spec_map = {s["key_num"]: s["subgroup"] for s in specs}

    # Lấy thành phần hóa học
    cur.execute(
        "SELECT * FROM chem_comp WHERE vk_key = ? ORDER BY key_num ASC, id ASC",
        (vk_key,),
    )
    chem_list = []
    for r in cur.fetchall():
        d = dict(r)
        d["subgroup"] = spec_map.get(d["key_num"], mat.get("subgroup") or "")
        chem_list.append(d)

    # Lấy cơ tính nhiệt độ thường
    cur.execute(
        "SELECT * FROM mech_props WHERE vk_key = ? ORDER BY key_num ASC, no ASC, id ASC",
        (vk_key,),
    )
    mech_list = []
    for r in cur.fetchall():
        d = dict(r)
        d["subgroup"] = spec_map.get(d["key_num"], mat.get("subgroup") or "")
        mech_list.append(d)

    # Lấy cơ tính nhiệt độ cao
    cur.execute(
        "SELECT * FROM high_temp_props WHERE vk_key = ? ORDER BY temperature ASC",
        (vk_key,),
    )
    htm_list = [dict(r) for r in cur.fetchall()]

    # Lấy hướng dẫn nhiệt luyện
    cur.execute(
        "SELECT * FROM heat_treatments WHERE vk_key = ? ORDER BY key_num ASC, id ASC",
        (vk_key,),
    )
    ht_list = []
    for r in cur.fetchall():
        d = dict(r)
        d["subgroup"] = spec_map.get(d["key_num"], mat.get("subgroup") or "")
        ht_list.append(d)

    # Lấy danh sách mác tương đương (Cross-Reference)
    cur.execute("SELECT DISTINCT crt_num FROM cross_ref WHERE vk_key = ?", (vk_key,))
    crt_nums = [r[0] for r in cur.fetchall()]

    equivalents = []
    if crt_nums:
        placeholders = ",".join(["?"] * len(crt_nums))
        cur.execute(
            f"""
            SELECT DISTINCT
                m.vk_key, m.material, m.standard, m.country,
                m.subgroup, m.chem_preview, m.mech_preview,
                m.is_internal, m.internal_code, c.crt_num
            FROM cross_ref c
            JOIN materials m ON m.vk_key = c.vk_key
            WHERE c.crt_num IN ({placeholders}) AND m.vk_key != ?
            ORDER BY
                m.is_internal DESC,
                CASE m.standard
                    WHEN 'NOI_BO' THEN 0
                    WHEN 'TCVN' THEN 1
                    WHEN 'JIS' THEN 2
                    WHEN 'ASTM' THEN 3
                    WHEN 'AISI' THEN 4
                    WHEN 'SAE' THEN 5
                    WHEN 'DIN' THEN 6
                    WHEN 'EN' THEN 7
                    WHEN 'GOST' THEN 8
                    WHEN 'GB' THEN 9
                    WHEN 'ISO' THEN 10
                    ELSE 11
                END,
                m.standard ASC,
                m.material ASC
            """,
            crt_nums + [vk_key],
        )
        seen_eq = set()
        for r in cur.fetchall():
            if r["vk_key"] not in seen_eq:
                seen_eq.add(r["vk_key"])
                equivalents.append(dict(r))

    conn.close()
    mat["specs"] = specs
    mat["chem"] = chem_list
    mat["mech"] = mech_list
    mat["high_temp"] = htm_list
    mat["heat_treatments"] = ht_list
    mat["crt_nums"] = crt_nums
    mat["equivalents"] = equivalents
    return mat


@app.post("/api/spectrometer-match")
def match_spectrometer(req: SpectrometerRequest):
    """
    Thuật toán đối sánh kết quả bắn Quang phổ (Spectrometer) tại lò nấu luyện:
    - Đầu vào: % các nguyên tố vừa phân tích (VD: C=0.21, Si=0.45, Mn=0.75, Cr=0.15...)
    - Chấm điểm từng mác thép theo mức độ nằm trong dải tiêu chuẩn [c_min, c_max] và dải nội bộ [internal_min, internal_max].
    - Phạt điểm nếu mác thép yêu cầu nguyên tố hợp kim chính (c_min >= 0.25%) mà mẫu quang phổ không có.
    """
    active_elems = {
        k.strip(): float(v)
        for k, v in req.elements.items()
        if v is not None and str(v).strip() != "" and float(v) >= 0
    }
    if not active_elems:
        raise HTTPException(status_code=400, detail="Vui lòng nhập ít nhất 1 nguyên tố hóa học")

    conn = get_db()
    cur = conn.cursor()

    # Tìm ứng viên dựa trên nguyên tố chính có hàm lượng đáng kể (ưu tiên C hoặc nguyên tố có giá trị lớn nhất)
    primary_elem = "C" if "C" in active_elems and active_elems["C"] > 0 else max(active_elems, key=active_elems.get)
    p_val = active_elems[primary_elem]
    tol_ratio = max(req.tolerance_pct, 5.0) / 100.0
    abs_margin = max(p_val * (tol_ratio * 2.0), 0.08 if primary_elem == "C" else 0.50)

    where_filters = ["c.element = ?"]
    params = [
        primary_elem,
        p_val + abs_margin,
        p_val - abs_margin,
        p_val - abs_margin,
        p_val + abs_margin,
    ]
    sql_candidates = """
        SELECT DISTINCT c.vk_key, c.key_num
        FROM chem_comp c
        JOIN materials m ON m.vk_key = c.vk_key
        WHERE c.element = ?
          AND (c.c_min IS NOT NULL OR c.c_max IS NOT NULL OR c.c_approx IS NOT NULL)
          AND (
              (
                  (c.c_min IS NULL OR c.c_min <= ?)
                  AND (c.c_max IS NULL OR c.c_max >= ?)
              )
              OR (c.c_approx IS NOT NULL AND c.c_approx BETWEEN ? AND ?)
          )
    """
    if req.standard:
        sql_candidates += " AND m.standard = ?"
        params.append(req.standard)
    if req.grp_code is not None:
        sql_candidates += " AND m.grp_code = ?"
        params.append(req.grp_code)
    if req.only_internal:
        sql_candidates += " AND m.is_internal = 1"

    sql_candidates += " ORDER BY m.is_internal DESC, (c.c_min IS NOT NULL AND c.c_max IS NOT NULL) DESC LIMIT 3500"
    cur.execute(sql_candidates, params)
    cand_pairs = cur.fetchall()
    if not cand_pairs:
        conn.close()
        return {"items": [], "total_evaluated": 0}

    cand_vks = list({r[0] for r in cand_pairs})
    cand_set = {(r[0], r[1]) for r in cand_pairs}

    # Lấy toàn bộ thành phần hóa học của các ứng viên
    placeholders = ",".join(["?"] * len(cand_vks))
    cur.execute(
        f"""
        SELECT vk_key, key_num, element, c_min, c_max, c_approx, internal_min, internal_max
        FROM chem_comp
        WHERE vk_key IN ({placeholders})
          AND (c_min IS NOT NULL OR c_max IS NOT NULL OR c_approx IS NOT NULL)
        """,
        cand_vks,
    )
    chem_by_pair = defaultdict(dict)
    for r in cur.fetchall():
        pair = (r["vk_key"], r["key_num"])
        if pair in cand_set:
            chem_by_pair[pair][r["element"].upper()] = dict(r)

    # Lấy thông tin mác thép của các ứng viên
    cur.execute(
        f"""
        SELECT m.vk_key, m.material, m.standard, m.country, m.grp_code,
               g.grp_desc_vi, m.subgroup, m.chem_preview, m.mech_preview,
               m.is_internal, m.internal_code
        FROM materials m
        LEFT JOIN steel_groups g ON g.grp_code = m.grp_code
        WHERE m.vk_key IN ({placeholders})
        """,
        cand_vks,
    )
    mat_map = {r["vk_key"]: dict(r) for r in cur.fetchall()}

    scored_by_vk = {}
    input_upper = {k.upper(): (k, v) for k, v in active_elems.items()}

    for (vk, kn), spec_elems in chem_by_pair.items():
        total_weight = 0.0
        earned_score = 0.0
        bounded_count = 0
        elem_details = []
        hard_reject = False

        # 1. Kiểm tra các nguyên tố người dùng đã nhập
        for el_up, (el_orig, val) in input_upper.items():
            weight = 2.5 if el_up in ("C", "SI", "MN", "CR", "NI", "MO", "MG") else 1.0
            spec = spec_elems.get(el_up)

            if not spec:
                # Nếu người dùng nhập hàm lượng hợp kim đáng kể (>= 0.10%) mà mác thép không quy định nguyên tố này -> trừ điểm nhẹ
                if val >= 0.35 and el_up not in ("FE",):
                    total_weight += weight
                    earned_score += 0.0
                    elem_details.append(
                        {
                            "element": el_orig,
                            "actual": val,
                            "range": "Không quy định",
                            "status": "excess",
                            "note": f"Dư {el_orig} ({val:g}%)",
                        }
                    )
                elif val >= 0.08 and el_up not in ("FE", "SI", "MN"):
                    total_weight += 0.35
                continue

            total_weight += weight
            cmin = spec["c_min"]
            cmax = spec["c_max"]
            capr = spec["c_approx"]
            imin = spec["internal_min"]
            imax = spec["internal_max"]

            if cmin is not None and cmax is not None:
                bounded_count += 1

            if cmin is None and cmax is None and capr is not None:
                cmin = capr * 0.85
                cmax = capr * 1.15

            range_str = (
                f"{cmin:g} - {cmax:g}"
                if (cmin is not None and cmax is not None)
                else f"≤ {cmax:g}"
                if cmax is not None
                else f"≥ {cmin:g}"
                if cmin is not None
                else "N/A"
            )

            # Kiểm tra nằm trong dải tiêu chuẩn
            in_std = True
            dev_ratio = 0.0
            status = "ok"
            note = "Đạt chuẩn"

            if cmin is not None and val < cmin:
                in_std = False
                span = max(cmax - cmin, cmin * 0.2, 0.05) if cmax is not None else max(cmin * 0.25, 0.05)
                dev_ratio = (cmin - val) / span
                status = "low"
                note = f"Thấp hơn Min ({val - cmin:+.3f}%)"
            elif cmax is not None and val > cmax:
                in_std = False
                span = max(cmax - cmin, cmax * 0.2, 0.05) if cmin is not None else max(cmax * 0.25, 0.02)
                dev_ratio = (val - cmax) / span
                status = "high"
                note = f"Vượt Max ({val - cmax:+.3f}%)"

            if in_std:
                if (imin is not None and val < imin) or (imax is not None and val > imax):
                    earned_score += weight * 0.94
                    note = f"Đạt chuẩn (Ngoài dải mục tiêu nội bộ {imin or 0:g}-{imax or 'Max'})"
                else:
                    earned_score += weight * 1.0
            else:
                if dev_ratio > 2.5:
                    hard_reject = True
                    break
                elem_score = max(0.0, 1.0 - (dev_ratio / (1.0 + tol_ratio * 4.0)))
                earned_score += weight * elem_score

            elem_details.append(
                {
                    "element": el_orig,
                    "actual": val,
                    "range": range_str,
                    "status": status,
                    "note": note,
                }
            )

        if hard_reject or total_weight == 0:
            continue

        # 2. Kiểm tra các nguyên tố hợp kim bắt buộc của mác thép mà người dùng KHÔNG nhập
        for el_up, spec in spec_elems.items():
            if el_up not in input_upper and el_up not in ("FE", "(C+MN)/6", "CE", "CEV"):
                cmin = spec["c_min"]
                if cmin is not None and cmin >= 0.20:
                    penalty_w = 2.5 if cmin >= 1.0 else 1.2
                    total_weight += penalty_w
                    elem_details.append(
                        {
                            "element": spec["element"],
                            "actual": 0.0,
                            "range": f"≥ {cmin:g}" if spec["c_max"] is None else f"{cmin:g} - {spec['c_max']:g}",
                            "status": "missing",
                            "note": f"Thiếu {spec['element']} (Yêu cầu ≥ {cmin:g}%)",
                        }
                    )

        match_pct = round((earned_score / total_weight) * 100.0, 1)
        if match_pct < 35.0:
            continue

        if vk not in scored_by_vk or (match_pct, bounded_count) > (scored_by_vk[vk]["match_pct"], scored_by_vk[vk]["bounded_count"]):
            mat_info = dict(mat_map.get(vk, {}))
            mat_info["match_pct"] = match_pct
            mat_info["bounded_count"] = bounded_count
            mat_info["elem_details"] = elem_details
            scored_by_vk[vk] = mat_info

    results = sorted(
        scored_by_vk.values(),
        key=lambda x: (x["match_pct"], x.get("is_internal", 0), x.get("bounded_count", 0)),
        reverse=True,
    )[: req.limit]

    conn.close()
    return {
        "total_evaluated": len(cand_vks),
        "items": results,
    }


@app.get("/api/compare")
def compare_materials(vk_keys: str = Query(..., description="Danh sách vk_key cách nhau dấu phẩy, VD: 9000001,10001")):
    keys = [int(k.strip()) for k in vk_keys.split(",") if k.strip().isdigit()][:6]
    if not keys:
        raise HTTPException(status_code=400, detail="Danh sách mã vật liệu không hợp lệ")

    items = [get_material_detail(k) for k in keys]
    # Tập hợp danh sách tất cả các nguyên tố hóa học xuất hiện ở các mác được so sánh
    all_elements = []
    for item in items:
        for c in item["chem"]:
            el = c["element"]
            if el not in all_elements:
                all_elements.append(el)

    return {
        "materials": items,
        "all_elements": all_elements,
    }


# --- Quản lý Mác Nội Bộ Công Ty Đúc (CRUD) ---

def _build_previews(chem_list: List[ChemElementInput], mech_list: List[MechPropInput]):
    c_parts = []
    for c in chem_list[:6]:
        if c.c_min is not None and c.c_max is not None:
            c_parts.append(f"{c.element}: {c.c_min:g}-{c.c_max:g}")
        elif c.c_max is not None:
            c_parts.append(f"{c.element}: ≤{c.c_max:g}")
        elif c.c_min is not None:
            c_parts.append(f"{c.element}: ≥{c.c_min:g}")

    m_parts = []
    if mech_list:
        m0 = mech_list[0]
        if m0.y_min or m0.y_max:
            v = f"{m0.y_min:g}-{m0.y_max:g}" if (m0.y_min and m0.y_max) else f"≥{m0.y_min:g}" if m0.y_min else f"≤{m0.y_max:g}"
            m_parts.append(f"Re: {v} MPa")
        if m0.t_min or m0.t_max:
            v = f"{m0.t_min:g}-{m0.t_max:g}" if (m0.t_min and m0.t_max) else f"≥{m0.t_min:g}" if m0.t_min else f"≤{m0.t_max:g}"
            m_parts.append(f"Rm: {v} MPa")
        if m0.e_min:
            m_parts.append(f"A: ≥{m0.e_min:g}%")
        if m0.hardness:
            m_parts.append(f"Độ cứng: {m0.hardness}")

    return " | ".join(c_parts), " | ".join(m_parts)


@app.post("/api/internal")
def create_internal_material(payload: InternalMaterialPayload):
    if not payload.material.strip():
        raise HTTPException(status_code=400, detail="Tên mác vật liệu không được để trống")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT MAX(vk_key) FROM materials WHERE vk_key >= 9000000")
    max_vk = cur.fetchone()[0]
    new_vk = (max_vk + 1) if max_vk else 9000001

    c_prev, m_prev = _build_previews(payload.chem, payload.mech)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        """
        INSERT INTO materials (
            vk_key, material, material_norm, standard, country,
            grp_code, subgroup, subgroup_desc, comment,
            application, product_form, heat_treatment,
            chem_preview, mech_preview, is_internal, internal_code, casting_notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            new_vk,
            payload.material.strip(),
            normalize_code(payload.material),
            payload.standard or "NOI_BO",
            payload.country or "Việt Nam (Nội bộ Công ty)",
            payload.grp_code or 6,
            payload.subgroup or "",
            payload.subgroup_desc or "",
            payload.comment or "",
            payload.application or "",
            payload.product_form or "",
            payload.heat_treatment or "",
            c_prev,
            m_prev,
            payload.internal_code or "",
            payload.casting_notes or "",
            now_str,
        ),
    )

    for c in payload.chem:
        if c.element.strip():
            cur.execute(
                """
                INSERT INTO chem_comp (vk_key, key_num, element, c_min, c_max, c_approx, c_text, internal_min, internal_max)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_vk, c.element.strip(), c.c_min, c.c_max, c.c_approx, c.c_text or "", c.internal_min, c.internal_max),
            )

    for idx, m in enumerate(payload.mech, 1):
        cur.execute(
            """
            INSERT INTO mech_props (vk_key, key_num, no, dimen, y_min, y_max, t_min, t_max, e_min, i_min, hardness)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (new_vk, idx, m.dimen or "", m.y_min, m.y_max, m.t_min, m.t_max, m.e_min, m.i_min, m.hardness or ""),
        )

    if payload.heat_treatment:
        cur.execute(
            "INSERT INTO heat_treatments (vk_key, key_num, treatment_text) VALUES (?, 1, ?)",
            (new_vk, payload.heat_treatment),
        )

    # Liên kết nhóm tương đương (Cross-reference)
    target_crt = payload.crt_num
    if not target_crt and payload.equivalent_vk_key:
        cur.execute("SELECT crt_num FROM cross_ref WHERE vk_key = ? LIMIT 1", (payload.equivalent_vk_key,))
        r = cur.fetchone()
        if r:
            target_crt = r[0]
        else:
            # Tạo mã nhóm CRT_NUM mới cho cặp này
            cur.execute("SELECT MAX(crt_num) FROM cross_ref")
            max_crt = cur.fetchone()[0] or 100000
            target_crt = max_crt + 1
            cur.execute(
                "INSERT INTO cross_ref (vk_key, crt_num, ref_type) VALUES (?, ?, 'INTERNAL')",
                (payload.equivalent_vk_key, target_crt),
            )

    if target_crt:
        cur.execute(
            "INSERT INTO cross_ref (vk_key, crt_num, ref_type) VALUES (?, ?, 'INTERNAL')",
            (new_vk, target_crt),
        )

    conn.commit()
    conn.close()
    return {"status": "ok", "vk_key": new_vk, "message": f"Đã thêm mác nội bộ {payload.material}"}


@app.put("/api/internal/{vk_key}")
def update_internal_material(vk_key: int, payload: InternalMaterialPayload):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT is_internal FROM materials WHERE vk_key = ?", (vk_key,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy mác vật liệu")
    if row["is_internal"] != 1:
        conn.close()
        raise HTTPException(status_code=400, detail="Chỉ được phép chỉnh sửa trực tiếp Mác nội bộ công ty (Bạn có thể nhân bản mác quốc tế thành mác nội bộ)")

    c_prev, m_prev = _build_previews(payload.chem, payload.mech)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(
        """
        UPDATE materials SET
            material = ?, material_norm = ?, standard = ?, country = ?,
            grp_code = ?, subgroup = ?, subgroup_desc = ?, comment = ?,
            application = ?, product_form = ?, heat_treatment = ?,
            chem_preview = ?, mech_preview = ?, internal_code = ?, casting_notes = ?, updated_at = ?
        WHERE vk_key = ?
        """,
        (
            payload.material.strip(),
            normalize_code(payload.material),
            payload.standard or "NOI_BO",
            payload.country or "Việt Nam (Nội bộ Công ty)",
            payload.grp_code or 6,
            payload.subgroup or "",
            payload.subgroup_desc or "",
            payload.comment or "",
            payload.application or "",
            payload.product_form or "",
            payload.heat_treatment or "",
            c_prev,
            m_prev,
            payload.internal_code or "",
            payload.casting_notes or "",
            now_str,
            vk_key,
        ),
    )

    cur.execute("DELETE FROM chem_comp WHERE vk_key = ?", (vk_key,))
    for c in payload.chem:
        if c.element.strip():
            cur.execute(
                """
                INSERT INTO chem_comp (vk_key, key_num, element, c_min, c_max, c_approx, c_text, internal_min, internal_max)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (vk_key, c.element.strip(), c.c_min, c.c_max, c.c_approx, c.c_text or "", c.internal_min, c.internal_max),
            )

    cur.execute("DELETE FROM mech_props WHERE vk_key = ?", (vk_key,))
    for idx, m in enumerate(payload.mech, 1):
        cur.execute(
            """
            INSERT INTO mech_props (vk_key, key_num, no, dimen, y_min, y_max, t_min, t_max, e_min, i_min, hardness)
            VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (vk_key, idx, m.dimen or "", m.y_min, m.y_max, m.t_min, m.t_max, m.e_min, m.i_min, m.hardness or ""),
        )

    cur.execute("DELETE FROM heat_treatments WHERE vk_key = ?", (vk_key,))
    if payload.heat_treatment:
        cur.execute(
            "INSERT INTO heat_treatments (vk_key, key_num, treatment_text) VALUES (?, 1, ?)",
            (vk_key, payload.heat_treatment),
        )

    if payload.crt_num or payload.equivalent_vk_key:
        cur.execute("DELETE FROM cross_ref WHERE vk_key = ?", (vk_key,))
        target_crt = payload.crt_num
        if not target_crt and payload.equivalent_vk_key:
            cur.execute("SELECT crt_num FROM cross_ref WHERE vk_key = ? LIMIT 1", (payload.equivalent_vk_key,))
            r = cur.fetchone()
            target_crt = r[0] if r else None
        if target_crt:
            cur.execute(
                "INSERT INTO cross_ref (vk_key, crt_num, ref_type) VALUES (?, ?, 'INTERNAL')",
                (vk_key, target_crt),
            )

    conn.commit()
    conn.close()
    return {"status": "ok", "vk_key": vk_key, "message": f"Đã cập nhật mác nội bộ {payload.material}"}


@app.delete("/api/internal/{vk_key}")
def delete_internal_material(vk_key: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT is_internal, material FROM materials WHERE vk_key = ?", (vk_key,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy mác vật liệu")
    if row["is_internal"] != 1:
        conn.close()
        raise HTTPException(status_code=400, detail="Không thể xóa mác vật liệu chuẩn quốc tế của Key to Steel")

    for tbl in ("materials", "chem_comp", "mech_props", "heat_treatments", "cross_ref", "mat_specs"):
        cur.execute(f"DELETE FROM {tbl} WHERE vk_key = ?", (vk_key,))

    conn.commit()
    conn.close()
    return {"status": "ok", "message": f"Đã xóa mác nội bộ {row['material']}"}


# --- Xuất dữ liệu Excel (.xlsx) ---

@app.get("/api/export/excel")
def export_excel(vk_keys: str = Query("", description="Danh sách vk_key cách nhau dấu phẩy (Bỏ trống để xuất toàn bộ Mác Nội bộ)")):
    conn = get_db()
    cur = conn.cursor()

    if vk_keys.strip():
        keys = [int(k.strip()) for k in vk_keys.split(",") if k.strip().isdigit()]
    else:
        cur.execute("SELECT vk_key FROM materials WHERE is_internal = 1 ORDER BY vk_key ASC")
        keys = [r[0] for r in cur.fetchall()]

    conn.close()
    if not keys:
        raise HTTPException(status_code=400, detail="Không có mác vật liệu nào để xuất Excel")

    details = [get_material_detail(k) for k in keys]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Thong_So_Mac_Thep"

    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    sub_fill = PatternFill(start_color="DBEAFE", end_color="DBEAFE", fill_type="solid")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    bold_font = Font(name="Arial", size=10, bold=True)
    normal_font = Font(name="Arial", size=10)
    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    headers = [
        "Mã VK_KEY", "Tên Mác Vật Liệu", "Mã Nội Bộ", "Tiêu Chuẩn", "Quốc Gia",
        "Nhóm Vật Liệu", "Tiêu Chuẩn Nhánh",
        "Thành Phần Hóa Học (Tiêu chuẩn)", "Cơ Tính (Tiêu chuẩn)",
        "Chế Độ Nhiệt Luyện", "Ghi Chú Công Nghệ Đúc / Ứng Dụng", "Mác Tương Đương Quốc Tế"
    ]
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for d in details:
        eq_str = ", ".join([f"{e['material']} ({e['standard']})" for e in d.get("equivalents", [])[:12]])
        notes = "\n".join(filter(None, [d.get("casting_notes"), d.get("application"), d.get("comment")]))
        row_vals = [
            d["vk_key"],
            d["material"],
            d.get("internal_code") or "",
            d.get("standard") or "",
            d.get("country") or "",
            d.get("grp_desc_vi") or "",
            d.get("subgroup") or "",
            d.get("chem_preview") or "",
            d.get("mech_preview") or "",
            d.get("heat_treatment") or "",
            notes,
            eq_str,
        ]
        ws.append(row_vals)
        r_idx = ws.max_row
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=r_idx, column=c_idx)
            cell.font = bold_font if c_idx == 2 else normal_font
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if d.get("is_internal") == 1 and c_idx in (1, 2, 3):
                cell.fill = sub_fill

    col_widths = [12, 28, 15, 14, 22, 32, 24, 42, 38, 45, 45, 45]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    # Sheet 2: Bảng chi tiết Thành phần hóa học từng nguyên tố
    ws_chem = wb.create_sheet(title="Chi_Tiet_Hoa_Hoc")
    chem_headers = ["Mã VK_KEY", "Tên Mác Vật Liệu", "Tiêu Chuẩn", "Nguyên Tố", "Min (%)", "Max (%)", "Mục Tiêu Nội Bộ Min (%)", "Mục Tiêu Nội Bộ Max (%)", "Ghi Chú"]
    ws_chem.append(chem_headers)
    for col_idx in range(1, len(chem_headers) + 1):
        cell = ws_chem.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font

    for d in details:
        for c in d.get("chem", []):
            ws_chem.append([
                d["vk_key"],
                d["material"],
                d.get("standard") or "",
                c["element"],
                c.get("c_min"),
                c.get("c_max"),
                c.get("internal_min"),
                c.get("internal_max"),
                c.get("c_text") or "",
            ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"Bang_Thong_So_Mac_Thep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# Phục vụ giao diện tĩnh
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
