# agent-oncall：智能体安全调用与意图发现协议规格说明书 (Specification) 📞

本规格说明书（Spec）专为开发独立的 `agent-oncall` 库设计。该库作为智能体框架（如 Hermes/OpenClaw）的**安全协同与 RPC 发现层**，通过 `agent-comm` 或其他通信管道进行底层的安全传输。

你可以将此文档直接复制到全新的项目中作为开发大纲与设计蓝本。

---

## 🏗️ 1. 软件架构设计 (Software Architecture)

`agent-oncall` 被定位为一个独立的库（推荐使用 Python 或 Go 编写，以下以 Python 为例进行接口设计）。它作为中间件连接**智能体框架（Host Agent）**与**通信层（agent-comm）**。

```
+-------------------------------------------------------------------------+
|                    Host Agent Framework (Hermes / OpenClaw)             |
+------------------------------------+------------------------------------+
                                     | 1. Register Intents / Call Remote
                                     v
+------------------------------------+------------------------------------+
|                               agent-oncall                              |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  |                           Router & Manager                        |  |
|  |     - Dispatch incoming RPCs      - Load/Validate Schemas         |  |
|  +-----------------+---------------------------------+---------------+  |
|                    |                                 |                  |
|  +-----------------v-----------------+     +---------v---------------+  |
|  |             Policy Engine         |     |    Discovery Protocol   |  |
|  |   - Verify Caller URN Trust       |     |   - Capability query    |  |
|  |   - Parse Capability Tokens (HCT) |     |   - Scheme advertising  |  |
|  +-----------------+-----------------+     +-------------------------+  |
|                    |                                                    |
|  +-----------------v-----------------+                                  |
|  |          HITL Interceptor         |                                  |
|  |   - Ask User for High-Risk action |                                  |
|  +-----------------+-----------------+                                  |
+--------------------|----------------------------------------------------+
                     | 2. Serialize to Protobuf & Send
                     v
+--------------------+----------------------------------------------------+
|                         agent-comm (Security & Transport)               |
|            - P2P E2EE  - Identity URN Proofs  - Off-line MQ             |
+-------------------------------------------------------------------------+
```

---

## 🧬 2. 数据契约定义 (`proto/agent_oncall.proto`)

`agent-oncall` 的所有网络传输数据包均使用 Protobuf 进行序列化，包装成二进制流由 `agent-comm` 进行加密传输。

```proto
syntax = "proto3";

package agent_oncall.v1;

// 统一的外部通信封套
message OnCallEnvelope {
  string version = 1;          // 协议版本 (e.g., "1.0.0")
  int64 timestamp = 2;         // 发送时间戳 (防止重放攻击)
  string signature = 3;        // 发送方对其 Payload 签名的十六进制串
  
  oneof payload {
    CallRequest call_request = 4;
    CallResponse call_response = 5;
    DiscoveryRequest discovery_request = 6;
    DiscoveryResponse discovery_response = 7;
  }
}

// 意图调用请求
message CallRequest {
  string request_id = 1;       // 调用 UUID
  string caller_urn = 2;       // 调用方的 URN (例如 urn:hermes:agent:alice...)
  string intent_name = 3;      // 请求调用的意图名 (例如 "calendar.book_event")
  string arguments_json = 4;   // 调用的结构化参数 (符合 Intent Schema)
  CapabilityToken token = 5;   // 携带的能力令牌，如果是链式授权的话
}

// 意图调用响应
message CallResponse {
  string request_id = 1;
  bool success = 2;
  int32 error_code = 3;        // 错误状态码 (e.g., 0=OK, 403=Forbidden, 500=ExecError)
  string error_message = 4;
  string result_json = 5;      // 调用结果 JSON
}

// 意图发现请求 (Capability Discovery)
message DiscoveryRequest {
  string request_id = 1;
  string query_urn = 2;        // 发起查询的 URN
  string category_filter = 3;  // 可选过滤分类 (e.g., "calendar", "files")
}

// 意图发现响应
message DiscoveryResponse {
  string request_id = 1;
  repeated IntentMetadata intents = 2; // 允许该查询者调用的意图列表
}

// 意图元数据声明
message IntentMetadata {
  string name = 1;             // 意图名 (e.g., "calendar.query_availability")
  string description = 2;      // 描述，供 LLM 理解作用
  string input_schema_json = 3;// 符合 JSON Schema 规范的入参定义
  string output_schema_json = 4;// 响应的 Schema 定义
  bool requires_hitl = 5;      // 执行该意图是否默认需要宿主的人类确认
}

// 基于能力的授权令牌 (HCT - Capability Token)
message CapabilityToken {
  string issuer_urn = 1;       // 授权签发者
  string audience_urn = 2;     // 被授权者
  int64 expires_at = 3;        // 过期时间
  repeated AllowedConstraint constraints = 4; // 限制规则
  bytes signature = 5;         // 签发者对该 Token 的 Ed25519 签名
}

message AllowedConstraint {
  string resource = 1;         // "file", "calendar", "command"
  string action = 2;           // "read", "write", "execute"
  repeated string filters = 3; // 限制性过滤，例如 "path=/workspace", "max_budget=0.50"
}
```

---

## 🗺️ 3. 意图发现协议规范 (Intent Discovery Protocol)

当 Alice 的 Agent (Hermes A) 首次尝试与 Bob 的 Agent (Hermes B) 合作时，如何发现对方具有哪些可用功能？

### 发现时序流 (Discovery Handshake Flow)

```
Alice's Agent                        Alice's OnCall                   Bob's OnCall                     Bob's Agent
      │                                    │                               │                                │
      │ 1. 查找可用功能                      │                               │                                │
      ├───────────────────────────────────>│                               │                                │
      │                                    │ 2. 构建 DiscoveryRequest       │                                │
      │                                    ├──────────────────────────────>│                                │
      │                                    │   (经 agent-comm 通道传输)     │                                │
      │                                    │                               │ 3. 评估安全策略                 │
      │                                    │                               ├──────────┐                     │
      │                                    │                               │          │ 根据 Alice_URN       │
      │                                    │                               │<─────────┘ 过滤暴露的意图        │
      │                                    │                               │                                │
      │                                    │ 4. 返回 DiscoveryResponse      │                                │
      │                                    │<──────────────────────────────│                                │
      │                                    │   (仅包含允许 Alice 调用的意图)│                                │
      │ 5. 返回 Intent 清单 (含 Schema)     │                               │                                │
      │<───────────────────────────────────┤                               │                                │
      │                                    │                               │                                │
      │ 6. LLM 决定填参并调用                │                               │                                │
      ├────────────────────────────────────┼──────────────────────────────>│                                │
      │                                    │      (发送 CallRequest)        │ 7. 派发执行任务                 │
      │                                    │                               ├───────────────────────────────>│
```

### 关键原则：
1.  **动态视图过滤 (Dynamic Visibility)**：Bob 的 Agent 在响应 Discovery 时，返回的意图列表是**动态根据 Alice 的 URN 过滤**的。如果是陌生人，响应可能直接为空或仅包含 `info.hello`；如果是好友，才展示 `calendar.query_availability`。
2.  **安全静态 Schema 缓存**：接收端可以在本地缓存对端 Agent 的意图定义（JSON Schema），后续调用时直接在本地完成参数类型校验，减少网络交互并防止注入异常数据。

---

## 🐍 4. Python API 核心接口代码模板 (Code Template)

你可以在新项目中实现以下核心类结构：

```python
import json
from typing import Callable, Dict, Any, List

class IntentMetadata:
    def __init__(self, name: str, description: str, input_schema: dict, requires_hitl: bool = False):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.requires_hitl = requires_hitl

class AgentOnCall:
    def __init__(self, agent_urn: str, comm_adapter):
        self.agent_urn = agent_urn
        self.comm_adapter = comm_adapter  # 用于对接 agent-comm CLI/Daemon 的适配器
        self.intents: Dict[str, Dict[str, Any]] = {}
        self.trust_database = {} # 保存联系人及其信任评级
        
    def register_intent(self, name: str, description: str, input_schema: dict, handler: Callable[[str, dict], Any], requires_hitl: bool = False):
        """
        供 Host Agent 注册可导出的本地工具
        """
        self.intents[name] = {
            "metadata": IntentMetadata(name, description, input_schema, requires_hitl),
            "handler": handler
        }

    def handle_incoming_envelope(self, sender_urn: str, raw_envelope_bytes: bytes) -> bytes:
        """
        当 agent-comm 收到数据包时的统一入口 (Callee)
        """
        envelope = self._deserialize_envelope(raw_envelope_bytes)
        
        # 1. 安全校验 (时间戳、重放、签名)
        if not self._verify_envelope_security(envelope, sender_urn):
            return self._build_error_response(envelope.request_id, 401, "Signature/Security Verification Failed")
            
        # 2. 路由派发
        if envelope.HasField("discovery_request"):
            return self._execute_discovery(sender_urn, envelope.discovery_request)
        elif envelope.HasField("call_request"):
            return self._execute_call(sender_urn, envelope.call_request)
        
        return self._build_error_response("unknown", 400, "Unsupported Payload")

    def call_remote(self, target_urn: str, intent_name: str, arguments: dict) -> dict:
        """
        供 Host Agent 调用外部智能体时的接口 (Caller)
        """
        request_id = self._generate_uuid()
        # 1. 组装 CallRequest
        # 2. 调用 comm_adapter 发送，并同步等待返回 (或异步 await)
        # 3. 解析返回的 CallResponse 并输出结果
        pass

    def _execute_call(self, sender_urn: str, request) -> bytes:
        """
        具体执行远程调用
        """
        intent_name = request.intent_name
        if intent_name not in self.intents:
            return self._build_error_response(request.request_id, 404, f"Intent {intent_name} not found")
            
        intent_info = self.intents[intent_name]
        metadata = intent_info["metadata"]
        
        # 1. 策略引擎鉴权 (Policy Check)
        allowed, reason = self.evaluate_policy(sender_urn, metadata)
        if not allowed:
            return self._build_error_response(request.request_id, 403, f"Policy block: {reason}")
            
        # 2. 人机交互确认 (HITL)
        if metadata.requires_hitl or self._should_trigger_hitl(sender_urn, intent_name):
            approved = self._trigger_human_approval(sender_urn, intent_name, request.arguments_json)
            if not approved:
                return self._build_error_response(request.request_id, 403, "User rejected the operation")
                
        # 3. 运行本地工具
        try:
            args = json.loads(request.arguments_json)
            result = intent_info["handler"](sender_urn, args)
            return self._build_success_response(request.request_id, result)
        except Exception as e:
            return self._build_error_response(request.request_id, 500, f"Execution failed: {str(e)}")

    def evaluate_policy(self, sender_urn: str, metadata: IntentMetadata) -> (bool, str):
        """
        基于 URN 与意图要求的策略判定器
        """
        trust_level = self.trust_database.get(sender_urn, "Tier_3_Stranger")
        
        # 举例策略逻辑
        if metadata.name == "calendar.query_availability" and trust_level in ["Tier_1_Family", "Tier_2_Friend"]:
            return True, "OK"
        if metadata.name == "calendar.book_event" and trust_level == "Tier_1_Family":
            return True, "OK"
        if trust_level == "Tier_3_Stranger":
            return False, "Strangers are not allowed to invoke intents automatically"
            
        return False, "Default deny"
```

---

## 🔌 5. 与 `agent-comm` 通信层的桥接约定 (IPC/CLI Contract)

既然作为独立库，它需要通过 `agent-comm` 发送与监听。`agent-comm` 的 Go 实现可以通过以下两种形式与之桥接：

### 模式 A：STDIN/STDOUT 管道模式 (子进程集成)
在接收端，`agent-comm listen` 保持长连接运行。每当收到发往本地 URN 的加密流时，它在本地解密并以 JSON 格式输出至 Python 子进程的 stdin，Python 侧计算出响应后写入 stdout 返回。
*   **Stdin 载荷格式**：
    ```json
    {
      "event": "message_received",
      "sender_urn": "urn:hermes:agent:alice...",
      "payload_base64": "Gg0KB2hlbGxv..."
    }
    ```
*   **Stdout 响应格式**：
    ```json
    {
      "event": "send_reply",
      "payload_base64": "Hh8KB29uY2Fsb..."
    }
    ```

### 模式 B：本地 Localhost gRPC 模式 (双进程 Sidecar)
`agent-comm` 作为系统级的 Daemon 服务启动，在本地监听一个 Unix Socket 或 Localhost 端口（例如 `localhost:50051`）。
*   `agent-oncall` (Python 库) 通过 gRPC 客户端连接 `agent-comm` Daemon，调用 `SendMessage` 发送 E2EE 数据。
*   `agent-oncall` 保持一个 gRPC Stream 监听来自 `agent-comm` 的入站通知。
