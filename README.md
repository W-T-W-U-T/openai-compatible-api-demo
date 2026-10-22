# 接入第三方 API：最小可运行示例（Python · 零依赖）

一个"复制就能跑"的最小项目，演示对接 **OpenAI 兼容格式** 的大模型接口：
DeepSeek、OpenAI、通义千问、Kimi、本地 Ollama 都能用同一份代码，改 3 个环境变量即可。
只用 Python 标准库，**不需要 pip install 任何东西**。

除了单条调用，项目里还带了三件「跑大模型」时真正用得上的工具：

| 工具 | 用途 |
| --- | --- |
| `batch.py` | 提示词文件整批跑，记录 token / 耗时 / 费用，支持断点续跑 |
| `compare.py` | 同一提示词横向对比多个模型，看谁快、谁便宜、谁答得好 |
| `doctor.py` | 一键体检：Python / Key / 接口连通性 / 可选真实调用 |

## 0. 运行环境（已自动装好，无需你再操作）

| 项目 | 值 |
| --- | --- |
| Python | `3.12.14`（完整 CPython，标准库全量） |
| 安装位置 | `C:\Users\1\AppData\Local\Programs\Python\Python312` |
| pip | `26.2.1`，已可用 |
| 附加能力 | `venv` 虚拟环境、`sqlite3`、`ssl`、`tkinter` 均可用 |
| PATH | 已写入用户 PATH 并置顶，**新开的终端**里 `python` / `pip` 直接可用 |

安装过程说明：官方图形安装器需要管理员权限（依赖 Windows Installer 服务），当前会话无法提权；
python.org 免安装版（embeddable）虽免权限，但它的 `._pth` 会锁死 `sys.path`，导致
`python main.py` 无法导入同目录模块。因此这里改用**完整的 CPython 运行时**（与 DSH 内置运行时
同源，3.12.14）装到用户目录：行为与标准安装一致，不需要管理员权限，也不会被系统更新覆盖。

老终端里如果还没有 `python`，**重开一个终端**即可，或直接用 `run.cmd`（自动定位解释器）。

## 目录结构

```
default-workspace/
├── api_client.py        # 核心：HTTP 调用、Bearer 鉴权、超时、重试、SSE 流式解析、token 用量
├── main.py              # 命令行入口：--mock / --stream / --system
├── batch.py             # 批量跑模型：提示词文件 -> JSONL（用量/耗时/费用，可续跑）
├── compare.py           # 多模型对比：同一提示词跑多个模型，输出对比表
├── doctor.py            # 环境体检：Key / 接口 / 连通性 / 真实调用
├── run.cmd              # 一键运行（自动定位解释器）
├── mock_server.py       # 本地 mock 服务，没 Key 也能跑通整条链路
├── examples/            # 提示词示例：prompts.txt / prompts_multiline.txt / prompts.jsonl
├── tests/               # 离线测试（不需要 Key、不联网）
├── .github/             # CI、CodeQL、依赖审查、Issue/PR 模板等自动化
├── .env.example         # 配置模板
├── .env                 # 已生成，只差填 Key（已被 git 忽略）
└── .gitignore           # 已忽略 .env 和 results/，密钥和跑数结果不会进仓库
```

## 1. 先离线跑通（不需要 Key）

`python` 已经装好并加进 PATH，新终端里直接运行即可：

```powershell
python main.py --mock "你好"
python main.py --mock --stream "讲个笑话"   # 看流式效果
```

`run.cmd` 是等价的快捷方式（会自动定位解释器，适合不想关心 Python 在哪的情况）：

```powershell
run.cmd --mock "你好"
```

## 2. 接真实 API（只差一个 Key）

`.env` 已经生成好了，只剩填 Key 这一件事：

```powershell
# 第 1 步：打开 .env（记事本）
notepad .env

# 第 2 步：把这一行换成你在 https://platform.deepseek.com/api_keys 申请的 Key
#         OPENAI_API_KEY=sk-在这里替换成你的真实Key

# 第 3 步：先体检，再运行
python doctor.py            # 看 Key / 网络是否就绪
python doctor.py --live     # 真发一条最短请求（几分钱），确认能扣费、能出结果
run.cmd "用一句话介绍 Python"
run.cmd --stream "写一首关于秋天的五言绝句"
run.cmd --system "你只答一句话" "什么是递归"
```

填了占位符不会误发请求：程序会识别出 `.env` 里还是模板值，直接提示你去填，
不会拿假 Key 去打接口。填好后再运行就会真正调用。

不想用 .env 的话，直接设环境变量也行（PowerShell 临时生效）：

```powershell
$env:OPENAI_API_KEY = "sk-你的key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:OPENAI_MODEL = "deepseek-flash"
python main.py "你好"
```

## 3. 批量跑模型（后期主力用法）

一条命令把整个提示词文件跑完，结果写成 JSONL，**每条都带 token 用量、耗时、预估费用**，
中途断了重跑会跳过已成功的条目。

```powershell
# 先离线看效果（不花钱）
python batch.py examples/prompts.txt --out results/demo.jsonl --mock

# 真实跑：默认读 .env 里的模型/Key；DeepSeek 建议并发 1~4
python batch.py examples/prompts.txt --out results/run1.jsonl --workers 2

# 上次跑到一半断了？加 --resume 接着跑，已成功的不会重复花钱
python batch.py examples/prompts.txt --out results/run1.jsonl --resume

# 先小样本试水：只跑前 5 条
python batch.py examples/prompts.txt --out results/try.jsonl --limit 5
```

提示词文件两种格式（按扩展名自动识别）：

```text
# prompts.txt：一行一条，空行和 # 开头忽略
用一句话解释什么是 API。
把「今天天气很好」翻译成英文。
```

```text
# prompts_multiline.txt：用单独一行 --- 分隔，每块算一条多行提示词
请用三点总结《三国演义》的主要情节，
每点不超过 20 个字。
---
把下面这段话翻译成英文，只输出译文：
「模型跑得越久，账单越贵。」
```

```jsonl
{"id": "translate-1", "system": "你是一名严谨的中英翻译", "prompt": "把「你好」翻译成英文。"}
{"id": "extract-1", "prompt": "从这句话里提取公司名和金额。"}
```

结果文件一行一条 JSON，字段含义：

| 字段 | 说明 |
| --- | --- |
| `id` / `prompt` | 提示词 ID 和原文 |
| `reply` | 模型回复（失败时为 `null`） |
| `error` | 失败原因（成功时为 `null`，401/429/超时都会写在这里） |
| `latency_ms` | 本条耗时 |
| `prompt_tokens` / `completion_tokens` | 输入 / 输出 token 数 |
| `cost_cny` | 按模型单价估算的费用（元） |
| `created_at` | 完成时间 |

费用默认按 **DeepSeek 高峰价**估算（`deepseek-flash` 输入 2 元 / 输出 8 元每百万 tokens，
`deepseek-v4-pro` 输入 9 元 / 输出 27 元），想按实际价格算可以覆盖：

```powershell
python batch.py examples/prompts.txt --out results/run1.jsonl --price-in 1 --price-out 4
```

跑完终端会打印汇总：成功/失败条数、总 token、预估费用、平均耗时、结果文件路径。
单条失败不会中断整批，适合晚上挂着跑（**把结果文件放在 `results/` 里，已被 git 忽略**）。

## 4. 多模型对比（选型用）

同一句提示词分别发给多个模型，一张表看清速度、token 和费用：

```powershell
# 离线演示
python compare.py "用一句话解释什么是递归" --mock

# 真实对比 DeepSeek 的两个模型
python compare.py "用一句话解释什么是递归" --models deepseek-flash,deepseek-v4-pro

# 从提示词文件里取前 3 条，一条条对比
python compare.py --input examples/prompts.txt --limit 3 --models deepseek-flash,deepseek-v4-pro
```

表格后面会打印完整回复；每次对比结果还会存一份 `results/compare-<时间>.json`，方便回看。

## 5. 环境体检（出问题先跑这个）

```powershell
python doctor.py            # 不花钱：查 Python / .env / Key 格式 / 接口是否可达
python doctor.py --live     # 真发一条最短请求，确认 Key 能扣费、能出结果
```

体检会明确告诉你卡在哪一步：Key 还是占位符 / 接口 404（地址缺 `/v1`）/ 网络不通 /
余额不足（402）等，并给出下一步该做什么。

## 6. 换平台只改配置

| 平台 | OPENAI_BASE_URL | OPENAI_MODEL |
| --- | --- | --- |
| DeepSeek（默认） | `https://api.deepseek.com` | `deepseek-flash` / `deepseek-v4-pro` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` / `gpt-4o` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 月之暗面 Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `llama3.1`（Key 随便填） |

`batch.py` / `compare.py` / `doctor.py` 都走同一套配置，换平台只改 `.env`。

注意：DeepSeek 官方旧模型名 `deepseek-chat` / `deepseek-reasoner` **已不是当前模型名**，
现在请用 `deepseek-flash`（DeepSeek-V4.1-Flash，默认思考模式开启，上下文 1M）或 `deepseek-v4-pro`。

## 7. 在你自己的项目里复用

```python
from api_client import ChatClient, build_messages

client = ChatClient.from_env()                      # 读 OPENAI_API_KEY 等
print(client.chat(build_messages(None, "你好")))     # 一次性
for piece in client.chat_stream(build_messages(None, "你好")):
    print(piece, end="", flush=True)                # 流式

result = client.chat_with_usage(build_messages(None, "你好"))  # 带 token 用量，批量记账用
print(result.content, result.prompt_tokens, result.completion_tokens)
```

## 8. 这份代码里的"接入要点"

| 要点 | 位置 | 说明 |
| --- | --- | --- |
| 接口地址 | `api_client._endpoint` | `base_url + /chat/completions`；缺 `/v1` 是 404 最常见原因 |
| 鉴权 | `_headers` | `Authorization: Bearer <key>` |
| 超时 | `ChatClient.timeout` | 默认 60s，避免请求无限挂起 |
| 重试 | `_post_with_retries` | 429/5xx/网络错误 指数退避重试；401 不重试 |
| 错误可读 | `_friendly_message` | 状态码翻译成中文提示，并带出服务端 message |
| 流式 | `chat_stream` | 解析 SSE 的 `data:` 行，遇 `[DONE]` 结束 |
| 用量 | `chat_with_usage` | 返回 `ChatResult`：文本 + usage + 耗时，批量记账用 |
| 密钥安全 | `.env` + `.gitignore` | 前端/客户端绝不能内置 Key，必须走后端代理 |

## 9. 自测（不花钱、不联网）

```powershell
python -m unittest discover -s tests -t . -v
```

测试用本地 mock 服务，覆盖真实 HTTP 调用、鉴权、流式、重试、批量写文件、断点续跑、
多模型对比、体检逻辑；`results/` 下的跑数结果不会进仓库。

## 10. GitHub 自动化（推上去就生效）

`.github/` 里已经配好这些「插件」，推送到 GitHub 后自动运行：

| 文件 | 作用 |
| --- | --- |
| `workflows/ci.yml` | 每次 push / PR 自动跑单元测试（Python 3.12） |
| `workflows/codeql.yml` | CodeQL 代码安全扫描，push / PR / 每周一自动执行 |
| `workflows/dependency-review.yml` | PR 新增依赖时检查已知漏洞，高危直接拦下 |
| `workflows/stale.yml` | 90 天没动静的 issue / PR 打标记，再过 30 天自动关闭 |
| `dependabot.yml` | 每周一自动提 PR 升级 GitHub Actions 版本 |
| `ISSUE_TEMPLATE/` | Bug / 功能建议模板，填表就能让人看懂问题 |
| `pull_request_template.md` | PR 自检清单（测试、密钥泄露、文档更新） |

安全扫描的结果在仓库 **Security → Code scanning** 里看；Dependabot 的升级 PR 在 **Pull requests** 里看。
（CodeQL 首次启用需要仓库是公开仓库，或私有仓库已开通 GitHub Advanced Security。）

## 11. 常见报错对照

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `HTTP 401` | Key 没设/写错/带空格 | 检查 `.env`，Key 前后不要有引号和空格 |
| `HTTP 402` | 余额不足 | 平台充值 |
| `HTTP 404` | base_url 少 `/v1`，或模型名写错 | 对照第 6 节表格 |
| `HTTP 429` | 限流 | 代码已自动退避重试，降低并发即可 |
| `网络错误: [WinError 10054]` | 代理/防火墙/断网 | 检查代理设置，`$env:HTTPS_PROXY` |
| `ModuleNotFoundError` | 不在项目目录运行 | `cd` 到本目录再执行 |

## 12. Key 怎么选

推荐顺序（2026-10 官方价目，单位：元 / 百万 tokens）：

| 方案 | 模型 | 输入（缓存未命中） | 输出 | 适合 |
| --- | --- | --- | --- | --- |
| **DeepSeek 官方（首选）** | `deepseek-flash` | 1 元（空闲）/ 2 元（高峰） | 4 元 / 8 元 | 国内直连、OpenAI 兼容、1M 上下文，10 元能用很久 |
| DeepSeek 官方 | `deepseek-v4-pro` | 4.5 元 / 9 元 | 13.5 元 / 27 元 | 复杂推理、对质量要求高 |
| 本地 Ollama | `llama3.1` 等 | 0 | 0 | 完全免费、断网可用，但要显存/内存 |
| 其他云厂商 | 各家免费模型 | 0 或赠送额度 | 0 或赠送额度 | 先试水，额度与可用模型以控制台为准 |

- 高峰时段：北京时间工作日 9:00-12:00、14:00-18:00；其余时段（含周末）自动半价。
- 价格以官方页面为准：<https://api-docs.deepseek.com/zh-cn/quick_start/pricing>
- 申请 Key：<https://platform.deepseek.com/api_keys>（注册 → 充值 → 创建 API Key，只显示一次，记得复制保存）
- 安全习惯：每个项目单独建 Key、设消费上限、只写进 `.env`（已 gitignore），绝不提交仓库或贴到聊天里。
