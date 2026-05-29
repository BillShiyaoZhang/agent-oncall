# agent-oncall 📞

`agent-oncall` 是一个专为 AI 智能体框架（如 Hermes 或 OpenClaw）设计的轻量级、安全的**协同执行与能力发现层**中间件。它运行在 [agent-comm](https://github.com/BillShiyaoZhang/agent-comm) 或其他 P2P 传输适配器之上，为自治智能体之间的交互提供零信任、经过身份验证的安全协同保障。

## 🏗️ 核心架构与功能特性

- **信封安全传输**: 所有传输的数据包都被封装在带有签名的 Protobuf 信封 (`OnCallEnvelope`) 中。签名校验采用 Ed25519 异步加密技术（基于 Python `cryptography` 库）进行，并提供重放攻击防御支持。
- **动态视图过滤与被动暴露**: 在响应意图发现（Handshake）时，智能体暴露的可用意图列表会**根据调用者的 URN 及其关联的信任等级**（`Tier_1_Family` 亲人、`Tier_2_Friend` 好友、`Tier_3_Stranger` 陌生人）进行动态过滤。
- **策略引擎与能力令牌 (HCT)**: 对每个注册的本地意图强制执行信任等级限制。支持通过 Ed25519 签名的能力令牌（HCT - Capability Tokens）将权限安全委托给第三方，支持资源、操作、过滤约束校验。
- **人机交互确认 (HITL)**: 提供可插拔的拦截器框架，在执行高风险意图（例如敏感数据写入、大额预算更改）之前，暂停执行并向宿主用户请求授权确认。
- **通信通道适配器**:
  - `MockCommAdapter`: 提供本地内存级的数据帧路由，用于多智能体的离线模拟和单元测试。
  - `SubprocessCommAdapter`: 与底层的 Go 语言 `agent-comm` 客户端无缝桥接，拉起后台守护进程监听并解析标准输出流中的 P2P/MQ 加密信封。
- **管道模式集成 (模式 A)**: 提供标准输入输出处理模块（`StdinStdoutHandler`），外部传输进程（如 `agent-comm listen`）可以通过子进程管道将 JSON 数据包直接推送到 Python 进程处理，并捕获回复。
- **服务描述对齐 (SDA)**: 实现了学术论文中描述的基于对话的状态机协议，能够通过可插拔的 LLM 回调，自主对齐其他对端智能体暴露的模糊服务描述（Ambiguous Descriptions）至本地参数 Schema。

---

## 📂 项目目录结构

```text
agent-oncall/
├── proto/
│   └── agent_oncall.proto       # Protobuf 协议数据契约定义
├── src/agent_oncall/
│   ├── pb/                      # 自动生成的 Python Protobuf 绑定
│   ├── crypto.py                # Ed25519 密钥管理、签名生成与校验
│   ├── policy.py                # 信任评级管理、策略引擎与能力令牌（HCT）校验
│   ├── hitl.py                  # 可插拔的 HITL 拦截器实现（控制台命令行交互）
│   ├── comm.py                  # 传输适配层（包括 Mock 通道与 agent-comm 子进程包装器）
│   ├── stdin_handler.py         # STDIN/STDOUT 子进程管道模式运行器（模式 A）
│   ├── alignment.py             # 服务描述对齐（SDA）状态机实现
│   └── core.py                  # AgentOnCall 核心调度器与路由管理器
├── tests/
│   └── test_agent_oncall.py     # 完善的 pytest 单元测试用例
├── run_demo.py                  # 完整的场景编排演示脚本
└── pyproject.toml               # 项目配置、依赖与打包定义 (uv)
```

---

## 🚀 快速上手

### 1. 环境准备与依赖安装

推荐使用 `uv` 管理 Python 依赖：
```bash
# 同步虚拟环境并安装所有依赖
uv sync
```

### 2. 基础 API 使用示例

以下是一个注册本地意图并执行基于策略鉴权的远程 RPC 调用的简单示例：

```python
from agent_oncall import AgentOnCall, MockCommAdapter, crypto, TIER_2_FRIEND

# 1. 初始化模拟的 P2P 传输网络
comm = MockCommAdapter()

# 2. 生成身份密钥对
alice_priv, alice_pub = crypto.generate_keypair()
bob_priv, bob_pub = crypto.generate_keypair()

# 3. 初始化 Alice 智能体
alice = AgentOnCall(
    agent_urn="urn:hermes:agent:alice",
    private_key_hex=crypto.private_key_to_hex(alice_priv),
    comm_adapter=comm
)
comm.register_agent(alice.agent_urn, alice)

# 4. 初始化 Bob 智能体
bob = AgentOnCall(
    agent_urn="urn:hermes:agent:bob",
    private_key_hex=crypto.private_key_to_hex(bob_priv),
    comm_adapter=comm
)
comm.register_agent(bob.agent_urn, bob)

# 5. 互加好友并设定信任等级
alice.trust_db.add_contact(bob.agent_urn, crypto.public_key_to_hex(bob_pub), TIER_2_FRIEND)
bob.trust_db.add_contact(alice.agent_urn, crypto.public_key_to_hex(alice_pub), TIER_2_FRIEND)

# 6. Alice 注册对外可导出的本地工具（带入参 Schema 校验）
alice.register_intent(
    name="calendar.query_availability",
    description="查询日程空闲状态",
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": "string"}
        },
        "required": ["date"]
    },
    handler=lambda sender, args: {"date": args["date"], "available": True}
)

# 7. Bob 发起安全远程调用
response = bob.call_remote("urn:hermes:agent:alice", "calendar.query_availability", {"date": "2026-06-01"})
print("调用结果:", response)
# 输出: {'success': True, 'result': {'date': '2026-06-01', 'available': True}}
```

### 3. 子进程管道桥接集成 (模式 A)

您可以使用 `StdinStdoutHandler` 编写脚本，作为子进程挂载在 `agent-comm listen` 背后，以 JSON 流接收并处理消息：

```python
import sys
from agent_oncall import AgentOnCall, StdinStdoutHandler

# 初始化您的 AgentOnCall 智能体实例 ...
agent = AgentOnCall(...)

# 启动 STDIN/STDOUT 事件轮询环
handler = StdinStdoutHandler(agent)
handler.run_loop()
```

输入载荷格式 (Stdin 输入):
```json
{
  "event": "message_received",
  "sender_urn": "urn:hermes:agent:bob",
  "payload_base64": "Gg0KB2hlbGxv..."
}
```

响应载荷格式 (Stdout 输出):
```json
{
  "event": "send_reply",
  "payload_base64": "Hh8KB29uY2Fsb..."
}
```

---

## 🧪 验证与测试

### 运行自动化单元测试
运行 pytest 执行全面的安全性、鉴权、管道和状态机测试：
```bash
uv run pytest tests/
```

### 运行场景演示
运行包含动态发现过滤、直接策略校验限制、能力令牌（HCT）授权绕过、人机交互拦截（HITL）机制、以及服务描述对齐（SDA）的完整场景仿真：
```bash
uv run python run_demo.py
```
