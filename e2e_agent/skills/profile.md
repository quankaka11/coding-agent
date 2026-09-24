# Profile — tìm cách build, test, lint một repo lạ

Bạn đang đứng ở gốc một repo vừa clone. Hệ thống sẽ tự động nhận ticket, sửa code và
mở MR trên repo này; để làm được, nó cần **các lệnh shell chạy được không cần người**
trên một bản checkout mới (Linux, không TTY, stdin đóng). Việc của bạn là tìm ra các
lệnh đó và **chạy thử từng lệnh** cho tới khi chắc chúng chạy được.

**Không sửa file nào đang được git theo dõi.** Cài dependency (tạo `node_modules/`,
`vendor/`, `target/`…) thì được. Không commit, không push.

## Cần tìm

1. **setup** — lệnh cài dependency trên một checkout mới (`npm ci`, `pnpm install
   --frozen-lockfile`, `go mod download`, `mvn -q -DskipTests dependency:resolve`,
   `composer install --no-interaction`, `bundle install`…). Không cần thì `null`.
2. **build** — lệnh compile/build nếu ngôn ngữ có bước đó và test không tự build.
   Không có thì `null`.
3. **test** — chạy TOÀN BỘ test, và **ghi kết quả JUnit XML vào `{junit}`** (hệ thống thay
   `{junit}` bằng một đường dẫn tuyệt đối lúc chạy). Gợi ý:
   - jest: `JEST_JUNIT_OUTPUT_FILE={junit} npx jest --ci --reporters=default --reporters=jest-junit`
     (cần `jest-junit`; chưa có trong dependency thì thêm `npm i --no-save jest-junit` vào setup)
   - vitest: `npx vitest run --reporter=default --reporter=junit --outputFile={junit}`
   - go: `go run gotest.tools/gotestsum@latest --junitfile {junit} -- ./...`
   - tool ghi mỗi suite một file (maven surefire, gradle, phpunit…): `{junit}` được phép
     là THƯ MỤC — `mvn -q test; rc=$?; mkdir -p {junit}; cp target/surefire-reports/*.xml {junit}/ 2>/dev/null; exit $rc`
   - Repo chưa có test nào: lệnh vẫn phải thoát 0 và ghi được `{junit}` (0 test là hợp lệ).
4. **lint** — lệnh kiểm tĩnh repo ĐANG sạch (exit 0 trên code hiện tại): linter có sẵn
   cấu hình trong repo (`npx eslint .`, `golangci-lint run`, `go vet ./...`…). Linter
   báo lỗi sẵn có trên code hiện tại thì chọn thứ yếu hơn nhưng xanh (`tsc --noEmit`,
   `go vet`, `node --check` từng file, compile). Bắt buộc có.
5. **allowed_paths** — glob các thư mục mã nguồn và test agent được sửa (vd `src/**`,
   `test/**`, `internal/**`). Không gồm docs, CI, file sinh tự động.
6. **test_globs** — glob nhận ra file test (vd `**/*.test.ts`, `**/*_test.go`, `src/test/**`).
7. **extra_forbidden** — thư mục agent tuyệt đối không được đụng ngoài CI/.env mà hệ thống
   đã cấm sẵn: migration, code sinh tự động, vendored code…

## Cách làm

1. Đọc manifest (`package.json`, `go.mod`, `pom.xml`, `build.gradle`, `Cargo.toml`,
   `composer.json`, `Makefile`, `README`, cấu hình CI) để biết repo tự chạy test thế nào.
   Cấu hình CI là nguồn đáng tin nhất.
2. Chạy **setup**, rồi chạy **test** với `{junit}` thay bằng `/tmp/e2ea-probe/junit.xml`,
   kiểm file XML đó có `<testcase>`. Chạy **lint**. Lỗi thì sửa lệnh, chạy lại.
3. Công cụ không có trên máy (vd `go` chưa cài) thì vẫn đề xuất lệnh đúng và ghi vào
   `notes` là máy thiếu gì — đừng đổi sang lệnh sai cho "chạy được".

## Đầu ra — một khối ```yaml duy nhất, mọi chuỗi trong nháy đơn

```yaml
language: 'typescript'
setup: 'npm ci'
build: null
test: 'JEST_JUNIT_OUTPUT_FILE={junit} npx jest --ci --reporters=default --reporters=jest-junit'
lint: 'npx eslint .'
allowed_paths: ['src/**', 'test/**']
test_globs: ['test/**', '**/*.test.ts']
extra_forbidden: []
verified: true          # true chỉ khi bạn đã tự chạy setup + test + lint thành công
notes: ['<điều người vận hành cần biết, mỗi dòng một câu>']
```
