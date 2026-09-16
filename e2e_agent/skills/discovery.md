# Discovery — tìm hiểu chỗ sẽ sửa

Bạn khảo sát repo để chuẩn bị cho bước lập plan. **Chỉ đọc, không sửa file nào.**

## Việc cần làm

1. **File liên quan** — tìm code chịu trách nhiệm cho vấn đề trong spec.
2. **Test hiện có** — test nào đang phủ vùng đó, đặt ở đâu, đặt tên theo kiểu gì.
3. **Convention** — đọc cấu hình lint, CONTRIBUTING, ADR nếu có.
4. **Fix tương tự trong lịch sử git** — `git log` những thay đổi cùng loại trước đây.
   Đây là cách giữ convention đáng tin nhất khi repo có lint yếu.

## Đầu ra

In ra Markdown, ngắn gọn, không suy diễn thứ chưa kiểm chứng:

```markdown
## File liên quan
- `đường/dẫn.py` — vì sao liên quan

## Test hiện có
- `tests/...` — đang phủ gì

## Convention
- <điều rút ra được, kèm nguồn>

## Fix tương tự
- <sha ngắn> <tiêu đề> — làm theo cách nào
```
