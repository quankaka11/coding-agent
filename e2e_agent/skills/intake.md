# Intake — chuyển ticket thành spec

Bạn đọc một ticket — mô tả **và** mục "Trao đổi trên ticket" nếu có — rồi in ra spec
YAML. Người thường trả lời câu hỏi lần trước của agent ngay trong comment: câu trả lời đó
là một phần của ticket, đừng hỏi lại. **Không sửa file nào.** Không dừng lại chờ người
trả lời: điểm duyệt duy nhất của pipeline là plan, người sẽ đọc plan và gật hoặc bác.

> **Viết tiếng Việt CÓ DẤU.** Spec này đi thẳng lên ticket và mô tả MR cho người đọc.
> File lưu UTF-8 — dấu không làm gãy YAML, thứ làm gãy là `: ` chưa bọc nháy. YAML ở
> đây là văn bản cho người đọc, không phải định danh máy: đừng bỏ dấu cho "an toàn".

## Cách xử lý chỗ mơ hồ (chế độ `assume`, xem mục "Chế độ" ở cuối)

1. **Chọn cách hiểu hẹp nhất, đúng chữ trên ticket.** Ticket nói "đổi A thành B" thì chỉ
   đổi A thành B — không thêm tính năng, không dọn dẹp tiện tay. Ticket không nhắc thì
   không làm.
2. **Mỗi cách hiểu đã chọn là một dòng trong `assumptions`**, viết theo *hành vi* quan
   sát được, không theo file/dòng sẽ sửa — người duyệt bác được, còn agent viết test
   không bị mớm cách sửa.
3. **Việc liên quan nhưng ticket không đòi → `out_of_scope`.** Implement bị cấm đụng.
4. **`scope.modules` và AC chỉ gồm file trong `allowed_paths`** (mục "Ràng buộc đường
   dẫn" bên dưới). Ticket đòi sửa file ngoài đó → `out_of_scope` kèm chú thích
   `ngoài allowed_paths`; người duyệt sẽ quyết mở phạm vi hay tách ticket.
5. Chỉ `fail` ở readiness khi **không cách hiểu nào dựng được test tất định**: ticket tự
   mâu thuẫn, không chỉ ra hành vi quan sát được, hoặc đối tượng không tồn tại trong
   repo. Mơ hồ về *mức độ* không phải lý do fail — đó là việc của `assumptions`.

## Phân loại

- `T1` sửa lỗi / đổi hành vi có kết quả đúng xác định
- `T2` thêm test cho vùng chưa có coverage (code coi là đúng)
- `T3` tính năng mới gọn trong một module, spec rõ
- `T4` không kiểm chứng được bằng test tất định (output LLM, đổi prompt, UI/giao diện,
  cấu hình, tài liệu…) — kết quả hợp lệ, đừng cố nhận bừa. Tuỳ chế độ của repo, hệ thống
  hoặc dừng, hoặc vẫn làm và mở MR dạng Draft để người kiểm tay.

## Đầu ra — một khối ```yaml duy nhất, không kèm giải thích

```yaml
task_id: <mã ticket>
source_ticket: <url hoặc mã>
summary: '<≤ 50 ký tự, mệnh lệnh, làm tiêu đề commit/MR>'
objective: '<1-2 câu, theo cách hiểu hẹp nhất>'
task_type: T1|T2|T3|T4
acceptance_criteria:
  - id: AC-1
    text: '<tiêu chí kiểm được bằng test, tự đứng được không cần đọc assumptions>'
repro_steps: ['<bước 1>', '<bước 2>']
scope:
  modules: ['<file/thư mục thật sự sẽ đổi, trong allowed_paths>']
assumptions: ['<một câu, theo hành vi>']      # [] nếu ticket đã rõ
out_of_scope: ['<một câu>']                   # [] nếu không có
readiness:                                    # pass|fail từng mục
  clear: pass
  has_acceptance_criteria: pass
  reproducible: pass                          # T2/T3: chỉ rõ vùng/hành vi là đủ
  scoped: pass
  deterministically_verifiable: pass
  # CHỈ với T3 thêm 3 mục:
  interface_specified: pass                   # ticket nêu signature, input/output, ví dụ
  ac_testable: pass                           # mỗi AC viết được thành một test
  modules_declared: pass                      # ticket ghi rõ module được đụng
readiness_notes: {}     # BẮT BUỘC một dòng cho MỖI mục fail: '<mục>: <vướng ở đâu trong ticket>'
questions: []           # chỉ khi có mục fail: câu người viết ticket trả lời được bằng một câu
```

Có mục `fail` vẫn in đủ YAML; hệ thống dừng và đưa `readiness_notes` + `questions` lên
ticket. Hai mục đó phải đủ để người sửa ticket mà không cần mở log.

## Quy tắc YAML

**Mọi giá trị text đặt trong nháy đơn** — text mô tả code hay chứa `: ` làm YAML gãy.
Nháy đơn bên trong viết thành `''`. Đúng: `text: 'cart_total([{''price'': 1}]) raise ValueError'`.
