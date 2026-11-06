# Hệ thống Tra cứu Mác Thép & Gang Nội bộ - Công ty Đúc (VICO Material)

Phần mềm tra cứu mác vật liệu kim loại tích hợp cơ sở dữ liệu quốc tế **Key to Steel v2005.11 (40.163 mác thép/gang)** và **Mác nội bộ Công ty Đúc**.

## Tính năng chính
1. **Tra cứu & Quy đổi Mác tương đương (Cross-Reference)**:
   - Tra cứu 40.167 mác vật liệu thuộc 36 bộ tiêu chuẩn quốc tế (`JIS`, `ASTM`, `AISI`, `SAE`, `DIN`, `EN`, `GOST`, `GB`, `ISO`, `BS`...) và tiêu chuẩn nội bộ.
   - Lọc theo nhóm vật liệu (Thép đúc, Gang đúc, Thép không gỉ, Thép kết cấu, Thép dụng cụ) và lọc theo cơ tính ($R_e$, $R_m$, $A\%$, $KV$, Độ cứng).
2. **Đối sánh Quang phổ Lò nấu (Spectrometer Matcher)**:
   - Nhập % các nguyên tố vừa phân tích mẫu tại lò nấu (`C`, `Si`, `Mn`, `P`, `S`, `Cr`, `Ni`, `Mo`, `Mg`...) để định danh mác thép và kiểm tra nguyên tố dư/thiếu.
3. **So sánh Trực quan & Xuất Phiếu Kỹ thuật (Excel / PDF)**:
   - Đặt 2–5 mác cạnh nhau để đối chiếu thành phần hóa học, cơ tính, nhiệt luyện và xuất báo cáo Excel.
4. **Quản lý Mác Nội bộ Công ty Đúc**:
   - Tạo mới, chỉnh sửa, sao chép từ mác quốc tế sang mác nội bộ, lưu dải kiểm soát nội bộ lò nấu và ghi chú công nghệ đúc.

## Chế độ hoạt động Kép (Hybrid Dual-Mode)
- **Chạy trực tiếp trên GitHub Pages**: Tự động giải nén bộ dữ liệu tĩnh `data/*.json.gz` (~2.7 MB chứa toàn bộ 40.167 mác thép) ngay trên trình duyệt bằng `DecompressionStream('gzip')`.
- **Chạy Server nội bộ LAN (Windows)**: Chạy `run_server.bat` để khởi động FastAPI + SQLite (`kts_database.db`) tại `http://localhost:8055`.

