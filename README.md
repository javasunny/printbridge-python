# 东芝 PDF 打印中转站

Mac 浏览器上传 PDF，Windows 上的 SumatraPDF 调用已安装的东芝 GDI 驱动打印。第一版支持 A4、1–99 份、单面／长边双面／短边双面以及任务状态。默认是模拟模式，不会出纸。

## 推荐部署：Windows 便携应用

现在提供配置窗口和系统托盘入口。Windows 构建机在项目根目录执行：

```powershell
powershell.exe -NoProfile -File .\scripts\build-windows.ps1
```

产物为 `dist\PrintBridge-windows.zip`。目标 Windows 电脑完整解压后双击 `PrintBridge.exe`，不需要安装 Python 或 uv。首次选择打印机、确认自动检测的 SumatraPDF 路径，按需要勾选局域网访问后保存并启动。默认模拟模式，不会出纸。

窗口支持启停服务、打开网页和令牌管理页；关闭窗口缩至托盘，托盘菜单可打开窗口或退出。登录启动选项仅在 EXE 中可用，写入当前用户启动项，不需要管理员权限。

配置保存在 `%LOCALAPPDATA%\PrintBridge\desktop.json`，模拟与真实任务分别保存在 `runtime-simulate` 和 `runtime-windows` 子目录。两种模式的设备令牌也独立，切换后需在对应模式的管理页生成令牌。更新程序不会覆盖这些数据。旧命令行版的 `runtime/` 不会自动迁移；旧启动方式仍可使用，但不要与桌面版同时占用同一端口。

SumatraPDF 和东芝驱动仍需单独安装。Windows EXE 尚需在 Windows 构建和实机验收；详见 [便携版部署与构建](docs/portable-windows.md)。下面的环境变量启动方式作为开发和命令行部署入口保留。

## 环境

- Python 3.11+；使用 uv 安装锁定依赖。
- 真实打印：Windows、能正常打印的东芝驱动，以及 [SumatraPDF](https://www.sumatrapdfreader.org/download-free-pdf-viewer)。第三方程序不包含在仓库内。
- Windows 电脑保持开机、用户保持登录；Mac 与其位于同一可信局域网。

## 安装与本地验证

在项目目录运行：

```sh
uv sync --frozen
uv run pytest -q
```

macOS/Linux 模拟启动：

```sh
export PRINT_MODE=simulate
uv run python -m printbridge
```

在服务端本机浏览器访问 `http://127.0.0.1:8765/admin`，输入设备名称并生成令牌。完整令牌仅在生成时显示一次，复制后隐藏；列表只显示前缀，无法重新查看。丢失时撤销旧令牌并生成新的。

客户端访问打印页面，粘贴令牌连接。连接成功后，令牌保存在该浏览器的 localStorage 中，刷新或重新打开同一地址时自动连接；“退出并清除令牌”删除浏览器保存的令牌。服务端只存随机令牌的 SHA-256 哈希，不保存原文。客户端仍需保留原文才能自动认证。

管理页显示设备名称、客户端标识、IP、最近在线时间；每 5 秒心跳，60 秒未收到心跳即离线，后台标签页休眠也可能显示离线。同一浏览器使用同一客户端标识，不同浏览器分别显示。每台设备建议使用独立令牌。撤销立即阻止后续请求，已经接收的打印任务仍继续执行。

管理页和管理 API 校验真实连接来源、Host、Origin 与防跨站令牌，仅允许在服务端本机通过 `127.0.0.1`、`localhost` 或 `::1` 访问。请使用 `python -m printbridge` 启动（已禁用代理头信任），不要放在反向代理后运行。

**旧版本升级：** 停止服务后更新 `printbridge/`、`static/`、新增的 `templates/` 目录，再重启服务；保留现有 `runtime/` 和本地启动配置。`PRINT_TOKEN` 已停用，已有客户端令牌需重新生成并输入。旧任务数据库保留，启动时自动添加令牌和客户端表。

`.env.example` 仅说明配置项，程序不会自动加载 `.env`。所有配置通过环境变量提供。

## Windows 启动

1. 先确认 Windows 本机可以使用目标打印机打印 PDF。
2. 安装 Python 3.11+ 和 uv，在项目目录执行 `uv sync --frozen`。
3. 将 `start-windows.example.ps1` 复制为 `start-windows.local.ps1`。本地文件已被 Git 忽略。
4. 编辑本地脚本：先保持 `PRINT_MODE=simulate`；无需配置 `PRINT_TOKEN`。
5. 在 PowerShell 执行 `powershell.exe -NoProfile -File .\start-windows.local.ps1`，先打开 `http://127.0.0.1:8765/admin` 生成客户端令牌，再验证模拟上传。
6. 准备真实出纸时，将 `PRINT_MODE` 改为 `windows`，填写 Windows 中的准确打印机名称和 SumatraPDF 路径，重启服务。可用 `Get-Printer | Select-Object Name` 查看名称。
7. 供 Mac 访问时设置 `PRINT_HOST=0.0.0.0`，在 Windows 防火墙中仅允许专用网络、本地子网访问 TCP 8765。Mac 打开 `http://Windows电脑局域网IP:8765`。

服务使用普通 HTTP，仅用于可信局域网；访问令牌不提供传输加密。不做公网端口转发。需要跨不可信网络使用时，应先部署 HTTPS。

任务计划程序：创建“用户登录时”触发的任务，选择“仅当用户登录时运行”；程序填 `powershell.exe`，参数填 `-NoProfile -File "C:\项目目录\start-windows.local.ps1"`，起始目录填项目目录。不需要管理员权限。不要同时手动启动第二个实例。

## 状态与防重复

- **等待／处理中**：进入本地串行队列或正在调用打印程序。
- **模拟完成**：只验证链路，没有提交打印机。
- **已提交**：SumatraPDF 返回成功，不代表确认出纸。第一版未接入 Windows spooler 任务跟踪。
- **失败**：打印程序无法启动。
- **待核查**：打印超时、返回错误、执行异常或执行中服务重启；可能已经打印部分或全部页面。先检查 Windows 队列与实际出纸，不自动重试。

同一提交的重试使用同一 Idempotency-Key，不创建第二个任务；同一编号更改文件或参数会被拒绝。页面网络错误后应保留当前页面并重试原请求；刷新或另一设备的新提交无法自动识别为同一请求。

服务重启恢复排队任务，执行中的任务转为待核查。单个运行时目录使用进程锁，只允许一个实例。不要使用多 worker 或开发热重载运行真实打印。

PDF 默认在任务进入终态 24 小时后清理；等待和执行中的文件不清理。任务元数据和请求编号保留以防旧请求重放；页面显示最近 100 条任务。`runtime/` 是私有工作目录，不经 HTTP 暴露。上传只允许有效、未加密、有页面的 PDF，默认上限 25 MB。

## 配置

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| PRINT_MODE | simulate | simulate 或 windows |
| PRINT_IPP_ENABLED | 0 | 设置为 1 开启 IPP 接口 |
| PRINT_HOST / PRINT_PORT | 127.0.0.1 / 8765 | 监听地址与端口 |
| PRINT_RUNTIME | runtime | 文件与 SQLite 目录 |
| PRINT_PRINTER | 空 | Windows 打印机准确名称 |
| PRINT_SUMATRA | 空 | SumatraPDF 可执行文件路径 |
| PRINT_MAX_UPLOAD_MB | 25 | 单文件大小上限 |
| PRINT_TIMEOUT_SECONDS | 120 | 单次打印程序超时 |
| PRINT_RETENTION_HOURS | 24 | 终态 PDF 保留小时数 |

## Windows 实机验收（尚需执行）

先用少量非敏感测试页验证：中文字体、A4 缩放、两份打印、单面、长边及短边双面。对照所选参数和实际出纸，确认驱动支持。打印参数参考 [SumatraPDF 官方文档](https://www.sumatrapdfreader.org/docs/Command-line-arguments)。程序固定黑白、适合页面，不提供纸盒选择。

检查打印程序不可启动时的失败状态、打印超时和服务中断后的待核查状态。断纸／离线不一定导致 SumatraPDF 返回错误，仍需查看 Windows 队列；第一版不会报告纸张已打印完成。

Mac 自动化测试只验证鉴权、PDF 校验、大小限制、去重、任务恢复、清理、命令参数和失败处理，不等同于 Windows 实机通过。


## macOS 原生打印：IPP 入口

已提供可选的 IPP 接口，复用网页的 PDF 校验、持久化队列与 Windows 打印流程。支持 Print-Job、Validate-Job、Get-Printer-Attributes、Get-Job-Attributes、Get-Jobs 和 Cancel-Job。当前是 PDF 转发网关，不声明为完整 AirPrint 或 IPP Everywhere 设备；不支持 Create-Job/Send-Document、多文档、PostScript、PWG Raster 或自动发现。

### 1. Windows 开启

更新 `printbridge/` 和 `static/`，保留 `runtime/` 与本地启动脚本。在 `start-windows.local.ps1` 的启动命令之前设置：

```powershell
$env:PRINT_IPP_ENABLED = '1'
$env:PRINT_HOST = '0.0.0.0'
```

重启服务。先保持 `PRINT_MODE=simulate` 测试；正式出纸再使用 `windows`。模式切换前应等待模拟任务处理完毕，或为真实打印配置独立的 `PRINT_RUNTIME` 目录。沿用已有 TCP 8765 防火墙规则，无需开放 631。

在 Windows 本机访问 `http://127.0.0.1:8765/admin`，为 Mac 单独生成设备令牌。IPP 使用 HTTP Basic：用户名固定为 `printbridge`，密码为设备令牌。撤销令牌同样阻止后续 IPP 打印和查询，不撤销已接收的任务。不要把令牌写进打印机 URL。能力查询不需要密码；任务操作均需认证，且只能查询、取消同一令牌的 IPP 任务。

### 2. Mac 添加打印机

在“系统设置 → 打印机与扫描仪 → 添加打印机 → IP”中填写：

| 项目 | 内容 |
|---|---|
| 地址 | Windows 的局域网 IP 和端口，例如 `192.168.1.50:8765` |
| 协议 | Internet Printing Protocol（IPP） |
| 队列 | `ipp/print` |
| 名称 | 自定，例如 `TOSHIBA PrintBridge` |
| 使用 | “其他”，选择本项目的 `drivers/PrintBridge.ppd` |

使用提供的 PPD 让 CUPS 发送 PDF；不要选择“通用 PostScript”或“通用 PCL”。PPD 是这个中转服务的能力描述，不是东芝硬件驱动。首次打印若提示认证，用户名填 `printbridge`，密码填设备令牌。若任务显示“等待认证”，在 Mac 打印队列中点击恢复并输入凭据。

也可在项目目录通过终端添加（会修改 Mac 打印机配置，请自行执行）：

```sh
sudo lpadmin -p TOSHIBA_PrintBridge -E \
  -v ipp://192.168.1.50:8765/ipp/print \
  -P "$PWD/drivers/PrintBridge.ppd" \
  -o auth-info-required=username,password
```

将示例 IP 替换为 Windows 地址。不要在命令里填写令牌。之后从支持打印的 Mac 应用按 ⌘P 选择该打印机；应用/CUPS 负责生成 PDF，用户不必手动导出。界面添加、首次认证提示及最终出纸仍需在你的 Mac/Windows 组合上验收。

### 状态与重试限制

- 仅排队中的 IPP 任务可以取消；已经进入 Windows 打印程序的任务需在 Windows 队列处理。
- 网页任务列表也显示 IPP 任务；IPP Get-Jobs 仅返回当前设备令牌下的 IPP 任务。
- Windows 提交成功后，IPP 按 RFC 8011 网关语义返回 `completed` + `queued-in-device`，含义是已转交，**不是实际出纸确认**。模拟模式返回完成并明确标注无打印。
- 结果不明返回 `aborted`，同时提示人工核查，不自动重打。
- 同一令牌、来源 IP、请求头（含 request-id）的重复提交在首次接收后的 10 分钟内返回原任务；内容冲突则拒绝。新 request-id 或超过窗口的请求视为新提交，因此“重新打印”不是幂等重试。
- 支持 A4、1–99 份和单／双面。非 A4、原始 page-ranges、颜色和未支持的布局选项可能被拒绝；不要依赖网关完成复杂分页。
- 当前 HTTP Basic 无传输加密，仅用于可信局域网。令牌拥有与网页相同的访问权限。

### 验证命令

```sh
uv run pytest -q
# 以下两项仅 macOS；模拟验证不会连接真实打印机，也不会添加系统打印队列
cupstestppd -q drivers/PrintBridge.ppd
uv run python scripts/check_ipp_macos.py
```

模拟脚本使用临时目录和临时设备令牌，在回环地址启动服务，调用 macOS 自带 ipptool 执行真实的能力查询和 PDF 提交，并确认任务进入模拟完成状态；结束后停止服务和清理临时数据。不得将这项测试视为 Windows 驱动或实际出纸验证。

协议参考：[RFC 8010 编码](https://www.rfc-editor.org/rfc/rfc8010)、[RFC 8011 任务语义](https://www.rfc-editor.org/rfc/rfc8011)、[CUPS PPD 扩展](https://www.cups.org/doc/spec-ppd.html)。
