# 本地 Modbus 协议编解码与设备模拟器

纯 Python（仅标准库）实现的 **本地** Modbus 编解码器与多设备模拟器。
所有寄存器、协议帧、设备状态和测试数据只存在于进程内存或本地文件中：

- ❌ 不连接 PLC、真实串口、工业设备或任何云平台/外部服务
- ✅ 单元测试中的网络通信全部走 `127.0.0.1` localhost socket
- ✅ RTU 模式用 localhost TCP 承载 `地址 + PDU + CRC16` 帧，仅用于在无串口
  硬件的终端环境里验证 RTU 编解码与 CRC

需要 Python 3.10+，无任何第三方运行时依赖（测试使用 `pytest`）。

## 目录结构

```
modbus_sim/
├── crc16.py       # CRC-16/MODBUS（多项式 0xA001，初值 0xFFFF，小端发送）
├── exceptions.py  # Modbus 标准异常码与错误类型
├── registers.py   # 设备模型：独立的保持/输入寄存器空间、16/32 位与字序
├── pdu.py         # PDU 功能码编解码、校验、执行与异常响应
├── mbap.py        # Modbus TCP MBAP 头与流式分帧器（粘包/半包重组）
├── rtu.py         # RTU 帧（地址+PDU+CRC16）与流式分帧器
├── bus.py         # 多 unit 设备总线，unit id 严格隔离
├── config.py      # 本地 JSON 设备配置加载
├── server.py      # TCP 服务器 / RTU-over-localhost 服务器
├── client.py      # 配套阻塞式客户端（测试与 CLI 使用）
└── cli.py         # 命令行入口
examples/devices.json
tests/             # 78 个自动化测试，全部可在普通终端执行
```

## 支持的协议范围

### Modbus TCP（MBAP header）

MBAP 7 字节：`事务ID(2) | 协议ID(2,=0) | 长度(2) | 单元ID(1)`，后接 PDU。
长度字段统计“单元 ID + PDU”的字节数。

| 功能码 | 功能 | 请求/响应要点 |
|--------|------|----------------|
| `0x03` | 读保持寄存器 Read Holding Registers | 地址、数量（1–125）大端；响应带 byte count |
| `0x04` | 读输入寄存器 Read Input Registers | 输入寄存器只读，数量 1–125 |
| `0x06` | 写单个寄存器 Write Single Register | 响应原样回显请求 |
| `0x10` | 写多个寄存器 Write Multiple Registers | 数量 1–123，带 byte count；响应回地址+数量 |

### Modbus RTU（PDU + CRC16）

帧格式：`从站地址(1) | PDU | CRC低字节 | CRC高字节`

- CRC 为标准 **CRC-16/MODBUS**：初值 `0xFFFF`、多项式（反射）`0xA001`；
  校验值对 ASCII `123456789` 为 `0x4B37`（测试中有断言）。
- 本地用 localhost socket 承载 RTU 帧，不打开任何真实串口。

### 异常响应

异常响应把功能码最高位置 1（`fc | 0x80`），第二字节为异常码：

| 异常码 | 触发条件 |
|--------|----------|
| `0x01` Illegal Function | 不支持/非法的功能码（如 `0x05`）；对只读输入空间写入 |
| `0x02` Illegal Data Address | 起始地址越界，或 地址+数量 超出寄存器空间 |
| `0x03` Illegal Data Value | PDU 长度错误、数量为 0/超上限、byte count 与数量不符、载荷截断 |
| `0x0B` Gateway Target No Response | TCP 请求的 unit id 不存在（绝不会落到其他设备的数据上） |
| `0x04` Slave Device Failure | 可选：RTU 模式下收到 CRC 错误帧时的显式应答（测试钩子） |

RTU 按规范默认 **静默丢弃 CRC 错误帧**；启动 `rtu --crc-error-response`
可改为返回 `0x80 0x04` 异常帧，便于确定性测试。广播地址 `0`（仅写请求）
被接受但不回应；发给不存在从站的帧同样不回应。

## 寄存器模型

每个 unit id 对应一台独立设备，拥有两块相互独立的 16 位寄存器空间：

- **保持寄存器**（`0x03` 读 / `0x06`、`0x10` 写），容量可配置（示例 100）
- **输入寄存器**（`0x04` 只读），容量可配置（示例 50）

设备之间完全隔离：unit 1 无法读到 unit 2 的任何寄存器；错误/未知的
unit id 在 TCP 上得到 `0x0B` 异常，在 RTU 上得不到任何响应。

### 字节序 / 字序（大小端规则）

- 每个 **16 位寄存器在链路上恒为大端**（Modbus 标准，不可配置）。
- 一个 **32 位值占两个连续寄存器**；高/低 16 位字的存放顺序可按设备配置
  （`word_order`），四种取值表示 32 位值字节 `A B C D` 在链路上的顺序：

| 配置 | 低地址寄存器 | 高地址寄存器 | 说明 |
|------|--------------|--------------|------|
| `ABCD`（默认） | `A B` = 高字 | `C D` = 低字 | 全大端，最常见 |
| `CDAB` | `C D` = 低字 | `A B` = 高字 | **字交换**（很多 PLC 默认） |
| `BADC` | `B A` | `D C` | 大端字序 + 字内字节交换 |
| `DCBA` | `D C` | `B A` | 全小端 |

以 `0x11223344` 为例，两个寄存器的内容分别为：

```
ABCD -> [0x1122, 0x3344]
CDAB -> [0x3344, 0x1122]
BADC -> [0x2211, 0x4433]
DCBA -> [0x4433, 0x2211]
```

`Device` 提供 `read_u32 / write_u32 / read_i32 / read_float32 /
write_float32`（32 位 IEEE754 浮点，按同一字序映射）。

## TCP 粘包 / 半包处理

`mbap.TcpFramer` 是有状态的流式重组器：把每次 `socket.recv()` 得到的
任意长度字节块交给 `feed()`，它依据 MBAP 长度字段切分出 0..N 个完整
帧并缓冲剩余字节，**绝不假设一次 read 就是一个完整 frame**。以下情况
均有测试覆盖：逐字节到达、先到 MBAP 后到 PDU、一次 read 含多个帧、
切分点落在任意字段中间（3 字节碎片）等。RTU 侧 `rtu.RtuFramer` 对固定
长度请求、byte-count 驱动的写多帧、变长读响应和异常帧同样支持流式重组。

## 使用方法

进程内自测（启动内存服务器并做一轮读写）：

```bash
python3 -m modbus_sim.cli demo
```

用本地 JSON 配置启动 Modbus TCP 模拟器：

```bash
python3 -m modbus_sim.cli tcp --config examples/devices.json --host 127.0.0.1 --port 1502
```

启动 RTU-over-localhost 模拟器（无真实串口；端口仅承载 RTU 帧）：

```bash
python3 -m modbus_sim.cli rtu --config examples/devices.json --port 1503
# 可选：--crc-error-response 对 CRC 错帧返回异常应答
```

配置文件示例见 `examples/devices.json`：

```json
{
  "devices": [
    {
      "unit_id": 1,
      "holding_size": 100,
      "input_size": 50,
      "word_order": "ABCD",
      "holding": {"0": 1111, "10": [100, 200, 300]},
      "inputs":  {"0": 9001}
    }
  ]
}
```

`holding` / `inputs` 的种子值既可以是 `"地址": 16位整数`，也可以用
`"起始地址": [值1, 值2, ...]` 连续填充。

作为库使用：

```python
from modbus_sim import DeviceConfig, DeviceBus
from modbus_sim.server import TcpModbusServer
from modbus_sim.client import TcpClient

bus = DeviceBus()
bus.add_config(DeviceConfig(1, holding_size=100, input_size=50, word_order="CDAB"))
server = TcpModbusServer(bus, port=1502)
# ... server.serve_forever() ...
with TcpClient(port=1502) as c:
    c.write_multiple(1, 0, [0x3344, 0x1122])  # 以 CDAB 存放 0x11223344
    print(c.read_holding(1, 0, 2))
```

## 运行测试

测试全部在终端执行，不需要串口、PLC 或任何外部服务（仅监听
127.0.0.1 临时端口）：

```bash
# 如未安装 pytest：
python3 -m pip install pytest

# 运行全部 78 个用例
python3 -m pytest

# 也可不依赖 pytest，用 unittest 风格以外的方式运行单个文件（可选）
python3 -m pytest tests/test_pdu.py -v
```

测试覆盖范围：

- 16 位读/写：`0x03 / 0x04 / 0x06 / 0x10` 的请求解析、响应编码与状态变更
- 多个 unit id 的寄存器隔离、未知 unit id（TCP `0x0B` / RTU 无响应）、RTU 广播
- CRC16 标准校验向量、帧往返、载荷/CRC 损坏检出
- 异常码：非法功能码、地址越界、数量/长度/byte-count 错误
- TCP 粘包与半包：逐字节、多帧一包、MBAP/PDU 切分、任意 3 字节碎片
- RTU 分帧：固定帧、写多帧 byte-count、变长读响应、5 字节异常帧
- 边界地址：最后一个寄存器可读、越界即异常、数量上下限（125/123）
- 32 位转换：`ABCD/CDAB/BADC/DCBA` 四种字序、有符号 32 位、float32 往返
- 本地 JSON 配置加载与重复 unit id 拒绝

## 安全边界说明

本项目仅用于本地协议开发、教学与测试。服务器只绑定 `127.0.0.1`，
不会主动连接任何外部地址；代码中不存在任何串口 I/O 或工业网络协议栈。
