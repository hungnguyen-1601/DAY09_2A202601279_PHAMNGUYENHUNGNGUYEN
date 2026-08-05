# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                                      |
| --------------- | --------------------------------------------- |
| Họ và tên       | Phạm Nguyễn Hưng Nguyên                       |
| MSSV            | 2A202601279                                   |
| Khóa/Lớp        | K3                                            |
| Vai trò chính   | Pipeline & Agents lead (nhóm 2 người, cùng 2A202601813 Nguyễn Văn Tuấn Anh — Verification & Delivery) |
| Ngày hoàn thành | 2026-08-05                                    |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Data tool + fact sheet | `src/data_access.py` (`get_order_facts`) | 3 CSV Olist, `claimed_order_id` | Fact sheet chuẩn hóa (totals, so sánh mốc thời gian) | Hoàn thành |
| Policy engine EC_POLICY_V1 | `src/policy_engine.py` (`decide`, `build_output`) | Fact sheet | Decision + output JSON đúng schema | Hoàn thành |
| 4 LLM agent + Verifier runtime | `src/agents.py`, `src/llm_client.py` | Fact sheet, findings | Findings/proposal JSON, verifier tính lại policy và gọi tầng kiểm full output | Hoàn thành |
| Coordinator + trace + metadata | `src/run_all.py`, `src/tracer.py`, `src/config.py` | 50 file `input/EC_*.json` | 50 output qua staging, trace atomic, metadata K3 | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Viết tài liệu kiến trúc | `architecture.md` | Sơ đồ mermaid + bảng phân quyền dữ liệu từng agent |
| Chốt schema handoff cùng Tuấn Anh | `src/handoffs.py` (Tuấn Anh phụ trách) | Thống nhất các field bắt buộc của contract để coordinator tích hợp validate trước khi giao việc |
| Chạy thử validator và đóng gói của Tuấn Anh trên pipeline thật | `scripts/verify_outputs.py`, `scripts/verify_trace.py`, `scripts/package_submission.py` | Xác nhận 0 errors trên batch 50 case trước khi nộp |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Chạy 50 case end-to-end với 4 LLM agent + verifier | `src/run_all.py` | 50 JSON trong `output/`, phân bố 6 loại issue (8–9 case/loại) | `python -m src.run_all` |
| Chạy kiểm tra độc lập output (script do Tuấn Anh viết) | `scripts/verify_outputs.py` | 0 errors trên 50 file (schema, evidence, số tiền, rule) | `python scripts/verify_outputs.py` |
| Trace chạy thật | `logging/trace.jsonl` | 50 case × 5 structured handoff, full-output verification và output_written | `python scripts/verify_trace.py` |
| Chạy đóng gói hai biểu mẫu (script do Tuấn Anh viết) | `scripts/package_submission.py` | `output.zip` theo README và `submission.zip` theo Codelab | Validator chạy lại trước khi tạo ZIP |

Output cụ thể phần việc của tôi tạo ra: bộ 50 file `output/EC_001.json` → `EC_050.json` cùng `trace.jsonl` chứng minh luồng handoff thật giữa các agent, và script verify xác nhận 0 sai lệch giữa output và dữ liệu CSV.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Từ một khiếu nại tự do của khách (`claimed_order_id` + message), hệ thống phải đối chiếu 3 nguồn dữ liệu (orders, order_items, order_payments), xác định đúng 1 trong 6 primary issue theo thứ tự ưu tiên của EC_POLICY_V1, tính đúng số tiền hoàn và chỉ nộp evidence ID có thật trong CSV — trong khi mỗi agent chỉ được dùng model ≤ 10B params.

### Cách triển khai

- **Tách "phân tích" khỏi "quyết định"**: 3 specialist agent (Order&Seller, Payment, Delivery) mỗi agent chỉ nhận đúng domain facts của mình (least privilege), gọi `llama3.2:3b` trả finding JSON; Policy Agent (LLM) nhận handoff 3 findings và đề xuất rule; Verifier (code) tính lại từ CSV, dựng full output rồi kiểm schema/entity/evidence/amount/action trước khi cho phép publish.
- **Handoff có contract thật**: mỗi case có 5 handoff (3 specialist, Policy, Verifier), bắt buộc gồm ticket ID, câu hỏi gốc, nhiệm vụ, facts kèm source IDs, fact thiếu/mâu thuẫn và next action. Schema contract và validator (`src/handoffs.py`, `verify_trace.py`) do Tuấn Anh phụ trách; tôi tích hợp việc validate vào coordinator trước khi mỗi agent nhận việc.
- **Quy tắc dữ liệu**: seller bị coi là bàn giao muộn nếu `order_delivered_carrier_date > shipping_limit_date` của item thuộc seller đó; đối soát payment với sai số 0.10 BRL; mọi số tiền làm tròn 2 chữ số; 6 rule áp theo đúng thứ tự ưu tiên trong README.
- **Ép LLM trả JSON**: gọi Ollama với `format=json`, `temperature=0`, `num_predict` giới hạn để chạy đủ nhanh trên CPU.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `input/EC_*.json` (case_id, claimed_order_id, policy_version) |
| Output | `output/EC_*.json` đúng schema README mục 6 + giới hạn 5 ID/entity, 10 evidence, 3 causes, 3 parties, 5 actions |
| Handoff | `ticket_id`, `question`, `assigned_task`, `sourced_facts[{name,value,source_ids}]`, `missing_or_conflicting_facts`, `next_action` |
| Module phụ thuộc | `data_access.py` (fact sheet), `handoffs.py` (contract), `llm_client.py` (Ollama) |
| Module sử dụng output | Verifier runtime, `verify_outputs.py`, `verify_trace.py`, hệ thống chấm điểm |
| Điều kiện lỗi cần xử lý | LLM trả JSON hỏng/sai shape (thử tối đa 2 lần, vẫn lỗi thì fail batch), policy version/order không hợp lệ (fail-closed), timestamp thiếu (`NaT` → `None`, không suy thành đúng hạn) |

### Cách xác minh

```bash
python -m src.run_all
python -B scripts/verify_outputs.py
python -B scripts/verify_trace.py
python -B scripts/package_submission.py
```

- **Kết quả mong đợi:** 50 output; 250 handoff; 50 verification/output_written; cả hai validator báo 0 errors.
- **Kết quả thực tế:** 50/50 output, 200 LLM call thành công, 250 handoff, 50 verification và 50 output_written; cả output validator và trace validator đều báo 0 errors (output validator thêm 0 warnings).
- **Artifact/log:** `output/`, `logging/trace.jsonl`, `logging/metadata.json`, `output.zip`, `submission.zip`; ZIP chỉ lấy allowlist nên không chứa secret/cache/venv.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Model bị giới hạn ≤ 10B params; nếu để LLM tự tính tiền và tự sinh evidence ID thì gần như chắc chắn sai định dạng hoặc sai số, mà evidence sai bị tính false positive.
- **Các phương án đã cân nhắc:** (1) LLM làm hết từ phân tích đến JSON cuối; (2) code deterministic làm hết, không có agent thật; (3) hybrid — LLM agent phân tích/đề xuất theo domain riêng, verifier deterministic tính lại và quyết định cuối.
- **Phương án đã chọn:** (3) hybrid.
- **Lý do:** (1) không đảm bảo correctness với model 3B (JSON dài, nhiều ID hash 32 ký tự); (2) không đúng tinh thần bài lab multi-agent A2A; (3) giữ được handoff thật giữa các agent (có trace chứng minh) mà vẫn đảm bảo mọi con số khớp CSV — đúng yêu cầu "ưu tiên dữ liệu có thể kiểm chứng" của đề, chi phí chạy local bằng 0.
- **Bằng chứng quyết định phù hợp:** trace mới có 15/50 proposal đồng ý ngay và 35/50 proposal được Verifier sửa; mỗi case lưu corrections, bộ checks và full final output. `verify_outputs.py` và `verify_trace.py` đều đạt trước khi đóng gói.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Kế hoạch ban đầu dùng OpenRouter API nhưng call test trả `402 {"error": {"message": "Insufficient credits. This account never purchased credits...", "code": 402}}`.
- **Lệnh hoặc bước tái hiện:** POST `https://openrouter.ai/api/v1/chat/completions` với model `meta-llama/llama-3.1-8b-instruct`.
- **Nguyên nhân gốc:** API key OpenRouter thuộc tài khoản chưa từng nạp credit; model trả phí không gọi được, còn tier free bị giới hạn ~50 request/ngày trong khi pipeline cần ~200 call LLM (4 call × 50 case).
- **Cách xử lý:** Chuyển sang chạy local qua Ollama. Model có sẵn trên máy (`qwen2.5-coder:14b`) vượt giới hạn 10B của đề nên pull `llama3.2:3b` (3B params, 2GB) và trỏ `src/llm_client.py` vào `http://localhost:11434/api/chat`.
- **Cách xác minh sau khi sửa:** `ollama list` thấy `llama3.2:3b`; chạy `python -m src.run_all` hoàn tất 50 case, trace ghi đủ `llm_call` với `prompt_tokens`/`output_tokens` thật.
- **Điều học được:** Kiểm tra hạn mức provider trước khi thiết kế số lượng call; thiết kế client tách rời (chỉ đổi endpoint + model name trong `config.py`) giúp chuyển provider trong vài phút; local model nhỏ + verifier deterministic là tổ hợp rẻ và ổn định cho bài toán có ground truth từ dữ liệu.

## 7. Hiểu biết về luồng end-to-end

(Bộ câu hỏi trong template thuộc lab RAG/Crossref của ngày khác nên không áp dụng được cho Day 9; tôi trình bày luồng end-to-end của chính bài này.)

1. **Dữ liệu đi từ input đến output như thế nào?** Coordinator đọc `EC_xxx.json`, lấy `claimed_order_id`, tool pandas join 3 bảng Olist thành fact sheet. Ba specialist nhận handoff theo domain, trả findings; Coordinator handoff findings cho Policy rồi handoff proposal cho Verifier. Verifier tính lại từ CSV, dựng và kiểm full output; 50 output được staging trước khi publish.
2. **Vì sao cần thứ tự ưu tiên rule?** Một order có thể thỏa nhiều điều kiện cùng lúc (ví dụ canceled nhưng cũng có split payment); thứ tự ưu tiên trong EC_POLICY_V1 đảm bảo mọi hệ thống chấm cùng một kết quả duy nhất.
3. **Evidence được kiểm soát ra sao?** ID order/item/payment/seller do code sinh từ row thật trong CSV; ID policy sinh deterministic từ root-cause EC_POLICY_V1. LLM không tự sinh evidence cuối, và Verifier kiểm lại từng ID trước khi ghi.
4. **Vì sao verifier phải độc lập với policy agent?** Nếu verifier dùng lại kết luận của LLM thì sai lệch của model 3B đi thẳng vào output. Verifier chạy lại toàn bộ logic trên dữ liệu gốc, nên output cuối không phụ thuộc chất lượng model — trace cho thấy các case LLM chọn sai rule đều bị sửa.
5. **Kết quả được xem là đạt dựa trên artifact nào?** `output/` đủ 50 file; `verify_outputs.py` báo 0 errors; `verify_trace.py` xác nhận 50 case/250 handoff/full-output checks; metadata khai báo K3, EC_POLICY_V1 và model 3B; hai ZIP có member list đúng allowlist.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Phạm Nguyễn Hưng Nguyên
**Ngày xác nhận:** 2026-08-05
