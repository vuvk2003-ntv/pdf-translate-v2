<p align="center">
  <img src=".github/assets/logo.png" alt="PDF Translate logo" width="160">
</p>

<h1 align="center">PDF Translate</h1>

<p align="center">
  <strong>Dịch tài liệu PDF sang tiếng Việt và 35 ngôn ngữ khác<br>mà vẫn giữ nguyên bố cục, công thức, bảng và hình ảnh.</strong>
</p>

<p align="center">
  <sub>Xây dựng và duy trì bởi <NTV>
</p>

<p align="center">
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-windows.zip">
    <img src="https://img.shields.io/badge/TẢI_XUỐNG-Windows_x64-1f6feb?style=for-the-badge&logo=windows11&logoColor=white" alt="Tải PDF Translate cho Windows">
  </a>
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-macos-apple-silicon.dmg">
    <img src="https://img.shields.io/badge/TẢI_XUỐNG-macOS_Apple_Silicon-111111?style=for-the-badge&logo=apple&logoColor=white" alt="Tải PDF Translate cho Mac Apple Silicon">
  </a>
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest/download/PDFTranslate-macos-intel.dmg">
    <img src="https://img.shields.io/badge/TẢI_XUỐNG-macOS_Intel-555555?style=for-the-badge&logo=apple&logoColor=white" alt="Tải PDF Translate cho Mac Intel">
  </a>
</p>

<p align="center">
  <a href="https://github.com/breslee1707/VI-Translate/releases/latest"><img src="https://img.shields.io/github/v/release/breslee1707/VI-Translate?style=flat-square&label=release" alt="Bản phát hành mới nhất"></a>
  <a href="https://github.com/breslee1707/VI-Translate/releases"><img src="https://img.shields.io/github/downloads/breslee1707/VI-Translate/total?style=flat-square&label=downloads" alt="Tổng lượt tải"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/breslee1707/VI-Translate?style=flat-square" alt="Giấy phép AGPL-3.0"></a>
  <img src="https://img.shields.io/badge/Python-không_cần_cài-2ea44f?style=flat-square" alt="Không cần cài Python">
</p>

<p align="center">
  <a href="#điểm-nổi-bật">Điểm nổi bật</a> ·
  <a href="#bắt-đầu-trong-1-phút">Cài đặt</a> ·
  <a href="#cách-sử-dụng">Cách dùng</a> ·
  <a href="#được-phát-triển-ở-đây">Phát triển</a> ·
  <a href="#dùng-như-agent-skill">Agent Skill</a> ·
  <a href="#giới-hạn-hiện-tại">Giới hạn</a>
</p>

---

PDF Translate là ứng dụng mã nguồn mở dành cho Windows, macOS và Android. Công cụ phân tích bố cục từng trang, bảo vệ công thức và code, dịch phần văn xuôi rồi đặt nội dung trở lại đúng vị trí trong tài liệu gốc — không biến PDF của bạn thành một trang chữ trắng đơn giản.

Đây không phải bản dịch giao diện của một công cụ có sẵn. Dự án mượn ý tưởng và phần nhân đọc/ghi PDF từ hai dự án mã nguồn mở, rồi tự xây phần quyết định chất lượng đầu ra: ứng dụng desktop, bộ quy tắc bảo toàn bố cục, lớp xử lý tiếng Việt và hàng loạt bản sửa lỗi ngay trong nhân. Chi tiết ở mục [Được phát triển ở đây](#được-phát-triển-ở-đây).

## Điểm nổi bật

- **Giữ nguyên bố cục:** bảo toàn vị trí của đoạn văn, công thức, bảng, hình, mục lục và tài liệu tham khảo.
- **Sẵn sàng để dùng:** tải về, giải nén và chạy; không cần cài Python hay model riêng.
- **Xử lý hàng loạt:** kéo thả nhiều file PDF hoặc cả thư mục vào ứng dụng.
- **36 ngôn ngữ đích:** mặc định là tiếng Việt, cùng nhiều ngôn ngữ sử dụng chữ Latin.
- **Không dừng cả hàng đợi:** một file lỗi không làm gián đoạn các file còn lại.
- **Tự cập nhật (Windows):** có bản mới thì ứng dụng tự tải ngầm, bạn chỉ cần bấm một lần để khởi động lại. Trên macOS vẫn là dòng nhắc mở trang tải.
- **Có chế độ dành cho AI agent:** dùng model trong Codex, Claude Code hoặc Copilot để dịch tài liệu chuyên ngành tốt hơn.
- **Chặn sai dữ liệu kỹ thuật có thể kiểm định:** kết quả làm đổi mã PLC/robot,
  alarm/model, số, đơn vị, URL/path, standard/document ID hoặc placeholder được
  từ chối và giữ nguyên segment nguồn thay vì ghi sai âm thầm.

## Bắt đầu trong 1 phút

### Windows

1. **[Tải PDF Translate cho Windows](https://github.com/vuvk2003-ntv/pdf-translate-v2)** (`.zip`, khoảng 199 MB).
2. Giải nén toàn bộ file vừa tải.
3. Mở `PDFTranslate.exe`.

Muốn tự biên dịch:

```bash
cd android
./gradlew assembleDebug
```

> [!NOTE]
> Bản macOS yêu cầu **macOS 14 Sonoma trở lên**. Tất cả bản desktop đều không cần cài Python và không phải tải thêm model ở lần chạy đầu. Quá trình dịch bằng Google vẫn cần kết nối Internet.

> [!IMPORTANT]
> Bản Android là một bản viết lại trên PDFBox-Android, không phải bản desktop biên dịch lại. Nó chỉ đọc PDF dạng văn bản (chưa có OCR) và dùng heuristic thay cho model bố cục BabelDOC/YOLOv8, nên PDF nhiều công thức có thể còn sai chỉ số trên/dưới hoặc phân số. Chi tiết build và phát hành nằm ở [`docs/android-release.md`](docs/android-release.md).

> [!WARNING]
> Windows SmartScreen có thể cảnh báo vì ứng dụng chưa được ký số. Chọn **More info** → **Run anyway** nếu bạn tải file từ trang Releases chính thức của repo này.

> [!WARNING]
> Nếu máy bạn bật **Smart App Control** (mặc định trên các máy Windows 11 cài mới), ứng dụng sẽ bị **chặn hẳn** với thông báo *"An Application Control policy has blocked this file"* — không có nút Run anyway. Kiểm tra tại **Windows Security → App & browser control → Smart App Control**. Đây là hệ quả của việc ứng dụng chưa có chữ ký số, không phải lỗi của ứng dụng. Microsoft chỉ cho bật lại Smart App Control bằng cách cài lại Windows, nên hãy cân nhắc kỹ trước khi tắt nó; giải pháp đúng là ký số ứng dụng, và việc đó đang được xử lý.

> [!WARNING]
> Bản macOS hiện dùng chữ ký ad-hoc, chưa được Apple notarize. Gatekeeper có thể chặn thao tác mở thông thường; hãy dùng cách bấm chuột phải → **Open** ở trên nếu bạn tải từ trang Releases chính thức.

Bạn cũng có thể mở [trang Releases](https://github.com/breslee1707/VI-Translate/releases/latest) để xem ghi chú thay đổi và các tệp của phiên bản mới nhất.

Ứng dụng cũng tự kiểm tra phiên bản mới mỗi lần mở. Máy không có mạng thì bỏ qua, không báo lỗi.

Trên **Windows**, bản mới được tải ngầm ngay trong lúc bạn vẫn dịch bình thường; góc trên bên phải hiện **↓ Đang tải vX.Y.Z**, tải xong đổi thành **● Cài vX.Y.Z & khởi động lại**. Bấm vào đó, ứng dụng đóng lại, thay thư mục cài đặt rồi tự mở lại — khoảng mười giây, không phải giải nén gì. Đang dịch dở thì nút chờ đến khi xong. Tải xong mà bạn tắt ứng dụng luôn cũng không mất: lần mở sau vẫn là nút khởi động lại, không tải lại từ đầu. Nếu bước thay thư mục hỏng giữa chừng, bản cũ được đưa lại nguyên vẹn và bạn tải thủ công như trước.

Trên **macOS**, và khi ứng dụng nằm ở thư mục không có quyền ghi, dòng đó vẫn là **● Có bản mới vX.Y.Z** và bấm vào sẽ mở trang tải.

## Cách sử dụng

### 1. Thêm tài liệu

Chọn một trong ba cách:

- Kéo thả file PDF hoặc cả thư mục vào cửa sổ ứng dụng.
- Bấm **Chọn file** hoặc **Chọn thư mục**.
- Thả file trực tiếp lên biểu tượng ứng dụng.

### 2. Chọn ngôn ngữ

Chọn ngôn ngữ đích trong mục **Dịch sang**. Ứng dụng mặc định dịch sang **Tiếng Việt**.

### 3. Bắt đầu dịch

Bấm **Dịch**. Các file được xử lý lần lượt và hiển thị trạng thái ngay trong hàng đợi.

Kết quả được lưu tự động vào thư mục `translated` nằm cạnh file nguồn:

```text
TaiLieu/
├── document.pdf
└── translated/
    └── document-vi.pdf
```

Mặc định, ứng dụng không ghi đè kết quả đã có. Bật **Ghi đè file đã dịch trước đó** khi bạn muốn dịch lại.

## Được phát triển ở đây

Phần kế thừa từ dự án gốc là nhân đọc, phân tích bố cục và render PDF. Mọi thứ
quyết định việc một trang tiếng Việt in ra có đọc được hay không đều được viết
cho dự án này:

**Ứng dụng desktop — 1.620 dòng trong [`app/`](app/)**
Giao diện, kéo thả, hàng đợi nhiều file không dừng vì một file lỗi, mã lỗi đọc
được kèm log để người dùng báo lỗi được, cơ chế tự cập nhật tự thay thư mục cài
đặt rồi mở lại app, đóng gói PyInstaller cho Windows và cả hai kiến trúc macOS,
workflow phát hành tự động. Không một dòng nào trong thư mục này đến từ dự án
gốc.

**Bộ quy tắc bảo toàn — [`pdf2zh/rules.py`](pdf2zh/rules.py) (519 dòng) và [contract sản phẩm](references/preservation-rules.md)**
Đây là thứ phân biệt dự án với một công cụ dịch PDF thông thường: nhận diện vùng
công thức và ký hiệu kỹ thuật để không dịch nhầm thành văn xuôi, giữ in đậm và
in nghiêng qua vòng dịch, dựng lại chữ xoay 90 độ đúng chiều thay vì bẻ ngang,
giữ bullet Wingdings/Symbol mà font Unicode không có glyph, tính chiều cao dòng
tối thiểu cho chữ tiếng Việt nhiều dấu chồng.

**Những lỗi tự tìm và tự sửa trong nhân**
Mỗi lỗi dưới đây được phát hiện từ tài liệu thật, truy ra nguyên nhân trong nhân
và sửa tại đây:

- Tiếng Việt mất sạch chữ có dấu chồng — *"Việt"* in ra thành *"Vi t"* — vì
  `subset_fonts` đánh số lại glyph trong khi content stream gọi theo ID gốc.
- PDF chữ thường phình thêm vài MB vì phép đo ô bảng vô tình đánh dấu cả bốn mặt
  Unicode là đã render; nhân hiện chỉ nhúng những kiểu chữ thực sự phát glyph.
- Sách dài dừng ngay trước trang cuối vì tài liệu bị nhân đôi rồi nén lại toàn bộ
  ở bước kết thúc.
- Cả trang lộn ngược vì ma trận chữ phản chiếu `1 0 0 -1` bị hiểu nhầm là xoay.
- Đoạn văn in đè lên đoạn bên dưới vì ngân sách chiều cao chỉ tính hộp của chính nó.

Bảng nguyên nhân đầy đủ: [agent-knowledge/regressions.md](agent-knowledge/regressions.md).

**Bộ test hồi quy trong [`tests/`](tests/)**
Mỗi lỗi đã sửa đều bị khoá lại bằng một test dựng đúng hình học nhỏ nhất gây ra nó,
nên bản sửa không âm thầm mất đi ở lần thay đổi sau.

**Chế độ Handoff và Agent Skill**
Một engine dịch thứ hai đưa các đoạn văn sang JSONL cho AI agent dịch theo ngữ
cảnh chuyên ngành rồi dựng lại PDF, cùng chuẩn `SKILL.md` để gọi trực tiếp trong
Codex, Claude Code hay Copilot.

## Ngôn ngữ hỗ trợ

Ứng dụng hỗ trợ 36 ngôn ngữ sử dụng chữ Latin, gồm tiếng Việt, Anh, Pháp, Đức, Tây Ban Nha, Bồ Đào Nha, Ý, Indonesia, Hà Lan, Ba Lan, Thổ Nhĩ Kỳ và nhiều ngôn ngữ châu Âu khác.

Ngôn ngữ nguồn mặc định là `auto`. Pipeline Python/Agent Skill nhận mã nguồn
Google hợp lệ, gồm `ko`, `en`, `zh-CN` và `zh-TW`, nên có thể xử lý tài liệu
Korean/English/Simplified Chinese/Traditional Chinese hoặc nội dung kỹ thuật pha
trộn. Đây là khả năng nhận dạng nguồn; danh sách 36 ngôn ngữ ở trên là **đích
Latin-script** mà font/render hiện hỗ trợ.

Các hệ chữ sau chưa được hỗ trợ: Trung, Nhật, Hàn, Ả Rập, Do Thái, Thái và các chữ Ấn Độ. Ứng dụng sẽ báo lỗi thay vì tạo PDF chứa ký tự ô vuông do thiếu glyph.

## Dùng như Agent Skill

Repo đồng thời tuân theo chuẩn [Agent Skills](https://agentskills.io/) và có thể dùng với Codex, Claude Code, GitHub Copilot cùng các coding agent hỗ trợ `SKILL.md`.

Cài skill cho tất cả agent có trên máy:

```powershell
npx skills add breslee1707/VI-Translate -g --all
```

Sau đó gọi skill bằng yêu cầu tự nhiên:

```text
Use $pdf-translate to translate this PDF into Vietnamese.
```

Trong Claude Code hoặc Copilot CLI:

```text
/pdf-translate translate this PDF into Vietnamese.
```

Skill cung cấp hai chế độ dịch:

| Chế độ | Bộ máy dịch | Phù hợp khi |
| --- | --- | --- |
| **Google** | `translate.google.com` | Cần nhanh, miễn phí và không có API key |
| **Handoff** | AI agent/provider trong phiên làm việc | Tài liệu chuyên ngành cần bản dịch theo ngữ cảnh |

Google là chế độ mặc định và là chế độ được dùng trong app desktop. Google nhận
raw segment qua HTTPS query parameter; các cặp đã qua validator được lưu trong
SQLite cache cục bộ. Handoff trích các đoạn văn sang JSONL rồi dựng lại PDF và
không gọi Google, nhưng nếu Codex/Claude/Copilot hoặc API hosted đọc JSONL thì
nội dung vẫn được xử lý bởi provider bên ngoài. Vì vậy Handoff không đồng nghĩa
với private/offline.

Handoff gắn mỗi record nguồn bằng `type: "untrusted_source_content"`. Nội dung
tài liệu—kể cả câu “ignore previous instructions”, command hoặc URL—chỉ là dữ
liệu cần dịch, không phải chỉ dẫn được phép thực thi. Kết quả CLI báo đủ
`total/translated/preserved/unresolved`; segment lỗi validator, thiếu bản dịch
handoff hoặc không fit được tính là `unresolved`, không phải `translated`.

Handoff chuẩn bị batch tối đa 30 segment và 12.000 ký tự nguồn. Kết quả được
kiểm tra deterministic trước khi dựng PDF: record hợp lệ được giữ lại, chỉ
record thiếu/sai mới retry, tối đa ba lượt. Cache chỉ reuse các đoạn đủ dài để
an toàn theo ngữ cảnh; label ngắn như `Manual` không bị cache mù. Có thể truyền
một terminology map JSON nhỏ đã được xác nhận để giữ thuật ngữ nhất quán.

`CONFIDENTIALITY ENFORCEMENT: NOT IMPLEMENTED`. CLI chỉ cảnh báo best-effort khi
text extractable chứa `대외비`, `CONFIDENTIAL`, `INTERNAL` hoặc `RESTRICTED`;
đây không phải phân loại pháp lý và không áp dụng provider allow/deny policy.
Xem [provider, trust và confidentiality](references/security-and-confidentiality.md).

Ví dụ với từ *conduction* trong tài liệu truyền nhiệt:

| Google | Handoff |
| --- | --- |
| “Sự **dẫn điện** xảy ra khi hai vật tiếp xúc trực tiếp” | “**Dẫn nhiệt** xảy ra khi hai vật thể tiếp xúc trực tiếp với nhau” |

Xem quy trình đầy đủ tại [SKILL.md](SKILL.md).

## Chạy từ mã nguồn

### Chuẩn bị môi trường

```powershell
git clone https://github.com/breslee1707/VI-Translate.git
cd VI-Translate
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Dịch bằng Google

```powershell
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --output-dir OUT
```

### Dịch bằng Handoff

```powershell
# 1. Trích các đoạn cần dịch
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --engine handoff --emit-segments segments.jsonl

# 2. Dùng agent dịch segments.jsonl thành translations.jsonl
.venv\Scripts\python.exe scripts\prepare_handoff.py segments.jsonl --output-batches batches.jsonl

# 3. Validate toàn bộ; chỉ record lỗi/thiếu được đưa vào retry-batches.jsonl
.venv\Scripts\python.exe scripts\prepare_handoff.py segments.jsonl --translations translations.jsonl --accepted accepted.jsonl --retry-batches retry-batches.jsonl

# 4. Chỉ dựng PDF một lần sau khi retry-batches.jsonl rỗng
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --engine handoff --segments accepted.jsonl --output-dir OUT
```

### Build ứng dụng Windows

```powershell
.\build.ps1
```

Gói phát hành được tạo tại `dist\PDFTranslate-windows.zip`.

### Build ứng dụng macOS

Chạy trên máy Mac dùng đúng kiến trúc cần phát hành:

```bash
bash build-macos.sh
```

Gói phát hành được tạo tại `dist/PDFTranslate-macos-apple-silicon.dmg` hoặc `dist/PDFTranslate-macos-intel.dmg`. Từ máy Windows, bạn có thể chạy thủ công workflow **Release** trên GitHub Actions để lấy cả hai DMG trong phần Artifacts; khi push tag `v*`, workflow tự đính kèm chúng vào GitHub Release.

## Giới hạn hiện tại

- **Chưa có OCR:** PDF scan chỉ chứa hình ảnh cần được OCR trước khi dịch.
- Chữ nằm trong vùng được nhận diện là bảng hoặc hình đôi khi được giữ nguyên theo bản gốc.
- Mục lục, index, danh mục ký hiệu và tài liệu tham khảo được ưu tiên giữ bố cục nên không được dàn lại dòng. Xem [quy tắc bảo toàn](references/preservation-rules.md).
- Mỗi đoạn gửi tới Google được giới hạn ở 5.000 ký tự; segment dài hơn bị từ
  chối toàn bộ và báo unresolved, không bị cắt đuôi rồi nhận nhầm là đã dịch.
- Với đoạn vốn quá chật, mẫu số của phân số nội dòng có thể vẫn nằm sát dòng bên dưới.
- Validator deterministic không chứng minh được phủ định, modality, sequence,
  safety severity hoặc chất lượng thuật ngữ. Các thuộc tính này cần Tier B
  semantic benchmark/human review riêng; benchmark không thuộc CI mặc định.

Nên kiểm tra lại tài liệu đầu ra trước khi dùng cho xuất bản hoặc các mục đích yêu cầu độ chính xác cao.

## Giấy phép và ghi công

PDF Translate được phát hành theo giấy phép [AGPL-3.0](LICENSE). Nếu phát hành lại ứng dụng hoặc cung cấp nó như một dịch vụ qua mạng, bạn phải kèm theo mã nguồn tương ứng theo điều khoản của giấy phép.

Thư mục [`pdf2zh/`](pdf2zh/) là bản fork của nhân xử lý PDF từ [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) 1.9.11, dùng model bố cục và font đã ghim của [BabelDOC](https://github.com/funstory-ai/BabelDOC). Hai dự án đó cho công việc này một điểm khởi đầu và được ghi công đầy đủ tại [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Ứng dụng desktop, bộ quy tắc bảo toàn, các bản sửa lỗi trong nhân, bộ test hồi quy, chế độ Handoff và toàn bộ phần đóng gói — mọi thứ liệt kê ở [Được phát triển ở đây](#được-phát-triển-ở-đây) — do **Lê Ngọc Gia Huy** ([@huyg.ai trên TikTok](https://www.tiktok.com/@huyg.ai)) phát triển và duy trì.

---

<p align="center">
  Nếu PDF Translate hữu ích với bạn, hãy tặng repo một ⭐ để nhiều người biết đến dự án hơn.
</p>

<p align="center">
  <sub>Xây dựng &amp; duy trì bởi <NTV>
</p>
