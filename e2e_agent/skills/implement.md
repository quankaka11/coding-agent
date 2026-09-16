# Implement — sửa code cho test xanh

Test đã được viết sẵn và đang fail. Việc của bạn là sửa code nguồn cho nó xanh.

## Ràng buộc

- **Không sửa file test.** Test là hợp đồng. Thấy test sai thì dừng lại và nói ra,
  đừng sửa test cho khớp code.
- Không thêm `skip`, `xfail`, `deselect` ở bất kỳ đâu.
- Không đụng `forbidden_paths`. Có hook chặn sẵn, và có luật hậu kiểm bắt đường vòng.
- Chỉ đụng file trong `allowed_paths` và trong plan đã duyệt.
- Giữ đúng convention của repo đã ghi trong discovery.

## Cách làm

1. Chạy test để thấy nó fail và fail vì lý do gì.
2. Sửa code nguồn, chạy lại test sau mỗi lần sửa.
3. Khi test xanh, chạy lint và sửa nốt.
4. Không commit — hệ thống tự commit sau khi gate và anti-gaming chạy xong.

Nếu sau vài vòng vẫn không sửa được, hãy nói rõ vướng ở đâu thay vì tìm cách
cho gate xanh bằng mánh.
