"""Giao diện web: đọc trạng thái từ đĩa, cấu hình .env và hồ sơ, duyệt plan.

Không phải một nguồn sự thật thứ hai. Mọi thứ hiển thị đọc từ `runs/`, `.env`
và hồ sơ; mọi thay đổi trạng thái ticket đi qua đúng `tracker` mà orchestrator
dùng. Lớp này không tự chạy pipeline.
"""
