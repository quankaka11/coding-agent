# Chuẩn giao diện e2ea

Một chuẩn, không có ngoại lệ ngầm. Sửa UI thì sửa theo file này; thấy file này
sai thì sửa file này trước, đừng sửa lệch trong component.

Nền: **Swiss / Minimalism + Data-Dense Dashboard**, mẫu **Real-Time Monitoring**.
Quy tắc gốc của mẫu đó, và cũng là lý do bản trước không track được gì:

> Chỉ được gọi là *live* khi có nguồn hiện hành — kèm **thời điểm cập nhật** và
> **trạng thái mất tín hiệu**.

## 1. Bốn trạng thái, bốn màu

Đây là toàn bộ bảng màu mang nghĩa. Không có màu thứ năm.

| Trạng thái | Token | Dùng cho |
|---|---|---|
| Xong việc | `--ok` (verdigris) | đã mở MR, kiểm tra đạt, vòng quét sống |
| **Đang chạy** | `--busy` (amber) | run đang chạy, job đang dựng, đang thử kết nối |
| Cần người | `--human` (stamp) | plan chờ duyệt, cần người, luật G không đạt |
| Trung tính | `--quiet` (xám) | không ra MR, không áp dụng, đã dừng |

`--busy` là nấc thiếu của bản trước: "đang chạy" rơi vào xám, cùng ô với "không
ra MR". Trạng thái duy nhất đang diễn ra mà lại là trạng thái vô hình nhất.

**Mất tín hiệu** (`stale`) không phải màu thứ năm — nó là `--human` kèm chữ
"mất tín hiệu", vì đó là việc đòi người ra tay.

Màu không bao giờ là kênh thông tin duy nhất: mọi pill đều có chữ, mọi chấm
trạng thái đều đi kèm nhãn.

## 2. Từ vựng — mỗi khái niệm đúng một chữ

Tất cả nằm trong `src/lib/strings.ts`. Không viết chữ trực tiếp trong component.

| Dùng | Không dùng |
|---|---|
| lần chạy | run |
| nhãn | category, label |
| Thời gian | Mất |
| Chi phí | Tốn |
| mất tín hiệu | treo, chết, timeout |
| vòng quét | watcher, watch |

- Dấu ba chấm: luôn `…`, không bao giờ `...`
- Câu trong `block-note` viết thường, không chấm câu cuối; câu phản hồi thao tác
  viết hoa đầu câu và có chấm câu.
- Tên tab luôn kèm mô tả một dòng ở `page-sub`.
- Lỗi kỹ thuật không lọt thẳng ra màn hình: đi qua `humanError()`.

## 3. Định dạng số — một hàm, dùng khắp nơi

| Kiểu | Chuẩn | Ví dụ |
|---|---|---|
| Thời lượng | `duration()` | `2 phút 3 giây`, `47 giây` |
| Khoảng cách | `since()` | `3 phút trước` |
| Tiền | `money()` | `$0.043` — 3 chữ số thập phân, vì chi phí agent thường dưới 1 cent |
| Thời điểm | `clock()` | `21/09 14:30` (hôm nay bỏ ngày), có `title` đầy đủ khi hover |
| Tỉ lệ | `pct()` | `62%` |

Bỏ `2′03″`: dấu prime là ký hiệu góc/phút cung, không phải thời lượng.

## 4. Tiến độ

Nguồn duy nhất: `e2e_agent/web/progress.py`.

- Mọi lần chạy đang sống hiện `Bước 4/9 · Sửa code`, kèm thanh tiến độ.
- Bước tuỳ nghi vẫn nằm trong mẫu số → thanh không bao giờ tụt ngược.
- Vòng tự sửa lặp lại một bước không đẩy tiến độ lùi.
- Không có sự kiện mới quá 10 phút → `stale`, đổi sang "mất tín hiệu".

## 5. Điều hướng

- URL là nguồn sự thật: `#/truc`, `#/run`, `#/run/<ticket>/<run>`, `#/cau-hinh`,
  `#/so-do`. F5 phải về đúng chỗ cũ; link một lần chạy phải dán được cho người khác.
- Nút Back của trình duyệt đi lùi trong app, không thoát app.

## 6. Ngưỡng bắt buộc

- Vùng bấm: 44×44 cho hành động chính, 32×32 cho nút trong vùng dày đặc,
  24×24 cho link chữ nằm trong dòng hoặc trong ô bảng (ngoại lệ inline của
  WCAG 2.2 Target Size Minimum). `.btn-small` cũ cao 24px — không đạt nấc nào.
- Không tràn ngang ở bất kỳ bề rộng nào. Cột lưới bọc nội dung cuộn được phải
  là `minmax(0, 1fr)`, không phải `1fr`.
- Tương phản chữ ≥ 4.5:1 ở cả hai theme.
- `:focus-visible` luôn thấy được, không bao giờ gỡ outline.
- Chuyển động 150–250ms, và `prefers-reduced-motion` tắt hết.
- Chạy được ở 375 / 768 / 1024 / 1440.
- Không emoji làm icon; icon là SVG inline.
- Không `style={{}}` trong component. Ngoại lệ duy nhất: giá trị tính từ dữ
  liệu mà CSS không biết trước, ví dụ bề rộng thanh tiến độ.
