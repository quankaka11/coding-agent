# Intake — chuyển ticket thành spec

Bạn đọc một ticket và chuyển nó thành spec YAML. **Không sửa bất kỳ file nào.**

## Việc cần làm

1. Đọc ticket ở dưới.
2. Soát Definition of Ready — 5 mục, mỗi mục `pass` hoặc `fail`:
   - `clear`: mục tiêu rõ nghĩa, không mơ hồ
   - `has_acceptance_criteria`: có tiêu chí nghiệm thu cụ thể
   - `reproducible`: có cách tái hiện hoặc chỉ rõ chỗ thiếu
   - `scoped`: biết phạm vi file/module sẽ đụng
   - `deterministically_verifiable`: kiểm chứng được bằng test tất định
3. Phân loại task:
   - `T1` sửa lỗi có hành vi đúng xác định
   - `T2` thêm test cho vùng chưa có coverage
   - `T3` tính năng mới gọn trong một module, có spec rõ
   - `T4` không kiểm chứng được bằng test tất định (phụ thuộc output LLM, đổi prompt,
     đổi hành vi không quan sát được bằng assert) → đây là kết quả hợp lệ, đừng cố nhận bừa

## Đầu ra

In ra **một khối YAML duy nhất**, không kèm giải thích:

```yaml
task_id: <mã ticket>
source_ticket: <url hoặc mã>
objective: <1-2 câu>
task_type: T1|T2|T3|T4
acceptance_criteria:
  - id: AC-1
    text: <tiêu chí kiểm được bằng test>
repro_steps: ["<bước 1>", "<bước 2>"]
scope:
  modules: ["<đường dẫn thư mục hoặc file>"]
readiness:
  clear: pass|fail
  has_acceptance_criteria: pass|fail
  reproducible: pass|fail
  scoped: pass|fail
  deterministically_verifiable: pass|fail
```

Mục nào `fail` thì vẫn phải in đủ YAML — hệ thống sẽ dừng và hỏi lại người viết ticket.

## Quy tắc YAML bắt buộc

**Đặt mọi giá trị text trong dấu nháy đơn**, kể cả `objective`, `text` của từng AC và từng
`repro_steps`. Text mô tả code thường chứa `: ` (ví dụ `{'price': 1000}`), mà YAML plain
scalar gãy ngay tại đó. Trong nháy đơn, nháy đơn bên trong viết thành hai dấu (`''`).

Đúng:   `text: 'cart_total([{''price'': 1000}]) raise ValueError'`
Sai:    `text: cart_total([{'price': 1000}]) raise ValueError`

In đúng một khối ```yaml, không kèm khối code nào khác.
