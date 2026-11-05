# -*- coding: utf-8 -*-
"""
Xuất toàn bộ cơ sở dữ liệu SQLite (kts_database.db - 40.167 mác thép)
sang bộ dữ liệu nén Gzip siêu gọn (~2.5 MB) trong thư mục data/
để chạy trực tiếp 100% trên GitHub Pages (Static Web Hosting) không cần backend.
"""

import gzip
import json
import os
import sqlite3
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "kts_database.db")
DATA_DIR = os.path.join(BASE_DIR, "data")


def save_gz_json(filename: str, obj):
    path = os.path.join(DATA_DIR, filename)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=9)
    with open(path, "wb") as f:
        f.write(gz)
    print(f"  -> {filename:20s}: raw={len(raw)/1024/1024:5.2f} MB | gz={len(gz)/1024/1024:5.2f} MB")


def export_all():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("Đang đóng gói dữ liệu tĩnh cho GitHub Pages...")

    # 1. Meta (standards + steel_groups)
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
        LEFT JOIN materials m ON m.grp_code = m.grp_code
        GROUP BY g.grp_code
        ORDER BY g.grp_code ASC
        """
    )
    groups = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) FROM materials")
    total_materials = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM materials WHERE is_internal = 1")
    internal_materials = cur.fetchone()[0]

    meta = {
        "total_materials": total_materials,
        "internal_materials": internal_materials,
        "standards": standards,
        "steel_groups": groups,
        "common_elements": [
            "C", "Si", "Mn", "P", "S", "Cr", "Ni", "Mo", "Cu", "V", "W", "Al", "Ti", "Nb", "B", "Mg", "Co", "N"
        ],
    }
    with open(os.path.join(DATA_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 2. Danh sách Mác thép (40.167 mác):
    # Lưu dạng mảng: [vk_key, material, material_norm, standard, grp_code, subgroup, chem_preview, mech_preview, is_internal, internal_code]
    cur.execute(
        """
        SELECT vk_key, material, material_norm, standard, grp_code, subgroup,
               chem_preview, mech_preview, is_internal, internal_code
        FROM materials
        ORDER BY is_internal DESC, material ASC
        """
    )
    materials_list = [list(r) for r in cur.fetchall()]
    save_gz_json("materials.json.gz", materials_list)

    # 3. Thành phần hóa học (Deduplicated per vk_key + element)
    cur.execute(
        """
        SELECT vk_key, element, c_min, c_max, c_approx, internal_min, internal_max, c_text
        FROM chem_comp
        ORDER BY vk_key ASC, key_num ASC, id ASC
        """
    )
    chem_map = defaultdict(list)
    seen_chem = set()
    for vk, el, cmin, cmax, capr, imin, imax, ctxt in cur.fetchall():
        key = (vk, el, cmin, cmax, capr)
        if key not in seen_chem:
            seen_chem.add(key)
            chem_map[str(vk)].append([el, cmin, cmax, capr, imin, imax, ctxt or ""])
    save_gz_json("chem.json.gz", chem_map)

    # 4. Cơ tính (mech_props)
    cur.execute(
        """
        SELECT vk_key, dimen, y_min, y_max, t_min, t_max, e_min, i_min, hardness
        FROM mech_props
        ORDER BY vk_key ASC, key_num ASC, no ASC
        """
    )
    mech_map = defaultdict(list)
    for vk, dim, ymin, ymax, tmin, tmax, emin, imin, hard in cur.fetchall():
        mech_map[str(vk)].append([dim or "", ymin, ymax, tmin, tmax, emin, imin, hard or ""])
    save_gz_json("mech.json.gz", mech_map)

    # 5. Bảng tương đương (cross_ref): cả chiều vk_key -> [crt_num] và crt_num -> [vk_key]
    cur.execute("SELECT DISTINCT vk_key, crt_num FROM cross_ref")
    vk_to_crt = defaultdict(list)
    crt_to_vk = defaultdict(list)
    for vk, crt in cur.fetchall():
        vk_to_crt[str(vk)].append(crt)
        crt_to_vk[str(crt)].append(vk)
    save_gz_json("crt.json.gz", {"vk_to_crt": vk_to_crt, "crt_to_vk": crt_to_vk})

    # 6. Mô tả, ứng dụng, nhiệt luyện, ghi chú đúc
    cur.execute(
        """
        SELECT vk_key, subgroup_desc, comment, application, product_form, heat_treatment, casting_notes
        FROM materials
        WHERE subgroup_desc != '' OR comment != '' OR application != ''
           OR product_form != '' OR heat_treatment != '' OR casting_notes != ''
        """
    )
    desc_map = {}
    for vk, sdesc, comm, appl, form, ht, cast in cur.fetchall():
        desc_map[str(vk)] = [sdesc or "", comm or "", appl or "", form or "", ht or "", cast or ""]
    save_gz_json("desc.json.gz", desc_map)

    conn.close()
    print("Hoàn tất xuất dữ liệu tĩnh trong thư mục data/!")


if __name__ == "__main__":
    export_all()
