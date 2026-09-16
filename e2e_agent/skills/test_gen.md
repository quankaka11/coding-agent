# Test Generation — viết test TRƯỚC khi sửa code

Bạn chỉ viết test. **Tuyệt đối không sửa code nguồn.**

Bạn đang chạy trong một tiến trình riêng và **không được thấy** quá trình implement.
Đó là cố ý: thấy implement thì test sẽ chép lại đúng cái bug của implement.

## Ràng buộc

- Test phải **fail trên code hiện tại**. Nếu nó pass ngay, nghĩa là không có bug nào
  để sửa — hãy nói rõ điều đó thay vì bịa ra test khác.
- Mỗi test phải có **assert về giá trị**. `assert_called_once()` một mình không tính.
- Không mock chính hàm/module mà task này sắp sửa — mock nó thì test không kiểm gì cả.
- Đặt test theo đúng convention đã ghi trong phần discovery.
- Với task T3, mỗi acceptance criteria phải có ít nhất một test và đánh dấu
  `@pytest.mark.ac("AC-x")`.

## Đầu ra

Ghi thẳng file test vào repo, rồi in ra danh sách test đã viết và test nào kiểm AC nào.
