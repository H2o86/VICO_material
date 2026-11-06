# -*- coding: utf-8 -*-
"""
Script nhập toàn bộ 685 mác thép/gang nội bộ của Công ty TNHH Đúc Thắng Lợi (VICO)
từ sheet 'NCCN-Vật liệu - Mác thép' (vico_sheet_extracted.json) vào cơ sở dữ liệu SQLite (kts_database.db).
Tự động:
1. Phân tích cú pháp thành phần hóa học từ cột 'Thành phần hóa học', 'Mã vật liệu' và 'Tên vật liệu'.
2. Kế thừa thành phần hóa học giữa các biến thể mác nội bộ VICO (VD: G13X2 -> G13X2 tái sinh, E28C-H...).
3. Đối chiếu tự động với 40.163 mác thép quốc tế trong Key to Steel để gắn bảng tương đương (cross_ref)
   và bổ sung thông số hóa học/cơ tính tiêu chuẩn cho các mác chỉ ghi tên mã hiệu (như C45, FCD450, SKD61, SCW480...).
4. Suy luận phạm vi thành phần từ quy ước ký hiệu mác đúc VICO/GOST (VD: 280Cr28, 110Mn13Cr2, G15X2...) nếu chưa có.
"""

import json
import os
import re
import sqlite3
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "kts_database.db")
VICO_JSON_PATH = os.path.join(BASE_DIR, "vico_sheet_extracted.json")

# Bảng ánh xạ tên nhóm từ Sheet sang (grp_code, Tên nhóm tiếng Việt chuẩn)
GROUP_MAPPING = [
    ("Thep Mangan cao", 6, "Thép Mangan cao chịu mài mòn & va đập"),
    ("Thep Crom cao (TXC/TZL)", 6, "Hợp kim Crom cao chịu mòn (TXC/TZL)"),
    ("Thep Crom cao", 6, "Thép & Gang trắng Crom cao chịu mài mòn"),
    ("Thep dung cu (TXS/TCP)", 2, "Thép dụng cụ & hợp kim đúc (TXS/TCP)"),
    ("Thep chiu nhiet (SCH)", 4, "Thép đúc chịu nhiệt JIS (SCH)"),
    ("Thep chiu nhiet", 4, "Thép đúc chịu nhiệt cao"),
    ("Thep hop kim", 6, "Thép hợp kim & Thép đúc hợp kim thấp"),
    ("Thep Carbon duc (E-series)", 6, "Thép đúc hợp kim nội bộ (E-series)"),
    ("Thep Carbon duc (GS)", 6, "Thép đúc kết cấu DIN/EN (GS/GE)"),
    ("Thep Carbon duc", 6, "Thép Carbon đúc kết cấu"),
    ("Thep Carbon", 1, "Thép Carbon kết cấu & chế tạo máy"),
    ("Thep khong gi", 3, "Thép không gỉ (Inox / Stainless Steel)"),
    ("Gang trang hop kim (Ni-hard)", 10, "Gang trắng hợp kim Ni-Hard"),
    ("Gang trang hop kim", 10, "Gang trắng hợp kim Crom cao"),
    ("Gang (Cast Iron)", 10, "Gang xám & Gang cầu đúc"),
    ("Vautid", 10, "Hợp kim chịu mài mòn Vautid"),
    ("Thep khuon (SKD)", 2, "Thép làm khuôn dập nóng/nguội (SKD)"),
    ("Thep tam chiu mon", 1, "Thép tấm chịu mài mòn (Hardox / XAR)"),
    ("Thep tieu chuan ASTM", 6, "Thép & Gang đúc tiêu chuẩn ASTM"),
    ("Thep tieu chuan DIN", 1, "Thép tiêu chuẩn DIN / EN"),
    ("Thep tieu chuan nuoc ngoai", 6, "Thép tiêu chuẩn quốc tế khác (AS/EN)"),
    ("Thep duc hop kim", 6, "Thép đúc hợp kim Cr"),
    ("Vat lieu dac biet", 9, "Vật liệu đặc biệt & Tấm đắp chịu mòn"),
    ("Nhom & Kim loai mau", 9, "Nhôm đúc & Hợp kim đặc biệt"),
    ("Mac noi bo (Composition-based)", 6, "Mác đúc theo yêu cầu thành phần hóa học"),
    ("Khac/Chua phan loai", 6, "Vật liệu & Chi tiết cơ khí khác"),
]

ELEMENT_MAP = {
    "C": "C",
    "SI": "Si",
    "MN": "Mn",
    "P": "P",
    "S": "S",
    "CR": "Cr",
    "CROM": "Cr",
    "NI": "Ni",
    "MO": "Mo",
    "CU": "Cu",
    "V": "V",
    "W": "W",
    "AL": "Al",
    "TI": "Ti",
    "T": "Ti",
    "NB": "Nb",
    "B": "B",
    "MG": "Mg",
    "CO": "Co",
    "N": "N",
    "ZR": "Zr",
    "ZIRCON": "Zr",
    "CA": "Ca",
    "SN": "Sn",
    "PB": "Pb",
    "FE": "Fe",
    "ZN": "Zn",
    "CE": "Ce",
    "RE": "RE",
}


def normalize_code(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def map_group_info(raw_group: str):
    for prefix, gcode, vn_name in GROUP_MAPPING:
        if raw_group.startswith(prefix):
            return gcode, vn_name
    return 6, raw_group or "Mác vật liệu nội bộ VICO"


def parse_chem_string(s: str):
    """Phân tích chuỗi thành phần hóa học (VD: 'C0.9-1.35, Si≤0.8, Mn11-14, P,S≤0.04')
    Trả về list các tuple: (element, c_min, c_max, c_approx, raw_token)
    """
    if not s:
        return []
    # Chuẩn hóa dấu phẩy thập phân giữa 2 chữ số (VD: 1,7-2,2 -> 1.7-2.2)
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    s = s.replace("÷", "-").replace("~", "≈")

    # Loại bỏ các cụm từ kích thước hoặc cấp bền gây nhiễu (VD: '14x80', 'M24x60', 'cấp bền 8.8', '10+10')
    s_clean = re.sub(r"\bM\d+x\d+\b", "", s, flags=re.IGNORECASE)
    s_clean = re.sub(r"\bcấp bền\s*\d+(\.\d+)?", "", s_clean, flags=re.IGNORECASE)

    results = []
    seen_els = set()
    pattern = re.compile(
        r"(?:^|[\s,;(/])(?:(P\s*[,+]\s*S|S\s*[,+]\s*P|PS)|([A-Za-z]+))\s*(?:từ|=|:)?\s*([<>≤≥≈]?\s*\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)\s*%?"
    )
    for m in pattern.finditer(s_clean):
        ps_comb, el_raw, val_str = m.groups()
        val_str = val_str.replace(" ", "")
        cmin = cmax = capr = None
        try:
            if "-" in val_str:
                clean_v = re.sub(r"^[<>≤≥≈]+", "", val_str)
                p1, p2 = clean_v.split("-", 1)
                cmin, cmax = float(p1), float(p2)
                # Sửa lỗi gõ thiếu dấu chấm như Mn0.4-06 -> 0.4-0.6
                if p2.startswith("0") and len(p2) > 1 and "." not in p2:
                    cmax = float("0." + p2[1:])
                if cmin > cmax:
                    cmin, cmax = cmax, cmin
            elif val_str.startswith(("<", "≤")):
                cmax = float(re.sub(r"^[<≤]+", "", val_str))
            elif val_str.startswith((">", "≥")):
                cmin = float(re.sub(r"^[>≥]+", "", val_str))
            elif val_str.startswith("≈"):
                capr = float(re.sub(r"^[≈]+", "", val_str))
            else:
                capr = float(val_str)
        except ValueError:
            continue

        # Bỏ qua nếu giá trị vô lý (> 99%)
        for v in (cmin, cmax, capr):
            if v is not None and (v < 0 or v > 99):
                continue

        if ps_comb:
            for e in ("P", "S"):
                if e not in seen_els:
                    seen_els.add(e)
                    results.append((e, cmin, cmax, capr, m.group(0).strip(" ,;(/")))
        else:
            el_up = el_raw.upper()
            if el_up in ELEMENT_MAP:
                el_std = ELEMENT_MAP[el_up]
                if el_std not in seen_els:
                    seen_els.add(el_std)
                    results.append((el_std, cmin, cmax, capr, m.group(0).strip(" ,;(/")))
    return results


def infer_chem_from_vico_code(code: str, raw_group: str):
    """Suy luận thành phần hóa học gần đúng cho các mã nội bộ theo quy ước GOST/VICO
    khi không có chuỗi thành phần trực tiếp và không khớp mác quốc tế.
    """
    c = code.strip()
    items = []

    # 1. Dạng 280Cr28, 240Cr20, 200X16, 110Cr12NiMo, 260X24HM...
    m = re.match(
        r"^(\d{2,3})(?:Cr|X)(\d{1,2})(?:(Ni|H)(\d+(?:\.\d+)?)?)?(?:(Mo|M)(\d+(?:\.\d+)?)?)?(?:(Ti|T)|(V))?",
        c,
        re.IGNORECASE,
    )
    if m:
        c_val = int(m.group(1)) / 100.0
        cr_val = float(m.group(2))
        # Nếu thuộc nhóm Thép hợp kim mà có 2 chữ số đầu (VD: 40Cr13, 30Cr13, 20X12)
        if c_val >= 0.10 and c_val <= 3.60 and cr_val >= 1.0 and cr_val <= 35.0:
            c_tol = 0.20 if c_val >= 1.5 else 0.05
            cr_tol = 1.5 if cr_val >= 10 else 0.3
            items.append(("C", round(max(0.05, c_val - c_tol), 2), round(c_val + c_tol, 2), None, "Theo ký hiệu mác"))
            items.append(("Cr", round(max(0.5, cr_val - cr_tol), 2), round(cr_val + cr_tol, 2), None, "Theo ký hiệu mác"))
            if m.group(3):
                ni_v = float(m.group(4)) if m.group(4) else 0.6
                items.append(("Ni", round(max(0.2, ni_v - 0.2), 2), round(ni_v + 0.3, 2), None, "Theo ký hiệu mác"))
            if m.group(5):
                mo_v = float(m.group(6)) if m.group(6) else 0.3
                items.append(("Mo", round(max(0.1, mo_v - 0.15), 2), round(mo_v + 0.2, 2), None, "Theo ký hiệu mác"))
            return items

    # 2. Dạng Thép Mangan cao: 110Mn13Cr2, 110G14XML, G13X2, G15X2, ZGMn13Cr2, Mn18Cr2...
    m_mn = re.match(
        r"^(?:ZG)?(?:(\d{3}))?(?:Mn|G)(\d{1,2})(?:(?:Cr|X)(\d+(?:\.\d+)?)?)?",
        c,
        re.IGNORECASE,
    )
    if m_mn and ("Mangan" in raw_group or int(m_mn.group(2)) >= 8):
        c_nom = int(m_mn.group(1)) / 100.0 if m_mn.group(1) else 1.10
        mn_nom = float(m_mn.group(2))
        items.append(("C", round(c_nom - 0.2, 2), round(c_nom + 0.2, 2), None, "Theo ký hiệu mác Mn cao"))
        items.append(("Mn", round(mn_nom - 1.0, 1), round(mn_nom + 1.5, 1), None, "Theo ký hiệu mác Mn cao"))
        if m_mn.group(3):
            cr_nom = float(m_mn.group(3))
            items.append(("Cr", round(max(0.5, cr_nom - 0.5), 2), round(cr_nom + 0.5, 2), None, "Theo ký hiệu mác Mn cao"))
        elif "X" in c.upper() or "CR" in c.upper():
            items.append(("Cr", 1.5, 2.5, None, "Theo ký hiệu mác Mn cao"))
        if "MO" in c.upper() or "M" in c[4:].upper():
            items.append(("Mo", 0.15, 0.5, None, "Theo ký hiệu mác Mn cao"))
        if "NI" in c.upper() or "H" in c[4:].upper():
            items.append(("Ni", 0.3, 0.8, None, "Theo ký hiệu mác Mn cao"))
        return items

    # 3. Dạng Thép chịu nhiệt / Hợp kim Cr-Ni: ZG40Cr30Ni25, 3Cr24Ni7, ZG35Cr24Ni7SiN, GX40CrNiSi25...
    m_crni = re.match(
        r"^(?:ZG|G-?X)?(\d{1,2})Cr(?:NiSi)?(\d{2})(?:Ni|-|\.)?(\d{1,2})?",
        c,
        re.IGNORECASE,
    )
    if m_crni:
        c_raw = int(m_crni.group(1))
        c_nom = c_raw / 10.0 if c_raw < 10 else c_raw / 100.0
        cr_nom = float(m_crni.group(2))
        items.append(("C", round(max(0.05, c_nom - 0.08), 2), round(c_nom + 0.08, 2), None, "Theo ký hiệu mác"))
        items.append(("Cr", round(cr_nom - 1.5, 1), round(cr_nom + 1.5, 1), None, "Theo ký hiệu mác"))
        if m_crni.group(3):
            ni_nom = float(m_crni.group(3))
            items.append(("Ni", round(max(0.5, ni_nom - 1.5), 1), round(ni_nom + 1.5, 1), None, "Theo ký hiệu mác"))
        return items

    # 4. Dạng Thép hợp kim thấp GOST/TCVN: 30XG, 45XG, 65XG, 45CrMn, 35CrNi, 40XGMo, 45XGMo
    m_hk = re.match(r"^(\d{2})(X|Cr)(G|Mn|Ni)(Mo|M)?$", c, re.IGNORECASE)
    if m_hk:
        c_nom = int(m_hk.group(1)) / 100.0
        items.append(("C", round(c_nom - 0.05, 2), round(c_nom + 0.05, 2), None, "Theo ký hiệu mác hợp kim"))
        items.append(("Cr", 0.8, 1.2, None, "Theo ký hiệu mác hợp kim"))
        if m_hk.group(3).upper() in ("G", "MN"):
            items.append(("Mn", 0.8, 1.2, None, "Theo ký hiệu mác hợp kim"))
        elif m_hk.group(3).upper() == "NI":
            items.append(("Ni", 1.0, 1.5, None, "Theo ký hiệu mác hợp kim"))
        if m_hk.group(4):
            items.append(("Mo", 0.15, 0.30, None, "Theo ký hiệu mác hợp kim"))
        return items

    return items


def format_chem_preview(chem_list):
    parts = []
    for el, cmin, cmax, capr, _ in chem_list[:7]:
        if cmin is not None and cmax is not None:
            parts.append(f"{el}: {cmin:g}-{cmax:g}")
        elif cmax is not None:
            parts.append(f"{el}: ≤{cmax:g}")
        elif cmin is not None:
            parts.append(f"{el}: ≥{cmin:g}")
        elif capr is not None:
            parts.append(f"{el}: ~{capr:g}")
    return " | ".join(parts)


def candidate_kts_norms(code: str):
    """Sinh danh sách các mã chuẩn hóa ứng viên để tra cứu trong 40.163 mác quốc tế."""
    c = code.strip()
    # Bỏ các phần chú thích tiếng Việt phía sau
    c_base = re.split(
        r"\s*(?:\(|gắn|tôi|cấp bền|KH cấp|Liệu tái sinh|tương đương|equivalent|equiv|according to|hoặc|\bor\b|,|/|\+)",
        c,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    c_base = re.sub(
        r"^(?:Thép|Gang cầu-|Gang cầu|Gang gầu|Gang xám|Gang|Nhôm|Inox|Vautid|Steel)\s*",
        "",
        c_base,
        flags=re.IGNORECASE,
    ).strip()

    norms = []
    for cand in (c, c_base):
        n = normalize_code(cand)
        if n and n not in norms:
            norms.append(n)
        if n.startswith("ZG") and len(n) > 4:
            if n[2:] not in norms:
                norms.append(n[2:])
        if n.startswith("DIN") and len(n) > 4:
            if n[3:] not in norms:
                norms.append(n[3:])
        if n.startswith("ASTM") and len(n) > 5:
            if n[4:] not in norms:
                norms.append(n[4:])
        # Nếu có dấu gạch ngang nối mã chuẩn với W-Nr (VD: 42CrMo4-1.7225, X120Mn12-1.3401)
        if "-" in cand:
            for sub_p in cand.split("-"):
                sn = normalize_code(sub_p)
                if len(sn) >= 4 and sn not in norms:
                    norms.append(sn)

    # Một số ánh xạ tương đương thông dụng tại xưởng đúc Việt Nam (GOST / TCVN / JIS)
    common_aliases = {
        "CT3": ["ST3SP", "SS400", "Q235A"],
        "CT45": ["C45", "S45C", "1045", "45"],
        "C45": ["C45", "S45C", "1045", "45"],
        "C38": ["C35", "S38C", "S35C", "40"],
        "C35": ["C35", "S35C", "1035", "35"],
        "C30": ["C30", "S30C", "1030", "30"],
        "C20": ["C20", "S20C", "1020", "20"],
        "C25": ["C25", "S25C", "1025", "25"],
        "C50": ["C50", "S50C", "1050", "50"],
        "Q345": ["Q345A", "Q345B", "16MN", "S355JR"],
        "Q2335AF": ["Q235AF", "Q235A", "SS400"],
        "235": ["Q235A", "SS400"],
        "35L": ["ZG270500", "35", "SC480"],
        "4130": ["4130", "30CRMO", "SCM430"],
        "S355J2N": ["S355J2G3", "S355J2", "10570"],
        "40X": ["40CR", "40KH", "SCR440", "5140"],
        "20X": ["20CR", "20KH", "SCR420", "5120"],
        "40CR": ["40CR", "SCR440", "5140"],
        "35CRMO": ["35CRMO", "SCM435", "4135"],
        "42CRMO": ["42CRMO", "42CRMO4", "SCM440", "4140"],
        "40XM": ["40CRMO", "SCM440", "4140"],
        "40XMMA": ["40KHNMA", "40CRNIMO", "SNCM439", "4340"],
        "65MN": ["65MN", "65G", "1566"],
        "65G": ["65G", "65MN"],
        "55G": ["55G", "55MN", "1055"],
        "55MN": ["55MN", "55G", "1055"],
        "16G": ["16MN", "Q345A"],
        "60SIMN": ["60SI2MN", "60S2", "SUP7"],
        "9XC": ["9KHS", "90CRSI5"],
        "X12M": ["KH12M", "CR12MO1V1", "SKD11", "D2"],
        "FCD450": ["FCD450", "FCD45010", "GJS45010", "45010"],
        "FDC450": ["FCD450", "FCD45010", "GJS45010", "45010"],
        "QT40015": ["FCD400", "GJS40015", "40015"],
        "QT45010": ["FCD450", "GJS45010", "45010"],
        "FCD500": ["FCD500", "FCD5007", "GJS5007", "5007"],
        "FCD600": ["FCD600", "FCD6003", "GJS6003", "6003"],
        "FCD700": ["FCD700", "FCD7002", "GJS7002", "7002"],
        "FC200": ["FC200", "GG20", "HT200", "GJL200"],
        "F200": ["FC200", "GG20", "HT200"],
        "FC250": ["FC250", "GG25", "HT250", "GJL250"],
        "FC300": ["FC300", "GG30", "HT300", "GJL300"],
        "ENGJL300": ["GJL300", "FC300", "GG30"],
        "GXHT250": ["HT250", "FC250", "GG25"],
        "110G13L": ["110G13L", "ZGMN131", "SCMNH1", "X120MN12", "13401"],
        "G13X2": ["SCMNH11", "ZGMN13CR2", "110G13KH2L"],
        "T13X2": ["G13X2", "SCMNH11"],
        "SCMNH12": ["SCMNH11", "SCMNH2"],
        "ZGMN13CR2": ["SCMNH11", "110G13L"],
        "SCW480": ["SCW480", "WCB"],
        "SCW410": ["SCW410"],
        "SUS304": ["SUS304", "304", "14301", "08KH18N10"],
        "SUS316": ["SUS316", "316", "14401"],
        "SUS310": ["SUS310", "310", "SUS310S", "14845"],
        "SUS420": ["SUS420J1", "SUS420J2", "420", "14021"],
        "14310": ["14310", "SUS301", "301"],
        "43100": ["14310", "SUS301"],
        "SKD61": ["SKD61", "H13", "12344"],
        "SKD11": ["SKD11", "D2", "12379"],
        "SCH21": ["SCH21", "HK40", "GX40CRNISI2520", "14848"],
        "SCH22": ["SCH22", "HK40", "GX40CRNISI2520", "14848"],
        "SCH13": ["SCH13", "HH", "GX40CRNISI2512", "14837"],
        "SCH11": ["SCH11", "HD"],
        "SCH2": ["SCH2"],
        "BTMCR26": ["CR26", "HICR27", "TXC3"],
        "BTMCR26GT": ["CR26", "HICR27", "TXC3"],
        "KMTBCR26": ["CR26", "HICR27", "TXC3"],
        "KMTBCR27MO2CU1": ["HICR27", "270CR27MO15"],
        "E28CA": ["E28CH"],
        "E28HC": ["E28CH"],
        "E28H": ["E28CH"],
        "E20CH": ["E20H"],
        "TXL1": ["TZL1"],
        "TXS3": ["TXS1"],
    }
    for n in list(norms):
        if n in common_aliases:
            for al in common_aliases[n]:
                if al not in norms:
                    norms.append(al)
    return norms


def import_vico_grades(conn: sqlite3.Connection = None):
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    cur = conn.cursor()

    if not os.path.exists(VICO_JSON_PATH):
        print(f"Không tìm thấy {VICO_JSON_PATH}, bỏ qua bước nạp 685 mác VICO.")
        return

    with open(VICO_JSON_PATH, "r", encoding="utf-8") as f:
        vico_rows = json.load(f)

    # Cập nhật tên tiêu chuẩn NOI_BO trong bảng standards
    cur.execute(
        """
        INSERT OR REPLACE INTO standards (standard, country, is_internal)
        VALUES ('NOI_BO', 'Tiêu chuẩn Nội bộ VICO (Công ty TNHH Đúc Thắng Lợi)', 1)
        """
    )

    # Xóa các mác VICO cũ (vk_key >= 9000010) nếu chạy lại script để tránh trùng lặp
    cur.execute("DELETE FROM chem_comp WHERE vk_key >= 9000010")
    cur.execute("DELETE FROM mech_props WHERE vk_key >= 9000010")
    cur.execute("DELETE FROM heat_treatments WHERE vk_key >= 9000010")
    cur.execute("DELETE FROM cross_ref WHERE vk_key >= 9000010")
    cur.execute("DELETE FROM materials WHERE vk_key >= 9000010")

    # Tải bảng tra cứu nhanh các mác quốc tế từ kts_database.db (is_internal = 0)
    print("Đang lập chỉ mục đối chiếu với 40.163 mác quốc tế Key to Steel...")
    cur.execute(
        """
        SELECT vk_key, material, material_norm, standard, chem_preview, mech_preview
        FROM materials
        WHERE is_internal = 0
        """
    )
    kts_by_norm = defaultdict(list)
    for vk, mat, mnorm, std, cprev, mprev in cur.fetchall():
        if mnorm:
            kts_by_norm[mnorm].append((vk, mat, std, cprev, mprev))

    # Tải bảng cross_ref của KTS (vk_key -> list crt_num)
    cur.execute("SELECT vk_key, crt_num FROM cross_ref WHERE ref_type != 'INTERNAL'")
    kts_crt_by_vk = defaultdict(list)
    for vk, crt in cur.fetchall():
        kts_crt_by_vk[vk].append(crt)

    # PASS 1: Phân tích thành phần hóa học trực tiếp trên từng dòng VICO
    vico_chem_by_norm = {}
    parsed_direct_list = []

    for r in vico_rows:
        code = r["code"]
        name = r["name"]
        chem_raw = r["chem"]

        # Ưu tiên lấy từ cột Thành phần hóa học, sau đó gộp thêm từ Mã/Tên nếu có
        c_items = parse_chem_string(chem_raw)
        seen_e = {x[0] for x in c_items}

        # Nếu tên hoặc mã có chứa chuỗi hóa học (VD: C2.7-3.2, Cr>=12... hoặc Cr12-14 hoặc C2.8-3.2 Cr28-30)
        for extra_src in (name, code):
            if any(ch in extra_src for ch in (",", "<", ">", "≤", "≥", "~", "%")) or re.search(
                r"\b(?:C|Cr|Mn|Si|Ni|Mo)\s*\d+(?:\.\d+)?\s*-\s*\d+", extra_src
            ):
                for item in parse_chem_string(extra_src):
                    if item[0] not in seen_e:
                        seen_e.add(item[0])
                        c_items.append(item)

        parsed_direct_list.append(c_items)
        if c_items:
            nc = normalize_code(code)
            if nc and nc not in vico_chem_by_norm:
                vico_chem_by_norm[nc] = (code, c_items)

    # PASS 2: Nạp toàn bộ 685 mác vào cơ sở dữ liệu
    base_vk = 9000010
    mat_inserts = []
    chem_inserts = []
    mech_inserts = []
    crt_inserts = []

    stats = {"direct_chem": 0, "vico_inherited_chem": 0, "kts_inherited_chem": 0, "inferred_chem": 0, "linked_crt": 0}

    for idx, r in enumerate(vico_rows, 1):
        vk = base_vk + idx
        internal_code = f"VICO-{idx:03d}"
        code = r["code"]
        name = r["name"]
        std_hint = r["std_hint"]
        raw_group = r["group"]
        chem_raw = r["chem"]
        note_raw = r["note"]

        grp_code, vn_group_name = map_group_info(raw_group)
        subgroup = f"{vn_group_name}" + (f" [{std_hint}]" if std_hint else "")
        subgroup_desc = name if (name and not name.startswith("Mac thep duc noi bo VICO:")) else f"Mác vật liệu VICO: {code} ({vn_group_name})"

        # Tìm mác quốc tế tương đương trong Key to Steel
        cand_norms = candidate_kts_norms(code)
        matched_kts_vks = []
        best_kts = None
        for cn in cand_norms:
            if cn in kts_by_norm:
                matches = kts_by_norm[cn]
                # Ưu tiên bản ghi có cả chem_preview và mech_preview
                matches_sorted = sorted(matches, key=lambda x: (bool(x[3]), bool(x[4])), reverse=True)
                if best_kts is None:
                    best_kts = matches_sorted[0]
                for m_item in matches_sorted[:5]:
                    if m_item[0] not in matched_kts_vks:
                        matched_kts_vks.append(m_item[0])

        # Liên kết bảng tương đương (cross_ref)
        linked_crts = set()
        for k_vk in matched_kts_vks:
            for crt_num in kts_crt_by_vk.get(k_vk, []):
                linked_crts.add(crt_num)
        for crt_num in linked_crts:
            crt_inserts.append((vk, crt_num, "INTERNAL"))
        if linked_crts:
            stats["linked_crt"] += 1

        # Xác định thành phần hóa học
        c_items = list(parsed_direct_list[idx - 1])
        chem_source_note = ""
        if c_items:
            stats["direct_chem"] += 1
        else:
            # Thử kế thừa từ mác gốc VICO trong cùng sheet
            for cn in cand_norms:
                if cn in vico_chem_by_norm:
                    src_code, src_items = vico_chem_by_norm[cn]
                    c_items = [
                        (el, cmin, cmax, capr, f"Theo mác VICO gốc ({src_code})")
                        for el, cmin, cmax, capr, _ in src_items
                    ]
                    chem_source_note = f"Kế thừa thành phần từ mác VICO gốc: {src_code}"
                    stats["vico_inherited_chem"] += 1
                    break

        if not c_items and best_kts is not None:
            # Kế thừa từ mác quốc tế tương đương trong Key to Steel
            k_vk, k_mat, k_std, _, _ = best_kts
            cur.execute(
                """
                SELECT element, c_min, c_max, c_approx
                FROM chem_comp
                WHERE vk_key = ?
                ORDER BY key_num ASC, id ASC
                """,
                (k_vk,),
            )
            seen_el = set()
            for el, cmin, cmax, capr in cur.fetchall():
                if el and el not in seen_el and (cmin is not None or cmax is not None or capr is not None):
                    seen_el.add(el)
                    c_items.append((el, cmin, cmax, capr, f"Tham chiếu TC {k_std} ({k_mat})"))
            if c_items:
                chem_source_note = f"Tham chiếu thành phần từ mác quốc tế: {k_mat} ({k_std})"
                stats["kts_inherited_chem"] += 1

        if not c_items:
            # Suy luận từ ký hiệu mác đúc VICO / GOST (VD: 280Cr28, 110Mn13Cr2, G15X2)
            c_items = infer_chem_from_vico_code(code, raw_group)
            if c_items:
                chem_source_note = "Ước tính từ ký hiệu mác nội bộ VICO"
                stats["inferred_chem"] += 1

        for el, cmin, cmax, capr, ctxt in c_items:
            # Với mác nội bộ VICO, gán luôn internal_min / internal_max bằng c_min / c_max để hiển thị nổi bật
            chem_inserts.append((vk, 1, el, cmin, cmax, capr, ctxt, cmin, cmax))

        chem_preview = format_chem_preview(c_items)

        # Kế thừa cơ tính tham chiếu từ Key to Steel nếu có mác quốc tế khớp
        mech_preview = ""
        if best_kts is not None:
            k_vk, k_mat, k_std, _, k_mprev = best_kts
            cur.execute(
                """
                SELECT no, dimen, y_min, y_max, t_min, t_max, e_min, i_min, hardness
                FROM mech_props
                WHERE vk_key = ?
                ORDER BY key_num ASC, no ASC
                LIMIT 3
                """,
                (k_vk,),
            )
            m_rows = cur.fetchall()
            for mno, dim, ymin, ymax, tmin, tmax, emin, imin, hard in m_rows:
                dim_label = f"{dim} (TC {k_std}: {k_mat})" if dim else f"Tham chiếu TC {k_std} ({k_mat})"
                mech_inserts.append((vk, 1, mno, dim_label, ymin, ymax, tmin, tmax, emin, imin, hard))
            mech_preview = k_mprev or ""

        # Xây dựng thông tin Ứng dụng (application) & Ghi chú đúc (casting_notes)
        # Cột Ghi chú có dạng: 'SP: 610 | VD: Tấm đe búa đập clinker HT3-PN418397'
        sp_count_str = ""
        vd_prod_str = ""
        if note_raw:
            for part in note_raw.split("|"):
                p = part.strip()
                if p.startswith("SP:"):
                    sp_count_str = p.replace("SP:", "").strip()
                elif p.startswith("VD:"):
                    vd_prod_str = p.replace("VD:", "").strip()

        appl_parts = []
        if vd_prod_str:
            appl_parts.append(f"Sản phẩm điển hình tại VICO: {vd_prod_str}")
        if sp_count_str:
            appl_parts.append(f"Số lượng mã sản phẩm sử dụng: {sp_count_str} SP")
        application = " | ".join(appl_parts) if appl_parts else note_raw

        casting_notes_parts = [f"Phân nhóm NCCN: {vn_group_name}"]
        if std_hint:
            casting_notes_parts.append(f"Tiêu chuẩn gốc/tham chiếu: {std_hint}")
        if chem_raw:
            casting_notes_parts.append(f"TPHH gốc trên hồ sơ NCCN: {chem_raw}")
        elif chem_source_note:
            casting_notes_parts.append(chem_source_note)
        if name and "trước" in name.lower():
            casting_notes_parts.append(f"Lưu ý nấu luyện: {name}")

        casting_notes = " | ".join(casting_notes_parts)
        comment = f"Mã danh mục NCCN: {internal_code} ({vn_group_name})" + (f" - {sp_count_str} sản phẩm" if sp_count_str else "")

        mat_inserts.append(
            (
                vk,
                code,
                normalize_code(code),
                "NOI_BO",
                "Việt Nam (Nội bộ VICO)",
                grp_code,
                subgroup,
                subgroup_desc,
                comment,
                application,
                "Đúc / Gia công cơ khí VICO",
                "",
                chem_preview,
                mech_preview,
                1,
                internal_code,
                casting_notes,
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
        mat_inserts,
    )

    cur.executemany(
        """
        INSERT INTO chem_comp (vk_key, key_num, element, c_min, c_max, c_approx, c_text, internal_min, internal_max)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        chem_inserts,
    )

    cur.executemany(
        """
        INSERT INTO mech_props (vk_key, key_num, no, dimen, y_min, y_max, t_min, t_max, e_min, i_min, hardness)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        mech_inserts,
    )

    cur.executemany(
        """
        INSERT INTO cross_ref (vk_key, crt_num, ref_type)
        VALUES (?, ?, ?)
        """,
        crt_inserts,
    )

    conn.commit()
    total_with_chem = (
        stats["direct_chem"] + stats["vico_inherited_chem"] + stats["kts_inherited_chem"] + stats["inferred_chem"]
    )
    print(f"Đã nạp thành công {len(mat_inserts)} mác vật liệu VICO từ sheet 'NCCN-Vật liệu - Mác thép'!")
    print(f"  - Có thành phần hóa học trực tiếp trên sheet : {stats['direct_chem']} mác")
    print(f"  - Kế thừa từ mác VICO gốc cùng họ            : {stats['vico_inherited_chem']} mác")
    print(f"  - Đối chiếu & kế thừa từ mác quốc tế KTS     : {stats['kts_inherited_chem']} mác")
    print(f"  - Suy luận từ ký hiệu mác đúc (Cr/Mn/C)      : {stats['inferred_chem']} mác")
    print(f"  => Tổng số mác VICO có phổ thành phần hóa học: {total_with_chem}/{len(mat_inserts)} mác")
    print(f"  => Số mác VICO tự động gắn bảng tương đương  : {stats['linked_crt']} mác ({len(crt_inserts)} liên kết)")

    if close_conn:
        conn.close()


if __name__ == "__main__":
    import_vico_grades()
