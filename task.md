# LLVoiceTool — Danh sách công việc

Tài liệu thiết kế: [docs/thiet-ke.html](docs/thiet-ke.html)

Quy ước: `[ ]` chưa làm · `[x]` xong · `[~]` đang làm. Mã task (ví dụ `1.12`) dùng để nhắc tới khi trao đổi.

---

## Cần chốt trước khi code

- [ ] `0.01` **Cách khách đăng nhập lại.** Thiết kế hiện chỉ có "nhập key → nhận token". Khi khách cài lại Windows hoặc mất token thì không có cách đăng nhập lại.
  Đề xuất: khách có tài khoản (tên đăng nhập + mật khẩu), key chỉ dùng để kích hoạt gói và nạp credit.
- [ ] `0.02` Domain cụ thể cho proxy (ví dụ `api.llvoice.vn`).
- [ ] `0.03` Giá mặc định (tính bằng credit) cho một lần clone giọng và một lần thiết kế giọng. Admin sửa được sau.
- [ ] `0.04` Số giọng clone tối đa mỗi khách được giữ.
- [ ] `0.05` Kênh báo lỗi cho admin khi tài khoản MiniMax chết hoặc hết tiền (đề xuất: bot Telegram).
- [ ] `0.06` Có mua chứng chỉ ký code (code signing) không. Không có thì Windows SmartScreen sẽ cảnh báo khi khách chạy exe. Chứng chỉ có phí hàng năm.

## Giai đoạn 0 — Chuẩn bị

### Tài khoản và hạ tầng
- [ ] `0.10` Mua 1 tài khoản MiniMax (API key chính thức) để phát triển.
- [ ] `0.11` Mua domain, chuyển nameserver về Cloudflare.
- [ ] `0.12` Thuê VPS thử (2 vCPU / 4 GB RAM). Trước khi chốt vùng, đo độ trễ từ vài vùng (Singapore, Tokyo, US West) tới `api.minimax.io`.

### Kiểm chứng API MiniMax
Làm bằng script nhỏ trước khi viết proxy, ghi kết quả vào `docs/minimax-notes.md`.
- [ ] `0.20` Gọi `POST /v1/t2a_v2`: đo thời gian trả về theo độ dài văn bản, kiểm tra `usage_characters` so với số ký tự thực.
- [ ] `0.21` Thử các lỗi: key sai (1004), gửi dồn dập để gặp rate limit (1002 / 1039). Ghi lại giới hạn thực tế của gói đã mua.
- [ ] `0.22` Upload file mẫu + `POST /v1/voice_clone`, ghi lại chi phí mỗi lần clone.
- [ ] `0.23` **Xác minh chính sách giữ giọng clone.** Tài liệu ghi giọng clone không dùng sẽ bị xóa sau 7 ngày. Cần biết: chỉ cần dùng 1 lần trong 7 ngày đầu là giữ vĩnh viễn, hay phải dùng định kỳ. Kết quả quyết định worker `2.13` chạy một lần hay hàng ngày.
- [ ] `0.24` `POST /v1/voice_design`: giọng thiết kế có bị xóa như giọng clone không.
- [ ] `0.25` Tìm API lấy danh sách giọng và xóa giọng của một tài khoản.
- [ ] `0.26` API bất đồng bộ `t2a_async_v2`: endpoint tra trạng thái và tải file.

### Khởi tạo repo
- [x] `0.30` Tạo repo git, cấu trúc thư mục:
  ```
  server/   proxy FastAPI, worker, trang quản trị
  client/   app PySide6
  shared/   mã lỗi, hằng số dùng chung
  deploy/   docker-compose, Caddyfile, script backup
  docs/
  ```
- [~] `0.31` Công cụ chung: `ruff`, `pytest`, `pre-commit`. Đang dùng `venv` + `pip` vì máy chưa cài `uv`. Còn thiếu: cài `pre-commit` và chạy `pre-commit install`.
- [x] `0.32` File `.env.example`, không commit secret.
- [ ] `0.33` Cài Docker Desktop (hoặc PostgreSQL + Redis bản Windows) để chạy DB khi dev. Cần trước task `1.03`.

---

## Giai đoạn 1 — Proxy + TTS cơ bản (2–3 tuần)

### Server: nền tảng
- [x] `1.01` Khung FastAPI: cấu hình bằng `pydantic-settings`, logging dạng JSON, xử lý lỗi trả về `{code, message, data}`.
- [~] `1.02` `docker-compose.yml` cho môi trường dev: proxy, PostgreSQL 16, Redis 7. Đã viết, chưa chạy thử vì máy chưa có Docker.
- [ ] `1.03` SQLAlchemy models + Alembic migration đầu tiên: `customers`, `plans`, `activation_keys`, `account_slots`, `usage_log`, `refresh_tokens`, `system_settings`, `system_voices`.
- [ ] `1.04` Mã hóa API key MiniMax trong DB (AES-GCM, khóa lấy từ biến môi trường). Không bao giờ ghi key ra log.
- [ ] `1.05` Bảng `system_settings`: `credit_unit_chars` (1 credit = bao nhiêu ký tự) và các cấu hình chung khác.

### Server: gọi MiniMax
- [ ] `1.10` Module `minimax_client`: gọi `t2a_v2`, chuyển audio hex sang bytes, ánh xạ mã lỗi MiniMax sang lỗi nội bộ.
- [ ] `1.11` Bộ chọn slot: trạng thái `active / cooling / auth_failed / disabled`, ưu tiên slot ít bị rate limit trong 5 phút gần nhất, lưu trạng thái trong Redis.
- [ ] `1.12` Khi gặp 1002 / 1039: đưa slot vào cooling (mặc định 60 giây), thử slot khác, tối đa 3 lần.
- [ ] `1.13` Khi gặp 1004: chuyển slot sang `auth_failed`, gửi cảnh báo admin.

### Server: credit
- [ ] `1.20` Sổ credit với `SELECT … FOR UPDATE`: `reserve`, `commit` (theo `usage_characters` thực tế, hoàn phần dư), `release` khi lỗi.
- [ ] `1.21` Quy đổi ký tự sang credit theo `credit_unit_chars`, làm tròn lên.
- [ ] `1.22` Hết hạn gói thì credit về 0, kể cả credit nạp thêm.
- [ ] `1.23` Dọn các khoản reserve bị treo quá 10 phút (proxy chết giữa chừng).
- [ ] `1.24` Test chạy song song: 50 yêu cầu cùng lúc trên một khách, credit không bao giờ âm.

### Server: đăng nhập và khóa máy
- [ ] `1.30` Đăng ký / đăng nhập (theo quyết định `0.01`), mật khẩu băm bằng argon2.
- [ ] `1.31` `POST /api/activate`: key dùng một lần, lưu hash SHA-256 của key, loại `plan` (tạo mới / gia hạn) và `topup` (cộng credit).
- [ ] `1.32` JWT access token 24 giờ, refresh token 30 ngày, mỗi lần làm mới thì hủy token cũ.
- [ ] `1.33` Kiểm tra header `X-HWID` ở mọi yêu cầu, lưu HWID lần đầu kích hoạt.
- [ ] `1.34` Một phiên đăng nhập: đăng nhập máy mới thì phiên cũ bị hủy.
- [ ] `1.35` Giới hạn tần suất theo khách (chống spam API).

### Server: endpoint cho app
- [ ] `1.40` `GET /api/me`: credit còn, gói, ngày hết hạn.
- [ ] `1.41` `POST /api/tts`: văn bản tối đa 10 000 ký tự, trả audio.
- [ ] `1.42` `GET /api/voices`: danh sách giọng hệ thống (nhập tay vào `system_voices`).
- [ ] `1.43` `GET /api/version`: phiên bản app mới nhất (chưa cần thông báo).
- [ ] `1.44` Test tự động cho toàn bộ endpoint trên.

### Trang quản trị
- [ ] `1.50` Đăng nhập admin + mã TOTP, đường dẫn không đoán được.
- [ ] `1.51` Tạo / sửa / tắt gói: tên, loại (plan / topup), thời hạn, số credit, ghi chú.
- [ ] `1.52` Sửa đơn vị credit `credit_unit_chars`.
- [ ] `1.53` Sinh key hàng loạt theo gói, xuất file CSV để bán.
- [ ] `1.54` Danh sách khách: tìm kiếm, xem credit, reset HWID, cộng / trừ credit tay, khóa tài khoản.
- [ ] `1.55` Quản lý tài khoản MiniMax: thêm key, nút "Kiểm tra", bật / tắt, xem trạng thái.
- [ ] `1.56` Nhật ký sử dụng theo khách.

### App (client)
- [x] `1.60` Khung PySide6: cửa sổ chính, các tab, giao diện tiếng Việt qua file `vi.json` (i18n).
- [ ] `1.61` Tính HWID: UUID bo mạch chủ + CPU ID + serial ổ C, băm SHA-256.
- [ ] `1.62` API client (`httpx`), tự làm mới token, lưu token bằng Windows DPAPI.
- [ ] `1.63` Màn hình đăng nhập / đăng ký / nhập key.
- [ ] `1.64` Tab **Tạo giọng**: ô nhập văn bản, mở file `.txt` / `.docx`, chọn giọng, tốc độ, âm lượng, cao độ, cảm xúc, model.
- [ ] `1.65` Chia văn bản dài tại ranh giới câu (tối đa 4 000 ký tự mỗi đoạn), gửi song song, ghép lại đúng thứ tự.
- [ ] `1.66` Đóng gói kèm `ffmpeg` để ghép và xuất `mp3` / `wav`.
- [ ] `1.67` Nghe thử trong app, nút Dừng, lưu file.
- [ ] `1.68` Tab **Tài khoản**: credit còn, gói, ngày hết hạn, nhập key mới.
- [ ] `1.69` Hiện thông báo lỗi tiếng Việt theo mã lỗi server (hết credit, sai máy, hết hạn…).

### Triển khai
- [ ] `1.80` Cài Docker trên VPS, chạy stack với Caddy (HTTPS tự động).
- [ ] `1.81` Cloudflare: bật proxy, SSL Full (strict), bỏ cache cho `/api/*`.
- [ ] `1.82` Backup `pg_dump` hàng ngày lên Cloudflare R2, giữ 14 bản. Thử khôi phục một lần.
- [ ] `1.83` Build Nuitka thử, chạy trên máy Windows sạch.

---

## Giai đoạn 2 — Clone giọng, hàng loạt, cập nhật (2–3 tuần)

### Server: giọng riêng của khách
- [ ] `2.01` Bảng `customer_voices`: slot đã tạo giọng, `voice_id`, loại (clone / design), đường dẫn file mẫu hoặc prompt, `prev_voice_ids`.
- [ ] `2.02` `POST /api/voices/clone`: nhận file mẫu (mp3 / m4a / wav, 10 giây – 5 phút, tối đa 20 MB), upload lên MiniMax, clone, trừ credit.
- [ ] `2.03` `POST /api/voices/design`: tạo giọng từ mô tả, trả audio nghe thử, trừ credit.
- [ ] `2.04` `DELETE /api/voices/{id}`.
- [ ] `2.05` `POST /api/tts` với giọng riêng: luôn gọi qua đúng slot đã tạo giọng.
- [ ] `2.06` Đặt `voice_id` theo quy ước `{customer_id}_v{n}`, đúng luật MiniMax (8–256 ký tự, bắt đầu bằng chữ).
- [ ] `2.07` Lưu file mẫu vào `/data/voice_samples/`, đồng bộ lên R2.

### Server: worker nền (arq)
- [ ] `2.10` Thêm service `worker` vào docker-compose.
- [ ] `2.11` Kiểm tra sức khỏe từng slot định kỳ, phục hồi slot hết cooling.
- [ ] `2.12` Khi slot chết: clone lại mọi giọng đang ghim vào slot đó sang slot khác, cập nhật `voice_id`.
- [ ] `2.13` Giữ giọng clone không bị xóa (chạy một lần hay hàng ngày tùy kết quả `0.23`).
- [ ] `2.14` Gửi cảnh báo admin qua kênh đã chọn ở `0.05`.

### App
- [ ] `2.20` Tab **Giọng của tôi**: danh sách giọng, upload mẫu (kiểm tra độ dài trước khi gửi), tạo giọng bằng mô tả, nghe thử, xóa.
- [ ] `2.21` Hàng đợi xử lý hàng loạt: thả nhiều file, tối đa 4 luồng (chỉnh được), tạm dừng / tiếp tục.
- [ ] `2.22` Lưu hàng đợi vào SQLite để chạy tiếp sau khi tắt app.
- [ ] `2.23` Tab **Lịch sử**: file đã tạo theo ngày, nghe lại, mở thư mục.

### Cập nhật và thông báo
- [ ] `2.30` Tạo bộ cài bằng Inno Setup.
- [ ] `2.31` Tự cập nhật: app kiểm tra `/api/version`, tải bộ cài, chạy và tự đóng. Bắt buộc cập nhật khi thấp hơn `min_required`.
- [ ] `2.32` Thông báo từ admin: bảng `announcements`, app hiện tin chưa đọc.
- [ ] `2.33` Trang quản trị: đăng bản cập nhật, đăng thông báo, cấu hình giá clone / design.

---

## Giai đoạn 3 — Lồng tiếng SRT, hoàn thiện (1–2 tuần)

- [ ] `3.01` Đọc file `.srt` (xử lý mã hóa UTF-8 / UTF-8 BOM, cue lỗi định dạng).
- [ ] `3.02` Tab **Lồng tiếng SRT**: xem danh sách cue, sửa chữ từng cue, chọn giọng.
- [ ] `3.03` TTS từng cue, đặt audio đúng thời điểm bắt đầu, chèn khoảng lặng giữa các cue.
- [ ] `3.04` Cue có audio dài hơn thời lượng: tăng tốc độ đọc (tối đa mức admin cho phép) hoặc đánh dấu để khách sửa.
- [ ] `3.05` Xuất `wav` / `mp3` theo tổng thời lượng phụ đề.
- [ ] `3.06` Trang quản trị: thống kê sử dụng theo ngày / khách / slot, cấu hình model MiniMax được phép dùng.
- [ ] `3.07` Rà soát toàn bộ chữ tiếng Việt trong app.
- [ ] `3.08` Kiểm thử trên Windows 10 và Windows 11, máy yếu, mạng chậm.
- [ ] `3.09` Quét exe trên VirusTotal, xử lý báo nhầm (ký code nếu đã chọn ở `0.06`).
- [ ] `3.10` Phát hành bản 1.0.

---

## Giai đoạn 4 — Làm sau

- [ ] `4.01` Đọc điều khoản sử dụng của MiniMax về việc dùng tài khoản web qua proxy trước khi làm `4.02`.
- [ ] `4.02` Slot loại `web`: đăng nhập bằng cookie / token, tự làm mới, bộ chọn ưu tiên key chính thức.
- [ ] `4.03` Tính năng **Đổi giọng**: chọn cách nhận dạng giọng nói (Whisper trên server hoặc API bên ngoài), nhận dạng → sửa chữ → đọc lại bằng giọng khác.
- [ ] `4.04` Thêm ngôn ngữ giao diện khác (thêm file JSON trong `i18n/`).

---

## Việc chạy suốt dự án

- [ ] `X.01` Theo dõi lỗi server (Sentry hoặc tương tự) và kiểm tra uptime.
- [ ] `X.02` Theo dõi changelog API MiniMax.
- [ ] `X.03` Cập nhật tài liệu thiết kế khi có quyết định mới.
