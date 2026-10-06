# Project Survey — AirGuard Hanoi

## 1. Kết luận điều hành

Đề tài nên được chốt là:

> **AirGuard Hanoi — Hệ thống dự báo PM2.5 ngắn hạn và cảnh báo chất lượng không khí theo VN_AQI cho Hà Nội.**

Sản phẩm dự báo nồng độ PM2.5 tại một hoặc một số trạm quan trắc ở các horizon 1, 6, 12 và 24 giờ; sau đó chuyển kết quả sang mức cảnh báo VN_AQI, giải thích các yếu tố ảnh hưởng và cung cấp kết quả qua dashboard/API.

Quyết định quan trọng nhất về dữ liệu:

- **Target chính phải là số đo PM2.5 từ trạm quan trắc**, nếu có thể lấy được chuỗi lịch sử đủ dài.
- Dữ liệu thời tiết/reanalysis được dùng làm biến đầu vào.
- CAMS/Open-Meteo Air Quality là dữ liệu mô hình khí quyển; nên dùng làm benchmark, biến bổ sung hoặc fallback, không nên gọi là ground truth quan trắc.
- IQAir và Google Air Quality là sản phẩm tham khảo tính năng, không mặc định là nguồn dữ liệu huấn luyện do vấn đề khóa API, chi phí và điều khoản sử dụng.

Đóng góp của project không cần là một thuật toán hoàn toàn mới. Giá trị nằm ở pipeline tái lập được, kiểm soát chất lượng dữ liệu, đánh giá time-series không leakage, cảnh báo VN_AQI, giải thích mô hình và sản phẩm demo end-to-end.

## 2. Khảo sát sản phẩm tương tự

| Sản phẩm | Chức năng đáng học hỏi | Hạn chế/không nên sao chép |
|---|---|---|
| [IQAir Hanoi](https://www.iqair.com/air-quality/vietnam/ha-noi/hanoi) | AQI hiện tại, dự báo nhiều ngày, bản đồ, nhiều chất ô nhiễm, thời tiết và khuyến nghị sức khỏe | Là sản phẩm thương mại; không coi dữ liệu hiển thị trên trang là dataset huấn luyện nếu chưa có quyền sử dụng |
| [IQAir AirVisual](https://play.google.com/store/apps/details?id=com.airvisual) | Dự báo 48 giờ/7 ngày, lịch sử, cảnh báo cho nhóm nhạy cảm, bản đồ 2D/3D | Scope quá lớn đối với một học kỳ; không làm ứng dụng di động, bản đồ 3D hay thiết bị IoT trong MVP |
| [Google Air Quality API](https://developers.google.com/maps/documentation/air-quality) | Current, history, forecast, heatmap, chỉ số và khuyến nghị sức khỏe; độ phân giải công bố đến 500 m | Yêu cầu xác thực Google Cloud, có yếu tố chi phí và ràng buộc sử dụng; phù hợp làm đối chứng sản phẩm hơn là nguồn dữ liệu cốt lõi |
| [Cổng quan trắc môi trường Hà Nội](https://airhanoi.hanoi.gov.vn/) | Sử dụng chuẩn VN_AQI và quy chuẩn Việt Nam, có giá trị thực tiễn địa phương | Cần kiểm tra riêng khả năng tải lịch sử và quyền truy cập tự động; không phụ thuộc vào scraping giao diện trong MVP |
| [AirNow Embassy Monitor](https://gispub.epa.gov/airnowembassy/) | Số đo PM2.5 từ trạm tại cơ quan ngoại giao, có dữ liệu lịch sử và raw concentration | Một trạm không đại diện toàn bộ Hà Nội; dữ liệu thời gian thực là sơ bộ và chưa phải dữ liệu regulatory đã chứng nhận |

### Khoảng trống có thể khai thác

Các sản phẩm phổ biến chủ yếu cho người dùng xem chỉ số và dự báo. Project của nhóm có thể khác biệt ở phần minh bạch khoa học:

1. Công bố nguồn gốc và chất lượng dữ liệu.
2. So sánh mô hình với baseline đơn giản.
3. Đánh giá riêng từng horizon và từng đợt ô nhiễm cao.
4. Hiển thị khoảng dự báo hoặc độ tin cậy.
5. Giải thích biến nào tác động đến dự báo.
6. Dùng chuẩn **VN_AQI** thay vì mặc định US AQI.

## 3. Khảo sát nguồn dữ liệu

### 3.1. Nguồn target PM2.5

| Nguồn | Loại dữ liệu | Điểm mạnh | Rủi ro | Vai trò đề xuất |
|---|---|---|---|---|
| [OpenAQ API v3](https://docs.openaq.org/) | Tổng hợp số đo quan trắc công khai; có measurement và hourly aggregation | API rõ ràng, truy vấn theo tọa độ/trạm/thời gian; phù hợp pipeline tự động | Cần API key; độ phủ và độ liên tục tại Hà Nội phải kiểm chứng; phải tuân thủ điều khoản của nguồn gốc bên thứ ba | **Ứng viên target số 1**, nếu data audit đạt yêu cầu |
| [AirNow Embassy/Consulate](https://gispub.epa.gov/airnowembassy/) | PM2.5 quan trắc theo giờ tại trạm đại sứ quán/lãnh sự quán | Target quan trắc thực, có archive; phù hợp mô hình một trạm | Có thể chỉ còn một trạm, có gap, dữ liệu sơ bộ; dịch vụ/archive có thể thay đổi | **Ứng viên target số 2** và fallback thực tế |
| [Cổng quan trắc Hà Nội](https://airhanoi.hanoi.gov.vn/) | Dữ liệu/ chỉ số từ mạng quan trắc địa phương | Đúng bối cảnh Hà Nội và chuẩn Việt Nam | Chưa xác nhận API lịch sử công khai, schema và giấy phép | Nguồn bổ sung; chỉ dùng sau feasibility spike |
| IQAir | Tổng hợp trạm, sensor và mô hình | Nhiều vị trí, giao diện tốt | Điều khoản và cách truy xuất không phù hợp để mặc định crawl cho huấn luyện | Chỉ khảo sát sản phẩm |
| Google Air Quality | Dữ liệu đã tổng hợp/mô hình hóa | Độ phân giải cao, có history/forecast | Khóa API, billing và điều khoản | Chỉ benchmark nếu nhóm đã có tài khoản phù hợp |

OpenAQ cho biết API cung cấp measurement gốc và hourly mean; tài liệu cũng cảnh báo dữ liệu không bao phủ toàn bộ mạng quan trắc trên thế giới và người dùng phải tuân thủ điều khoản của nhà cung cấp gốc. Vì vậy, **việc nhìn thấy “Hanoi” trong một nền tảng không đồng nghĩa chuỗi lịch sử đủ tốt để huấn luyện**.

AirNow nêu rõ số đo hiện hành có kiểm tra chất lượng sơ bộ nhưng không phải dữ liệu regulatory đã chứng nhận. Điều này vẫn phù hợp cho project học thuật nếu nhóm mô tả đúng hạn chế.

### 3.2. Nguồn biến thời tiết

Nguồn khuyến nghị là [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api), cung cấp dữ liệu thời tiết theo giờ từ ERA5, ERA5-Land và ECMWF IFS. Các biến cần lấy:

- `temperature_2m`
- `relative_humidity_2m`
- `dew_point_2m`
- `precipitation`
- `surface_pressure`
- `cloud_cover`
- `wind_speed_10m`
- `wind_direction_10m`
- có thể thêm `boundary_layer_height` nếu nguồn/model hỗ trợ

ERA5/ERA5-Land là reanalysis: có kết hợp quan trắc và mô hình để tạo chuỗi nhất quán, không phải số đo tại đúng vị trí trạm. Báo cáo phải gọi đúng bản chất dữ liệu này.

### 3.3. Nguồn CAMS/Open-Meteo Air Quality

[Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) cung cấp PM2.5, PM10, NO2, O3, CO, SO2 và các AQI dựa trên CAMS. Đối với Hà Nội, dữ liệu thuộc CAMS Global, tài liệu công bố độ phân giải khoảng 45 km, thời gian gốc 3 giờ và có từ tháng 8/2022.

Do đó nguồn này:

- hữu ích để tạo benchmark “mô hình khí quyển có sẵn”;
- có thể dùng làm một feature ngoại sinh;
- có thể dùng để lấp khoảng trống có gắn cờ nguồn;
- **không nên được trình bày như cảm biến đo tại mặt đất ở một quận Hà Nội**.

Nếu chỉ huấn luyện một mô hình để dự báo chính chuỗi CAMS, project sẽ chủ yếu học cách ngoại suy đầu ra của một mô hình khác và đóng góp thực tế yếu hơn.

## 4. Chuẩn AQI và cảnh báo

Project nên dùng [Quyết định 1459/QĐ-TCMT về VN_AQI](https://cem.gov.vn/tin-tuc-moi-truong/tong-cuc-moi-truong-ban-hanh-huong-dan-ky-thuat-tinh-toan-va-cong-bo-chi-so-chat-luong-khong-khi-viet-nam) làm chuẩn chính.

VN_AQI:

- sử dụng SO2, CO, NO2, O3, PM10 và PM2.5;
- bắt buộc có ít nhất PM10 hoặc PM2.5;
- có AQI giờ và AQI ngày;
- PM10/PM2.5 giờ sử dụng NowCast từ 12 giá trị trung bình giờ gần nhất;
- chia sáu mức: Tốt, Trung bình, Kém, Xấu, Rất xấu và Nguy hại.

Không nên lấy trực tiếp US AQI do API trả về rồi gọi đó là VN_AQI. Nhóm cần cài đặt và kiểm thử công thức VN_AQI theo tài liệu chính thức.

[WHO Air Quality Guidelines 2021](https://www.who.int/publications/i/item/9789240034228) có thể dùng để giải thích ý nghĩa sức khỏe và bối cảnh, nhưng không thay thế công thức VN_AQI trong dashboard.

## 5. Khảo sát nghiên cứu

### 5.1. Kết quả liên quan trực tiếp đến Hà Nội

Nghiên cứu đa điểm tại Hà Nội và Thái Nguyên cho thấy PM2.5 giữa các điểm có xu hướng đồng biến đáng kể; điều kiện khí tượng và vận chuyển ô nhiễm đường dài, đặc biệt từ phía bắc/đông bắc, có ảnh hưởng rõ đến các đợt haze mùa đông. Đây là lý do nên đưa gió, độ ẩm, nhiệt độ và mùa vào feature set, nhưng không nên tuyên bố mọi biến động đều do giao thông địa phương. Xem [Journal of Aerosol Science](https://doi.org/10.1016/j.jaerosci.2020.105716).

Một nghiên cứu khác sử dụng dữ liệu chín trạm trong các giai đoạn giãn cách COVID-19 và Random Forest để weather-normalize PM2.5/CO. Sau khi hiệu chỉnh ảnh hưởng thời tiết, mức giảm do thay đổi giao thông nhỏ hơn mức giảm quan sát trực tiếp. Bài học cho project là: **tương quan giữa PM2.5 và thời tiết không tự động đồng nghĩa quan hệ nhân quả**. Xem [Aerosol and Air Quality Research](https://link.springer.com/article/10.4209/aaqr.210081).

### 5.2. Hướng mô hình đã phổ biến

Nghiên cứu forecasting hiện có thường so sánh:

- ARIMA/SARIMA;
- SVR;
- Random Forest và XGBoost;
- CNN/LSTM/GRU;
- Transformer và các biến thể attention.

Ví dụ, một nghiên cứu so sánh ARIMA, SVR, RF, XGBoost, CNN, LSTM và Transformer trên dữ liệu PM2.5 theo giờ, sử dụng MAE/RMSE/R² và dự báo nhiều bước. Xem [International Journal of Environmental Science and Technology](https://link.springer.com/article/10.1007/s13762-023-04900-1).

Khoảng trống thường gặp trong các bài sinh viên không phải thiếu mô hình sâu, mà là:

- random split gây leakage;
- không có persistence/seasonal baseline;
- target hoặc rolling feature vô tình dùng dữ liệu tương lai;
- chỉ báo cáo metric trung bình, không đánh giá peak pollution;
- chọn model trên test set;
- không lưu pipeline và cấu hình để tái lập.

Project nên cạnh tranh ở tính đúng đắn và hoàn chỉnh trước khi cạnh tranh bằng deep learning.

## 6. Câu hỏi nghiên cứu được đề xuất

1. Có thể dự báo PM2.5 quan trắc tại Hà Nội trước 1, 6, 12 và 24 giờ tốt hơn persistence và seasonal persistence hay không?
2. Thông tin khí tượng cải thiện dự báo bao nhiêu so với chỉ sử dụng lịch sử PM2.5?
3. Hiệu năng suy giảm như thế nào khi horizon tăng?
4. Mô hình nào giữ được Recall tốt nhất đối với các giờ VN_AQI ở mức Kém trở lên?
5. Mô hình có tổng MAE thấp nhất có đồng thời dự báo tốt các đỉnh ô nhiễm hay không?
6. Yếu tố nào ảnh hưởng nhiều nhất đến dự báo và ảnh hưởng đó có thay đổi theo mùa không?

### Giả thuyết kiểm chứng

- H1: Lag và rolling statistics của PM2.5 là nhóm feature mạnh nhất cho horizon ngắn.
- H2: Thời tiết mang lại mức cải thiện rõ hơn ở horizon 12–24 giờ so với 1 giờ.
- H3: Mô hình tree boosting vượt linear model về MAE nhưng có thể vẫn bỏ lỡ peak.
- H4: Tối ưu riêng cho Recall cảnh báo tạo trade-off với MAE chung.
- H5: Persistence rất khó bị vượt ở horizon 1 giờ nhưng yếu dần khi horizon tăng.

## 7. Thiết kế thực nghiệm đề xuất

### Target

- Primary: `pm2_5(t+h)` với `h ∈ {1, 6, 12, 24}`.
- Secondary: mức VN_AQI tại `t+h`, tính từ PM2.5/NowCast theo đúng quy định.

### Feature groups

1. PM2.5 lags: 1, 2, 3, 6, 12, 24, 48, 72, 168 giờ.
2. Rolling statistics: mean/std/min/max với cửa sổ 3, 6, 12, 24 và 168 giờ; mọi cửa sổ phải kết thúc tại thời điểm dự báo.
3. Calendar: giờ, thứ, cuối tuần, tháng, mùa; mã hóa cyclical.
4. Weather: nhiệt độ, độ ẩm, điểm sương, mưa, áp suất, mây, tốc độ/hướng gió.
5. Optional: PM10/NO2/O3 cùng trạm nếu chuỗi đủ hoàn chỉnh.
6. Optional benchmark feature: CAMS PM2.5 tại ô lưới chứa trạm.

### Model ladder

Không chạy quá nhiều model ngay từ đầu. Thứ tự khuyến nghị:

1. Naive persistence.
2. Seasonal persistence (`t-24`, `t-168`).
3. Ridge/Elastic Net.
4. Random Forest.
5. Gradient boosting (XGBoost/LightGBM/HistGradientBoosting).
6. Một mô hình sequence như LSTM/GRU chỉ khi 1–5 đã hoàn chỉnh.

### Evaluation

- Chia train/validation/test theo thời gian.
- Test là giai đoạn mới nhất và không được dùng để chọn hyperparameter.
- Dùng rolling-origin backtesting trên train/validation.
- Fit scaler/imputer chỉ trên từng training fold.
- Báo cáo MAE, RMSE và R² cho từng horizon.
- Với cảnh báo: Precision, Recall, F1, PR-AUC và confusion matrix.
- Báo cáo thêm MAE trong top 10% giờ ô nhiễm cao và sai số theo tháng/mùa.
- So sánh bằng cùng timestamp hợp lệ cho tất cả model.

## 8. Scope chốt cho nhóm 10 người

### MVP bắt buộc

1. Một trạm PM2.5 tại Hà Nội có tối thiểu 12 tháng dữ liệu theo giờ và mức hoàn chỉnh mục tiêu ≥70% trước xử lý.
2. Pipeline tải raw data, lưu metadata và checksum/snapshot date.
3. Data-quality report: coverage, gap, duplicate, timezone, unit và outlier.
4. EDA theo giờ/ngày/mùa và phân tích các pollution episode.
5. Bốn horizon 1/6/12/24 giờ.
6. Hai baseline và ít nhất hai ML model.
7. Backtesting không leakage.
8. VN_AQI calculator có unit test.
9. API + dashboard hiển thị lịch sử, dự báo, cảnh báo và độ tin cậy.
10. README tái lập được, báo cáo và slide.

### Chỉ mở rộng khi MVP đã chạy

- nhiều trạm và bản đồ không gian;
- prediction interval/quantile regression;
- SHAP;
- LSTM/GRU;
- tự động cập nhật hằng giờ;
- drift monitoring;
- cảnh báo qua email/Telegram.

### Không thuộc scope

- ứng dụng mobile native;
- thiết kế phần cứng cảm biến;
- dự báo mọi chất ô nhiễm;
- bản đồ 3D;
- chẩn đoán hoặc tư vấn y khoa cá nhân;
- Hadoop/Spark khi dữ liệu chỉ ở quy mô vài trăm nghìn dòng.

## 9. Data feasibility gate — việc phải làm ngay

Do tiến độ gấp, nhóm chỉ dành **một ngày** để xác nhận dữ liệu, không tiếp tục khảo sát mở rộng.

### Điều kiện GO

Chọn một nguồn target nếu thỏa:

- PM2.5 theo giờ;
- ít nhất 12 tháng;
- timestamp và timezone xác định được;
- đơn vị µg/m³ hoặc chuyển đổi rõ ràng;
- tỷ lệ có dữ liệu ban đầu ≥70%;
- có quyền dùng cho mục đích học thuật;
- có thể snapshot về file để mọi thành viên dùng cùng phiên bản.

### Thứ tự thử

1. OpenAQ quanh trung tâm Hà Nội.
2. AirNow U.S. Embassy Hanoi archive.
3. Cổng quan trắc Hà Nội nếu có lịch sử/API tải được hợp lệ.
4. Dataset nghiên cứu công khai có provenance và license rõ ràng.

Nếu sau một ngày không có nguồn quan trắc đạt điều kiện, nhóm phải ghi rõ limitation và chuyển sang **MVP một trạm với dataset archive tốt nhất**, thay vì mất thêm nhiều ngày để săn dữ liệu đa trạm.

## 10. Quyết định cuối cùng

Project nên được trình bày là một hệ thống mới hoàn toàn:

> Nhóm xây dựng một pipeline độc lập để dự báo PM2.5 quan trắc tại Hà Nội và cảnh báo theo VN_AQI. Nhóm khảo sát các sản phẩm và nghiên cứu hiện có để xác định yêu cầu, baseline và phương pháp đánh giá, nhưng không tái sử dụng mã nguồn của project tham khảo.

Ưu tiên theo thứ tự:

1. Chốt và snapshot dữ liệu quan trắc.
2. Làm baseline đúng.
3. Hoàn thiện đánh giá và data quality.
4. Xây dashboard/API.
5. Chỉ sau đó mới thêm deep learning hoặc tính năng mở rộng.

## Tài liệu chính

- [VN_AQI — Quyết định 1459/QĐ-TCMT](https://cem.gov.vn/tin-tuc-moi-truong/tong-cuc-moi-truong-ban-hanh-huong-dan-ky-thuat-tinh-toan-va-cong-bo-chi-so-chat-luong-khong-khi-viet-nam)
- [OpenAQ API documentation](https://docs.openaq.org/)
- [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)
- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api)
- [AirNow Embassy and Consulate Monitor](https://gispub.epa.gov/airnowembassy/)
- [AirNow — About the data](https://www.airnow.gov/about-the-data/)
- [Hanoi Environmental Monitoring Portal](https://airhanoi.hanoi.gov.vn/)
- [WHO Global Air Quality Guidelines 2021](https://www.who.int/publications/i/item/9789240034228)
- [Hanoi multi-site PM2.5 and meteorology study](https://doi.org/10.1016/j.jaerosci.2020.105716)
- [Hanoi traffic-emission/weather-normalization study](https://link.springer.com/article/10.4209/aaqr.210081)
- [PM2.5 forecasting model comparison](https://link.springer.com/article/10.1007/s13762-023-04900-1)
- [IQAir Hanoi product reference](https://www.iqair.com/air-quality/vietnam/ha-noi/hanoi)
- [Google Air Quality API product reference](https://developers.google.com/maps/documentation/air-quality)
