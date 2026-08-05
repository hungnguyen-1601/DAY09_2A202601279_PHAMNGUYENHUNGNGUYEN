# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                                      |
| --------------- | --------------------------------------------- |
| Họ và tên       | Phạm Nguyễn Hưng Nguyên                       |
| MSSV            | 2A202601279                                   |
| Khóa/Lớp        | K3                                            |
| Vai trò chính   | Xây dựng pipeline multi-agent end-to-end      |
| Ngày hoàn thành | 2026-08-05                                    |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Data tool + fact sheet | `src/data_access.py` (`get_order_facts`) | 3 CSV Olist, `claimed_order_id` | Fact sheet chuẩn hóa (totals, so sánh mốc thời gian) | Hoàn thành |
| Policy engine EC_POLICY_V1 | `src/policy_engine.py` (`decide`, `build_output`) | Fact sheet | Decision + output JSON đúng schema | Hoàn thành |
| 4 LLM agent + Verifier | `src/agents.py`, `src/llm_client.py` | Fact sheet, findings | Findings/proposal JSON, decision đã kiểm chứng | Hoàn thành |
| Coordinator + trace + metadata | `src/run_all.py`, `src/tracer.py` | 50 file `input/EC_*.json` | 50 file `output/EC_*.json`, `logging/trace.jsonl`, `logging/metadata.json` | Hoàn thành |
| Script kiểm tra trước khi nộp | `scripts/verify_outputs.py` | `output/`, CSV | Báo cáo lỗi/warning độc lập | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Viết tài liệu kiến trúc | `architecture.md` | Sơ đồ mermaid + bảng phân quyền dữ liệu từng agent |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Chạy 50 case end-to-end với 4 LLM agent + verifier | `src/run_all.py` | 50 JSON trong `output/`, phân bố 6 loại issue (8–9 case/loại) | `python -m src.run_all` |
| Kiểm tra độc lập output | `scripts/verify_outputs.py` | 0 errors trên 50 file (schema, evidence, số tiền, rule) | `python scripts/verify_outputs.py` |
| Trace chạy thật | `logging/trace.jsonl` | Đầy đủ event handoff/llm_call/verification cho 50 case | Đọc file, đếm `output_written` = 50 |

Output cụ thể phần việc của tôi tạo ra: bộ 50 file `output/EC_001.json` → `EC_050.json` cùng `trace.jsonl` chứng minh luồng handoff thật giữa các agent, và script verify xác nhận 0 sai lệch giữa output và dữ liệu CSV.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Từ một khiếu nại tự do của khách (`claimed_order_id` + message), hệ thống phải đối chiếu 3 nguồn dữ liệu (orders, order_items, order_payments), xác định đúng 1 trong 6 primary issue theo thứ tự ưu tiên của EC_POLICY_V1, tính đúng số tiền hoàn và chỉ nộp evidence ID có thật trong CSV — trong khi mỗi agent chỉ được dùng model ≤ 10B params.

### Cách triển khai

- **Tách "phân tích" khỏi "quyết định"**: 3 specialist agent (Order&Seller, Payment, Delivery) mỗi agent chỉ nhận đúng domain facts của mình (least privilege), gọi `llama3.2:3b` trả finding JSON; Policy Agent (LLM) nhận handoff 3 findings và đề xuất rule; Verifier (code) tính lại toàn bộ từ CSV bằng policy engine deterministic và có quyền phủ quyết. Nhờ vậy model 3B không thể làm sai số liệu cuối.
- **Quy tắc dữ liệu**: seller bị coi là bàn giao muộn nếu `order_delivered_carrier_date > shipping_limit_date` của item thuộc seller đó; đối soát payment với sai số 0.10 BRL; mọi số tiền làm tròn 2 chữ số; 6 rule áp theo đúng thứ tự ưu tiên trong README.
- **Ép LLM trả JSON**: gọi Ollama với `format=json`, `temperature=0`, `num_predict` giới hạn để chạy đủ nhanh trên CPU.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `input/EC_*.json` (case_id, claimed_order_id, policy_version) |
| Output | `output/EC_*.json` đúng schema README mục 6 + giới hạn 5 ID/entity, 10 evidence, 3 causes, 3 parties, 5 actions |
| Module phụ thuộc | `data_access.py` (fact sheet), `llm_client.py` (Ollama) |
| Module sử dụng output | `verify_outputs.py`, hệ thống chấm điểm |
| Điều kiện lỗi cần xử lý | LLM trả JSON hỏng (retry 2 lần rồi để verifier quyết), order không có item row (`item_total = freight_total = 0.0`, entity rỗng), timestamp thiếu (`NaT` → so sánh trả `None`, không đoán) |

### Cách xác minh

```bash
python -m src.run_all
python scripts/verify_outputs.py
```

- **Kết quả mong đợi:** 50 file output, script verify báo 0 errors.
- **Kết quả thực tế:** 50/50 file được ghi, phân bố issue cân bằng (8–9 case mỗi loại — khớp với cách đề sinh case), verify 0 errors.
- **Artifact/log:** `output/`, `logging/trace.jsonl`, `logging/metadata.json` (không chứa secret).

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Model bị giới hạn ≤ 10B params; nếu để LLM tự tính tiền và tự sinh evidence ID thì gần như chắc chắn sai định dạng hoặc sai số, mà evidence sai bị tính false positive.
- **Các phương án đã cân nhắc:** (1) LLM làm hết từ phân tích đến JSON cuối; (2) code deterministic làm hết, không có agent thật; (3) hybrid — LLM agent phân tích/đề xuất theo domain riêng, verifier deterministic tính lại và quyết định cuối.
- **Phương án đã chọn:** (3) hybrid.
- **Lý do:** (1) không đảm bảo correctness với model 3B (JSON dài, nhiều ID hash 32 ký tự); (2) không đúng tinh thần bài lab multi-agent A2A; (3) giữ được handoff thật giữa các agent (có trace chứng minh) mà vẫn đảm bảo mọi con số khớp CSV — đúng yêu cầu "ưu tiên dữ liệu có thể kiểm chứng" của đề, chi phí chạy local bằng 0.
- **Bằng chứng quyết định phù hợp:** `logging/trace.jsonl` có event `verification` từng case (đa số `agrees: true`, các case LLM lệch đều bị sửa và log `corrections`); `scripts/verify_outputs.py` báo 0 errors trên 50 file.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Kế hoạch ban đầu dùng OpenRouter API nhưng call test trả `402 {"error": {"message": "Insufficient credits. This account never purchased credits...", "code": 402}}`.
- **Lệnh hoặc bước tái hiện:** POST `https://openrouter.ai/api/v1/chat/completions` với model `meta-llama/llama-3.1-8b-instruct`.
- **Nguyên nhân gốc:** API key OpenRouter thuộc tài khoản chưa từng nạp credit; model trả phí không gọi được, còn tier free bị giới hạn ~50 request/ngày trong khi pipeline cần ~200 call LLM (4 call × 50 case).
- **Cách xử lý:** Chuyển sang chạy local qua Ollama. Model có sẵn trên máy (`qwen2.5-coder:14b`) vượt giới hạn 10B của đề nên pull `llama3.2:3b` (3B params, 2GB) và trỏ `src/llm_client.py` vào `http://localhost:11434/api/chat`.
- **Cách xác minh sau khi sửa:** `ollama list` thấy `llama3.2:3b`; chạy `python -m src.run_all` hoàn tất 50 case, trace ghi đủ `llm_call` với `prompt_tokens`/`output_tokens` thật.
- **Điều học được:** Kiểm tra hạn mức provider trước khi thiết kế số lượng call; thiết kế client tách rời (chỉ đổi endpoint + model name trong `config.py`) giúp chuyển provider trong vài phút; local model nhỏ + verifier deterministic là tổ hợp rẻ và ổn định cho bài toán có ground truth từ dữ liệu.

## 7. Hiểu biết về luồng end-to-end

(Bộ câu hỏi trong template thuộc lab RAG/Crossref của ngày khác nên không áp dụng được cho Day 9; tôi trình bày luồng end-to-end của chính bài này.)

1. **Dữ liệu đi từ input đến output như thế nào?** Coordinator đọc `EC_xxx.json`, lấy `claimed_order_id`, tool pandas join 3 bảng Olist thành fact sheet (totals + các so sánh mốc thời gian tính sẵn). Ba specialist agent nhận từng phần fact sheet qua handoff, trả findings; Policy Agent gộp findings và đề xuất rule; Verifier tính lại từ CSV, sửa sai lệch rồi mới dựng output JSON đúng schema.
2. **Vì sao cần thứ tự ưu tiên rule?** Một order có thể thỏa nhiều điều kiện cùng lúc (ví dụ canceled nhưng cũng có split payment); thứ tự ưu tiên trong EC_POLICY_V1 đảm bảo mọi hệ thống chấm cùng một kết quả duy nhất.
3. **Evidence được kiểm soát ra sao?** Evidence ID do code sinh từ row thật trong CSV (order/item/payment/seller/policy), LLM không tự sinh ID nên không thể tạo false positive về định dạng hay ID không tồn tại.
4. **Vì sao verifier phải độc lập với policy agent?** Nếu verifier dùng lại kết luận của LLM thì sai lệch của model 3B đi thẳng vào output. Verifier chạy lại toàn bộ logic trên dữ liệu gốc, nên output cuối không phụ thuộc chất lượng model — trace cho thấy các case LLM chọn sai rule đều bị sửa.
5. **Kết quả được xem là đạt dựa trên artifact nào?** `output/` đủ 50 file đúng schema; `scripts/verify_outputs.py` (đọc CSV độc lập) báo 0 errors; `trace.jsonl` chứng minh handoff thật; `metadata.json` khai báo model 3B ≤ 10B.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Phạm Nguyễn Hưng Nguyên
**Ngày xác nhận:** 2026-08-05
