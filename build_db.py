# -*- coding: utf-8 -*-
"""
Script trích xuất và giải mã toàn bộ cơ sở dữ liệu Key to Steel v2005.11
từ file KTSUp2005-11.exe (Visual FoxPro 9 DBF + FPT) sang SQLite (kts_database.db).
Hỗ trợ mở rộng cho Mác thép/gang nội bộ của Công ty Đúc.
"""

import os
import re
import sqlite3
import struct
import time
import zipfile
from collections import defaultdict

KTS_ARCHIVE_PATH = r"D:\SOFTWARE\M&E\Mechanic\3D design\Key to Steel v2005.11\KTSUp2005-11.exe"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kts_database.db")

STEEL_GROUPS_VI = {
    1: "Thép kết cấu & Xây dựng (Structural & Constructional Steels)",
    2: "Thép dụng cụ & Khuôn mẫu (Tool Steels)",
    3: "Thép không gỉ & Bền nhiệt (Stainless & Heat Resisting Steels)",
    4: "Thép & Thép đúc không gỉ / Bền nhiệt (Stainless & Heat Resisting Castings)",
    5: "Vật liệu hàn đắp (Welding Filler Materials)",
    6: "Thép đúc (Steel Castings)",
    7: "Thép không gỉ, Bền nhiệt & Thép xupáp (Valve Steels)",
    8: "Vật liệu hàng không vũ trụ (Aerospace Materials)",
    9: "Hợp kim có tính chất vật lý đặc biệt (Special Physical Properties)",
    10: "Gang đúc (Cast Iron / Iron)",
}


def normalize_code(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def parse_float(val_str: str):
    s = val_str.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_memo_block(fpt_bytes: bytes, block_size: int, block_num: int) -> str:
    if not block_num or block_num <= 0:
        return ""
    offset = block_num * block_size
    if offset + 8 > len(fpt_bytes):
        return ""
    btype, blen = struct.unpack(">II", fpt_bytes[offset : offset + 8])
    if blen <= 0 or offset + 8 + blen > len(fpt_bytes):
        return ""
    raw = fpt_bytes[offset + 8 : offset + 8 + blen]
    text = raw.decode("cp1252", errors="replace").replace("\x00", "").strip()
    return text


def build_database():
    t0 = time.time()
    print(f"[1/6] Mở gói dữ liệu gốc: {KTS_ARCHIVE_PATH}")
    if not os.path.exists(KTS_ARCHIVE_PATH):
        raise FileNotFoundError(f"Không tìm thấy file: {KTS_ARCHIVE_PATH}")

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -128000")
    cur = conn.cursor()

    # 1. Tạo cấu trúc bảng SQLite
    cur.executescript(
        """
        CREATE TABLE standards (
            standard TEXT PRIMARY KEY,
            country TEXT,
            is_internal INTEGER DEFAULT 0
        );

        CREATE TABLE steel_groups (
            grp_code INTEGER PRIMARY KEY,
            grp_desc TEXT,
            grp_desc_vi TEXT
        );

        CREATE TABLE materials (
            vk_key INTEGER PRIMARY KEY,
            material TEXT NOT NULL,
            material_norm TEXT NOT NULL,
            standard TEXT,
            country TEXT,
            grp_code INTEGER,
            subgroup TEXT,
            subgroup_desc TEXT,
            comment TEXT,
            application TEXT,
            product_form TEXT,
            heat_treatment TEXT,
            chem_preview TEXT,
            mech_preview TEXT,
            is_internal INTEGER DEFAULT 0,
            internal_code TEXT,
            casting_notes TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE mat_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vk_key INTEGER NOT NULL,
            key_num INTEGER NOT NULL,
            subgroup TEXT,
            grp_code INTEGER,
            subgroup_desc TEXT,
            comment TEXT,
            application TEXT,
            product_form TEXT
        );

        CREATE TABLE chem_comp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vk_key INTEGER NOT NULL,
            key_num INTEGER DEFAULT 1,
            element TEXT NOT NULL,
            c_min REAL,
            c_max REAL,
            c_approx REAL,
            c_text TEXT,
            internal_min REAL,
            internal_max REAL
        );

        CREATE TABLE mech_props (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vk_key INTEGER NOT NULL,
            key_num INTEGER DEFAULT 1,
            no INTEGER DEFAULT 1,
            dimen TEXT,
            y_min REAL,
            y_max REAL,
            y_approx REAL,
            t_min REAL,
            t_max REAL,
            t_approx REAL,
            e_min REAL,
            e_max REAL,
            e_approx REAL,
            i_min REAL,
            i_max REAL,
            i_approx REAL,
            hardness TEXT,
            y_txt TEXT,
            e_txt TEXT,
            i_txt TEXT
        );

        CREATE TABLE heat_treatments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vk_key INTEGER NOT NULL,
            key_num INTEGER DEFAULT 1,
            treatment_text TEXT
        );

        CREATE TABLE high_temp_props (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vk_key INTEGER NOT NULL,
            key_num INTEGER DEFAULT 1,
            temperature INTEGER,
            yield_val REAL,
            yield_1 REAL,
            tensile REAL,
            hardness REAL,
            crs_1 REAL,
            crs_10 REAL,
            crs_100 REAL
        );

        CREATE TABLE cross_ref (
            vk_key INTEGER NOT NULL,
            crt_num INTEGER NOT NULL,
            ref_type TEXT DEFAULT 'STD'
        );
        """
    )

    with zipfile.ZipFile(KTS_ARCHIVE_PATH, "r") as z:
        # Lấy bảng dịch mã (shift table 250 bytes) từ DELIVERY.DBF
        with z.open("DELIVERY.DBF") as f:
            hdr = f.read(32)
            hlen, rlen = struct.unpack("<HH", hdr[8:12])
            f.seek(hlen)
            rec = f.read(rlen)
            shifts = [0x20 - b for b in rec[15:265]]

        # Tạo bảng tra nhanh 256 bytes cho mỗi vị trí cột (0..249)
        shift_len = len(shifts)
        trans_tables = []
        for pos_idx in range(shift_len):
            s = shifts[pos_idx]
            trans_tables.append(bytes(((b + s) & 0xFF) for b in range(256)))

        def decode_kts(raw: bytes) -> str:
            # Giải mã cực nhanh bằng bytearray
            n = len(raw)
            out = bytearray(n)
            for i in range(n):
                out[i] = (raw[i] + shifts[i % shift_len]) & 0xFF
            return out.decode("cp1252", errors="replace").rstrip(" \x00")

        # 2. Đọc standard.dbf & STEEL_GR.DBF
        print("[2/6] Đọc bảng Tiêu chuẩn (standard.dbf) & Nhóm vật liệu (STEEL_GR.DBF)...")
        std_country_map = {}
        with z.open("standard.dbf") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            std_rows = []
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                std_name = rec[1:12].decode("cp1252", errors="replace").strip()
                country = rec[12:47].decode("cp1252", errors="replace").strip()
                if std_name:
                    std_country_map[std_name] = country
                    std_rows.append((std_name, country, 0))
            cur.executemany(
                "INSERT OR IGNORE INTO standards (standard, country, is_internal) VALUES (?, ?, ?)",
                std_rows,
            )
            # Thêm tiêu chuẩn Nội bộ & TCVN
            cur.execute(
                "INSERT OR IGNORE INTO standards (standard, country, is_internal) VALUES (?, ?, ?)",
                ("NOI_BO", "Việt Nam (Nội bộ Công ty)", 1),
            )
            cur.execute(
                "INSERT OR IGNORE INTO standards (standard, country, is_internal) VALUES (?, ?, ?)",
                ("TCVN", "Việt Nam", 0),
            )

        with z.open("STEEL_GR.DBF") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            grp_rows = []
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                gcode = struct.unpack("<i", rec[1:5])[0]
                gdesc = decode_kts(rec[5:85])
                gdesc_vi = STEEL_GROUPS_VI.get(gcode, gdesc)
                grp_rows.append((gcode, gdesc, gdesc_vi))
            cur.executemany(
                "INSERT OR IGNORE INTO steel_groups (grp_code, grp_desc, grp_desc_vi) VALUES (?, ?, ?)",
                grp_rows,
            )

        # 3. Đọc H_TREAT.DBF + h_treat.FPT
        print("[3/6] Giải mã dữ liệu Nhiệt luyện (H_TREAT.DBF + h_treat.FPT)...")
        ht_fpt_bytes = z.read("h_treat.FPT")
        ht_bsize = struct.unpack(">H", ht_fpt_bytes[6:8])[0]
        ht_by_vk = defaultdict(list)
        ht_rows = []
        with z.open("H_TREAT.DBF") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                vk, kn, blk = struct.unpack("<iii", rec[1:13])
                txt = read_memo_block(ht_fpt_bytes, ht_bsize, blk)
                if txt:
                    ht_rows.append((vk, kn, txt))
                    if txt not in ht_by_vk[vk]:
                        ht_by_vk[vk].append(txt)
        cur.executemany(
            "INSERT INTO heat_treatments (vk_key, key_num, treatment_text) VALUES (?, ?, ?)",
            ht_rows,
        )
        del ht_fpt_bytes, ht_rows

        # 4. Đọc MAT_DESC.DBF + mat_desc.FPT
        print("[4/6] Giải mã Mô tả & Phân nhóm vật liệu (MAT_DESC.DBF + mat_desc.FPT)...")
        md_fpt_bytes = z.read("mat_desc.FPT")
        md_bsize = struct.unpack(">H", md_fpt_bytes[6:8])[0]
        primary_desc_by_vk = {}
        spec_rows = []
        with z.open("MAT_DESC.DBF") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                vk, kn = struct.unpack("<ii", rec[1:9])
                subgroup = rec[14:44].decode("cp1252", errors="replace").strip()
                grp_code = struct.unpack("<i", rec[44:48])[0]
                subgroup_e = rec[548:798].decode("cp1252", errors="replace").strip()
                comment = decode_kts(rec[1298:1548])
                appl_blk = struct.unpack("<i", rec[2548:2552])[0]
                form_blk = struct.unpack("<i", rec[2568:2572])[0]
                appl_txt = read_memo_block(md_fpt_bytes, md_bsize, appl_blk)
                form_txt = read_memo_block(md_fpt_bytes, md_bsize, form_blk)

                spec_rows.append((vk, kn, subgroup, grp_code, subgroup_e, comment, appl_txt, form_txt))
                if vk not in primary_desc_by_vk:
                    primary_desc_by_vk[vk] = (subgroup, grp_code, subgroup_e, comment, appl_txt, form_txt)
                else:
                    # Bổ sung thông tin nếu bản ghi đầu trống
                    prev = list(primary_desc_by_vk[vk])
                    if not prev[3] and comment:
                        prev[3] = comment
                    if not prev[4] and appl_txt:
                        prev[4] = appl_txt
                    if not prev[5] and form_txt:
                        prev[5] = form_txt
                    primary_desc_by_vk[vk] = tuple(prev)

        cur.executemany(
            """
            INSERT INTO mat_specs (vk_key, key_num, subgroup, grp_code, subgroup_desc, comment, application, product_form)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            spec_rows,
        )
        del md_fpt_bytes, spec_rows

        # 5. Đọc CC.DBF (347,428 bản ghi) & MAT_PROP.DBF (71,529 bản ghi)
        print("[5/6] Giải mã Thành phần hóa học (CC.DBF - 347.428 dòng) & Cơ tính (MAT_PROP.DBF - 71.529 dòng)...")
        chem_rows = []
        chem_preview_map = defaultdict(list)
        seen_elem_vk = defaultdict(set)

        with z.open("CC.DBF") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                vk, kn = struct.unpack("<ii", rec[1:9])
                elem = decode_kts(rec[9:29])
                c_max = parse_float(rec[29:38].decode("cp1252", errors="replace"))
                c_min = parse_float(rec[38:47].decode("cp1252", errors="replace"))
                c_approx = parse_float(rec[47:56].decode("cp1252", errors="replace"))
                c_text = (
                     ""
                    if rec[56:58] == b"\x1f\x1f" and rec[58:62] == b"\x1e\x1d\x1d\x1c"
                    else decode_kts(rec[56:156])
                )
                chem_rows.append((vk, kn, elem, c_min, c_max, c_approx, c_text))

                if elem and elem not in seen_elem_vk[vk] and len(seen_elem_vk[vk]) < 8:
                    seen_elem_vk[vk].add(elem)
                    if c_min is not None and c_max is not None:
                        chem_preview_map[vk].append(f"{elem}: {c_min:g}-{c_max:g}")
                    elif c_max is not None:
                        chem_preview_map[vk].append(f"{elem}: ≤{c_max:g}")
                    elif c_min is not None:
                        chem_preview_map[vk].append(f"{elem}: ≥{c_min:g}")
                    elif c_approx is not None:
                        chem_preview_map[vk].append(f"{elem}: ~{c_approx:g}")

        cur.executemany(
            """
            INSERT INTO chem_comp (vk_key, key_num, element, c_min, c_max, c_approx, c_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            chem_rows,
        )
        del chem_rows, seen_elem_vk

        mech_rows = []
        mech_preview_map = {}
        with z.open("MAT_PROP.DBF") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                vk, kn, no = struct.unpack("<iii", rec[1:13])
                dimen = decode_kts(rec[13:163])
                y_min = parse_float(rec[763:767].decode("cp1252", errors="replace"))
                y_max = parse_float(rec[767:771].decode("cp1252", errors="replace"))
                y_apr = parse_float(rec[771:775].decode("cp1252", errors="replace"))
                t_min = parse_float(rec[775:779].decode("cp1252", errors="replace"))
                t_max = parse_float(rec[779:783].decode("cp1252", errors="replace"))
                t_apr = parse_float(rec[783:787].decode("cp1252", errors="replace"))
                e_min = parse_float(rec[787:791].decode("cp1252", errors="replace"))
                e_max = parse_float(rec[791:795].decode("cp1252", errors="replace"))
                e_apr = parse_float(rec[795:799].decode("cp1252", errors="replace"))
                i_min = parse_float(rec[799:802].decode("cp1252", errors="replace"))
                i_max = parse_float(rec[802:805].decode("cp1252", errors="replace"))
                i_apr = parse_float(rec[805:808].decode("cp1252", errors="replace"))
                hardness = decode_kts(rec[808:908])
                i_txt = decode_kts(rec[908:988])
                e_txt = decode_kts(rec[1308:1388])
                y_txt = decode_kts(rec[1708:1788])

                mech_rows.append(
                    (
                        vk,
                        kn,
                        no,
                        dimen,
                        y_min,
                        y_max,
                        y_apr,
                        t_min,
                        t_max,
                        t_apr,
                        e_min,
                        e_max,
                        e_apr,
                        i_min,
                        i_max,
                        i_apr,
                        hardness,
                        y_txt,
                        e_txt,
                        i_txt,
                    )
                )

                if vk not in mech_preview_map:
                    parts = []
                    if y_min or y_max or y_apr:
                        v = f"{y_min:g}-{y_max:g}" if (y_min and y_max) else f"≥{y_min:g}" if y_min else f"≤{y_max:g}" if y_max else f"~{y_apr:g}"
                        parts.append(f"Re: {v} MPa")
                    if t_min or t_max or t_apr:
                        v = f"{t_min:g}-{t_max:g}" if (t_min and t_max) else f"≥{t_min:g}" if t_min else f"≤{t_max:g}" if t_max else f"~{t_apr:g}"
                        parts.append(f"Rm: {v} MPa")
                    if e_min or e_max or e_apr:
                        v = f"≥{e_min:g}%" if e_min else f"{e_max:g}%" if e_max else f"~{e_apr:g}%"
                        parts.append(f"A: {v}")
                    if hardness:
                        parts.append(f"Độ cứng: {hardness}")
                    if parts:
                        mech_preview_map[vk] = " | ".join(parts)

        cur.executemany(
            """
            INSERT INTO mech_props (
                vk_key, key_num, no, dimen,
                y_min, y_max, y_approx,
                t_min, t_max, t_approx,
                e_min, e_max, e_approx,
                i_min, i_max, i_approx,
                hardness, y_txt, e_txt, i_txt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            mech_rows,
        )
        del mech_rows

        # Đọc HTM.DBF (Cơ tính nhiệt độ cao)
        htm_rows = []
        with z.open("HTM.DBF") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                vk, kn, temp = struct.unpack("<iii", rec[1:13])
                yv = parse_float(rec[13:23].decode("cp1252", errors="replace"))
                y1 = parse_float(rec[23:33].decode("cp1252", errors="replace"))
                ts = parse_float(rec[33:43].decode("cp1252", errors="replace"))
                hd = parse_float(rec[43:53].decode("cp1252", errors="replace"))
                c1 = parse_float(rec[53:63].decode("cp1252", errors="replace"))
                c10 = parse_float(rec[63:73].decode("cp1252", errors="replace"))
                c100 = parse_float(rec[73:83].decode("cp1252", errors="replace"))
                htm_rows.append((vk, kn, temp, yv, y1, ts, hd, c1, c10, c100))
        cur.executemany(
            """
            INSERT INTO high_temp_props (vk_key, key_num, temperature, yield_val, yield_1, tensile, hardness, crs_1, crs_10, crs_100)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            htm_rows,
        )
        del htm_rows

        # Đọc CRT.DBF & CRT_PROP.DBF (Bảng tương đương)
        crt_rows = []
        for tbl_name, rtype, offset_val in [("CRT.DBF", "STD", 0), ("CRT_PROP.DBF", "PROP", 1000000)]:
            with z.open(tbl_name) as f:
                hdr = f.read(32)
                num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
                f.seek(hlen)
                for _ in range(num_rec):
                    rec = f.read(rlen)
                    if rec[0] == 0x2A:
                        continue
                    vk, crt_num = struct.unpack("<ii", rec[1:9])
                    crt_rows.append((vk, crt_num + offset_val, rtype))
        cur.executemany(
            "INSERT INTO cross_ref (vk_key, crt_num, ref_type) VALUES (?, ?, ?)",
            crt_rows,
        )
        del crt_rows

        # 6. Đọc mat.dbf (40,163 mác thép)
        print("[6/6] Tổng hợp bảng Danh mục Mác thép (mat.dbf - 40.163 mác) & Tạo chỉ mục...")
        mat_rows = []
        with z.open("mat.dbf") as f:
            hdr = f.read(32)
            num_rec, hlen, rlen = struct.unpack("<IHH", hdr[4:12])
            f.seek(hlen)
            for _ in range(num_rec):
                rec = f.read(rlen)
                if rec[0] == 0x2A:
                    continue
                vk = struct.unpack("<i", rec[1:5])[0]
                mat_name = rec[5:55].decode("cp1252", errors="replace").strip()
                mat_norm = rec[55:105].decode("cp1252", errors="replace").strip() or normalize_code(mat_name)
                std_name = rec[105:116].decode("cp1252", errors="replace").strip()
                country = std_country_map.get(std_name, "")
                desc_info = primary_desc_by_vk.get(vk, ("", 0, "", "", "", ""))
                subgroup, grp_code, subgroup_desc, comment, appl_txt, form_txt = desc_info
                ht_txt = "\n\n".join(ht_by_vk.get(vk, []))
                c_prev = " | ".join(chem_preview_map.get(vk, []))
                m_prev = mech_preview_map.get(vk, "")

                mat_rows.append(
                    (
                        vk,
                        mat_name,
                        mat_norm,
                        std_name,
                        country,
                        grp_code,
                        subgroup,
                        subgroup_desc,
                        comment,
                        appl_txt,
                        form_txt,
                        ht_txt,
                        c_prev,
                        m_prev,
                        0,
                        "",
                        "",
                    )
                )

        cur.executemany(
            """
            INSERT OR REPLACE INTO materials (
                vk_key, material, material_norm, standard, country,
                grp_code, subgroup, subgroup_desc, comment,
                application, product_form, heat_treatment,
                chem_preview, mech_preview, is_internal, internal_code, casting_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            mat_rows,
        )

    # Khởi tạo một số Mác thép/gang Nội bộ mẫu của Công ty Đúc và liên kết tương đương quốc tế
    seed_internal_foundry_grades(cur)

    # Tạo Index tối ưu hóa truy vấn
    print("Đang tạo Index tối ưu tốc độ tìm kiếm...")
    cur.executescript(
        """
        CREATE INDEX idx_mat_norm ON materials(material_norm);
        CREATE INDEX idx_mat_name ON materials(material);
        CREATE INDEX idx_mat_std ON materials(standard);
        CREATE INDEX idx_mat_grp ON materials(grp_code);
        CREATE INDEX idx_mat_internal ON materials(is_internal);

        CREATE INDEX idx_chem_vk ON chem_comp(vk_key);
        CREATE INDEX idx_chem_elem ON chem_comp(element, c_min, c_max);

        CREATE INDEX idx_mech_vk ON mech_props(vk_key);
        CREATE INDEX idx_mech_y ON mech_props(y_min, y_max);
        CREATE INDEX idx_mech_t ON mech_props(t_min, t_max);

        CREATE INDEX idx_crt_vk ON cross_ref(vk_key);
        CREATE INDEX idx_crt_num ON cross_ref(crt_num);
        CREATE INDEX idx_specs_vk ON mat_specs(vk_key);
        CREATE INDEX idx_ht_vk ON heat_treatments(vk_key);
        CREATE INDEX idx_htm_vk ON high_temp_props(vk_key);
        """
    )

    conn.commit()
    conn.close()
    elapsed = time.time() - t0
    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"HOÀN TẤT! Đã tạo {DB_PATH} ({size_mb:.1f} MB) trong {elapsed:.1f} giây.")


def seed_internal_foundry_grades(cur: sqlite3.Cursor):
    """Tạo sẵn 4 mác đúc nội bộ mẫu (Thép đúc WCB, Gang cầu FCD450, Thép không gỉ đúc SCS14A/CF8M, Thép chịu mài mòn Cr-Mo)
    và tự động gắn vào nhóm tương đương CRT_NUM của Key to Steel."""

    def find_crt_nums(mat_norm_pattern, std_prefix=None):
        if std_prefix:
            cur.execute(
                """
                SELECT DISTINCT c.crt_num FROM cross_ref c
                JOIN materials m ON m.vk_key = c.vk_key
                WHERE m.material_norm LIKE ? AND m.standard LIKE ?
                """,
                (f"%{mat_norm_pattern}%", f"{std_prefix}%"),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT c.crt_num FROM cross_ref c
                JOIN materials m ON m.vk_key = c.vk_key
                WHERE m.material_norm LIKE ?
                """,
                (f"%{mat_norm_pattern}%",),
            )
        return [r[0] for r in cur.fetchall()]

    samples = [
        {
            "vk_key": 9000001,
            "material": "VICO-WCB (Thép đúc Carbon)",
            "internal_code": "TC-DUC-001",
            "standard": "NOI_BO",
            "country": "Việt Nam (Nội bộ Công ty)",
            "grp_code": 6,
            "subgroup": "Tiêu chuẩn Đúc Nội bộ - Thép đúc van & phụ kiện áp lực",
            "subgroup_desc": "Thép đúc carbon trung bình tương đương ASTM A216 Gr.WCB / JIS SCPH2",
            "comment": "Mác thép đúc chủ lực cho thân van, nắp van, chi tiết chịu áp lực nhiệt độ trung bình - cao.",
            "application": "Thân van công nghiệp, mặt bích, vỏ bơm, phụ kiện đường ống chịu áp.",
            "product_form": "Vật đúc cát nhựa Furan / Thủy tinh lỏng (Sand Casting)",
            "heat_treatment": "Ủ thường hóa (Normalizing): 900 - 930°C, giữ nhiệt 2h + làm nguội ngoài không khí tĩnh. Ram khử ứng suất (nếu cần): 600 - 650°C.",
            "casting_notes": "Nhiệt độ ra lò: 1610 - 1630°C | Nhiệt độ rót: 1550 - 1580°C | Khử oxy triệt để bằng Al (0.03 - 0.06% Al dư) + FeSiCaBa. Kiểm soát chặt P, S ≤ 0.030% để tránh nứt nóng.",
            "equiv_lookup": ("WCB", "ASTM"),
            "chem": [
                ("C", 0.18, 0.25, 0.20, 0.23, "Mục tiêu nội bộ: 0.20 - 0.23%"),
                ("Si", 0.30, 0.60, 0.35, 0.50, "Mục tiêu nội bộ: 0.35 - 0.50%"),
                ("Mn", 0.60, 1.00, 0.70, 0.90, "Mục tiêu nội bộ: 0.70 - 0.90%"),
                ("P", None, 0.035, None, 0.025, "Kiểm soát nội bộ ≤ 0.025%"),
                ("S", None, 0.035, None, 0.025, "Kiểm soát nội bộ ≤ 0.025%"),
                ("Cr", None, 0.40, None, 0.25, "Nguyên tố dư"),
                ("Ni", None, 0.40, None, 0.25, "Nguyên tố dư"),
                ("Mo", None, 0.15, None, 0.10, "Nguyên tố dư"),
                ("Cu", None, 0.30, None, 0.20, "Nguyên tố dư"),
            ],
            "mech": [
                ("Mẫu đúc tiêu chuẩn Y-block sau Thường hóa", 250, None, 485, 655, 22.0, 27, "140 - 185 HB"),
            ],
        },
        {
            "vk_key": 9000002,
            "material": "VICO-FCD450 (Gang cầu Ferrite-Pearlite)",
            "internal_code": "TC-DUC-002",
            "standard": "NOI_BO",
            "country": "Việt Nam (Nội bộ Công ty)",
            "grp_code": 10,
            "subgroup": "Tiêu chuẩn Đúc Nội bộ - Gang cầu kết cấu",
            "subgroup_desc": "Gang cầu tương đương JIS G5502 FCD450-10 / EN-GJS-450-10 / ASTM A536 65-45-12",
            "comment": "Gang cầu nền Ferrite chủ đạo, độ dẻo dai cao, dễ gia công cơ khí.",
            "application": "Vỏ hộp số, thân bơm, gối đỡ, chi tiết máy công nghiệp và ô tô.",
            "product_form": "Đúc khuôn cát tươi / Cát nhựa Furan",
            "heat_treatment": "Đúc trực tiếp (As-cast) hoặc Ủ Ferrite hóa ở 720 - 740°C nếu thành mỏng bị biến trắng.",
            "casting_notes": "Nhiệt độ cầu hóa: 1480 - 1510°C (Phương pháp Sandwich với 1.1 - 1.3% FeSiMg6RE) | Biến tính thứ cấp trên dòng rót bằng 0.4% FeSi75Ba | Tỷ lệ cầu hóa ≥ 85%, Mg dư: 0.035 - 0.050%.",
            "equiv_lookup": ("GJS45010", "EN"),
            "chem": [
                ("C", 3.50, 3.85, 3.60, 3.75, "Mục tiêu nội bộ: 3.60 - 3.75%"),
                ("Si", 2.30, 2.80, 2.45, 2.65, "Sau biến tính"),
                ("Mn", None, 0.40, 0.15, 0.30, "Giữ Mn thấp để đạt độ giãn dài ≥ 10%"),
                ("P", None, 0.050, None, 0.035, "Kiểm soát nội bộ ≤ 0.035%"),
                ("S", None, 0.020, None, 0.012, "S sau cầu hóa ≤ 0.012%"),
                ("Mg", 0.030, 0.055, 0.035, 0.048, "Mg dư cầu hóa"),
            ],
            "mech": [
                ("Mẫu đúc Y-block 25mm (Trạng thái đúc)", 310, None, 450, None, 10.0, None, "140 - 210 HB"),
            ],
        },
        {
            "vk_key": 9000003,
            "material": "VICO-CF8M / SCS14A (Thép đúc Inox 316)",
            "internal_code": "TC-DUC-003",
            "standard": "NOI_BO",
            "country": "Việt Nam (Nội bộ Công ty)",
            "grp_code": 4,
            "subgroup": "Tiêu chuẩn Đúc Nội bộ - Thép đúc không gỉ chống ăn mòn",
            "subgroup_desc": "Thép đúc Austenitic chứa Mo tương đương ASTM A351 CF8M / JIS SCS14A / DIN 1.4408",
            "comment": "Chống ăn mòn rỗ và ăn mòn hóa chất vượt trội nhờ 2.0 - 3.0% Mo.",
            "application": "Cánh bơm hóa chất, thân van inox chịu nước biển, thực phẩm, dầu khí.",
            "product_form": "Đúc mẫu chảy (Investment Casting) / Đúc cát nhựa",
            "heat_treatment": "Tôi hòa tan (Solution Annealing): Nung 1050 - 1120°C, giữ nhiệt theo chiều dày thành (tối thiểu 1.5h) và tôi nước nhanh qua vùng nhạy cảm 850 - 450°C để hòa tan hết Carbide Cr23C6.",
            "casting_notes": "Nhiệt độ rót: 1560 - 1600°C | Kiểm soát hàm lượng Delta-Ferrite trong khoảng 5 - 15% (theo giản đồ Schaeffler/DeLong) để chống nứt nóng khi đông đặc và đảm bảo độ bền.",
            "equiv_lookup": ("CF8M", "ASTM"),
            "chem": [
                ("C", None, 0.08, None, 0.06, "Kiểm soát nội bộ C ≤ 0.06%"),
                ("Si", None, 1.50, 0.60, 1.20, "Tăng tính chảy loãng khi đúc"),
                ("Mn", None, 1.50, 0.50, 1.20, ""),
                ("P", None, 0.040, None, 0.030, ""),
                ("S", None, 0.040, None, 0.025, ""),
                ("Cr", 18.00, 21.00, 18.50, 20.00, "Mục tiêu nội bộ: 18.5 - 20.0%"),
                ("Ni", 9.00, 12.00, 9.50, 11.00, "Mục tiêu nội bộ: 9.5 - 11.0%"),
                ("Mo", 2.00, 3.00, 2.10, 2.50, "Mục tiêu nội bộ: 2.1 - 2.5%"),
            ],
            "mech": [
                ("Sau tôi hòa tan (Solution Annealed)", 205, None, 485, None, 30.0, 50, "≤ 200 HB"),
            ],
        },
        {
            "vk_key": 9000004,
            "material": "VICO-CrMnMo (Thép đúc Hợp kim Chịu mài mòn)",
            "internal_code": "TC-DUC-004",
            "standard": "NOI_BO",
            "country": "Việt Nam (Nội bộ Công ty)",
            "grp_code": 6,
            "subgroup": "Tiêu chuẩn Đúc Nội bộ - Thép hợp kim chịu mài mòn & va đập",
            "subgroup_desc": "Thép đúc hợp kim thấp Cr-Mn-Mo tôi + ram cho tấm lót máy nghiền, răng gầu",
            "comment": "Độ thấm tôi cao, kết hợp độ cứng bề mặt cao (45-52 HRC) và độ dai va đập tốt.",
            "application": "Tấm lót máy nghiền bi, búa nghiền, răng gầu máy xúc, chi tiết khai khoáng.",
            "product_form": "Đúc khuôn cát nhựa / Khuôn mẫu cháy (Lost Foam)",
            "heat_treatment": "Thường hóa 920°C -> Tôi dầu/chất lỏng polymer từ 870 - 890°C -> Ram ở 220 - 280°C (lấy độ cứng cao) hoặc 500 - 560°C (lấy độ dai cao).",
            "casting_notes": "Nhiệt độ rót: 1530 - 1560°C | Cắt đậu ngót khi phôi còn ấm hoặc sau khi ủ mềm để tránh nứt nguội do ứng suất tổ chức Mactenxit.",
            "equiv_lookup": ("42CRMO4", "DIN"),
            "chem": [
                ("C", 0.35, 0.45, 0.38, 0.42, "Mục tiêu nội bộ: 0.38 - 0.42%"),
                ("Si", 0.30, 0.60, 0.35, 0.55, ""),
                ("Mn", 0.80, 1.20, 0.90, 1.10, ""),
                ("P", None, 0.030, None, 0.025, ""),
                ("S", None, 0.030, None, 0.025, ""),
                ("Cr", 0.90, 1.30, 1.00, 1.20, ""),
                ("Mo", 0.20, 0.35, 0.22, 0.30, ""),
            ],
            "mech": [
                ("Tôi + Ram trung bình (Q+T)", 750, None, 950, 1150, 10.0, 35, "300 - 360 HB"),
            ],
        },
    ]

    for s in samples:
        vk = s["vk_key"]
        c_prev_parts = []
        for el, cmin, cmax, imin, imax, ctxt in s["chem"]:
            cur.execute(
                """
                INSERT INTO chem_comp (vk_key, key_num, element, c_min, c_max, c_text, internal_min, internal_max)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (vk, el, cmin, cmax, ctxt, imin, imax),
            )
            if cmin is not None and cmax is not None:
                c_prev_parts.append(f"{el}: {cmin:g}-{cmax:g}")
            elif cmax is not None:
                c_prev_parts.append(f"{el}: ≤{cmax:g}")

        m_prev_parts = []
        for idx, (dimen, ymin, ymax, tmin, tmax, emin, imin, hard) in enumerate(s["mech"], 1):
            cur.execute(
                """
                INSERT INTO mech_props (vk_key, key_num, no, dimen, y_min, y_max, t_min, t_max, e_min, i_min, hardness)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (vk, idx, dimen, ymin, ymax, tmin, tmax, emin, imin, hard),
            )
            if ymin:
                m_prev_parts.append(f"Re: ≥{ymin:g} MPa")
            if tmin:
                m_prev_parts.append(f"Rm: ≥{tmin:g} MPa")
            if hard:
                m_prev_parts.append(f"Độ cứng: {hard}")

        cur.execute(
            "INSERT INTO heat_treatments (vk_key, key_num, treatment_text) VALUES (?, 1, ?)",
            (vk, s["heat_treatment"]),
        )

        for crt_num in find_crt_nums(*s["equiv_lookup"]):
            cur.execute(
                "INSERT INTO cross_ref (vk_key, crt_num, ref_type) VALUES (?, ?, 'INTERNAL')",
                (vk, crt_num),
            )

        cur.execute(
            """
            INSERT INTO materials (
                vk_key, material, material_norm, standard, country,
                grp_code, subgroup, subgroup_desc, comment,
                application, product_form, heat_treatment,
                chem_preview, mech_preview, is_internal, internal_code, casting_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                vk,
                s["material"],
                normalize_code(s["material"]),
                s["standard"],
                s["country"],
                s["grp_code"],
                s["subgroup"],
                s["subgroup_desc"],
                s["comment"],
                s["application"],
                s["product_form"],
                s["heat_treatment"],
                " | ".join(c_prev_parts[:6]),
                " | ".join(m_prev_parts),
                s["internal_code"],
                s["casting_notes"],
            ),
        )


if __name__ == "__main__":
    build_database()
