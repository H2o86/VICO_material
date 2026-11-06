# Hệ Thống Tra Cứu Mác Thép & Gang Nội Bộ - Công Ty Đúc (VICO)

Phần mềm tra cứu, quy đổi tương đương, đối sánh quang phổ lò nấu và quản lý danh mục vật liệu đúc nội bộ của **Công ty TNHH Đúc Thắng Lợi (VICO)**, được xây dựng trên nền tảng cơ sở dữ liệu **Key to Steel v2005.11 (40.163 mác quốc tế)** kết hợp với **689 mác thép & gang nội bộ VICO** từ hồ sơ `'NCCN-Vật liệu - Mác thép'`.

---

## 1. Giải Pháp Kiến Trúc Cốt Lõi

1. **Giải mã dữ liệu gốc Key to Steel v2005.11 ([build_db.py](build_db.py))**:
   - Giải nén và giải mã 19 bảng dữ liệu FoxPro (`.DBF` / `.FPT`) bằng thuật toán dịch mã vị trí ký tự (`PlainByte[i] = CipherByte[i] + (i % 7) + 1`) và đọc con trỏ khối nhớ nhị phân `.FPT`.
   - Khôi phục toàn vẹn **40.163 mác quốc tế**, **347.428 dòng thành phần hóa học**, **71.529 dòng cơ tính**, **11.562 hướng dẫn nhiệt luyện** và **32.332 liên kết quy đổi tương đương**.
2. **Tích hợp & Làm giàu 685 Mác Nội Bộ VICO ([import_vico_sheet.py](import_vico_sheet.py))**:
   - Tự động phân tích cú pháp thành phần hóa học từ sheet `'NCCN-Vật liệu - Mác thép'`, kế thừa biến thể nội bộ, suy luận theo quy ước ký hiệu mác đúc và đối chiếu tự động với 40.163 mác quốc tế (gắn 454 liên kết tương đương).
3. **Kiến trúc Chạy Kép (Hybrid Dual-Mode - [index.html](index.html) & [app.py](app.py))**:
   - **Chế độ Web Tĩnh trên GitHub Pages**: Nén toàn bộ cơ sở dữ liệu `57.9 MB` xuống `2.83 MB` (`data/*.json.gz` qua [export_static_data.py](export_static_data.py)) và giải nén trực tiếp trên trình duyệt bằng `DecompressionStream('gzip')` trong ~0.5 giây.
   - **Chế độ Máy chủ LAN Nội bộ**: Khởi chạy qua [run_server.bat](run_server.bat) (FastAPI + SQLite `kts_database.db` tại `http://localhost:8055`).
4. **Đồng bộ Đám mây 2 Chiều với Google Sheet ([google_apps_script_sync.gs](google_apps_script_sync.gs))**:
   - **Chiều ĐỌC tự động**: Mỗi khi mở trang hoặc nhấn F5, trình duyệt tự động đồng bộ danh sách mới nhất từ sheet `'NCCN-Vật liệu - Mác thép'`.
   - **Chiều GHI bảo mật**: Khi Admin Thêm/Sửa/Xóa mác nội bộ trên Web, dữ liệu được đẩy qua Google Apps Script Web App để cập nhật thẳng vào Google Sheet của công ty.

---

## 2. Các Phân Hệ Tính Năng Chính

- **🔍 Phân hệ 1 — Tra cứu Đa chiều & Quy đổi Tương đương**: Tìm theo tên mác, mã VICO, phân nhóm NCCN, lọc theo 62 bộ tiêu chuẩn, 10 nhóm vật liệu, lọc theo cơ tính ($R_e, R_m, A\%, KV, \text{HB/HRC}$) và tra bảng mác tương đương quốc tế.
- **⚡ Phân hệ 2 — Đối sánh Quang phổ Lò nấu (Spectrometer Matcher)**: Nhập kết quả bắn quang phổ thực tế tại lò để chấm điểm độ khớp (`0 - 100%`) và cảnh báo nguyên tố vượt ngưỡng/thiếu hụt so với dải tiêu chuẩn và dải kiểm soát nội bộ.
- **⚖️ Phân hệ 3 — So sánh Trực quan & Xuất Báo cáo Excel / In Phiếu**: Đặt cạnh nhau tối đa 6 mác vật liệu để đối chiếu từng nguyên tố hóa học, cơ tính, nhiệt luyện, ghi chú đúc và xuất file Excel.
- **🏭 Phân hệ 4 — Quản lý Mác Nội bộ VICO (26 phân nhóm NCCN)**: Tìm kiếm, lọc theo phân nhóm NCCN, xem thống kê sản phẩm sử dụng (`SP`) & ví dụ chi tiết đúc điển hình (`VD`), nhân bản nhanh mác quốc tế thành mác nội bộ.
- **🛡️ Phân hệ 5 — Phân quyền Admin (RBAC) & Bảo vệ Dữ liệu**: Mặc định khóa ở **Chế độ Chỉ đọc (Read-only)** cho người dùng thông thường; chỉ mở khóa Thêm/Sửa/Xóa khi đăng nhập mật khẩu Quản trị viên (`SHA-256`, mặc định: `vico@2026`).
