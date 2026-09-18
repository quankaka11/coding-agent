# Planning — lập plan để người duyệt

Bạn lập plan từ spec và kết quả discovery. **Chưa sửa file nào ở bước này.**

Plan này sẽ được **người đọc và duyệt** trước khi tốn compute vào việc implement,
nên phải đủ cụ thể để người đó biết bạn sẽ làm gì, và đủ ngắn để họ đọc trong 2 phút.

## Ràng buộc

- **Làm đúng và chỉ đúng phần spec nói.** Spec đã chốt cách hiểu hẹp nhất của ticket kèm
  `Giả định`; mục `NGOÀI PHẠM VI` là những thứ cố tình không làm — plan không được kéo
  chúng vào, kể cả khi bạn thấy "nên làm luôn". Muốn làm thì đó là ticket khác.
- Chỉ được đụng file nằm trong `allowed_paths` ghi ở dưới.
- Tuyệt đối không đụng `forbidden_paths`. Task bắt buộc phải đụng thì nói thẳng là
  không làm được, đừng tìm đường vòng.

## Lưu ý về mục "Test sẽ viết"

Đây là **mục duy nhất** của plan mà agent viết test được nhìn thấy (cùng với acceptance
criteria). Agent đó không thấy "Cách giải" hay "File sẽ đụng" — cố ý, để test không
chép lại giả định của cách sửa. Vì vậy mục này phải tự đứng được: tên test, hành vi
kiểm, giá trị mong đợi cụ thể. Viết đúng heading `## Test sẽ viết` và `## Rủi ro` —
hệ thống cắt theo heading.

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

## Giả định
- <chép từ spec, thêm nếu discovery lộ ra chỗ mơ hồ mới — người duyệt bác được từng dòng>

## Ngoài phạm vi (không làm)
- <chép từ spec + những gì discovery thấy liên quan nhưng ticket không yêu cầu>

## Rủi ro
- <điều có thể vỡ, cách phát hiện>
```

Hai mục `## Giả định` và `## Ngoài phạm vi` **bắt buộc có** (ghi `- không` nếu trống):
đó là chỗ người duyệt nhìn vào để quyết, thay cho việc agent hỏi lại.
