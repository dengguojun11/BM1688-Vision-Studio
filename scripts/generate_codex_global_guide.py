from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE = Path(r"D:\PycharmProjects\labelU")
GLOBAL_SKILLS_ROOT = Path(r"C:\Users\deng5\.codex\skills")
PLUGIN_SKILLS_ROOT = Path(r"C:\Users\deng5\.codex\plugins\cache\openai-plugins-local")
BUNDLED_SKILLS_ROOT = Path(r"C:\Users\deng5\.codex\plugins\cache\openai-bundled")
OUTPUT_PATH = WORKSPACE / "docs" / "codex_global_mcp_skill_guide.md"


PLUGIN_CN = {
    "airtable": "Airtable",
    "atlassian-rovo": "Atlassian Rovo",
    "base44": "Base44",
    "boltz-api-cli": "Boltz API CLI",
    "box": "Box",
    "browser": "Codex 内置浏览器",
    "build-web-apps": "Web 应用开发",
    "build-web-data-visualization": "Web 数据可视化",
    "canva": "Canva",
    "catalyst-by-zoho": "Zoho Catalyst",
    "chrome": "Chrome 浏览器",
    "chronograph-gp": "Chronograph GP",
    "chronograph-lp": "Chronograph LP",
    "circleci": "CircleCI",
    "cloudflare": "Cloudflare",
    "coderabbit": "CodeRabbit",
    "codex-security": "Codex Security",
    "computer-use": "Computer Use",
    "datasite": "Datasite",
    "deepnote": "Deepnote",
    "digitalocean": "DigitalOcean",
    "dnb-finance-analytics": "DNB 财务分析",
    "expo": "Expo",
    "figma": "Figma",
    "github": "GitHub",
    "google-drive": "Google Drive",
    "hex": "Hex",
    "heygen": "Heygen",
    "hubspot": "HubSpot",
    "hugging-face": "Hugging Face",
    "hyperframes": "Hyperframes",
    "life-science-research": "生命科学研究",
    "linear": "Linear",
    "midpage": "Midpage",
    "mixpanel-headless": "Mixpanel Headless",
    "moody-s": "Moody's",
    "morningstar": "Morningstar",
    "neon-postgres": "Neon Postgres",
    "netlify": "Netlify",
    "ngs-analysis": "NGS 分析",
    "notion": "Notion",
    "nvidia": "NVIDIA",
    "openai-developers": "OpenAI Developers",
    "plugin-eval": "Plugin Eval",
    "render": "Render",
    "replayio": "Replay.io",
    "sharepoint": "SharePoint",
    "shopify": "Shopify",
    "slack": "Slack",
    "stripe": "Stripe",
    "supabase": "Supabase",
    "superhuman": "Superhuman",
    "superpowers": "Superpowers",
    "teams": "Microsoft Teams",
    "temporal": "Temporal",
    "test-android-apps": "Android 应用测试",
    "twilio-developer-kit": "Twilio Developer Kit",
    "vercel": "Vercel",
    "weatherpromise": "WeatherPromise",
    "wix": "Wix",
    "zoom": "Zoom",
}


CATEGORY_RULES = [
    ("security", ["security", "threat", "attack", "finding", "scan", "vulnerability", "cve", "triage"]),
    ("browser", ["browser", "chrome", "playwright", "page", "screenshot", "automation", "webapp", "website"]),
    ("testing", ["test", "testing", "qa", "debug", "troubleshoot", "verification", "verify", "validate"]),
    ("design", ["figma", "design", "ui", "ux", "motion", "layout", "slides", "presentation", "canva"]),
    ("docs", ["docs", "documentation", "knowledge", "paper", "research", "report", "brief", "memo", "summar"]),
    ("data", ["data", "visualization", "dashboard", "analytics", "forecast", "sheet", "dataset", "sql", "postgres"]),
    ("deploy", ["deploy", "deployment", "wrangler", "worker", "cloudflare", "vercel", "netlify", "render", "infrastructure"]),
    ("cli", ["cli", "command", "terminal", "shell", "wrangler", "gh"]),
    ("api", ["api", "sdk", "webhook", "auth", "oauth", "route", "integration", "server"]),
    ("communication", ["slack", "teams", "mail", "message", "meeting", "reply", "notification", "calendar", "email"]),
    ("mobile", ["android", "ios", "expo", "react-native", "mobile", "swiftui", "jetpack"]),
    ("workflow", ["workflow", "plan", "planning", "backlog", "task", "orchestrator", "automation", "project"]),
]


GENERIC_TUTORIAL = [
    "在提示词里明确点名 skill 名称，避免让 Codex 只靠模糊语义猜测。",
    "补充你的目标、输入资料、环境约束、希望输出物；skill 只是方法，不会替你猜业务边界。",
    "如果你要它直接改代码、跑命令或做排查，要写清楚“先分析再执行”还是“直接实施”。",
    "涉及外部平台、账号、部署、浏览器或 API 时，最好同时给出版本、报错文本、目标环境和验收标准。",
    "完成后要求 Codex 回报验证证据，例如命令结果、页面行为、测试结论或生成文件路径。",
]


MCP_ENTRIES = [
    {
        "name": "mcp__node_repl",
        "title": "Node REPL 执行环境",
        "tools": ["js", "js_add_node_module_dir", "js_reset"],
        "summary": "这是一个持久化的 Node.js 执行环境，适合跑浏览器自动化、JavaScript 调试、脚本验证和快速原型实验。",
        "tutorial": [
            "先说明你要执行的是 JavaScript 逻辑、浏览器自动化还是临时数据处理。",
            "如果依赖额外 npm 包，先让 Codex 把对应 `node_modules` 目录加入搜索路径。",
            "把需要复用的状态写成持续变量，这样同一线程后续步骤可以接着用。",
            "如果出现变量重名或上下文污染，再要求重置会话，而不是每次都重建环境。",
            "当任务要返回结构化文本或图片时，要求 Codex 使用精确输出，而不是只打印调试日志。",
        ],
        "uses": [
            "请用 `mcp__node_repl` 跑一段 JavaScript，验证这个 JSON 转换逻辑是否正确。",
            "请用 `mcp__node_repl` 连接浏览器运行一个页面自动化检查，并告诉我失败步骤。",
            "请用 `mcp__node_repl` 临时读取本地 JS 模块并输出关键返回值。",
            "请用 `mcp__node_repl` 生成一张图片或截图，帮我核对渲染结果。",
            "请用 `mcp__node_repl` 做一个最小复现实验，证明这个前端 bug 是否稳定存在。",
        ],
        "notes": [
            "适合 JavaScript/浏览器场景，不是通用 Bash 替代品。",
            "会话是持久的，同一线程里旧变量可能继续存在。",
        ],
    },
    {
        "name": "mcp__context7",
        "title": "最新官方文档检索",
        "tools": ["resolve_library_id", "query_docs"],
        "summary": "这是面向库、框架、SDK、CLI 和云服务的最新文档检索 MCP，优先替代过时的记忆回答。",
        "tutorial": [
            "先告诉 Codex 你要查的产品名，例如 React、Next.js、Cloudflare Workers、Prisma。",
            "再给出具体问题，不要只说“帮我查文档”，最好写出版本、错误场景或目标能力。",
            "Context7 通常先解析库 ID，再查正文；因此你提问时要尽量给标准产品名。",
            "如果问题涉及迁移、配置、CLI 参数或新版本 API，优先要求 Codex 走 Context7。",
            "最后要求 Codex 基于检索结果给出结论、示例代码和与你项目相关的落地建议。",
        ],
        "uses": [
            "请用 `mcp__context7` 查 React 19 的 `useEffectEvent` 最新文档，并给我一个最小示例。",
            "请用 `mcp__context7` 查 Next.js 当前版本里 middleware 的限制和推荐写法。",
            "请用 `mcp__context7` 查 Wrangler v4 的 `wrangler.jsonc` 配置方式。",
            "请用 `mcp__context7` 对比 Prisma 新旧版本迁移时的 schema 变化。",
            "请用 `mcp__context7` 查某个 SDK 的鉴权流程，并转成我的项目接入步骤。",
        ],
        "notes": [
            "适合“最新规范”问题，尤其是版本变化快的技术栈。",
            "如果你能提供准确产品名，检索质量会明显更高。",
        ],
    },
    {
        "name": "mcp__codex_security",
        "title": "Codex Security 安全扫描工作台",
        "tools": [
            "open_codex_security_workspace",
            "await_codex_security_scan_start",
            "get_codex_security_scan_context",
            "update_codex_security_scan_progress",
            "complete_codex_security_scan",
        ],
        "summary": "这是 Codex 内置的安全扫描与修复工作台，适合做标准扫描、差异扫描、深度审查、发现跟踪和修复闭环。",
        "tutorial": [
            "先说明你要扫的是整个仓库、某个目录，还是某个提交/差异范围。",
            "再指定扫描模式：标准扫描、diff 扫描、deep 扫描，不同模式成本和覆盖不同。",
            "如果你已经锁定风险点，可以把目标模块、威胁模型和上下文一并给 Codex。",
            "扫描过程中要求 Codex 同步阶段进度、发现数量、是否需要人工确认。",
            "最后让 Codex 输出 findings、修复建议、验证结论，并在需要时驱动 remediation 状态更新。",
        ],
        "uses": [
            "请用 `mcp__codex_security` 对当前工作区做一次标准安全扫描。",
            "请用 `mcp__codex_security` 只审查这次提交相对 `main` 的安全差异。",
            "请用 `mcp__codex_security` 对登录与权限模块做 deep scan，并重点看越权风险。",
            "请用 `mcp__codex_security` 打开 findings 表格，帮我先做 triage。",
            "请用 `mcp__codex_security` 跟踪一个漏洞从发现、修复到验证通过的全流程。",
        ],
        "notes": [
            "这是面向工作流的 MCP，不只是单次命令。",
            "最适合高风险代码、发布前审查和定向修复任务。",
        ],
    },
    {
        "name": "mcp__fetch",
        "title": "网页抓取与内容提取",
        "tools": ["fetch"],
        "summary": "这是一个轻量网页抓取 MCP，适合抓取公开网页内容、读取 HTML、做网页文本抽取和快速在线验证。",
        "tutorial": [
            "告诉 Codex 要抓取的 URL，以及你关心的是正文、原始 HTML 还是页面片段。",
            "如果网页很长，可以要求分段抓取或从指定偏移继续读取。",
            "如果你只想要可读摘要，就要求输出提炼后的结论；如果你要排查页面结构，就要求原始 HTML。",
            "对时效性强的信息，可以要求抓取后附带链接和日期说明。",
            "抓到网页后，让 Codex 再结合你的问题做总结、对比或提取字段。",
        ],
        "uses": [
            "请用 `mcp__fetch` 抓这个文档页面，并提取安装步骤。",
            "请用 `mcp__fetch` 读取这个网页的原始 HTML，帮我确认 meta 标签是否正确。",
            "请用 `mcp__fetch` 分段抓取这篇长文，并总结其中的 API 变更。",
            "请用 `mcp__fetch` 打开某个状态页，告诉我当前是否有服务故障公告。",
            "请用 `mcp__fetch` 抓一篇教程页面，再整理成中文要点。",
        ],
        "notes": [
            "适合公开网页；需要登录的网站一般要换浏览器类能力。",
            "如果用户要最新信息，这个 MCP 很实用。",
        ],
    },
    {
        "name": "mcp__sequential_thinking",
        "title": "分步推理与计划拆解",
        "tools": ["sequentialthinking"],
        "summary": "这是一个把复杂问题拆成显式思考步骤的 MCP，适合方案设计、排错拆因、结构化决策和多分支分析。",
        "tutorial": [
            "先告诉 Codex 这是需要分步分析的问题，而不是直接要结论。",
            "把输入条件、未知点、你最关心的决策项说明清楚。",
            "如果你希望它在分步过程中修正假设，也可以明确要求允许回溯和改判。",
            "分析完成后要求输出最终结论，不要只停留在过程。",
            "当问题进入实施阶段，再让 Codex 把推理结果转成执行计划或代码修改。",
        ],
        "uses": [
            "请用 `mcp__sequential_thinking` 分析这个系统为什么会间歇性超时。",
            "请用 `mcp__sequential_thinking` 把这个重构任务拆成阶段、风险和验证点。",
            "请用 `mcp__sequential_thinking` 比较两种架构方案，并给出最终推荐。",
            "请用 `mcp__sequential_thinking` 复盘这个 bug 的可能根因并排序。",
            "请用 `mcp__sequential_thinking` 为一个模糊需求先做结构化澄清。",
        ],
        "notes": [
            "适合复杂问题，不适合本来就很直接的单步命令。",
            "它的价值在于显式拆解和修正，而不是替代最终执行。",
        ],
    },
    {
        "name": "mcp__openai_api_key_local_confirmation",
        "title": "OpenAI API Key 本地写入确认",
        "tools": ["confirm_ope_8781ece2af3d"],
        "summary": "这是一个安全确认 MCP，用于在本地工作区写入 OpenAI API Key 之前，让开发者确认目标环境变量名和 env 文件路径。",
        "tutorial": [
            "当你要在本地项目里创建或写入 OpenAI API Key 时，再使用这个 MCP。",
            "先确定变量名，默认通常是 `OPENAI_API_KEY`。",
            "再确定目标文件，比如 `.env`、`.env.local` 或某个指定环境文件。",
            "要求 Codex 在写入前先弹出确认，而不是直接落盘。",
            "确认后再继续执行创建或更新密钥的后续动作。",
        ],
        "uses": [
            "请用 `mcp__openai_api_key_local_confirmation` 帮我确认要把 key 写到哪个 `.env` 文件。",
            "请用这个 MCP 在写入前让我再核对一次变量名是否为 `OPENAI_API_KEY`。",
            "请用这个 MCP 把默认目标改成 `.env.local` 后再继续。",
            "请用这个 MCP 限制写入路径只能在当前工作区内。",
            "请用这个 MCP 做一次本地密钥落盘前的人工确认流程演示。",
        ],
        "notes": [
            "它不是用来生成 key 的，而是写入前确认目的地。",
            "适合本地安全写入，不适合替代一般配置管理。",
        ],
    },
    {
        "name": "mcp__memory",
        "title": "知识图谱式长期记忆",
        "tools": ["open_nodes"],
        "summary": "这是一个面向长期上下文的 MCP，可按实体节点读取历史知识，适合追溯过往决策、项目背景和固定偏好。",
        "tutorial": [
            "当问题与历史上下文、固定偏好、既有项目约束相关时，再要求 Codex 使用它。",
            "尽量给出明确节点名、项目名、仓库路径或主题词，避免泛泛搜索。",
            "读取后要求 Codex 明确区分：哪些是当前验证过的，哪些只是历史记忆。",
            "如果记忆可能过时，再要求 Codex 现场验证关键事实。",
            "最后把记忆信息用于提高一致性，而不是无条件替代现状。",
        ],
        "uses": [
            "请用 `mcp__memory` 回看这个项目之前关于部署流程的结论。",
            "请用 `mcp__memory` 查我在某个仓库里的长期偏好和工作流约束。",
            "请用 `mcp__memory` 找一下某个功能以前为什么这么设计。",
            "请用 `mcp__memory` 帮我定位和某个主题相关的历史节点。",
            "请用 `mcp__memory` 先补齐上下文，再继续当前任务。",
        ],
        "notes": [
            "记忆是辅助，不代表当前现场事实一定没变。",
            "适合跨回合、跨线程、跨项目的延续性任务。",
        ],
    },
]


@dataclass
class SkillEntry:
    identifier: str
    title: str
    plugin: str
    plugin_title: str
    source_group: str
    relative_key: str
    path: Path
    raw_name: str
    description: str
    heading: str
    category: str


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text

    body = "\n".join(lines[end_idx + 1 :])
    front_lines = lines[1:end_idx]
    data: dict[str, str] = {}
    i = 0
    while i < len(front_lines):
        line = front_lines[i]
        if not line.strip():
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        if rest == "|":
            block: list[str] = []
            i += 1
            while i < len(front_lines):
                nxt = front_lines[i]
                if nxt.startswith("  ") or nxt.startswith("\t"):
                    block.append(nxt.lstrip())
                    i += 1
                    continue
                break
            data[key] = "\n".join(block).strip()
            continue
        data[key] = rest.strip().strip('"').strip("'")
        i += 1
    return data, body


def clean_text(value: str) -> str:
    value = value.replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def heading_from_body(body: str) -> str:
    match = re.search(r"^#\s+(.+)$", body, flags=re.M)
    return clean_text(match.group(1)) if match else ""


def iter_skill_files() -> Iterable[Path]:
    yield from GLOBAL_SKILLS_ROOT.rglob("SKILL.md")
    yield from PLUGIN_SKILLS_ROOT.rglob("SKILL.md")
    yield from BUNDLED_SKILLS_ROOT.rglob("SKILL.md")


def plugin_title(plugin: str) -> str:
    if plugin == ".system":
        return "系统内置"
    if plugin == "global":
        return "本机全局"
    return PLUGIN_CN.get(plugin, plugin.replace("-", " "))


def infer_category(*values: str) -> str:
    blob = " ".join(v.lower() for v in values if v)
    if "wrangler" in blob or "deploy" in blob or "deployment" in blob:
        return "deploy"
    if "browser" in blob or "chrome" in blob or "playwright" in blob:
        return "browser"
    for category, needles in CATEGORY_RULES:
        if any(needle in blob for needle in needles):
            return category
    return "generic"


def local_skill_identifier(path: Path) -> tuple[str, str, str, str]:
    rel = path.relative_to(GLOBAL_SKILLS_ROOT)
    parts = list(rel.parts)
    if parts[0] == ".system":
        skill_name = "/".join(parts[1:-1])
        return f"system:{skill_name}", ".system", "系统内置 Skills", skill_name
    skill_name = "/".join(parts[:-1])
    return f"global:{skill_name}", "global", "本机全局 Skills", skill_name


def plugin_skill_identifier(path: Path) -> tuple[str, str, str, str]:
    rel = path.relative_to(PLUGIN_SKILLS_ROOT)
    plugin = rel.parts[0]
    skill_name = "/".join(rel.parts[3:-1])
    return f"{plugin}:{skill_name}", plugin, f"插件 Skills / {plugin_title(plugin)}", skill_name


def bundled_skill_identifier(path: Path) -> tuple[str, str, str, str]:
    rel = path.relative_to(BUNDLED_SKILLS_ROOT)
    plugin = rel.parts[0]
    skill_name = "/".join(rel.parts[2:-1])
    return f"{plugin}:{skill_name}", plugin, f"Bundled Skills / {plugin_title(plugin)}", skill_name


def build_skill_entry(path: Path) -> SkillEntry:
    text = path.read_text(encoding="utf-8")
    front, body = parse_frontmatter(text)
    desc = clean_text(front.get("description", ""))
    heading = heading_from_body(body)
    raw_name = clean_text(front.get("name", "")) or heading or path.parent.name

    if str(path).startswith(str(GLOBAL_SKILLS_ROOT)):
        identifier, plugin, source_group, relative_key = local_skill_identifier(path)
    elif str(path).startswith(str(PLUGIN_SKILLS_ROOT)):
        identifier, plugin, source_group, relative_key = plugin_skill_identifier(path)
    else:
        identifier, plugin, source_group, relative_key = bundled_skill_identifier(path)

    category = infer_category(identifier, raw_name, desc, heading)
    title = raw_name or relative_key.replace("/", " / ").replace("-", " ")
    return SkillEntry(
        identifier=identifier,
        title=title,
        plugin=plugin,
        plugin_title=plugin_title(plugin),
        source_group=source_group,
        relative_key=relative_key,
        path=path,
        raw_name=raw_name,
        description=desc,
        heading=heading,
        category=category,
    )


def focus_label(category: str) -> str:
    return {
        "security": "安全扫描、威胁建模与修复闭环",
        "browser": "浏览器自动化、页面检查与交互验证",
        "testing": "测试、调试、验证与回归排查",
        "design": "界面设计、视觉表达、动效与组件规划",
        "docs": "文档检索、研究整理、知识总结与报告输出",
        "data": "数据处理、分析、可视化与报表交付",
        "deploy": "部署、云平台配置、运维与发布流程",
        "cli": "CLI 命令编排、终端操作与配置校验",
        "api": "API / SDK 接入、鉴权、路由与集成实现",
        "communication": "邮件、消息、会议、通知与协作沟通",
        "mobile": "移动端、Expo、iOS / Android 与跨端开发",
        "workflow": "任务分解、工作流编排、计划与项目推进",
        "generic": "通用领域增强与结构化执行",
    }.get(category, "通用领域增强与结构化执行")


def scene_label(category: str) -> str:
    return {
        "security": "发现风险、评估影响、给出修复与复验",
        "browser": "打开页面、执行交互、截图比对和定位前端问题",
        "testing": "复现问题、跑验证步骤、收集证据并输出结论",
        "design": "做设计方案、界面重构、动效建议和组件拆解",
        "docs": "查资料、做摘要、产出教程、整理研究结论",
        "data": "做分析、看报表、生成图表和解释指标变化",
        "deploy": "初始化项目、部署服务、检查配置、追踪发布问题",
        "cli": "编写命令、解释参数、修复报错、校验环境",
        "api": "接入第三方、写调用样例、排查鉴权和整理接口方案",
        "communication": "整理会议、起草消息、跟进通知与协同工作",
        "mobile": "做移动端开发、运行测试、打包发布和排障",
        "workflow": "梳理流程、拆任务、编排执行顺序并控制风险",
        "generic": "把某一类工作方法显式加载到当前任务里",
    }.get(category, "把某一类工作方法显式加载到当前任务里")


def use_templates(skill: SkillEntry) -> list[str]:
    ident = skill.identifier
    title = skill.title
    label = skill.plugin_title
    if skill.category == "security":
        return [
            f"请使用 `{ident}` 对当前变更做一次安全审查，并只列高风险问题。",
            f"请使用 `{ident}` 为 `{label}` 相关模块做威胁建模，输出攻击面和缓解措施。",
            f"请使用 `{ident}` 复核我这段修复是否真正封堵了漏洞。",
            f"请使用 `{ident}` 给我一份从发现到修复再到验证的安全处理清单。",
            f"请使用 `{ident}` 对现有 findings 做优先级排序，并说明为什么先修哪一个。",
        ]
    if skill.category == "browser":
        return [
            f"请使用 `{ident}` 打开目标页面并复现我描述的交互问题。",
            f"请使用 `{ident}` 在页面上逐步点击、输入并截图记录每一步结果。",
            f"请使用 `{ident}` 帮我验证这个本地前端页面是否真的渲染正确。",
            f"请使用 `{ident}` 对比两个页面版本的可见差异。",
            f"请使用 `{ident}` 写一套浏览器回归检查步骤并实际执行。",
        ]
    if skill.category == "testing":
        return [
            f"请使用 `{ident}` 先复现 `{title}` 相关问题，再给出最小修复建议。",
            f"请使用 `{ident}` 为当前改动设计一组验证步骤和通过标准。",
            f"请使用 `{ident}` 对这个报错做系统化排查，告诉我最可能的根因。",
            f"请使用 `{ident}` 帮我写一个回归测试清单，并优先覆盖高风险路径。",
            f"请使用 `{ident}` 在修复后再做一次复验，并明确写出结论。",
        ]
    if skill.category == "design":
        return [
            f"请使用 `{ident}` 为这个页面做一次重设计，重点看信息层级和视觉节奏。",
            f"请使用 `{ident}` 产出 `{label}` 场景下的组件拆分和版式建议。",
            f"请使用 `{ident}` 帮我把一个模糊想法转成可执行的界面方案。",
            f"请使用 `{ident}` 增加动效、响应式和视觉语言建议，但不要脱离现有设计系统。",
            f"请使用 `{ident}` 对当前 UI 做批判性评审，只说真正影响体验的问题。",
        ]
    if skill.category == "docs":
        return [
            f"请使用 `{ident}` 先查清 `{title}` 的核心概念，再给我中文教程。",
            f"请使用 `{ident}` 把这个文档主题压缩成 10 条高价值结论。",
            f"请使用 `{ident}` 对比两套资料或两种方案，并说明取舍。",
            f"请使用 `{ident}` 生成一份从零开始的学习/实施路径。",
            f"请使用 `{ident}` 把外文资料整理成面向团队内部的中文说明。",
        ]
    if skill.category == "data":
        return [
            f"请使用 `{ident}` 帮我整理数据源、指标口径和分析路径。",
            f"请使用 `{ident}` 生成一个适合当前主题的图表/看板方案。",
            f"请使用 `{ident}` 对已有数据结果做解释，并指出还缺哪些数据。",
            f"请使用 `{ident}` 产出一份报告结构，重点突出异常与趋势。",
            f"请使用 `{ident}` 把分析任务拆成采集、清洗、展示和验证四段。",
        ]
    if skill.category == "deploy":
        return [
            f"请使用 `{ident}` 给我一份 `{label}` 相关部署清单和执行顺序。",
            f"请使用 `{ident}` 检查当前配置是否满足发布要求，并指出风险项。",
            f"请使用 `{ident}` 生成最小可运行部署命令，并解释关键参数。",
            f"请使用 `{ident}` 帮我排查发布失败、权限问题或环境不一致。",
            f"请使用 `{ident}` 把开发、预发、生产三套环境的差异整理清楚。",
        ]
    if skill.category == "cli":
        return [
            f"请使用 `{ident}` 为这个任务给出准确 CLI 命令，而不是伪代码。",
            f"请使用 `{ident}` 解释这条命令每个参数的作用和风险。",
            f"请使用 `{ident}` 帮我把一串手工操作整理成可重复执行的命令流。",
            f"请使用 `{ident}` 排查当前 CLI 报错，并给出最短修复路径。",
            f"请使用 `{ident}` 根据当前版本约束检查命令是否已经过时。",
        ]
    if skill.category == "api":
        return [
            f"请使用 `{ident}` 为 `{label}` 设计一条从鉴权到调用到错误处理的接入流程。",
            f"请使用 `{ident}` 生成一个最小 API / SDK 调用示例，要求可直接运行。",
            f"请使用 `{ident}` 检查我现在的集成方式是否遗漏了鉴权或回调环节。",
            f"请使用 `{ident}` 帮我把接口说明转成项目里的实现步骤。",
            f"请使用 `{ident}` 针对报错响应做排查，并告诉我该从哪一层开始看。",
        ]
    if skill.category == "communication":
        return [
            f"请使用 `{ident}` 帮我整理会议纪要，并抽出待办、风险和责任人。",
            f"请使用 `{ident}` 起草一封/一条适合当前场景的对外沟通消息。",
            f"请使用 `{ident}` 生成一个日常跟进或状态同步模板。",
            f"请使用 `{ident}` 对通知流或邮件流做一次流程优化建议。",
            f"请使用 `{ident}` 将分散信息汇总成可执行的协作清单。",
        ]
    if skill.category == "mobile":
        return [
            f"请使用 `{ident}` 设计 `{title}` 相关的移动端实现路径。",
            f"请使用 `{ident}` 排查 iOS / Android / Expo 环境下的构建或运行问题。",
            f"请使用 `{ident}` 生成一个最小跨端示例，并说明平台差异。",
            f"请使用 `{ident}` 审查现有移动端代码是否符合最佳实践。",
            f"请使用 `{ident}` 规划从开发、调试到发布的完整步骤。",
        ]
    if skill.category == "workflow":
        return [
            f"请使用 `{ident}` 把当前任务拆成阶段、里程碑、风险和验收标准。",
            f"请使用 `{ident}` 为这个需求生成 backlog 或执行计划。",
            f"请使用 `{ident}` 设计一个多人协作或多步骤的推进流程。",
            f"请使用 `{ident}` 帮我复盘卡点，并调整后续执行顺序。",
            f"请使用 `{ident}` 输出一个从分析到实施再到验证的闭环方案。",
        ]
    return [
        f"请使用 `{ident}` 先说明它最适合解决什么问题，再给我最短落地方案。",
        f"请使用 `{ident}` 帮我把这个任务转成分步骤执行清单。",
        f"请使用 `{ident}` 对现有做法做一次方法论审查，指出明显短板。",
        f"请使用 `{ident}` 生成一个从零开始的教程，并结合当前项目场景。",
        f"请使用 `{ident}` 给我 3 种做法并说明推荐顺序。",
    ]


def skill_summary(skill: SkillEntry) -> str:
    base = (
        f"这是一个面向 {skill.plugin_title} 的 skill，重点增强 {focus_label(skill.category)}。"
        f" 适合在 Codex 中显式点名后，让模型围绕“{scene_label(skill.category)}”这类任务组织分析、检索、实现和验证。"
    )
    if skill.description:
        base += f" 原始说明可概括为：{skill.description}"
    return base


def prompt_template(skill: SkillEntry) -> str:
    return (
        f"请使用 `{skill.identifier}`，目标是：<写你的目标>；"
        "已知输入：<文件/链接/报错/环境>；"
        "限制：<版本/时间/不允许做什么>；"
        "输出：<教程/代码/命令/报告/验证结论>。"
    )


def render_skill_entry(skill: SkillEntry) -> str:
    lines = [
        f"### `{skill.identifier}`",
        "",
        f"- 所属来源：{skill.source_group}",
        f"- 技能标题：{skill.title}",
        f"- 技能路径：`{skill.path}`",
        f"- 适用方向：{focus_label(skill.category)}",
        "",
        "**中文说明**",
        "",
        skill_summary(skill),
        "",
        "**使用教程**",
        "",
    ]
    for idx, item in enumerate(GENERIC_TUTORIAL, 1):
        lines.append(f"{idx}. {item}")
    lines.extend(
        [
            "",
            f"最短提示词模板：`{prompt_template(skill)}`",
            "",
            "**至少 5 种使用方式**",
            "",
        ]
    )
    for idx, item in enumerate(use_templates(skill), 1):
        lines.append(f"{idx}. {item}")
    lines.append("")
    return "\n".join(lines)


def render_mcp_entry(item: dict) -> str:
    lines = [
        f"### `{item['name']}`",
        "",
        f"- 中文定位：{item['title']}",
        f"- 主要工具：`{'`, `'.join(item['tools'])}`",
        "",
        "**中文说明**",
        "",
        item["summary"],
        "",
        "**使用教程**",
        "",
    ]
    for idx, step in enumerate(item["tutorial"], 1):
        lines.append(f"{idx}. {step}")
    lines.extend(["", "**至少 5 种使用方式**", ""])
    for idx, use in enumerate(item["uses"], 1):
        lines.append(f"{idx}. {use}")
    lines.extend(["", "**注意事项**", ""])
    for note in item["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def render_document(skills: list[SkillEntry]) -> str:
    source_counts = Counter(skill.source_group for skill in skills)
    plugin_counts = Counter(skill.plugin for skill in skills)
    grouped: dict[str, list[SkillEntry]] = defaultdict(list)
    for skill in skills:
        grouped[skill.source_group].append(skill)

    lines = [
        "# Codex 全局 MCP 与 Skill 使用说明",
        "",
        f"生成时间：{Path().cwd()} / 自动扫描 `{GLOBAL_SKILLS_ROOT}`、`{PLUGIN_SKILLS_ROOT}`、`{BUNDLED_SKILLS_ROOT}`。",
        "",
        "## 文档范围",
        "",
        "- 本文覆盖当前机器上可见的全局 Skill 文件，以及本线程可见的全局 MCP 命名空间。",
        "- Skill 统计口径：扫描所有 `SKILL.md`。",
        "- MCP 统计口径：以当前 Codex 会话可见的全局 MCP 服务器为准。",
        "- 使用方式中的示例提示词可以直接复制后再替换目标、路径、链接、报错和限制条件。",
        "",
        "## 快速结论",
        "",
        f"- Skill 总数：**{len(skills)}**",
        f"- Skill 来源分布：{'; '.join(f'{k} {v}' for k, v in sorted(source_counts.items()))}",
        f"- MCP 服务器数：**{len(MCP_ENTRIES)}**",
        "",
        "## 如何正确调用 Skill",
        "",
        "1. 最稳妥的方式是直接在提示词里点名 skill 标识，例如 `请使用 cloudflare:wrangler ...`。",
        "2. 把目标、上下文、约束、输出物写全；skill 负责增强方法，不负责替你猜需求。",
        "3. 涉及执行类任务时，最好写清楚是“先分析后执行”还是“直接实施并验证”。",
        "4. 如果一个 skill 明显覆盖某领域，Codex 会优先遵循它的工作方式、检查点和资料来源。",
        "5. 复杂场景可以组合多个 skill，但要写清主次顺序，例如“先用 context7 风格查文档，再用 wrangler 落配置”。",
        "",
        "## 如何正确调用 MCP",
        "",
        "1. MCP 更像 Codex 背后的能力接口，用户通常不需要记参数，但可以在提示词里点名要用哪个 MCP。",
        "2. 最常见写法是：`请用 <MCP 名称> 做 <动作>，并输出 <结果形式>`。",
        "3. 适合点名 MCP 的场景：要最新文档、要联网抓取、要显式多步分析、要浏览器自动化、要安全扫描、要读取长期记忆。",
        "4. 如果任务涉及最新信息或官方文档，优先点名 `mcp__context7` 或 `mcp__fetch`。",
        "5. 如果任务涉及复杂推理、扫描流程或持久执行环境，优先点名对应 MCP，而不是只说“帮我想一下”。",
        "",
        "## MCP 章节",
        "",
    ]

    for item in MCP_ENTRIES:
        lines.append(render_mcp_entry(item))

    lines.extend(
        [
            "## Skill 来源索引",
            "",
            "### 按插件/来源统计",
            "",
        ]
    )

    for plugin, count in sorted(plugin_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- `{plugin}`：{plugin_title(plugin)}，共 {count} 个 skill")

    for group_name in sorted(grouped.keys()):
        lines.extend([f"", f"## {group_name}", ""])
        for skill in sorted(grouped[group_name], key=lambda x: x.identifier):
            lines.append(render_skill_entry(skill))

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    skills = [build_skill_entry(path) for path in iter_skill_files()]
    skills.sort(key=lambda x: (x.source_group, x.identifier))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_document(skills), encoding="utf-8")
    print(f"generated: {OUTPUT_PATH}")
    print(f"skills: {len(skills)}")
    print(f"mcps: {len(MCP_ENTRIES)}")


if __name__ == "__main__":
    main()
