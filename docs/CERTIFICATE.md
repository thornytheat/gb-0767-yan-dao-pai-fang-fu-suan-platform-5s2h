# 复核监测证据单

从留档打开签发页，确认运行编号、监测烟道和分钟差额后下载一份 JSON。`originals` 中的原件解码成文本后以 UTF-8 重新编码，逐文件计算 SHA-256，应等于 `manifest.sha256`。

核验 `sha256` 时，从附件取出 `manifest`、`originals` 和 `results` 组成对象。深度排序对象键，以 UTF-8 编码 JSON，保留中文原字符，分隔符使用逗号和冒号且省略空白。结果是内容校验摘要。原件可用 `files` 对象重新归档，以 `run_id` 和逐行判据对照两次计算。

按 `results.validity.rows[].sources` 找分钟原件，`mode_sources` 定位工况；按 `results.reports.rows[].reports` 找报送单据，`results.reports.rows[].adopted` 说明差额采用的单据与回执（回执号、事件、回执原件行与收件时刻；`receipt` 为空表示当时无在截止前受理的更正，按已受理原值对账）。回执隔离件见 `results.reports.receipt_isolated`，错误版本及 CSV 结构错误分别见 `oxygen.isolated` 和 `intake.files[].rejected`。

晚到回执补齐或监管端撤回受理后，以同一小时原件重新归档会得到新运行编号；旧证据包中的 `originals.receipts` 与 `adopted` 定格了交班当时的回执状态，复验旧包仍能还原当时差额采用的单据。
