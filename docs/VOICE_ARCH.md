# 语音交互技术架构 — StudyBuddyAI

> 这是一份**架构决策记录**（ADR）。记录技术选型的原因、协议规范、以及未来升级路径。
> AI Agent 在修改任何 `ai/` 模块前必须先读本文档。

---

## 一、选型决策：三段式方案

### 两种方案对比

**三段式**：`ASR → LLM → TTS`（通过火山引擎 RTC 对话式 AI）
- 孩子说话 → 语音识别出文字 → 大模型生成回复 → 合成语音播放
- 延迟：~1 秒（全链路流式处理优化后）
- 每步可独立控制，LLM 层支持 Function Calling、Prompt 切换、视觉理解

**端到端**：`音频输入 → 端到端大模型 → 音频输出`
- 直接输入 PCM 音频，直接输出 PCM 音频
- 延迟：~230ms（豆包端到端）/ ~500ms（OpenAI/Gemini）
- 语调情绪理解好，但控制力弱——模型自主决定说什么

### 决策：采用三段式（火山引擎 RTC 对话式 AI）

原因：
1. **控制力优先**：项目核心需求是"自主判断孩子状态、精准控制反应"，需要 Orchestrator 对 AI 行为有强控制——动态切换 Prompt、触发 Function Calling、注入阶段指令。三段式的 LLM 层天然支持这些能力
2. **Function Calling 确定可用**：火山引擎 RTC 对话式 AI 明确支持 Function Calling（通过 `UpdateVoiceChat` API 的 `FunctionCallResult`），这是状态上报和阶段切换的基础
3. **视觉理解原生支持**：RTC 方案内置抽帧截图 + 多模态 LLM，开启 `VisionConfig` 即可让 AI 同时看到孩子画面
4. **国内直连**：火山引擎全链路国内部署，无需代理
5. **延迟可接受**：~1 秒响应延迟在"写作业陪伴"场景中可接受（不是即时对话场景）

### 为什么不选端到端

- **豆包端到端语音 API**（`/docs/6561/1594356`）：中文语音质量极好、延迟极低（~230ms），但官方文档未提及 Function Calling / tools / instructions 注入支持，无法满足"精准控制"的核心需求
- **OpenAI gpt-realtime / Gemini Live**：支持 Function Calling，但国内不可直连
- 端到端方案作为**未来升级路径保留**（见第六节）

---

## 二、技术方案：火山引擎 RTC 对话式 AI

### 产品定位

火山引擎 RTC 对话式 AI 是一套完整的语音交互解决方案，核心是 `ASR + LLM + TTS` 三段式流水线，通过 RTC（WebRTC）实现音视频传输。

- 文档入口：https://www.volcengine.com/docs/82379/1393085
- API 参考：`StartVoiceChat` / `UpdateVoiceChat` / `StopVoiceChat`

### 架构数据流

```
孩子端（手机 PWA）
  ├─ 麦克风 PCM ──→ RTC SDK ──→ 火山引擎云端
  ├─ 摄像头视频 ──→ RTC SDK ──→ 火山引擎云端（抽帧截图）
  └─ 扬声器 ←────── RTC SDK ←── 火山引擎云端

火山引擎云端
  ├─ ASR（豆包语音识别）：音频 → 文字
  ├─ LLM（豆包 Seed 2.0 Lite）：文字 + 图片 → 回复 + Function Call
  └─ TTS（豆包语音合成）：回复文字 → 语音

Orchestrator（我们的服务端）
  ├─ 通过 OpenAPI 管理会话（StartVoiceChat / UpdateVoiceChat / StopVoiceChat）
  ├─ 通过 LLMConfig.SystemMessages 注入 Prompt
  ├─ 通过 FunctionCallResult 处理 AI 的工具调用
  └─ 通过 ExternalTextToSpeech 主动插入播报
```

### 核心 API

| API | 作用 |
|-----|------|
| `StartVoiceChat` | 在 RTC 房间中创建 AI 智能体，配置 ASR/LLM/TTS |
| `UpdateVoiceChat` | 更新会话：打断 AI、返回 Function Call 结果、自定义播报 |
| `StopVoiceChat` | 结束会话，释放资源 |

请求地址：`POST https://rtc.volcengineapi.com?Action=StartVoiceChat&Version=2024-12-01`

### 关键配置

```json
{
  "AppId": "<RTC_APP_ID>",
  "RoomId": "<ROOM_ID>",
  "TaskId": "<TASK_ID>",
  "AgentConfig": {
    "TargetUserId": ["child_user_id"],
    "UserId": "ai_buddy",
    "WelcomeMessage": "你好呀！准备好开始写作业了吗？",
    "EnableConversationStateCallback": true,
    "AnsMode": 3
  },
  "Config": {
    "ASRConfig": {
      "Provider": "volcano",
      "ProviderParams": {
        "Mode": "bigmodel"
      },
      "VADConfig": { "SilenceTime": 600 }
    },
    "LLMConfig": {
      "Mode": "ArkV3",
      "EndPointId": "<SEED_2_LITE_ENDPOINT>",
      "SystemMessages": ["<system_prompt>"],
      "Temperature": 0.7,
      "MaxTokens": 512,
      "HistoryLength": 15,
      "VisionConfig": {
        "Enable": true,
        "SnapshotConfig": {}
      }
    },
    "TTSConfig": {
      "Provider": "volcano",
      "ProviderParams": {
        "app": { "appid": "<TTS_APP_ID>", "cluster": "volcano_tts" },
        "audio": {
          "voice_type": "BV700_streaming",
          "speed_ratio": 1.1,
          "volume_ratio": 1.0,
          "emotion": "happy",
          "emotion_strength": 0.6
        }
      }
    },
    "InterruptMode": 0
  }
}
```

### UpdateVoiceChat 命令

| Command | 用途 | 项目场景 |
|---------|------|---------|
| `Interrupt` | 打断 AI 当前输出 | Orchestrator 需要紧急插入指令时 |
| `ExternalTextToSpeech` | 自定义播报 | 主动播报计时提醒、鼓励话语 |
| `FunctionCallResult` | 返回 Function Call 结果 | 处理 `report_status` / `request_phase_change` 后回传 |

播报优先级（InterruptMode）：
- 1 = 高优先级：立即打断并播放（如"时间到了"）
- 2 = 中优先级：当前交互结束后播放
- 3 = 低优先级：如正在交互则丢弃

---

## 三、LLM 模型选型

### 选型结论：Seed 2.0 Lite

| 模型 | 输入价格 | 输出价格 | 上下文 | 视觉 | Function Calling | 选型 |
|------|---------|---------|--------|------|-----------------|------|
| Seed 2.0 Pro | 3.2 元/M | 16 元/M | 256K | 支持 | 支持 | 备选（复杂场景升级） |
| **Seed 2.0 Lite** | **0.6 元/M** | **3.6 元/M** | **256K** | **支持** | **支持** | **主力** |
| Seed 2.0 Mini | 0.2 元/M | 2 元/M | 256K | 支持 | 支持 | 不推荐（多轮对话质量不稳） |

选择理由：
- 价格仅 Pro 的 1/5，视觉理解和 Function Calling 均支持
- Benchmark 差距小：VideoMME 87.7（Pro 89.5）、GPQA 85.1（Pro 88.9）
- 项目不需要顶级数学推理，需要的是稳定的多轮对话 + 视觉判断 + Function Calling
- 参数量小 → 首 token 延迟更低 → 三段式链路中 LLM 延迟是关键瓶颈

### 成本估算（单次 30 分钟会话）

| 项目 | Lite 成本 | 说明 |
|------|----------|------|
| ASR | ~1 元 | 流式语音识别 30 分钟 |
| LLM（文本） | ~0.12 元 | ~120 次交互，每次 500+200 tokens |
| LLM（视觉） | ~0.5 元 | 每 15-30 秒一帧，图片 ~300 tokens |
| TTS | ~0.3 元 | AI 说话约 3000-5000 字符 |
| RTC | ~0.2 元 | 音频通信 |
| **合计** | **~2.1 元/次** | 月均 30 次约 63 元 |

---

## 四、Function Calling 工具定义

两个工具，通过 LLM 的 Function Calling 触发，Orchestrator 通过 `UpdateVoiceChat(Command=FunctionCallResult)` 回传结果：

### `report_status` — AI 主动上报观察到的学习状态

```json
{
  "name": "report_status",
  "description": "向编排器报告当前观察到的学习状态，约每15-30秒上报一次",
  "parameters": {
    "status": "focused | distracted | bad_posture | playing_with_pen | looking_away | task_seems_done | child_asking_question | child_left_seat",
    "confidence": "0-1 置信度",
    "detail": "具体描述（可选）"
  }
}
```

### `request_phase_change` — AI 请求切换阶段

```json
{
  "name": "request_phase_change",
  "description": "当 AI 判断当前任务已完成时，请求 Orchestrator 切换到下一阶段",
  "parameters": {
    "reason": "请求原因，如 task_completed / child_requested_stop"
  }
}
```

### 意图判断与防误判机制

```
孩子的声音（ASR → 文字） + 摄像头画面（抽帧截图）
        ↓
  豆包 Seed 2.0 Lite（多模态 LLM）
        ↓
  Function Call: report_status
  {"status": "distracted", "confidence": 0.85, "detail": "玩铅笔"}
        ↓
  BehaviorAnalyzer（60秒滑动窗口，防误判）
        ↓
  连续多次超阈值 → Orchestrator 触发 ATTENTION_LOST 事件
```

三层防误判保障：
1. **Prompt 定义**：`studying_monitor.md` 精确描述什么叫分心（如"离开桌子超过30秒"才算 child_left_seat）
2. **置信度**：AI 自报置信度，低置信度事件被 BehaviorAnalyzer 降权
3. **滑动窗口**：`BehaviorAnalyzer`（`monitor/behavior_analyzer.py`），60秒窗口内分心占比超阈值才触发

---

## 五、视觉理解

### 实现方式

通过 `LLMConfig.VisionConfig` 开启视觉理解，RTC 云端自动对孩子端摄像头视频流进行抽帧截图，截图和 ASR 文字一起发给多模态 LLM。

```json
"VisionConfig": {
  "Enable": true,
  "SnapshotConfig": { }
}
```

### 项目中的用途

- 判断孩子是否专注写作业（低头看书 vs 东张西望）
- 检测离座（孩子不在摄像头画面中）
- 检测不良坐姿
- 判断作业是否写完（看到孩子合上书本）

### 抽帧频率

不需要高频。写作业场景下每 15-30 秒抽一帧足够判断注意力状态，可大幅降低 LLM 的 token 消耗。

---

## 六、Orchestrator 控制机制

三段式方案的核心优势是 Orchestrator 对 AI 行为的精准控制。控制手段包括：

### 1. Prompt 切换（LLMConfig.SystemMessages）

不同状态阶段注入不同的 System Prompt：
- `TaskBriefing` → `task_briefing.md`：引导孩子了解当前任务
- `Studying` → `studying_monitor.md`：持续监督 + 状态上报
- `Nudge` → `nudge_templates.md`：分心提醒话术
- `BreakTime` → `break_time.md`：休息引导

通过 `UpdateVoiceChat` 或在 `StartVoiceChat` 时配置 `SystemMessages`。

### 2. Function Calling 反馈

AI 通过 Function Call 上报意图 → Orchestrator 决策 → 通过 `FunctionCallResult` 回传指令。

### 3. 主动播报（ExternalTextToSpeech）

Orchestrator 可以绕过 LLM，直接通过 TTS 播报紧急信息：
- 计时器到期："时间到啦，休息一下吧！"
- 紧急提醒：连续分心超限时的强提醒

---

## 七、升级路径

### 换 LLM 模型（Lite → Pro）

修改 `config/settings.py` 中的 `EndPointId`，指向 Pro 模型的端点。其余逻辑不变。

### 切换到端到端模型

火山引擎 RTC 于 2026.03 发布了"接入端到端实时语音大模型"选项。如未来该模式支持 Function Calling：

1. 新建 `ai/rtc_e2e_provider.py`，继承 `AIProvider`
2. 修改 `StartVoiceChat` 配置为端到端模式
3. `AIProvider` 接口和 `Orchestrator` 逻辑不变

### 切换到 CustomLLM 模式

如需更深度的控制（自定义 Agent 逻辑、RAG、MCP 工具链）：

1. 将 `LLMConfig.Mode` 从 `ArkV3` 改为 `CustomLLM`
2. 实现 CustomLLM 回调服务（OpenAI SSE 流式格式）
3. 火山引擎将 ASR 结果回调到我们的服务，我们自行调 LLM 并流式返回

### 本地部署降成本（M5 阶段）

用 FunASR + 自托管 LLM + CosyVoice 替换云服务，将 API 成本降至 ~0.1 元/次。
`AIProvider` 接口不变，上层代码不受影响。
