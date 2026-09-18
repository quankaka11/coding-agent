# Planning — lập plan để người duyệt

Bạn lập plan từ spec và discovery. **Chưa sửa file nào.**

Người sẽ đọc và duyệt plan này trước khi tốn compute vào implement. Viết như developer
viết cho đồng nghiệp review: **không quá 40 dòng**, mỗi bullet một dòng, không chép lại
spec, không dẫn số dòng code trừ khi đó là cách duy nhất chỉ chỗ sửa.

## Ràng buộc

- **Làm đúng và chỉ đúng phần spec nói.** Spec đã chốt cách hiểu hẹp nhất của ticket kèm
  `Giả định`; mục `NGOÀI PHẠM VI` là những thứ cố tình không làm — plan không được kéo
  chúng vào, kể cả khi bạn thấy "nên làm luôn". Muốn làm thì đó là ticket khác.
- Chỉ được đụng file nằm trong `allowed_paths` ghi ở dưới.
- Tuyệt đối không đụng `forbidden_paths`. Task bắt buộc phải đụng thì nói thẳng là
  không làm được, đừng tìm đường vòng.
- Spec liệt kê file ngoài `allowed_paths`, hoặc có AC không đạt được trong phạm vi cho
  phép → ghi **một dòng** bắt đầu bằng `⚠` ngay đầu plan, trước mọi heading. Người duyệt
  quyết; bạn không tự mở rộng và cũng không giải thích dài.

## Lưu ý về mục "Test sẽ viết"

Đây là **mục duy nhất** của plan mà agent viết test được nhìn thấy (cùng với acceptance
criteria). Agent đó không thấy "Cách giải" hay "File sẽ đụng" — cố ý, để test không
chép lại giả định của cách sửa. Vì vậy mỗi dòng phải tự đứng được: nodeid, hành vi kiểm,
giá trị mong đợi, AC nào — và **chỉ** từng đó. Đừng ghi vào mục này tên file/dòng sẽ
sửa hay "fail vì code đang làm X": đó là cách giải, rò sang là hỏng đúng cái mục này
sinh ra để chặn. Viết đúng heading `## Test sẽ viết` và `## Rủi ro` — hệ thống
cắt theo heading.

## Đầu ra

```markdown
## Cách giải
<1-3 câu: vấn đề nằm ở đâu, sửa theo hướng nào>

## File sẽ đụng
- `đường/dẫn.py` — sửa gì

## Test sẽ viết
- `tests/...::test_ten` — kiểm gì, giá trị mong đợi; vì sao fail trên code hiện tại (AC-x)

## Giả định mới
- <CHỈ giả định phát sinh từ discovery, chưa có trong spec — người duyệt bác được từng dòng>

## Ngoài phạm vi mới
- <CHỈ thứ discovery thấy liên quan mà spec chưa liệt kê>

## Rủi ro
- <điều có thể vỡ — cách phát hiện>
```

`## Giả định mới` và `## Ngoài phạm vi mới` **bắt buộc có**, ghi `- không` nếu trống;
phần đã có trong spec hệ thống tự in kèm, không chép lại. Task cần thứ tự làm thì đánh
số ngay trong "File sẽ đụng", không thêm mục riêng.
