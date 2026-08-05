# K3 Day 09 - Multi-Agent E-commerce Dispute Resolution

## 0. Nhóm thực hiện và phân công

| MSSV        | Họ và tên               | Vai trò                              | Phần việc chính                                                                                                                                    |
| ----------- | ----------------------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2A202601279 | Phạm Nguyễn Hưng Nguyên | Pipeline & Agents lead               | Data tool + fact sheet, 4 LLM agent + Verifier runtime, policy engine, coordinator, trace (`src/data_access.py`, `src/agents.py`, `src/llm_client.py`, `src/policy_engine.py`, `src/run_all.py`, `src/tracer.py`, `src/config.py`); chạy batch 50 case |
| 2A202601813 | Nguyễn Văn Tuấn Anh     | Verification & Delivery lead         | Handoff contract, full-output validator, 2 script verify độc lập, đóng gói submission, trang demo (`src/handoffs.py`, `src/output_validator.py`, `scripts/verify_outputs.py`, `scripts/verify_trace.py`, `scripts/package_submission.py`, `demo.html`) |

Báo cáo cá nhân tương ứng: `individual_01279_PhamNguyenHungNguyen.md` và `individual_01813_NguyenVanTuanAnh.md` (đặt ở root repo).

## 1. Bài toán

Xây dựng một hệ thống multi-agent để điều tra 50 yêu cầu hỗ trợ của khách hàng trên dữ liệu Olist. Với mỗi case, hệ thống phải đối chiếu nhiều nguồn dữ liệu, xác định vấn đề, bên chịu trách nhiệm, bằng chứng, khoản hoàn đề xuất và hành động xử lý.

Trong thực tế, một khiếu nại thương mại điện tử thường không thể được giải quyết chỉ từ nội dung khách hàng cung cấp. Nhân viên chăm sóc khách hàng phải kiểm tra trạng thái đơn, thời hạn seller bàn giao hàng, thời điểm đơn vị vận chuyển giao thực tế, các sản phẩm trong đơn và toàn bộ giao dịch thanh toán. Ví dụ, cùng một phản ánh "giao hàng trễ" nhưng trách nhiệm có thể thuộc về seller nếu bàn giao quá hạn, thuộc về đơn vị vận chuyển nếu seller đã bàn giao đúng hạn, hoặc khiếu nại có thể không chính xác nếu dữ liệu cho thấy đơn được giao đúng cam kết.

Quy trình này thường cần nhiều bộ phận phối hợp và trao đổi kết quả với nhau. Bài lab này mô phỏng quy trình đó bằng các agent: mỗi agent phân tích một domain dữ liệu, sau đó handoff bằng chứng cho agent điều phối để đưa ra kết luận cuối cùng. Hệ thống phải ưu tiên dữ liệu có thể kiểm chứng thay vì tin hoàn toàn vào lời khiếu nại hoặc tự tạo ra sự kiện không tồn tại.

## 2. Dữ liệu

https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce

Thư mục `data/` chứa 9 file CSV của Brazilian E-Commerce Public Dataset by Olist. Các khóa join chính:

- `orders.customer_id -> customers.customer_id`
- `orders.order_id -> order_items.order_id`
- `orders.order_id -> order_payments.order_id`
- `orders.order_id -> order_reviews.order_id`
- `order_items.product_id -> products.product_id`
- `order_items.seller_id -> sellers.seller_id`
- Các cột `*_zip_code_prefix` có thể nối với `geolocation_zip_code_prefix` sau khi gộp geolocation theo zip code.

Lưu ý về dữ liệu:

- Mỗi `customer_id` đại diện cho một order; dùng `customer_unique_id` khi cần nhận diện cùng khách hàng qua nhiều order.
- Một order có thể có nhiều item, seller hoặc payment row.
- `payment_value` là số tiền của từng payment row, không phải giá trị của từng installment.
- Olist không có refund ledger, transaction ID, tracking checkpoint theo item hoặc bằng chứng giao sai/giao thiếu. Không cần suy diễn các sự kiện này cho đỡ tốn công.
- Các timestamp được so sánh theo giá trị trong CSV; không cần chuyển múi giờ.

## 3. Input

Thư mục `input/` có:

```text
EC_001.json
EC_002.json
...
EC_050.json
```

Với địngh chuẩn:

```json
{
  "case_id": "EC_001",
  "opened_at": "2018-10-18T00:00:00-03:00",
  "customer_request": {
    "language": "vi",
    "message": "Đơn hàng của tôi có dấu hiệu giao trễ. Hãy kiểm tra nguyên nhân và quyền lợi phù hợp.",
    "claimed_order_id": "<olist_order_id>"
  },
  "policy_version": "EC_POLICY_V1"
}
```

Gợi ý: Hệ thống dùng `claimed_order_id` để truy xuất và join các CSV.

## 4. Quy tắc nghiệp vụ

Áp dụng theo thứ tự ưu tiên dưới đây. Mọi phép tính tiền làm tròn 2 chữ số thập phân.

| Primary issue             | Điều kiện                                                                         | Responsible party                           |       Refund | Action                        |
| ------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------- | -----------: | ----------------------------- |
| `canceled_order_paid`     | `order_status = canceled` và tổng payment > 0                                     | `platform` / `OLIST_PLATFORM`               | Tổng payment | `issue_full_refund`           |
| `unavailable_order_paid`  | `order_status = unavailable` và tổng payment > 0                                  | `platform` / `OLIST_PLATFORM`               | Tổng payment | `issue_full_refund`           |
| `late_delivery_seller`    | Giao sau estimated date và carrier nhận hàng sau `shipping_limit_date`            | `seller` / seller ID vi phạm                | Tổng freight | `refund_freight`              |
| `late_delivery_logistics` | Giao sau estimated date và carrier nhận hàng không muộn hơn `shipping_limit_date` | `logistics_provider` / `LOGISTICS_PROVIDER` | Tổng freight | `refund_freight`              |
| `valid_split_payment`     | Có từ 2 payment row; tổng payment khớp tổng item + freight trong sai số 0.10 BRL  | Không có                                    |            0 | `explain_valid_split_payment` |
| `unsupported_late_claim`  | Đơn giao không muộn hơn estimated date và payment khớp                            | Không có                                    |            0 | `reject_late_refund`          |

Quy ước khi order có nhiều item: seller bị coi là bàn giao muộn nếu `order_delivered_carrier_date > shipping_limit_date` của item thuộc seller đó. Bộ 50 case chính thức không chứa tình huống mơ hồ giữa nhiều seller.

Root-cause code tương ứng:

- `SELLER_HANDOFF_AFTER_LIMIT`
- `CARRIER_DELIVERED_AFTER_ESTIMATE`
- `ORDER_CANCELED_AFTER_PAYMENT`
- `ORDER_UNAVAILABLE_AFTER_PAYMENT`
- `MULTIPLE_PAYMENTS_RECONCILED`
- `DELIVERY_WITHIN_ESTIMATE`

## 5. Evidence ID

Chỉ được nộp evidence ID có thể dựng trực tiếp từ dữ liệu:

```text
order:<order_id>
item:<order_id>:<order_item_id>
payment:<order_id>:<payment_sequential>
seller:<seller_id>
policy:<root_cause_code>
```

Ví dụ: `item:abc123:2`. Evidence không tồn tại trong CSV hoặc sai định dạng bị tính là false positive.

## 6. Output schema

Mỗi input có một output tương ứng vào `output/` (tên file khớp với input):

```json
{
  "case_id": "EC_001",
  "assessment": {
    "primary_issue": "late_delivery_seller",
    "case_status": "action_required",
    "confidence": 0.92
  },
  "affected_entities": {
    "order_ids": ["<order_id>"],
    "item_ids": ["<order_id>:1"],
    "seller_ids": ["<seller_id>"],
    "payment_ids": ["<order_id>:1"]
  },
  "root_cause_analysis": {
    "ranked_causes": [
      { "cause_code": "SELLER_HANDOFF_AFTER_LIMIT", "rank": 1 }
    ],
    "responsible_parties": [
      { "party_type": "seller", "party_id": "<seller_id>" }
    ]
  },
  "evidence_ids": [
    "order:<order_id>",
    "item:<order_id>:1",
    "payment:<order_id>:1",
    "seller:<seller_id>",
    "policy:SELLER_HANDOFF_AFTER_LIMIT"
  ],
  "financial_resolution": {
    "currency": "BRL",
    "item_total_brl": 100.0,
    "freight_total_brl": 15.0,
    "payment_total_brl": 115.0,
    "recommended_refund_brl": 15.0
  },
  "resolution_actions": ["refund_freight"]
}
```

Giới hạn: tối đa 5 ID cho mỗi entity set, 10 evidence, 3 root causes, 3 responsible parties và 5 actions. `confidence` nằm trong `[0, 1]`.

`case_status` nhận một trong hai giá trị:

- `action_required`: cần hoàn tiền.
- `no_action`: không có khoản hoàn; chỉ cần giải thích hoặc bác bỏ claim.

Nếu order không có item row, `item_ids`, `seller_ids` để rỗng và `item_total_brl`, `freight_total_brl` bằng `0.0`.

## 7. Gợi ý kiến trúc multi-agent

Đây là gợi ý, bạn thoải mái tư duy thiết kế:

- **Coordinator Agent**: nhận case, giao việc và tổng hợp output.
- **Order & Seller Agent**: kiểm tra trạng thái, item, seller và mốc bàn giao.
- **Payment Agent**: đối soát payment với item + freight.
- **Delivery Agent**: so sánh thời điểm giao thực tế với hạn giao.
- **Policy Agent**: áp dụng `EC_POLICY_V1`, kiểm tra refund và action.
- **Verifier Agent**: kiểm tra ID, số tiền và schema trước khi ghi file.

Điểm cốt lõi là nên có phân công, handoff và kiểm chứng giữa các agent; không có điểm cho việc chỉ đặt tên nhiều agent nhưng toàn bộ xử lý nằm trong một prompt duy nhất.

## 8. Nộp bài và chấm điểm

Nén folder `output/` thành file zip. Zip phải chứa đúng 50 JSON từ `EC_001.json` đến `EC_050.json`; không chứa các file lạ khác

Điểm mỗi case là tổng có trọng số:

| Thành phần                        | Trọng số |
| --------------------------------- | -------: |
| Primary issue và confidence       |      20% |
| Affected entities                 |      20% |
| Root cause và responsible parties |      15% |
| Evidence IDs                      |      15% |
| Financial resolution              |      20% |
| Resolution actions                |      10% |

Điểm cuối là trung bình của 50 case. Case bị hard gate nhận 0 điểm.

Trong repo phải có thêm:

- `architecture.md`: sơ đồ agent, vai trò, quyền truy cập và luồng handoff (đặt ở root repo)
- `individual_5SoCuoiMHV_HoVaTen.md`: báo cáo cá nhân (đặt ở root repo)
- `trace.jsonl`: trace chạy thật của 50 case (không append, chỉ cần lượt chạy mới nhất)
- `metadata.json`: model, parameter size, framework và runtime

&rarr; Làm chung trên 1 repo nhóm, báo cáo cá nhân để chung trong repo và nộp repo nhóm này, giữ nguyên tên repo không đổi

| Thời gian  | Checkpoint   | Nội dung             |
| ---------- | ------------ | -------------------- |
| 9h-9h30    | Checkpoint 1 | Công bố input đề bài |
| 9h30-12h30 | Checkpoint 2 | Competition          |
| 12h30-1h   | Checkpoint 3 | Chốt leaderboard     |

## 9. Lưu ý

1. Mỗi agent chỉ được sử dụng model dưới hoặc bằng **10B parameters**, chạy local hoặc qua provider tùy ý.
2. Khi nộp bài, chỉ nén folder `output/` thành file zip; không đưa source code, `.env` hoặc các file audit vào zip này.
3. Luôn commit toàn bộ source code lên repo trước khi nộp file output zip để chấm điểm.
4. API key và secret phải đặt trong file `.env` và không được commit. Tên model sử dụng phải được khai báo rõ trong source code, đồng thời ghi lại trong `metadata.json` (Tức là model name không ghi vào .env, cho vào code để chấm)

## 10. Kiểm tra và đóng gói bài K3 của repo này

Chạy lần lượt từ root repo:

```bash
python -m src.run_all
python -B scripts/verify_outputs.py
python -B scripts/verify_trace.py
python -B scripts/package_submission.py
```

Hai ZIP được tạo để đáp ứng đồng thời hai biểu mẫu hướng dẫn:

- `output.zip`: đúng yêu cầu mục 8 của README, chỉ chứa `output/EC_001.json` đến `output/EC_050.json`.
- `submission.zip`: đúng cấu trúc Codelab, gồm đúng 50 JSON trong `output/` cùng `architecture.md`, `trace.jsonl` và `metadata.json` ở root ZIP.

Nguồn chuẩn của trace và metadata trong repo vẫn là `logging/trace.jsonl` và `logging/metadata.json`; script đóng gói ánh xạ hai file này về root của `submission.zip`. `submission.zip` được phép track để có thể nộp bằng link GitHub; `output.zip` vẫn được ignore và có thể tải trực tiếp lên LMS nếu biểu mẫu dùng validator của cohort.

Pipeline dùng staging trong suốt giai đoạn xử lý và publish trace theo cơ chế atomic. Nếu batch bị ngắt trong lúc gọi agent, `output/` và trace thành công trước đó được giữ nguyên. Chỉ sau khi đủ 50 case qua full-output Verifier, 50 output mới được publish rồi trace mới thay thế bản cũ.
