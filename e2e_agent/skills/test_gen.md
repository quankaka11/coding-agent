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

## Với task loại T2 (viết test bù cho code đã đúng)

Test của bạn sẽ **pass ngay** — đó là điều đúng, đừng cố làm nó đỏ. Bù lại, hệ thống
sẽ đổi ngược vài dòng code mà test của bạn đi qua và đòi test phải đỏ lên vì thay đổi
đó. Test chỉ gọi hàm rồi khẳng định "không None" sẽ không qua được.

## Đầu ra

Ghi thẳng file test vào repo, rồi in ra danh sách test đã viết và test nào kiểm AC nào.

Sau bước này file test được commit và **đóng băng**: bước sửa code không được đổi
một dòng nào trong đó. Viết cho chắc ngay từ đầu.
