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
- Có AC không đạt được trong phạm vi cho phép → ghi **một dòng** bắt đầu bằng `⚠` ngay
  đầu plan, trước mọi heading. Người duyệt quyết; bạn không tự mở rộng và cũng không giải
  thích dài. (Phạm vi file thì hệ thống đã kiểm trước khi tới bước này.)
- **Task T4 (tài liệu, cấu hình, giao diện, prompt…): không viết test chỉ để đọc lại nội
  dung vừa sửa** — test "README có khối mermaid" hay "file config có khoá X" không bắt được
  lỗi nào người review không thấy ngay trong diff, mà thành thêm một file người phải đọc và
  giữ. Mục `## Test sẽ viết` ghi đúng một dòng `- Không — T4, người review kiểm trong diff`,
  trừ khi thay đổi có hành vi chạy được thật (vd code đọc file cấu hình đó).

- **Không đổi tên, không đánh số lại, không sửa marker của test có sẵn** chỉ vì số AC trùng
với test của ticket trước. Số AC (`AC-1`, `AC-2`…) chỉ có nghĩa trong một ticket; file test
dùng chung nên hai ticket cùng có `AC-1` là bình thường. Đụng test cũ là lấn phạm vi và
làm người review phải đọc lại những test không liên quan tới ticket.

## Lưu ý về mục "Test sẽ viết"

Đây là **mục duy nhất** của plan mà agent viết test được nhìn thấy (cùng với acceptance
criteria). Agent đó không thấy "Giải pháp" hay "File sẽ thay đổi" — cố ý, để test không
chép lại giả định của cách sửa. Vì vậy mỗi dòng phải tự đứng được: nodeid, hành vi kiểm,
giá trị mong đợi, AC nào — và **chỉ** từng đó. Đừng ghi vào mục này tên file/dòng sẽ
sửa hay "fail vì code đang làm X": đó là cách giải, rò sang là hỏng đúng cái mục này
sinh ra để chặn. Viết đúng heading `## Test sẽ viết` và `## Rủi ro` — hệ thống
cắt theo heading.

## Đầu ra

```markdown
## Giải pháp
<1-3 câu: vấn đề nằm ở đâu, sửa theo hướng nào>

## File sẽ thay đổi
- `đường/dẫn.py` — sửa gì

## Test sẽ viết
- `tests/...::test_ten` — kiểm gì, giá trị mong đợi; vì sao fail trên code hiện tại (AC-x)

## Giả định phát sinh
- <CHỈ giả định phát sinh từ discovery, chưa có trong spec — người duyệt bác được từng dòng>

## Ngoài phạm vi phát sinh
- <CHỈ thứ discovery thấy liên quan mà spec chưa liệt kê>

## Rủi ro
- <điều có thể vỡ — cách phát hiện>
```

`## Giả định phát sinh` và `## Ngoài phạm vi phát sinh`: không có gì mới thì **bỏ hẳn mục đó** —
đừng ghi `- không`, một mục rỗng vẫn là một mục người duyệt phải đọc để biết là rỗng.
Phần đã có trong spec hệ thống tự in kèm, không chép lại. Task cần thứ tự làm thì đánh
số ngay trong "File sẽ thay đổi", không thêm mục riêng.
