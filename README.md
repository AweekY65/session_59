# 本地 Modbus 编解码器与设备模拟器

纯 Python（仅标准库）实现的 Modbus **协议编解码库 + 多设备模拟器**。
所有寄存器、协议帧、设备状态和测试数据只存在于**内存或本地 JSON 文件**中；
不连接任何 PLC、真实串口、工业设备或云平台。网络部分仅绑定 `127.0.0.1`
（localhost）用于自动化测试与本地联调，传输的字节可以是：

- Modbus TCP（MBAP 帧），或
- 套在 localhost socket 里的 **裸 RTU 帧**（含 CRC16），用于无串口硬件下
  端到端测试 RTU 编解码。

## 目录结构

```
modbus/
  __init__.py     公共 API
  crc.py          CRC-16/Modbus（多项式 0xA001，初值 0xFFFF）
  pdu.py          PDU 编解码：功能码、异常码、严格长度/数量校验
  device.py       模拟设备：独立寄存器空间、32 位 word order、JSON 装载
  rtu.py          RTU 帧编解码、CRC 校验、RTUFramer（粘包/半包）、RTUServer
  tcp.py          MBAP 编解码、TCPFramer（粘包/半包）、TCPServer
  __main__.py     命令行启动 TCP 模拟器
tests/            自动化测试（unittest，无需任何硬件）
devices.example.json  本地设备/寄存器配置示例
```

## 协议支持范围

| 功能码 | 功能 | 读 | 写 |
|---|---|---|---|
| `0x03` | Read Holding Registers | ✅ | — |
| `0x04` | Read Input Registers | ✅ | — |
| `0x06` | Write Single Register | — | ✅ |
| `0x10` | Write Multiple Registers | — | ✅ |

传输层：

- **Modbus TCP**：完整 MBAP 头（Transaction id / Protocol id=0 / Length /
  Unit id），Transaction id 原样回显。
- **Modbus RTU**：`Unit id + PDU + CRC16`，CRC 低字节在前、高字节在后。
- 多 **unit id**：每个设备拥有互相隔离的 Holding/Input 寄存器空间；
  错误/未注册的 unit id 无法访问任何其他设备的数据。

## 寄存器模型

- 每个 unit 有两个 16 位寄存器区：**Holding**（`0x03/0x06/0x10`）与
  **Input**（`0x04` 只读），地址从 0 开始，各自独立配置大小（1..65536）。
- 未写入的地址读回 `0`；寄存器使用稀疏字典存储，默认 65536 个地址几乎不占内存。
- 边界检查：起始地址越界、或 `address + quantity` 超出该区大小，均返回
  `02 Illegal Data Address`。

## 字节序与 32 位值

- **单寄存器**：按 Modbus 规范一律大端（big-endian）在线上传输，如 `0x1234`
  的线序为 `12 34`。
- **32 位值占两个寄存器**，两个 16 位 word 的先后顺序可按设备配置：
  - `WordOrder.HIGH_WORD_FIRST`（默认，别名 `"abcd"` / big-endian word）：
    `0x12345678` → 寄存器 `[0x1234, 0x5678]`
  - `WordOrder.LOW_WORD_FIRST`（别名 `"dcba"` / little-endian word）：
    `0x12345678` → 寄存器 `[0x5678, 0x1234]`
- 提供 `read_u32/write_u32`（无符号）与 `read_i32/write_i32`（有符号，
  二进制补码）；每次调用还可临时传入 word order 覆盖设备默认值。

## 异常响应

异常 PDU 为 `原功能码 | 0x80` + 异常码。实现的映射：

| 场景 | 异常码 |
|---|---|
| 非法功能码（如 `0x05`） | `01 Illegal Function` |
| 地址越界（起始地址或范围超出寄存器区） | `02 Illegal Data Address` |
| 长度/数量错误（帧长不符、byte count 不符、数量 0 或 >125/123） | `03 Illegal Data Value` |
| RTU CRC 错误（`crc_policy="respond"` 时） | `04 Slave Device Failure` |
| TCP 请求未注册的 unit id | `0B Gateway Target Device Failed To Respond` |
| Protocol id ≠ 0 | `04 Slave Device Failure` |

关于 **CRC 错误**的说明：真实串行总线上的从站通常直接丢弃 CRC 错误帧、
静默不答。本模拟器提供两种策略：

- `respond`（默认，便于测试与满足异常响应需求）：回 `04` 异常响应；
- `drop`（贴近真实 RTU 从站）：静默丢弃。

RTU 上对未注册 unit id 的请求一律**静默**（与多站串行总线行为一致）；
TCP 上则回 `0B`（Modbus TCP 网关语义）。

## 粘包 / 半包处理

- TCP 是字节流：`TCPFramer.feed()` 严格依据 MBAP 的 **Length** 字段重组，
  一次 `recv` 含多帧（粘包）会被逐帧切出，帧未到齐（半包）则继续缓存，
  绝不假设一次 read 就是完整帧。
- RTU：`RTUFramer.feed()` 对已知功能码按帧长预测立即成帧；无法预测长度的
  帧（如非法功能码）在 `flush()`（模拟 3.5 字符空闲间隔）时收尾。
- 测试在真实 localhost socket 上验证了逐字节发送、任意切点分包、多帧合并
  发送等场景。

## 运行模拟器

```bash
# 内置内存演示数据（unit 1..3，端口 127.0.0.1:5020）
python3 -m modbus

# 使用本地 JSON 配置
python3 -m modbus --config devices.example.json --port 1502
```

配置文件格式见 `devices.example.json`：

```json
{
  "devices": [
    {
      "unit_id": 1,
      "size": 1000,
      "input_size": 1000,
      "word_order": "high_word_first",
      "holding": {"0": [101, 202, 303], "10": [4660, 22136]},
      "input": {"0": [1111, 2222, 3333]}
    }
  ]
}
```

## 运行测试

无需串口、PLC 或任何外部服务，纯终端执行：

```bash
python3 -m unittest discover -s tests -v
```

覆盖场景（共 59 个用例）：

- Holding/Input 读写、单写/多写及正常响应格式
- 多 unit 隔离、未注册 unit（TCP `0B` / RTU 静默）
- CRC16 校验向量、坏帧检测、`respond`/`drop` 两种策略
- 非法功能码 `01`、地址越界 `02`、长度/数量错误 `03`
- TCP 与 RTU 的半包、粘包、逐字节、任意切点重组
- 边界地址（区末地址合法、跨边界非法）
- 32 位有符号/无符号与 ABCD/DCBA 两种 word order
- MBAP Transaction id 回显、Protocol id 错误、并发连接
- 从本地 JSON 装载设备与寄存器数据

## 快速上手（作为库使用）

```python
from modbus import ModbusDevice, DeviceMap, WordOrder
from modbus.pdu import encode_read_request
from modbus.tcp import TCPServer, encode_mbap

dev = ModbusDevice(unit_id=1, size=100, word_order=WordOrder.HIGH_WORD_FIRST)
dev.write_u32(0, 0x12345678)

server = TCPServer(DeviceMap([dev]), port=5020)  # 127.0.0.1 only
server.serve_forever()
```
