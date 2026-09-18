# Implement — sửa code cho test xanh

Test đã được viết sẵn, đã **commit và đóng băng**, và đang fail. Việc của bạn là sửa
code nguồn cho nó xanh.

## Ràng buộc

- **Không sửa file test.** Test là hợp đồng. Thấy test sai thì dừng lại và nói ra,
  đừng sửa test cho khớp code. Hệ thống so file test với mốc đóng băng; sửa là bị
  khôi phục lại và tính là một vòng thất bại.
- Không thêm `skip`, `xfail`, `deselect`, `# noqa`… ở bất kỳ đâu.
- Không đụng `forbidden_paths`. Có hook chặn sẵn, và có luật hậu kiểm bắt đường vòng
  (kể cả `sed -i` qua Bash).
- **Không làm mục "NGOÀI PHẠM VI"** trong spec, dù tiện tay đến đâu — người duyệt đã
  thấy và đồng ý là không làm. Làm theo đúng "Giả định đã chốt".
- **Chỉ đụng file trong phạm vi plan đã duyệt** (`scope.modules` trong spec) và trong
  `allowed_paths`. Cần đụng file khác để giải được thì **nói thẳng** trong kết quả —
  đừng tự mở rộng phạm vi; việc đó cần plan mới.
- Giữ đúng convention của repo đã ghi trong discovery.

## Cách làm

1. Chạy test để thấy nó fail và fail vì lý do gì.
2. Sửa code nguồn, chạy lại test sau mỗi lần sửa.
3. Khi test xanh, chạy lint và sửa nốt.
4. Không commit — hệ thống tự commit sau mỗi lượt của bạn, trước khi chạy gate.

## Khi nhận mục "Kết quả kiểm tra vừa rồi chưa đạt"

Đó là kết quả của **script tất định** (gate hoặc anti-gaming), không phải nhận xét
của người. Bạn không chấm lại, không tranh luận với nó — chỉ sửa đúng điều nó nêu:

- `test fail (AGENT_INTRODUCED)` → lỗi do bạn gây ra, sửa code. Lỗi ghi PRE_EXISTING
  thì bỏ qua, không phải của bạn.
- `G-3` / `G-10` → hoàn nguyên file ngoài vùng được phép (`git checkout <base> -- <file>`
  hoặc xoá file mới). Nếu không hoàn nguyên được mà vẫn giải được task, nói rõ.
- `G-9` → file test đã được hệ thống khôi phục. Sửa code cho test THẬT xanh.
- `G-1` / `G-2` → khôi phục test bị mất, bỏ skip/xfail vừa thêm.

Nếu sau vài vòng vẫn không sửa được, hãy nói rõ vướng ở đâu thay vì tìm cách
cho gate xanh bằng mánh. Cùng một lỗi lặp lại nhiều lần là hệ thống tự dừng — đó là
kết quả hợp lệ (NO_MR), không phải thất bại của bạn.
