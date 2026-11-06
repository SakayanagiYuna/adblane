# adblane

支持取消、输出上限和前后台调度的 Python ADB 会话库。
需要 Python 3.10+，运行时无第三方依赖。

[English](README.md)

## 安装

安装 [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools)，
将 `adb` 加入 `PATH`，然后在项目目录执行：

```sh
python -m pip install .
```

## 快速上手

通过 `adb devices` 获取设备序列号：

```python
from adblane import AdbSession

with AdbSession("YOUR_DEVICE_SERIAL") as device:
    sdk = device.read(
        "getprop ro.build.version.sdk",
        timeout=5,
        max_bytes=4096,
    )
    print(sdk.decode().strip())
```

`read()` 返回字节并检查远端命令的退出状态。多个线程访问同一设备时，
共享一个会话实例；退出 `with` 块会关闭会话及其本机 ADB 客户端。

## 功能

- 前台、后台两种优先级，共享并发上限。
- 通过 `threading.Event` 取消请求，超时覆盖排队和执行。
- 限制输出大小，可校验精确字节数。
- 小规模读取复用 shell，较大读取使用 `exec-out`。
- 显式重试只读操作，并限制尝试次数。

## 文档

- [API 参考与示例](docs/api.md)
- [开发与测试](docs/development.md)

## 许可证

[MIT](LICENSE)。Android Platform Tools 需另行安装。
