# Planning — lập plan để người duyệt

Bạn lập plan từ spec và kết quả discovery. **Chưa sửa file nào ở bước này.**

Plan này sẽ được **người đọc và duyệt** trước khi tốn compute vào việc implement,
nên phải đủ cụ thể để người đó biết bạn sẽ làm gì, và đủ ngắn để họ đọc trong 2 phút.

## Ràng buộc

- Chỉ được đụng file nằm trong `allowed_paths` ghi ở dưới.
- Tuyệt đối không đụng `forbidden_paths`. Task bắt buộc phải đụng thì nói thẳng là
  không làm được, đừng tìm đường vòng.

## Đầu ra

```markdown
## Cách giải
<2-4 câu: vấn đề nằm ở đâu, sửa theo hướng nào>

## File sẽ đụng
- `đường/dẫn.py` — sửa gì

## Test sẽ viết
- `tests/...::test_ten` — kiểm AC nào, vì sao test này fail trên code hiện tại

## Thứ tự làm
1. ...

## Rủi ro
- <điều có thể vỡ, cách phát hiện>
```
