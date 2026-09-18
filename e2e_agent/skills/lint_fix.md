# Lint fix — sửa lint có sẵn TRƯỚC khi sửa code

Lint đang đỏ trên code **chưa ai sửa**. Đây không phải lỗi của task, nhưng gate về
sau đòi lint xanh, nên phải dọn trước — bằng **một commit riêng**, tách hẳn khỏi
phần sửa nghiệp vụ để người review không lẫn hai thứ.

## Ràng buộc

- **Chỉ sửa đúng những gì lint báo.** Không đổi hành vi, không refactor, không
  "tiện tay" sửa thêm. Đổi hành vi ở bước này là làm mờ bằng chứng fail-trước của
  bước sau.
- Không sửa file test trừ khi lint báo lỗi ngay trong file test đó.
- Chỉ đụng file trong `allowed_paths`. Lint đỏ ở file cấm hoặc ngoài vùng được phép
  thì **để nguyên và nói rõ** — việc đó phải đi MR riêng.
- Không thêm `# noqa`, `# type: ignore`, `eslint-disable`… để tắt cảnh báo. Sửa thật.
- Không commit — hệ thống commit sau lượt của bạn và chạy lại lint để kiểm.

## Cách làm

1. Đọc kết quả lint ở dưới, sửa từng lỗi.
2. Chạy lại đúng lệnh lint đó cho tới khi xanh.
3. In ra danh sách file đã sửa và những lỗi bạn cố tình để lại (nếu có) kèm lý do.
