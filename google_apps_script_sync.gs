/**
 * ============================================================================
 * GOOGLE APPS SCRIPT ĐỒNG BỘ 2 CHIỀU CHO PHẦN MỀM TRA CỨU MÁC THÉP VICO
 * File Google Sheet: 1IcZfCBDyr1oQjFc1bkEJ_0ZdO2imQ114SVj0KYR5u4c
 * Sheet chính      : 'NCCN-Vật liệu - Mác thép'
 * Sheet phụ tự tạo : 'VICO_Web_Sync' (Lưu thông số mở rộng & lịch sử xóa từ Web)
 * ============================================================================
 *
 * HƯỚNG DẪN CÀI ĐẶT (Chỉ làm 1 lần duy nhất - mất khoảng 1 phút):
 * 1. Mở file Google Sheet của công ty:
 *    https://docs.google.com/spreadsheets/d/1IcZfCBDyr1oQjFc1bkEJ_0ZdO2imQ114SVj0KYR5u4c/edit
 * 2. Trên thanh menu của Google Sheet, chọn: Tiện ích mở rộng (Extensions) -> Apps Script.
 * 3. Xóa đoạn mã mặc định (myFunction) và DÁN TOÀN BỘ nội dung file này vào, sau đó bấm biểu tượng Đĩa mềm (Ctrl+S) để Lưu.
 * 4. Ở góc trên bên phải, bấm nút "Triển khai" (Deploy) -> "Tùy chọn triển khai mới" (New deployment):
 *    - Chọn loại (Select type - biểu tượng bánh răng): "Ứng dụng web" (Web app).
 *    - Thực thi dưới dạng (Execute as): "Tôi" (Me).
 *    - Ai có quyền truy cập (Who has access): Chọn "Bất kỳ ai" (Anyone).
 * 5. Bấm "Triển khai" (Deploy) -> Cấp quyền truy cập (Authorize access) -> Copy đường dẫn URL Web App
 *    (có dạng: https://script.google.com/macros/s/AKfycb.../exec).
 * 6. Mở trang Web Tra cứu Mác thép -> Đăng nhập Admin -> Bấm nút [☁️ Cấu hình Google Sheet] -> Dán URL đó vào!
 */

const SHEET_NAME_MAIN = 'NCCN-Vật liệu - Mác thép';
const SHEET_NAME_META = 'VICO_Web_Sync';
// Mặc định SHA-256 của mật khẩu Admin 'vico@2026'
const DEFAULT_ADMIN_HASH = 'ee2b60d43958c518f1dc1dca3722f88d2269610e13a19317b6b17e0e71746273';

function getOrCreateMetaSheet(ss) {
  let metaSheet = ss.getSheetByName(SHEET_NAME_META);
  if (!metaSheet) {
    metaSheet = ss.insertSheet(SHEET_NAME_META);
    metaSheet.getRange(1, 1, 1, 5).setValues([[
      'vk_key', 'material', 'action', 'json_payload', 'updated_at'
    ]]);
    metaSheet.setFrozenRows(1);
  }
  return metaSheet;
}

function getAdminHash() {
  const props = PropertiesService.getScriptProperties();
  return props.getProperty('VICO_ADMIN_HASH') || DEFAULT_ADMIN_HASH;
}

/**
 * Xử lý yêu cầu ĐỌC dữ liệu (GET) từ mọi máy tính mở trang Web / GitHub Pages
 */
function doGet(e) {
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const mainSheet = ss.getSheetByName(SHEET_NAME_MAIN);
    const metaSheet = getOrCreateMetaSheet(ss);

    // 1. Đọc toàn bộ dòng từ sheet 'NCCN-Vật liệu - Mác thép'
    const mainRows = [];
    if (mainSheet) {
      const vals = mainSheet.getDataRange().getValues();
      let curGroup = '';
      for (let r = 2; r < vals.length; r++) {
        const row = vals[r];
        const c1 = row[0] !== null && row[0] !== undefined ? String(row[0]).trim() : '';
        const c2 = row[1] !== null && row[1] !== undefined ? String(row[1]).trim() : '';
        if (c1 && !c2) {
          curGroup = c1;
          continue;
        }
        if (c2) {
          mainRows.push({
            sheet_row: r + 1,
            stt: c1,
            group: curGroup,
            code: c2,
            name: String(row[2] || '').trim(),
            std_hint: String(row[3] || '').trim(),
            chem: String(row[4] || '').trim(),
            mech: String(row[5] || '').trim(),
            note: String(row[6] || '').trim()
          });
        }
      }
    }

    // 2. Đọc các bản ghi chi tiết / lịch sử xóa từ sheet 'VICO_Web_Sync'
    const overrides = [];
    const deletedKeys = [];
    const metaVals = metaSheet.getDataRange().getValues();
    for (let i = 1; i < metaVals.length; i++) {
      const vk = Number(metaVals[i][0]);
      const act = String(metaVals[i][2] || '').trim();
      const rawJson = String(metaVals[i][3] || '').trim();
      if (act === 'DELETE') {
        if (vk) deletedKeys.push(vk);
      } else if (act === 'UPSERT' && rawJson) {
        try {
          overrides.push(JSON.parse(rawJson));
        } catch (err) {}
      }
    }

    const result = {
      status: 'ok',
      updated_at: new Date().toISOString(),
      sheet_rows: mainRows,
      overrides: overrides,
      deleted_vk_keys: deletedKeys
    };

    return ContentService
      .createTextOutput(JSON.stringify(result))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'error', message: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * Xử lý yêu cầu GHI dữ liệu (POST) khi Admin Thêm / Sửa / Xóa mác nội bộ trên Web
 */
function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents || '{}');
    const clientHash = String(body.admin_hash || '').trim();
    if (!clientHash || clientHash !== getAdminHash()) {
      return ContentService
        .createTextOutput(JSON.stringify({ status: 'error', message: 'Từ chối truy cập: Mật khẩu Admin không hợp lệ!' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const action = body.action;
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const mainSheet = ss.getSheetByName(SHEET_NAME_MAIN);
    const metaSheet = getOrCreateMetaSheet(ss);
    const nowStr = Utilities.formatDate(new Date(), 'GMT+7', 'yyyy-MM-dd HH:mm:ss');

    if (action === 'change_password') {
      const newHash = String(body.new_hash || '').trim();
      if (newHash.length === 64) {
        PropertiesService.getScriptProperties().setProperty('VICO_ADMIN_HASH', newHash);
        return ContentService
          .createTextOutput(JSON.stringify({ status: 'ok', message: 'Đã đồng bộ mật khẩu Admin mới lên Google Sheet!' }))
          .setMimeType(ContentService.MimeType.JSON);
      }
    }

    if (action === 'upsert') {
      const item = body.item || {};
      const vk = Number(item.vk_key);
      const code = String(item.material || '').trim();

      // Định dạng chuỗi Thành phần hóa học & Cơ tính chuẩn theo cột của sheet 'NCCN-Vật liệu - Mác thép'
      const chemStr = (item.chem || []).map(c => {
        if (c.c_min !== null && c.c_min !== undefined && c.c_max !== null && c.c_max !== undefined) {
          return c.element + c.c_min + '-' + c.c_max;
        } else if (c.c_max !== null && c.c_max !== undefined) {
          return c.element + '≤' + c.c_max;
        } else if (c.c_min !== null && c.c_min !== undefined) {
          return c.element + '≥' + c.c_min;
        }
        return '';
      }).filter(Boolean).join(', ');

      const mechStr = (item.mech || []).map(m => {
        const p = [];
        if (m.y_min) p.push('Re≥' + m.y_min);
        if (m.t_min) p.push('Rm≥' + m.t_min);
        if (m.e_min) p.push('A≥' + m.e_min + '%');
        if (m.hardness) p.push(m.hardness);
        return p.join(', ');
      }).filter(Boolean).join(' | ');

      const noteStr = [item.application, item.casting_notes].filter(Boolean).join(' | ');

      // 1. Cập nhật hoặc Thêm dòng mới vào sheet 'NCCN-Vật liệu - Mác thép'
      if (mainSheet && code) {
        const vals = mainSheet.getDataRange().getValues();
        let foundRow = -1;
        let maxStt = 0;
        for (let r = 2; r < vals.length; r++) {
          const sttVal = parseInt(vals[r][0], 10);
          if (!isNaN(sttVal) && sttVal > maxStt) maxStt = sttVal;
          const rowCode = String(vals[r][1] || '').trim();
          if (rowCode.toUpperCase() === code.toUpperCase()) {
            foundRow = r + 1;
            break;
          }
        }

        const nameStr = item.subgroup_desc || ('Mác thép đúc nội bộ VICO: ' + code);
        const stdStr = item.subgroup || item.standard || 'Nội bộ VICO';

        if (foundRow > 0) {
          mainSheet.getRange(foundRow, 2, 1, 6).setValues([[
            code, nameStr, stdStr, chemStr, mechStr, noteStr
          ]]);
        } else {
          mainSheet.appendRow([
            maxStt + 1, code, nameStr, stdStr, chemStr, mechStr, noteStr
          ]);
        }
      }

      // 2. Lưu bản ghi JSON đầy đủ vào 'VICO_Web_Sync'
      const mVals = metaSheet.getDataRange().getValues();
      let foundMetaRow = -1;
      for (let i = 1; i < mVals.length; i++) {
        if (Number(mVals[i][0]) === vk) {
          foundMetaRow = i + 1;
          break;
        }
      }
      const rowData = [vk, code, 'UPSERT', JSON.stringify(item), nowStr];
      if (foundMetaRow > 0) {
        metaSheet.getRange(foundMetaRow, 1, 1, 5).setValues([rowData]);
      } else {
        metaSheet.appendRow(rowData);
      }

      return ContentService
        .createTextOutput(JSON.stringify({ status: 'ok', vk_key: vk, message: 'Đã ghi trực tiếp vào Google Sheet!' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    if (action === 'delete') {
      const vk = Number(body.vk_key);
      const code = String(body.material || '').trim();

      // 1. Xóa dòng tương ứng trên sheet 'NCCN-Vật liệu - Mác thép' nếu khớp tên mác
      if (mainSheet && code) {
        const vals = mainSheet.getDataRange().getValues();
        for (let r = vals.length - 1; r >= 2; r--) {
          const rowCode = String(vals[r][1] || '').trim();
          if (rowCode.toUpperCase() === code.toUpperCase()) {
            mainSheet.deleteRow(r + 1);
            break;
          }
        }
      }

      // 2. Đánh dấu DELETE trong 'VICO_Web_Sync' để mọi trình duyệt khi F5 cũng ẩn mác này
      const mVals = metaSheet.getDataRange().getValues();
      let foundMetaRow = -1;
      for (let i = 1; i < mVals.length; i++) {
        if (Number(mVals[i][0]) === vk) {
          foundMetaRow = i + 1;
          break;
        }
      }
      const rowData = [vk, code, 'DELETE', '', nowStr];
      if (foundMetaRow > 0) {
        metaSheet.getRange(foundMetaRow, 1, 1, 5).setValues([rowData]);
      } else {
        metaSheet.appendRow(rowData);
      }

      return ContentService
        .createTextOutput(JSON.stringify({ status: 'ok', vk_key: vk, message: 'Đã xóa mác khỏi Google Sheet!' }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    return ContentService
      .createTextOutput(JSON.stringify({ status: 'error', message: 'Hành động không hợp lệ: ' + action }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'error', message: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
