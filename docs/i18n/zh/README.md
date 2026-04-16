<!-- i18n-sync: README.md @ ff11dce -->
<!-- i18n-sync-date: 2026-04-16 -->

[English](../../../README.md) | [中文](README.md)

# GamePadServer

GamePadServer 是一个部署在 Linux 服务器（树莓派）上的游戏手柄模拟服务。它对外提供统一的 REST / WebSocket API，接收外部程序的手柄操作指令，内部将指令转换为手柄信号发送到游戏主机。

**核心理念：统一接口，多后端实现。** 调用方不需要关心目标主机是什么、用的是蓝牙还是 USB，只需要通过同一套 API 发送"按 A 键"、"推左摇杆"等指令。

## 支持平台

| 主机 | 连接方式 | 模拟手柄 | 状态 |
|------|---------|---------|------|
| Nintendo Switch | 蓝牙 | Pro Controller | 已实现 |
| PlayStation 4 | USB | DualShock 4 | 计划中 |
| PlayStation 5 | USB | DualSense | 计划中 |
| Xbox One / Series | USB | Xbox 手柄 | 计划中 |

## 快速开始

### 环境要求

- **Linux**（推荐 Raspberry Pi 4）— Switch 后端使用自研蓝牙 HID 协议栈（L2CAP + Switch Pro Controller 协议），通过 BlueZ D-Bus 模拟虚拟手柄，因此**不支持 macOS 和 Windows**。在非 Linux 系统上服务可以启动，但创建手柄连接会失败。
- Python 3.10+
- 蓝牙适配器（Switch 方案）
- 需要 root 权限（蓝牙 HID 操作需管理 BlueZ）

### 安装

```bash
git clone <repo-url> GamePadServer
cd GamePadServer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 主机初始化

新机器首次运行前需要进行一次性系统配置：

```bash
sudo ./deploy/setup-host.sh
```

此脚本安装 bluetoothd systemd 覆盖配置并修改 `/etc/bluetooth/main.conf`。该脚本是幂等的，可安全重复执行。

### 启动

```bash
sudo .venv/bin/python -m gamepadserver
```

Server 默认监听 `0.0.0.0:8080`，可通过环境变量配置：

```bash
GAMEPAD_HOST=127.0.0.1 GAMEPAD_PORT=9090 sudo -E .venv/bin/python -m gamepadserver
```

### 测试页面

启动后访问 `http://<host>:8080/` 即可打开可视化测试页面，支持：

- 平台切换（Switch / PS4 / PS5 / Xbox），按键标签随平台自动变化
- 点击按键发送指令
- 拖拽虚拟摇杆实时操控
- 手柄连接状态显示

## API 使用

完整 API 文档启动后访问 `http://<host>:8080/docs`（Swagger UI）。

### 1. 创建手柄并连接

Switch 需先进入「更改握法/顺序」菜单（设置 > 手柄和传感器 > 更改握法/顺序），然后调用：

```bash
curl -X POST http://localhost:8080/api/v1/controllers \
  -H "Content-Type: application/json" \
  -d '{"platform": "switch"}'
```

返回：

```json
{"id": 0, "platform": "switch", "state": "connecting", "created_at": "..."}
```

### 2. 查询连接状态

```bash
curl http://localhost:8080/api/v1/controllers/0
```

等待 `state` 变为 `connected` 后即可发送指令。

### 3. 按键操作

```bash
# 按下 A 键（按下 0.1 秒后自动释放）
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["A"], "action": "press", "duration": 0.1}'

# 按住 B 键不放
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["B"], "action": "down"}'

# 释放 B 键
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["B"], "action": "up"}'

# 同时按多个键
curl -X POST http://localhost:8080/api/v1/controllers/0/buttons \
  -H "Content-Type: application/json" \
  -d '{"buttons": ["L", "R"], "action": "press"}'
```

### 4. 摇杆操作

```bash
# 左摇杆推到右上方
curl -X POST http://localhost:8080/api/v1/controllers/0/stick \
  -H "Content-Type: application/json" \
  -d '{"stick": "left", "x": 100, "y": 100}'

# 回中
curl -X POST http://localhost:8080/api/v1/controllers/0/stick \
  -H "Content-Type: application/json" \
  -d '{"stick": "left", "x": 0, "y": 0}'
```

摇杆 x/y 范围 `-100 ~ 100`，`0` 为中心位置，设置后持续保持直到下一次调用。

### 5. WebSocket 实时输入

适用于需要低延迟连续输入的场景（如实时操控）：

```javascript
const ws = new WebSocket("ws://localhost:8080/ws/controllers/0/input");

// 发送完整手柄状态帧
ws.send(JSON.stringify({
  buttons: { A: true },
  left_stick: { x: 50, y: 0 },
  right_stick: { x: 0, y: 0 }
}));

// 接收确认
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  // { "type": "ack", "timestamp": 1681380000000 }
};
```

### 6. 断开手柄

```bash
curl -X DELETE http://localhost:8080/api/v1/controllers/0
```

## 按键枚举

按键名称跟随硬件标签，而非物理位置。

### Switch / Xbox

`A` `B` `X` `Y` — 名称相同但物理位置不同（Switch A 在右侧，Xbox A 在下方）

### PlayStation

`CROSS` `CIRCLE` `SQUARE` `TRIANGLE` — PS 平台不接受 A/B/X/Y

### 全平台通用

`L` `R` `ZL` `ZR` `PLUS` `MINUS` `HOME` `CAPTURE` `DPAD_UP` `DPAD_DOWN` `DPAD_LEFT` `DPAD_RIGHT` `L_STICK` `R_STICK`

## 运行测试

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## 项目结构

```
gamepadserver/
├── __main__.py              # 启动入口（uvicorn）
├── app.py                   # FastAPI 应用 + ControllerManager 单例
├── config.py                # 配置（GAMEPAD_* 环境变量）
├── api/
│   ├── controllers.py       # /api/v1/controllers REST 端点
│   ├── system.py            # /health, /api/v1/system/adapters
│   └── ws.py                # WebSocket /ws/controllers/{id}/input
├── core/
│   ├── backend.py           # GamepadBackend 抽象接口
│   ├── manager.py           # ControllerManager 生命周期管理
│   └── models.py            # 枚举、Pydantic 模型、验证
├── bluetooth/               # 自研蓝牙 HID 协议栈
│   ├── adapter.py           # BlueZ 适配器配置（D-Bus + hciconfig）
│   ├── sdp.py               # SDP 注册（sdptool / D-Bus 双路径）
│   ├── l2cap.py             # L2CAP 套接字管理（PSM 17+19）
│   ├── switch_protocol.py   # Switch HID 握手状态机
│   ├── switch_report.py     # 50 字节输入报告编解码
│   └── constants.py         # 协议常量、按键映射、SPI 模板
├── backends/
│   └── switch.py            # SwitchBackend（使用 bluetooth/ 模块）
└── static/
    └── index.html           # 可视化测试页面
```

## 技术文档

详见 [SPEC.md](../../../SPEC.md)。

## License

MIT
